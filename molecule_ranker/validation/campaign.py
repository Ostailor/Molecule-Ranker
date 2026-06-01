from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.campaigns import (
    Campaign,
    CampaignBudget,
    CampaignMemo,
    CampaignObjective,
    CampaignPlan,
    CampaignStore,
    CampaignWorkPackage,
    build_campaign_approval_gate,
    build_generated_molecule_review_gate,
    check_budget_constraints,
    compute_campaign_budget_summary,
    contains_procedural_lab_detail,
    evaluate_replanning,
    import_external_status_update,
)
from molecule_ranker.integrations.schemas import ExternalRecordRef
from molecule_ranker.validation.reports import write_json_artifact, write_markdown_artifact

CampaignValidationFixture = Literal[
    "golden",
    "protocol_text",
    "generated_no_review_gate",
    "codex_invented_cost",
]

CAMPAIGN_VALIDATION_STEPS = [
    "build synthetic hypotheses",
    "build synthetic portfolio",
    "create campaign",
    "plan under budget",
    "create stage gates",
    "generate campaign memo",
    "trigger replan from synthetic assay result",
    "export campaign",
    "verify guardrails",
]

CAMPAIGN_GUARDRAIL_CATEGORIES = [
    "no_procedural_work_package_text",
    "no_chemistry_route_text",
    "no_administration_guidance",
    "no_clinical_care_guidance",
    "codex_cannot_approve_gates",
    "generated_molecules_require_review_gate",
    "failed_qc_does_not_create_false_conclusion",
    "external_status_does_not_create_assay_result",
    "campaign_plan_is_advisory",
    "codex_cannot_invent_costs",
]

_TEXT_ARTIFACT_SUFFIXES = {".json", ".md", ".txt", ".csv"}
_CHEMISTRY_ROUTE_RE = re.compile(
    r"\b(synthesis route|synthetic route|synthesis instructions|reagent|reagents)\b",
    re.IGNORECASE,
)
_ADMINISTRATION_RE = re.compile(r"\b(dosage|dosing|dose|administer)\b", re.IGNORECASE)
_CLINICAL_CARE_RE = re.compile(
    r"\b(patient treatment|clinical guidance|patient guidance|treats|cures|prevents)\b",
    re.IGNORECASE,
)
_FAILED_QC_FALSE_CONCLUSION_RE = re.compile(
    r"\b(failed qc|qc failed)\b.{0,120}\b(candidate is inactive|candidate inactive|retire "
    r"candidate|stop for lack of activity|failed target)\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CampaignGuardrailFinding:
    category: str
    check_id: str
    severity: str
    artifact_path: str
    message: str
    excerpt: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "check_id": self.check_id,
            "severity": self.severity,
            "artifact_path": self.artifact_path,
            "message": self.message,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class CampaignGuardrailAuditReport:
    status: Literal["pass", "fail"]
    root_dir: str
    artifact_count: int
    categories: list[str]
    findings: list[CampaignGuardrailFinding]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "root_dir": self.root_dir,
            "artifact_count": self.artifact_count,
            "categories": self.categories,
            "findings": [finding.as_dict() for finding in self.findings],
            "finding_count": len(self.findings),
        }


@dataclass(frozen=True)
class CampaignValidationReport:
    status: Literal["pass", "fail"]
    output_dir: str
    fixture: CampaignValidationFixture
    artifacts: list[str]
    required_steps: list[str]
    campaign_count: int
    work_package_count: int
    stage_gate_count: int
    replan_trigger_count: int
    guardrail_audit: CampaignGuardrailAuditReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": self.output_dir,
            "fixture": self.fixture,
            "artifacts": self.artifacts,
            "required_steps": self.required_steps,
            "campaign_count": self.campaign_count,
            "work_package_count": self.work_package_count,
            "stage_gate_count": self.stage_gate_count,
            "replan_trigger_count": self.replan_trigger_count,
            "guardrail_audit": self.guardrail_audit.as_dict(),
        }


