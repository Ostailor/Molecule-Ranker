from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.autonomy_validation.scenario_builder import (
    get_builtin_autonomy_scenario,
)
from molecule_ranker.autonomy_validation.schemas import (
    AutonomousWorkflowScenario,
    EndToEndResultCertification,
    ResultCertificationLevel,
)
from molecule_ranker.e2e.schemas import EndToEndResultBundle, WorkflowLineageRecord
from molecule_ranker.e2e.validation import EndToEndWorkflowValidator
from molecule_ranker.e2e.workflow_runner import (
    EndToEndWorkflowRunner,
    WorkflowRunRequest,
    WorkflowRunResult,
)

FORBIDDEN_REPORT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bvalidated\s+binder\b", re.I), "unsupported binding claim"),
    (re.compile(r"\bbinds?\b", re.I), "unsupported binding claim"),
    (re.compile(r"\bactivity\b|\bactive\b", re.I), "unsupported activity claim"),
    (re.compile(r"\bproven\s+safety\b|\bsafe\b", re.I), "unsupported safety claim"),
    (re.compile(r"\befficacy\b|\beffective\b", re.I), "unsupported efficacy claim"),
    (re.compile(r"\bmanufacturable\b", re.I), "unsupported manufacturability claim"),
    (re.compile(r"\btreats?\b|\bcures?\b", re.I), "therapeutic claim"),
    (re.compile(r"\bdosing\b|\bdose\b", re.I), "dosing guidance"),
    (re.compile(r"\blab\s+protocol\b|\bwet-lab\b", re.I), "lab protocol"),
    (re.compile(r"\bsynthesis\s+instruction\b|\bsynthesize\b", re.I), "synthesis instruction"),
)

SEPARATION_METADATA_KEYS = {
    "codex_outputs_are_separate": "Codex outputs separate",
    "model_predictions_are_separate": "model predictions separate",
    "graph_inferences_are_separate": "graph inference separate",
    "evaluation_artifacts_are_separate": "evaluation artifacts separate",
}


def certify_e2e_result(
    workflow_id: str,
    scenario: str | AutonomousWorkflowScenario,
) -> EndToEndResultCertification:
    """Certify a deterministic E2E workflow result for platform readiness.

    The certification is a platform/workflow certification artifact. It is not
    scientific, clinical, therapeutic, safety, efficacy, or manufacturability
    validation.
    """

    active_scenario = (
        get_builtin_autonomy_scenario(scenario) if isinstance(scenario, str) else scenario
    )
    result = _run_workflow(workflow_id=workflow_id, scenario=active_scenario)
    result = _apply_simulated_certification_faults(result, active_scenario.metadata)
    return _certify_result(result=result, scenario=active_scenario)


def _run_workflow(
    *,
    workflow_id: str,
    scenario: AutonomousWorkflowScenario,
) -> WorkflowRunResult:
    workflow_type = scenario.metadata.get("workflow_type")
    if not workflow_type:
        raise ValueError(f"scenario does not define an e2e workflow_type: {scenario.scenario_id}")
    runner = EndToEndWorkflowRunner()
    return runner.run(
        WorkflowRunRequest(
            workflow_type=workflow_type,
            mode=scenario.mode,
            disease_name="V3 autonomy certification fixture disease",
            project_id=f"project-{scenario.scenario_id}",
            requested_by="autonomy-result-certification",
            autonomy_level="governed",
            requested_external_write=bool(
                scenario.metadata.get("requested_external_write", False)
            ),
            approvals=list(scenario.metadata.get("approvals", [])),
            governance_permissions=list(scenario.metadata.get("governance_permissions", [])),
            antibody_generation_enabled=bool(
                scenario.metadata.get("antibody_generation_enabled", False)
            ),
            approved_antibody_generation_plugin_ids=list(
                scenario.metadata.get("approved_antibody_generation_plugin_ids", [])
            ),
            metadata={"workflow_id": workflow_id, "scenario_id": scenario.scenario_id},
        )
    )