def run_campaign_validation(
    *,
    output_dir: str | Path = ".molecule-ranker/validation/campaign",
    fixture: CampaignValidationFixture = "golden",
) -> CampaignValidationReport:
    resolved = Path(output_dir).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        (resolved / f"campaign_validation.sqlite{suffix}").unlink(missing_ok=True)
    workflow = _write_campaign_validation_workflow(resolved, fixture=fixture)
    audit = run_campaign_guardrail_audit(resolved)
    artifacts = sorted(
        str(path.relative_to(resolved))
        for path in resolved.iterdir()
        if path.is_file() and path.suffix in _TEXT_ARTIFACT_SUFFIXES
    )
    report = CampaignValidationReport(
        status="pass" if audit.status == "pass" else "fail",
        output_dir=str(resolved),
        fixture=fixture,
        artifacts=artifacts,
        required_steps=CAMPAIGN_VALIDATION_STEPS,
        campaign_count=1,
        work_package_count=int(workflow["work_package_count"]),
        stage_gate_count=int(workflow["stage_gate_count"]),
        replan_trigger_count=int(workflow["replan_trigger_count"]),
        guardrail_audit=audit,
    )
    write_json_artifact(resolved / "campaign_validation_report.json", report.as_dict())
    _write_campaign_validation_markdown(resolved / "campaign_validation_report.md", report)
    return report


def run_campaign_guardrail_audit(path: str | Path) -> CampaignGuardrailAuditReport:
    root = Path(path).resolve()
    artifacts = [
        item
        for item in sorted(root.rglob("*"))
        if item.is_file()
        and item.suffix in _TEXT_ARTIFACT_SUFFIXES
        and item.name
        not in {
            "campaign_guardrail_audit.json",
            "campaign_guardrail_audit.md",
            "campaign_validation_report.json",
            "campaign_validation_report.md",
        }
    ]
    findings: list[CampaignGuardrailFinding] = []
    for artifact in artifacts:
        text = artifact.read_text(errors="ignore")
        findings.extend(_text_guardrail_findings(artifact, text))
        if artifact.suffix == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            findings.extend(_json_guardrail_findings(artifact, payload))
    report = CampaignGuardrailAuditReport(
        status="pass" if not findings else "fail",
        root_dir=str(root),
        artifact_count=len(artifacts),
        categories=CAMPAIGN_GUARDRAIL_CATEGORIES,
        findings=findings,
    )
    write_json_artifact(root / "campaign_guardrail_audit.json", report.as_dict())
    _write_campaign_guardrail_markdown(root / "campaign_guardrail_audit.md", report)
    return report