def _certify_result(
    *,
    result: WorkflowRunResult,
    scenario: AutonomousWorkflowScenario,
    now: Callable[[], datetime] | None = None,
) -> EndToEndResultCertification:
    timestamp = now or (lambda: datetime.now(UTC))
    validation = EndToEndWorkflowValidator(now=timestamp).validate_run_result(result)
    findings = list(validation.findings)

    bundle_exists = result.bundle is not None
    if not bundle_exists:
        findings.append("result bundle missing")

    key_artifacts_exist = _key_artifacts_exist(result.bundle, findings)
    lineage_complete = validation.lineage_complete and _lineage_complete(
        result.bundle, result.lineage_records, findings
    )
    approvals_satisfied = validation.approvals_satisfied and _approvals_satisfied(
        result, findings
    )
    external_boundaries = _external_writes_absent_or_approved(result, findings)
    guardrails_passed = validation.guardrails_passed
    generated_labels_intact = _generated_labels_intact(result.bundle, findings)
    exact_imported_evidence = _exact_imported_evidence_rule_enforced(
        result.bundle, result.lineage_records, findings
    )
    failed_qc_safe = _failed_qc_not_evidence(result.bundle, result.lineage_records, findings)
    outputs_separate = _outputs_separate(result.bundle, findings)
    forbidden_text_absent = _no_forbidden_text(result.bundle, findings)
    reproducibility_valid = _reproducibility_manifest_valid(
        result.bundle, result.lineage_records, findings
    )
    certified = all(
        [
            validation.passed,
            bundle_exists,
            key_artifacts_exist,
            validation.artifact_contracts_valid,
            lineage_complete,
            approvals_satisfied,
            external_boundaries,
            guardrails_passed,
            generated_labels_intact,
            exact_imported_evidence,
            failed_qc_safe,
            outputs_separate,
            forbidden_text_absent,
            reproducibility_valid,
        ]
    )

    return EndToEndResultCertification(
        certification_id=f"autonomy-cert-{uuid4().hex[:16]}",
        workflow_id=result.workflow.workflow_id,
        result_bundle_id=result.bundle.bundle_id if result.bundle else None,
        scenario_id=scenario.scenario_id,
        certified=certified,
        certification_level=(
            _certification_level(result.workflow.mode) if certified else "failed"
        ),
        required_artifacts_present=bundle_exists and key_artifacts_exist,
        artifact_contracts_valid=validation.artifact_contracts_valid,
        lineage_complete=lineage_complete,
        guardrails_passed=guardrails_passed
        and generated_labels_intact
        and exact_imported_evidence
        and failed_qc_safe
        and outputs_separate
        and forbidden_text_absent,
        approvals_satisfied=approvals_satisfied,
        scientific_boundaries_passed=(
            generated_labels_intact
            and exact_imported_evidence
            and failed_qc_safe
            and outputs_separate
            and forbidden_text_absent
        ),
        integration_boundaries_passed=external_boundaries,
        reproducibility_manifest_valid=reproducibility_valid,
        limitations=[
            "Certification is platform/workflow certification only.",
            "Certification is not scientific validation, clinical validation, medical advice, "
            "or evidence of binding, activity, safety, efficacy, manufacturability, or "
            "therapeutic value.",
            "Generated molecules and generated antibodies remain computational hypotheses.",
            "Experimental evidence may only come from imported and validated result records.",
        ],
        findings=sorted(set(findings)),
        certified_at=timestamp(),
        metadata={
            "workflow_status": result.workflow.status,
            "workflow_mode": result.workflow.mode,
            "validation_id": validation.validation_id,
            "checks": {
                "result_bundle_exists": bundle_exists,
                "key_artifacts_exist": key_artifacts_exist,
                "artifact_contracts_valid": validation.artifact_contracts_valid,
                "lineage_complete": lineage_complete,
                "approvals_satisfied": approvals_satisfied,
                "external_writes_absent_or_approved": external_boundaries,
                "guardrails_passed": guardrails_passed,
                "generated_labels_intact": generated_labels_intact,
                "exact_imported_evidence_rule_enforced": exact_imported_evidence,
                "failed_qc_not_treated_as_evidence": failed_qc_safe,
                "outputs_separate": outputs_separate,
                "no_forbidden_text": forbidden_text_absent,
                "reproducibility_manifest_valid": reproducibility_valid,
            },
        },
    )


def _certification_level(mode: str) -> ResultCertificationLevel:
    levels: dict[str, ResultCertificationLevel] = {
        "mocked": "mocked_validated",
        "dry_run": "dry_run_validated",
        "read_only_live": "read_only_live_validated",
        "write_approved_live": "write_approved_live_validated",
    }
    return levels[mode]


def _key_artifacts_exist(
    bundle: EndToEndResultBundle | None,
    findings: list[str],
) -> bool:
    if bundle is None:
        return False
    if not bundle.key_artifact_ids:
        findings.append("key artifacts missing")
        return False
    return True


def _lineage_complete(
    bundle: EndToEndResultBundle | None,
    lineage_records: list[WorkflowLineageRecord],
    findings: list[str],
) -> bool:
    if bundle is None:
        return False
    lineage_artifacts = {
        artifact_id
        for record in lineage_records
        for artifact_id in record.artifact_ids
    }
    missing = sorted(set(bundle.key_artifact_ids) - lineage_artifacts)
    if missing:
        findings.append(f"lineage missing for key artifacts: {', '.join(missing)}")
        return False
    return True


def _approvals_satisfied(result: WorkflowRunResult, findings: list[str]) -> bool:
    if result.workflow.mode != "write_approved_live":
        return True
    approval_ids = set(result.bundle.metadata.get("approval_ids", []) if result.bundle else [])
    approval_ids.update(
        str(record.metadata.get("approval_id"))
        for record in result.lineage_records
        if record.metadata.get("approval_id")
    )
    if not approval_ids:
        findings.append("write-approved workflow missing approval lineage")
        return False
    return True


def _external_writes_absent_or_approved(
    result: WorkflowRunResult,
    findings: list[str],
) -> bool:
    if result.external_writes_performed == 0:
        return True
    if result.workflow.mode != "write_approved_live":
        findings.append("external write performed outside write-approved mode")
        return False
    approval_ids = set(result.bundle.metadata.get("approval_ids", []) if result.bundle else [])
    approval_ids.update(
        str(record.metadata.get("approval_id"))
        for record in result.lineage_records
        if record.metadata.get("approval_id")
    )
    if "external_write" not in approval_ids:
        findings.append("external write performed without explicit approval")
        return False
    return True


def _generated_labels_intact(
    bundle: EndToEndResultBundle | None,
    findings: list[str],
) -> bool:
    if bundle is None:
        return False
    failed = False
    generated = bundle.generated_summary
    biologics = bundle.biologics_summary
    if generated.get("generated_molecules_advanced_without_review", 0):
        findings.append("generated molecule advanced without review")
        failed = True
    if generated.get("advanced_without_review"):
        findings.append("generated artifact advanced without review")
        failed = True
    if generated.get("computational_hypothesis_only") is False:
        findings.append("generated molecule computational-hypothesis label missing")
        failed = True
    if biologics.get("generated_antibodies_advanced_without_review", 0):
        findings.append("generated antibody advanced without review")
        failed = True
    if biologics.get("antibody_generation_enabled") is True:
        required = (
            "deterministic_validation_required",
            "novelty_check_required",
            "developability_triage_required",
            "review_gate_required",
            "result_bundle_lineage_required",
        )
        missing = [key for key in required if biologics.get(key) is not True]
        if missing:
            findings.append("generated antibody gates missing: " + ", ".join(missing))
            failed = True
    return not failed


def _exact_imported_evidence_rule_enforced(
    bundle: EndToEndResultBundle | None,
    lineage_records: list[WorkflowLineageRecord],
    findings: list[str],
) -> bool:
    if bundle is None:
        return False
    evidence = bundle.evidence_summary
    if evidence.get("fabricated_evidence") or evidence.get("fake_evidence"):
        findings.append("fabricated evidence flag present")
        return False
    direct_evidence_count = int(evidence.get("direct_experimental_evidence_count", 0) or 0)
    imported_validated = [
        record
        for record in lineage_records
        if record.relation_type == "imported_from"
        and record.target_object_type in {"assay_result", "experimental_result"}
        and record.metadata.get("validation_status") in {None, "validated"}
        and record.metadata.get("qc_status") not in {"failed", "rejected"}
        and bool(record.external_record_refs)
    ]
    if direct_evidence_count and not imported_validated:
        findings.append("direct experimental evidence lacks exact imported validated result")
        return False
    biologics = bundle.biologics_summary
    if biologics.get("generated_antibodies_with_direct_evidence", 0):
        exact_ids = biologics.get("exact_imported_experimental_result_ids")
        if not exact_ids:
            findings.append("generated antibody direct evidence lacks exact imported result ids")
            return False
    return True