def _write_campaign_validation_workflow(
    output_dir: Path,
    *,
    fixture: CampaignValidationFixture,
) -> dict[str, int]:
    now = datetime.now(UTC)
    campaign_id = "campaign-v17-synthetic"
    campaign_plan_id = "campaign-plan-v17-synthetic"
    generated_candidate_id = "candidate-generated-v17-1"
    review_package_id = "wp-generated-review-v17"
    followup_package_id = "wp-generated-followup-v17"

    hypotheses = {
        "hypotheses": [
            {
                "hypothesis_id": "hypothesis-v17-1",
                "candidate_id": "candidate-existing-v17-1",
                "priority": 0.71,
                "status": "active",
                "source_artifacts": ["evidence-gap-v17-1"],
            },
            {
                "hypothesis_id": "hypothesis-generated-v17-1",
                "candidate_id": generated_candidate_id,
                "priority": 0.83,
                "status": "generated_computational_hypothesis",
                "generated_molecule": True,
                "source_artifacts": ["active-learning-v17-1"],
                "evidence": [],
            },
        ],
        "metadata": {
            "synthetic": True,
            "generated_molecules_are_computational_hypotheses": True,
        },
    }
    portfolio = {
        "portfolio_selection_id": "portfolio-selection-v17-1",
        "selected_candidate_ids": ["candidate-existing-v17-1", generated_candidate_id],
        "selection_rationale": "Synthetic portfolio fixture for campaign planning validation.",
        "metadata": {"deterministic_fixture": True},
    }
    write_json_artifact(output_dir / "hypotheses.json", hypotheses)
    write_json_artifact(output_dir / "portfolio_optimization.json", portfolio)

    campaign = Campaign(
        campaign_id=campaign_id,
        project_id="project-v17-synthetic",
        program_id="program-v17-synthetic",
        name="V1.7 Synthetic Campaign Validation",
        description="Synthetic campaign used to validate research-management planning artifacts.",
        disease_focus=["Synthetic Disease"],
        target_focus=["SYN1"],
        hypothesis_ids=[
            "hypothesis-v17-1",
            "hypothesis-generated-v17-1",
        ],
        portfolio_selection_ids=["portfolio-selection-v17-1"],
        status="draft",
        created_at=now,
        updated_at=now,
        metadata={"synthetic_validation": True},
    )
    objective = CampaignObjective(
        objective_id="objective-v17-1",
        campaign_id=campaign_id,
        name="Resolve learning gap for selected synthetic candidates",
        objective_type="close_evidence_gap",
        linked_hypothesis_ids=campaign.hypothesis_ids,
        linked_candidate_ids=["candidate-existing-v17-1", generated_candidate_id],
        success_criteria=["Review decision recorded against linked hypotheses."],
        stop_criteria=["Critical safety or developability review blocks continuation."],
        priority_weight=0.82,
        metadata={"linked_portfolio_selection_ids": campaign.portfolio_selection_ids},
    )
    review_package = CampaignWorkPackage(
        work_package_id=review_package_id,
        campaign_id=campaign_id,
        objective_ids=[objective.objective_id],
        package_type="expert_review",
        title="Generated candidate review",
        description="Human review of generated candidate rationale before follow-up triage.",
        linked_candidate_ids=[generated_candidate_id],
        linked_hypothesis_ids=["hypothesis-generated-v17-1"],
        high_level_activity_category="review_gate",
        dependencies=[],
        required_approvals=["generated_molecule_review_gate"],
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=1.0,
        estimated_compute_units=0.0,
        estimated_assay_slots=0,
        status="ready",
        blocking_reasons=[],
        warnings=[],
        metadata={
            "generated_molecule": True,
            "planning_object_only": True,
            "not_experimental_procedure": True,
        },
    )
    followup_package = CampaignWorkPackage(
        work_package_id=followup_package_id,
        campaign_id=campaign_id,
        objective_ids=[objective.objective_id],
        package_type="assay_triage_request",
        title="High-level follow-up triage request",
        description="Reserve one high-level triage slot after generated candidate review gate.",
        linked_candidate_ids=[generated_candidate_id],
        linked_hypothesis_ids=["hypothesis-generated-v17-1"],
        high_level_activity_category="management_triage",
        dependencies=[review_package_id],
        required_approvals=["generated_molecule_review_gate", "assay_triage_approval"],
        estimated_cost=None,
        cost_units=None,
        estimated_review_hours=0.5,
        estimated_compute_units=0.0,
        estimated_assay_slots=1,
        status="blocked",
        blocking_reasons=["generated candidate review gate pending"],
        warnings=["Selected candidate is not proven active, safe, effective, or useful."],
        metadata={
            "generated_molecule": True,
            "planning_object_only": True,
            "not_experimental_procedure": True,
        },
    )
    budget = CampaignBudget(
        budget_id="campaign-budget-v17-synthetic",
        campaign_id=campaign_id,
        max_total_cost=None,
        cost_units=None,
        max_assay_slots=1,
        max_review_hours=2.0,
        max_compute_units=1.0,
        max_codex_tasks=1,
        max_external_sync_jobs=1,
        reserved_budget={},
        metadata={
            "default_estimates": {
                "expert_review": {"review_hours": 1.0},
                "assay_triage_request": {"assay_slots": 1.0, "review_hours": 0.5},
            },
            "cost_basis": "relative_or_unknown_only",
            "require_generated_molecule_review": True,
        },
    )
    provisional_plan = CampaignPlan(
        campaign_plan_id=campaign_plan_id,
        campaign_id=campaign_id,
        objectives=[objective],
        work_packages=[review_package, followup_package],
        budget=budget,
        stage_gates=[],
        dependency_graph={
            "nodes": [review_package_id, followup_package_id],
            "edges": [
                {
                    "from": review_package_id,
                    "to": followup_package_id,
                    "dependency_type": "requires_review_before",
                }
            ],
        },
        expected_learning_value=0.76,
        risk_summary={"candidate_claims": "not_proven"},
        uncertainty_summary={"primary_uncertainty": "generated_candidate_review"},
        budget_summary={},
        recommended_sequence=[review_package_id, followup_package_id],
        replan_triggers=["failed_qc", "new_positive_result", "safety_concern"],
        human_approval_required=True,
        warnings=["Advisory campaign plan; human approval required before activation."],
        created_at=now,
        metadata={
            "advisory_plan": True,
            "deterministic_campaign_plan": True,
            "codex_computed_plan": False,
            "automatic_execution": False,
        },
    )
    budget_check = check_budget_constraints(provisional_plan, budget)
    gates = [
        build_campaign_approval_gate(campaign_id),
        build_generated_molecule_review_gate(review_package),
        build_generated_molecule_review_gate(followup_package),
    ]
    if fixture == "generated_no_review_gate":
        gates = [gate for gate in gates if gate.get("gate_type") != "generated_molecule_review"]
    plan = provisional_plan.model_copy(
        update={
            "stage_gates": gates,
            "budget_summary": {
                **compute_campaign_budget_summary(provisional_plan),
                "budget_check": budget_check,
            },
        },
        deep=True,
    )
    store = CampaignStore(output_dir / "campaign_validation.sqlite")
    store.create_campaign(campaign)
    store.save_campaign_plan(plan)
    for gate in gates:
        store.add_stage_gate_decision(gate)

    memo = CampaignMemo(
        memo_id="campaign-memo-v17-synthetic",
        campaign_id=campaign_id,
        title="V1.7 Synthetic Campaign Memo",
        executive_summary=(
            "Deterministic advisory campaign plan for linked synthetic hypotheses and "
            "portfolio selections."
        ),
        objectives_summary=(
            "One objective links generated and existing candidates to source artifacts."
        ),
        selected_work_packages=[review_package_id, followup_package_id],
        budget_summary="Relative resource use fits configured synthetic review and triage limits.",
        key_tradeoffs=["Learning value is balanced against review and triage capacity."],
        risks=["Generated candidate remains a computational hypothesis."],
        uncertainty_notes=[
            "Failed quality-control result would require review rather than a conclusion."
        ],
        replan_triggers=plan.replan_triggers,
        approvals_required=["campaign_approval", "generated_molecule_review_gate"],
        limitations=[
            "Research-management guidance only.",
            (
                "No procedural experimental, chemistry-route, administration, or "
                "clinical-care guidance."
            ),
            "Selected candidates are not proven active, safe, effective, or useful.",
        ],
        created_at=now,
        metadata={
            "source_campaign_plan_id": campaign_plan_id,
            "assistant_output": False,
            "deterministic_campaign_artifact_summary": True,
        },
    )
    store.save_campaign_memo(memo)

    failed_qc_event = {
        "event_id": "synthetic-assay-result-failed-qc-v17",
        "event_type": "result_imported",
        "result_interpretation": "failed_qc",
        "candidate_id": generated_candidate_id,
        "hypothesis_id": "hypothesis-generated-v17-1",
        "linked_entity_ids": [followup_package_id],
    }
    replan_report = evaluate_replanning(plan, new_events=[failed_qc_event])
    for trigger in replan_report.triggers:
        store.add_replan_trigger(trigger)

    external_event = import_external_status_update(
        store,
        work_package_id=followup_package_id,
        payload={
            "status": "completed",
            "external_task_id": "external-task-v17-synthetic",
            "summary": "External task status completed for campaign planning validation.",
        },
        external_ref=ExternalRecordRef(
            external_system_id="synthetic-generic-rest",
            external_record_type="campaign_task",
            external_record_id="external-task-v17-synthetic",
            external_url=None,
        ),
    )

    write_json_artifact(output_dir / "campaign.json", campaign.model_dump(mode="json"))
    write_json_artifact(output_dir / "campaign_budget.json", budget.model_dump(mode="json"))
    write_json_artifact(output_dir / "campaign_plan.json", plan.model_dump(mode="json"))
    write_json_artifact(output_dir / "campaign_stage_gates.json", {"stage_gates": gates})
    write_json_artifact(
        output_dir / "campaign_replan_triggers.json",
        {
            "synthetic_event": failed_qc_event,
            "triggers": [trigger.model_dump(mode="json") for trigger in replan_report.triggers],
            "rationale": replan_report.rationale,
            "failed_qc_does_not_create_false_conclusion": True,
            "codex_triggered_execution": replan_report.codex_triggered_execution,
        },
    )
    write_json_artifact(
        output_dir / "updated_campaign_plan.json",
        replan_report.updated_plan.model_dump(mode="json"),
    )
    write_json_artifact(
        output_dir / "external_status_update.json",
        external_event.model_dump(mode="json"),
    )
    write_json_artifact(output_dir / "campaign_memo.json", memo.model_dump(mode="json"))
    _write_safe_campaign_memo_markdown(output_dir / "campaign_memo.md", memo)
    store.export_campaign_json(campaign_id, output_dir / "campaign_export.json")

    if fixture == "protocol_text":
        write_json_artifact(
            output_dir / "unsafe_campaign_fixture.json",
            {
                "fixture": "protocol_text",
                "work_package_id": followup_package_id,
                "bad_text": "lab protocol: incubate sample for 30 minutes with reagent.",
            },
        )
    elif fixture == "codex_invented_cost":
        write_json_artifact(
            output_dir / "codex_campaign_memo_fixture.json",
            {
                "fixture": "codex_invented_cost",
                "assistant_output": True,
                "campaign_id": campaign_id,
                "campaign_plan_id": campaign_plan_id,
                "work_package_ids": [review_package_id],
                "codex_invented_cost": True,
                "estimated_cost": {"value": 12500, "units": "USD", "source": "codex"},
            },
        )

    return {
        "work_package_count": len(plan.work_packages),
        "stage_gate_count": len(gates),
        "replan_trigger_count": len(replan_report.triggers),
    }