def _failed_qc_not_evidence(
    bundle: EndToEndResultBundle | None,
    lineage_records: list[WorkflowLineageRecord],
    findings: list[str],
) -> bool:
    if bundle is None:
        return False
    failed = bundle.metadata.get("failed_qc_treated_as_support") is True
    failed = failed or bundle.evidence_summary.get("failed_qc_treated_as_evidence") is True
    failed = failed or any(
        record.metadata.get("qc_status") == "failed"
        and record.metadata.get("treated_as_evidence") is True
        for record in lineage_records
    )
    if failed:
        findings.append("failed QC treated as evidence")
    return not failed


def _outputs_separate(
    bundle: EndToEndResultBundle | None,
    findings: list[str],
) -> bool:
    if bundle is None:
        return False
    failed_keys = [
        label
        for key, label in SEPARATION_METADATA_KEYS.items()
        if bundle.metadata.get(key) is False
    ]
    if failed_keys:
        findings.append("outputs not separated: " + ", ".join(failed_keys))
        return False
    return True


def _no_forbidden_text(
    bundle: EndToEndResultBundle | None,
    findings: list[str],
) -> bool:
    if bundle is None:
        return False
    payload = bundle.model_dump(mode="json")
    payload.pop("limitations", None)
    text = json.dumps(payload, sort_keys=True)
    failed = False
    for pattern, label in FORBIDDEN_REPORT_PATTERNS:
        if pattern.search(text):
            findings.append(f"forbidden report text present: {label}")
            failed = True
    return not failed


def _reproducibility_manifest_valid(
    bundle: EndToEndResultBundle | None,
    lineage_records: list[WorkflowLineageRecord],
    findings: list[str],
) -> bool:
    if bundle is None:
        return False
    if not lineage_records:
        findings.append("reproducibility manifest missing lineage records")
        return False
    metadata = bundle.metadata
    if "lineage_records" not in metadata:
        findings.append("reproducibility manifest missing bundle lineage payload")
        return False
    if metadata.get("mode") is None or metadata.get("workflow_status") is None:
        findings.append("reproducibility manifest missing mode or workflow status")
        return False
    return True


def _apply_simulated_certification_faults(
    result: WorkflowRunResult,
    metadata: dict[str, Any],
) -> WorkflowRunResult:
    active = result
    if metadata.get("simulate_missing_lineage") is True:
        active = active.model_copy(
            update={
                "lineage_records": [],
                "bundle": active.bundle.model_copy(
                    update={"metadata": _metadata_without_lineage(active.bundle)}
                )
                if active.bundle
                else None,
            }
        )
    if metadata.get("simulate_generated_overclaim") is True and active.bundle is not None:
        active = active.model_copy(
            update={
                "bundle": active.bundle.model_copy(
                    update={
                        "result_summary": (
                            active.bundle.result_summary
                            + " Generated antibody is a validated binder with proven safety."
                        )
                    }
                )
            }
        )
    if metadata.get("simulate_unapproved_external_write") is True:
        bundle = active.bundle
        if bundle is not None:
            bundle = bundle.model_copy(
                update={
                    "metadata": {
                        **bundle.metadata,
                        "external_writes_performed": 1,
                        "approval_ids": [],
                    },
                    "integration_summary": {
                        **bundle.integration_summary,
                        "external_writes_performed": 1,
                    },
                }
            )
        active = active.model_copy(
            update={
                "bundle": bundle,
                "external_writes_performed": 1,
            }
        )
    return active


def _metadata_without_lineage(bundle: EndToEndResultBundle | None) -> dict[str, Any]:
    if bundle is None:
        return {}
    metadata = dict(bundle.metadata)
    metadata.pop("lineage_records", None)
    return metadata


__all__ = [
    "EndToEndResultCertification",
    "certify_e2e_result",
]