def _text_guardrail_findings(artifact: Path, text: str) -> list[CampaignGuardrailFinding]:
    findings: list[CampaignGuardrailFinding] = []
    checks = [
        (
            "no_procedural_work_package_text",
            "campaign_no_procedural_details",
            contains_procedural_lab_detail,
            "Campaign artifacts must not include procedural experimental details.",
        ),
        (
            "no_chemistry_route_text",
            "campaign_no_chemistry_route",
            lambda value: bool(_CHEMISTRY_ROUTE_RE.search(value)),
            "Campaign artifacts must not include chemistry-route instructions.",
        ),
        (
            "no_administration_guidance",
            "campaign_no_administration_guidance",
            lambda value: bool(_ADMINISTRATION_RE.search(value)),
            "Campaign artifacts must not include administration guidance.",
        ),
        (
            "no_clinical_care_guidance",
            "campaign_no_clinical_care_guidance",
            lambda value: bool(_CLINICAL_CARE_RE.search(value)),
            "Campaign artifacts must not include clinical-care guidance.",
        ),
    ]
    for category, check_id, matcher, message in checks:
        if not matcher(text):
            continue
        findings.append(
            CampaignGuardrailFinding(
                category=category,
                check_id=check_id,
                severity="high",
                artifact_path=str(artifact),
                message=message,
                excerpt=_excerpt(text),
            )
        )
    if _FAILED_QC_FALSE_CONCLUSION_RE.search(text):
        findings.append(
            CampaignGuardrailFinding(
                category="failed_qc_does_not_create_false_conclusion",
                check_id="campaign_failed_qc_false_conclusion",
                severity="high",
                artifact_path=str(artifact),
                message=(
                    "Failed quality-control data must not be converted into a false "
                    "conclusion."
                ),
                excerpt=_excerpt(text),
            )
        )
    return findings


def _json_guardrail_findings(artifact: Path, payload: Any) -> list[CampaignGuardrailFinding]:
    findings: list[CampaignGuardrailFinding] = []
    findings.extend(_plan_advisory_findings(artifact, payload))
    findings.extend(_generated_review_gate_findings(artifact, payload))
    findings.extend(_codex_gate_findings(artifact, payload))
    findings.extend(_external_status_findings(artifact, payload))
    findings.extend(_codex_cost_findings(artifact, payload))
    findings.extend(_failed_qc_marker_findings(artifact, payload))
    return findings


def _plan_advisory_findings(artifact: Path, payload: Any) -> list[CampaignGuardrailFinding]:
    if artifact.name not in {"campaign_plan.json", "updated_campaign_plan.json"} or not isinstance(
        payload, dict
    ):
        return []
    metadata = _dict_value(payload, "metadata")
    if (
        payload.get("human_approval_required") is True
        and metadata.get("advisory_plan") is True
        and metadata.get("automatic_execution") is False
        and metadata.get("codex_computed_plan") is False
    ):
        return []
    return [
        CampaignGuardrailFinding(
            category="campaign_plan_is_advisory",
            check_id="campaign_plan_must_be_advisory",
            severity="high",
            artifact_path=str(artifact),
            message="Campaign plan must be labeled advisory with no automatic execution.",
        )
    ]


def _generated_review_gate_findings(artifact: Path, payload: Any) -> list[CampaignGuardrailFinding]:
    if artifact.name not in {
        "campaign_plan.json",
        "updated_campaign_plan.json",
        "campaign_stage_gates.json",
    } or not isinstance(payload, dict):
        return []
    gates = _stage_gates_from_payload(payload)
    gate_work_package_ids = {
        str(gate.get("work_package_id"))
        for gate in gates
        if gate.get("gate_type") == "generated_molecule_review"
    }
    work_packages = _work_packages_from_payload(payload)
    findings: list[CampaignGuardrailFinding] = []
    for package in work_packages:
        if not _is_generated_package(package):
            continue
        package_id = str(package.get("work_package_id"))
        approvals = {str(item) for item in package.get("required_approvals", [])}
        has_package_gate = (
            package_id in gate_work_package_ids
            or "generated_molecule_review_gate" in approvals
        )
        if has_package_gate:
            continue
        findings.append(
            CampaignGuardrailFinding(
                category="generated_molecules_require_review_gate",
                check_id="campaign_generated_review_gate_required",
                severity="high",
                artifact_path=str(artifact),
                message=f"Generated work package {package_id} lacks a review gate.",
            )
        )
    if _payload_mentions_generated(payload) and not any(
        gate.get("gate_type") == "generated_molecule_review" for gate in gates
    ):
        findings.append(
            CampaignGuardrailFinding(
                category="generated_molecules_require_review_gate",
                check_id="campaign_generated_review_gate_missing",
                severity="high",
                artifact_path=str(artifact),
                message="Generated candidate campaign lacks a generated-molecule review gate.",
            )
        )
    return findings


def _codex_gate_findings(artifact: Path, payload: Any) -> list[CampaignGuardrailFinding]:
    findings: list[CampaignGuardrailFinding] = []
    for gate in _walk_dicts(payload):
        if not {"gate_id", "gate_type"}.issubset(gate):
            continue
        approved_by = str(gate.get("approved_by") or "")
        audit_event = _dict_value(gate, "audit_event")
        metadata = _dict_value(gate, "metadata")
        actor = str(audit_event.get("actor") or "")
        event_metadata = (
            _dict_value(audit_event, "metadata")
        )
        if (
            approved_by.lower().startswith("codex")
            or actor.lower().startswith("codex")
            or metadata.get("codex_approved") is True
            or event_metadata.get("codex_approved") is True
        ):
            findings.append(
                CampaignGuardrailFinding(
                    category="codex_cannot_approve_gates",
                    check_id="campaign_codex_gate_approval",
                    severity="critical",
                    artifact_path=str(artifact),
                    message="Codex approval was found on a campaign stage gate.",
                    excerpt=str(gate.get("gate_id")),
                )
            )
    return findings


def _external_status_findings(artifact: Path, payload: Any) -> list[CampaignGuardrailFinding]:
    findings: list[CampaignGuardrailFinding] = []
    for item in _walk_dicts(payload):
        if (
            item.get("external_status_creates_assay_evidence") is True
            or item.get("created_assay_result_from_external_status") is True
            or item.get("assay_evidence_created") is True
        ):
            findings.append(
                CampaignGuardrailFinding(
                    category="external_status_does_not_create_assay_result",
                    check_id="campaign_external_status_not_assay_result",
                    severity="high",
                    artifact_path=str(artifact),
                    message="External campaign status was converted into assay-result evidence.",
                )
            )
        if item.get("source") == "external_integration" and item.get(
            "does_not_create_assay_evidence"
        ) is not True:
            findings.append(
                CampaignGuardrailFinding(
                    category="external_status_does_not_create_assay_result",
                    check_id="campaign_external_status_missing_evidence_boundary",
                    severity="medium",
                    artifact_path=str(artifact),
                    message="External status event is missing the assay-evidence boundary flag.",
                )
            )
    return findings


def _codex_cost_findings(artifact: Path, payload: Any) -> list[CampaignGuardrailFinding]:
    findings: list[CampaignGuardrailFinding] = []
    for item in _walk_dicts(payload):
        assistant_output = item.get("assistant_output") is True or item.get("source") == "codex"
        if item.get("codex_invented_cost") is True:
            assistant_output = True
        has_cost = any(key in item for key in ("estimated_cost", "budget_cost", "cost"))
        estimated_cost = item.get("estimated_cost")
        if (
            isinstance(estimated_cost, dict)
            and str(estimated_cost.get("source", "")).lower() == "codex"
        ):
            assistant_output = True
            has_cost = True
        if assistant_output and has_cost:
            findings.append(
                CampaignGuardrailFinding(
                    category="codex_cannot_invent_costs",
                    check_id="campaign_codex_invented_cost",
                    severity="critical",
                    artifact_path=str(artifact),
                    message=(
                        "Assistant output contains a campaign cost estimate without "
                        "imported cost data."
                    ),
                )
            )
    return findings


def _failed_qc_marker_findings(artifact: Path, payload: Any) -> list[CampaignGuardrailFinding]:
    findings: list[CampaignGuardrailFinding] = []
    for item in _walk_dicts(payload):
        if item.get("failed_qc_false_conclusion") is True:
            findings.append(
                CampaignGuardrailFinding(
                    category="failed_qc_does_not_create_false_conclusion",
                    check_id="campaign_failed_qc_false_conclusion_marker",
                    severity="high",
                    artifact_path=str(artifact),
                    message="Artifact marks failed quality-control data as a conclusion.",
                )
            )
    return findings


def _write_safe_campaign_memo_markdown(path: Path, memo: CampaignMemo) -> Path:
    return write_markdown_artifact(
        path,
        memo.title,
        [
            "## Executive Summary",
            memo.executive_summary,
            "",
            "## Objectives",
            memo.objectives_summary,
            "",
            "## Work Packages",
            *[f"- {work_package_id}" for work_package_id in memo.selected_work_packages],
            "",
            "## Budget and Resources",
            memo.budget_summary,
            "",
            "## Tradeoffs",
            *[f"- {item}" for item in memo.key_tradeoffs],
            "",
            "## Risks and Uncertainty",
            *[f"- {item}" for item in [*memo.risks, *memo.uncertainty_notes]],
            "",
            "## Replan Triggers",
            *[f"- {item}" for item in memo.replan_triggers],
            "",
            "## Approvals",
            *[f"- {item}" for item in memo.approvals_required],
            "",
            "## Limitations",
            *[f"- {item}" for item in memo.limitations],
        ],
    )


def _write_campaign_guardrail_markdown(
    path: Path,
    report: CampaignGuardrailAuditReport,
) -> Path:
    lines = [
        f"- Status: `{report.status}`",
        f"- Artifacts audited: {report.artifact_count}",
        f"- Findings: {len(report.findings)}",
        "",
        "## Categories",
        "",
        *[f"- `{category}`" for category in report.categories],
        "",
        "## Findings",
        "",
    ]
    if report.findings:
        lines.extend(
            f"- `{finding.check_id}` ({finding.severity}): {finding.message}"
            for finding in report.findings
        )
    else:
        lines.append("- None.")
    return write_markdown_artifact(path, "V1.7 Campaign Guardrail Audit", lines)


def _write_campaign_validation_markdown(path: Path, report: CampaignValidationReport) -> Path:
    return write_markdown_artifact(
        path,
        "V1.7 Campaign Validation",
        [
            f"- Status: `{report.status}`",
            f"- Fixture: `{report.fixture}`",
            f"- Work packages: {report.work_package_count}",
            f"- Stage gates: {report.stage_gate_count}",
            f"- Replan triggers: {report.replan_trigger_count}",
            f"- Guardrail findings: {len(report.guardrail_audit.findings)}",
            "",
            "## Required Steps",
            "",
            *[f"- {step}" for step in report.required_steps],
        ],
    )


def _stage_gates_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("stage_gates")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [
        item
        for item in _walk_dicts(payload)
        if "gate_id" in item and "gate_type" in item
    ]


def _work_packages_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("work_packages") or payload.get("campaign_work_packages")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [
        item
        for item in _walk_dicts(payload)
        if "work_package_id" in item and "package_type" in item
    ]


def _is_generated_package(package: dict[str, Any]) -> bool:
    metadata = _dict_value(package, "metadata")
    linked_candidates = [str(item).lower() for item in package.get("linked_candidate_ids", [])]
    linked_hypotheses = [str(item).lower() for item in package.get("linked_hypothesis_ids", [])]
    return (
        metadata.get("generated_molecule") is True
        or any("generated" in item for item in linked_candidates)
        or any("generated" in item for item in linked_hypotheses)
    )


def _payload_mentions_generated(payload: Any) -> bool:
    return "generated" in json.dumps(payload, sort_keys=True).lower()


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if isinstance(value, dict):
        output.append(value)
        for item in value.values():
            output.extend(_walk_dicts(item))
    elif isinstance(value, list):
        for item in value:
            output.extend(_walk_dicts(item))
    return output


def _dict_value(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    return item if isinstance(item, dict) else {}


def _excerpt(text: str, width: int = 180) -> str:
    compact = " ".join(text.split())
    return compact[:width]
