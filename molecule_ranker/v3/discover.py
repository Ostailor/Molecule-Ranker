from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.e2e.validation import EndToEndWorkflowValidator
from molecule_ranker.e2e.workflow_runner import EndToEndWorkflowRunner, WorkflowRunRequest
from molecule_ranker.utils import slugify
from molecule_ranker.v3.certification import (
    certify_v3_result_bundle,
    write_v3_result_certification,
)
from molecule_ranker.v3.governance_matrix import (
    build_v3_human_governance_matrix,
    write_v3_human_governance_matrix,
)
from molecule_ranker.v3.orchestration import build_v3_default_orchestration
from molecule_ranker.v3.product_contract import v3_product_contract_payload
from molecule_ranker.v3.result_bundle import (
    build_v3_result_bundle,
    write_v3_result_bundle,
)

DiscoverMode = Literal[
    "mocked",
    "dry_run",
    "read_only_live",
    "write_approved_live",
]
DiscoverAutonomy = Literal[
    "observe_only",
    "suggest_only",
    "execute_safe_tools",
    "execute_with_approval",
    "supervised_auto",
]


class V3DiscoverRequest(BaseModel):
    disease: str
    project_id: str | None = None
    mode: DiscoverMode = "dry_run"
    enable_generation: bool = False
    enable_biologics: bool = False
    enable_antibody_generation: bool = False
    enable_structure: bool = False
    enable_integrations: bool = False
    enable_codex_summary: bool = False
    autonomy: DiscoverAutonomy = "execute_with_approval"
    require_approval: bool = False
    output_dir: Path

    @field_validator("disease")
    @classmethod
    def require_disease(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("disease is required")
        return value.strip()


class V3DiscoverResult(BaseModel):
    status: str
    workflow_id: str
    workflow_type: str
    project_id: str
    mode: str
    output_dir: str
    bundle_id: str | None
    validation_passed: bool
    certification_passed: bool
    external_writes_performed: int
    planned_external_writes: int
    warnings: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


def run_v3_discover(
    request: V3DiscoverRequest,
    *,
    now: Callable[[], datetime] | None = None,
    runner: EndToEndWorkflowRunner | None = None,
) -> V3DiscoverResult:
    timestamp = now or (lambda: datetime.now(UTC))
    output_dir = request.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    project_id = request.project_id or f"project-{slugify(request.disease)}"
    workflow_id = f"discover-{uuid4().hex}"

    workflow_request = WorkflowRunRequest(
        workflow_type="full_discovery_loop",
        mode=request.mode,
        disease_name=request.disease,
        project_id=project_id,
        requested_by="cli",
        autonomy_level=request.autonomy,
        requested_external_write=request.enable_integrations,
        approvals=_approval_ids(request),
        governance_permissions=_governance_permissions(request),
        antibody_generation_enabled=request.enable_antibody_generation,
        approved_antibody_generation_plugin_ids=_antibody_plugin_ids(request),
        metadata={
            "workflow_id": workflow_id,
            "cli_command": "discover",
            "v3_primary_one_command_workflow": True,
            "enable_generation": request.enable_generation,
            "enable_biologics": request.enable_biologics,
            "enable_antibody_generation": request.enable_antibody_generation,
            "enable_structure": request.enable_structure,
            "enable_integrations": request.enable_integrations,
            "enable_codex_summary": request.enable_codex_summary,
            "require_approval": request.require_approval,
            "product_contract": v3_product_contract_payload(),
            "safe_defaults": _safe_defaults(request),
        },
    )
    active_runner = runner or EndToEndWorkflowRunner(now=timestamp)
    run_result = active_runner.run(workflow_request)
    validation = EndToEndWorkflowValidator(now=timestamp).validate_run_result(run_result)
    certification_passed = False

    artifacts: dict[str, str] = {}
    _write_json(output_dir / "workflow_result.json", run_result.model_dump(mode="json"))
    _record_artifact(artifacts, output_dir, "trace.json", _trace_payload(request, run_result))

    if run_result.bundle is not None:
        bundle_payload = run_result.bundle.model_dump(mode="json")
        _record_artifact(artifacts, output_dir, "candidates.json", _candidates(bundle_payload))
        if request.enable_generation:
            _record_artifact(
                artifacts,
                output_dir,
                "generated_candidates.json",
                _generated_candidates(bundle_payload),
            )
        if request.enable_biologics:
            _record_artifact(
                artifacts,
                output_dir,
                "biologic_candidates.json",
                _biologic_candidates(bundle_payload),
            )
        if request.enable_antibody_generation:
            _record_artifact(
                artifacts,
                output_dir,
                "generated_antibodies.json",
                _generated_antibodies(bundle_payload),
            )
        _record_artifact(
            artifacts, output_dir, "developability.json", _developability(bundle_payload)
        )
        _record_artifact(
            artifacts,
            output_dir,
            "literature_evidence.json",
            _literature_evidence(bundle_payload),
        )
        _record_artifact(artifacts, output_dir, "graph.json", _graph(bundle_payload))
        _record_artifact(artifacts, output_dir, "hypotheses.json", _hypotheses(bundle_payload))
        _record_artifact(
            artifacts,
            output_dir,
            "portfolio_optimization.json",
            _portfolio_optimization(bundle_payload),
        )
        _record_artifact(
            artifacts, output_dir, "campaign_plan.json", _campaign_plan(bundle_payload)
        )
        _record_artifact(artifacts, output_dir, "review_queue.json", _review_queue(bundle_payload))
        _record_artifact(
            artifacts, output_dir, "evaluation_report.json", _evaluation_report(bundle_payload)
        )
        _record_artifact(
            artifacts,
            output_dir,
            "e2e_lineage.json",
            {
                "workflow_id": run_result.workflow.workflow_id,
                "lineage_records": [
                    record.model_dump(mode="json") for record in run_result.lineage_records
                ],
            },
        )
        _record_artifact(
            artifacts,
            output_dir,
            "e2e_result_bundle.json",
            bundle_payload,
        )
        artifacts["e2e_result_bundle.md"] = str(output_dir / "e2e_result_bundle.md")
        (output_dir / "e2e_result_bundle.md").write_text(
            _bundle_markdown(bundle_payload), encoding="utf-8"
        )
        _record_artifact(
            artifacts,
            output_dir,
            "e2e_validation.json",
            validation.model_dump(mode="json"),
        )
        _record_artifact(
            artifacts,
            output_dir,
            "v3_result_certification.json",
            _legacy_certification_payload(
                request=request,
                run_result=run_result,
                validation_payload=validation.model_dump(mode="json"),
                certified_at=timestamp(),
            ),
        )
        governance_matrix = build_v3_human_governance_matrix()
        artifacts.update(
            write_v3_human_governance_matrix(governance_matrix, output_dir=output_dir)
        )
        v3_bundle = build_v3_result_bundle(
            e2e_bundle=run_result.bundle,
            validation_summary=validation.model_dump(mode="json"),
            artifact_manifest=_artifact_manifest(artifacts),
            codex_agent_summary={
                "autonomy": request.autonomy,
                "approved_tools_only": True,
                "codex_outputs_are_separate": True,
                "summary_enabled": request.enable_codex_summary,
            },
            governance_summary={
                "external_writes_enabled": False,
                "campaign_activation_enabled": False,
                "codex_stage_gate_approval_enabled": False,
                "generated_advancement_without_review": False,
                "human_governance_matrix": governance_matrix.model_dump(mode="json"),
            },
            approval_summary={
                "require_approval": request.require_approval,
                "approval_ids": _approval_ids(request),
                "stage_gate_approved_by_codex": False,
            },
            lineage_summary={
                "lineage_record_count": len(run_result.lineage_records),
                "lineage_artifact": str(output_dir / "e2e_lineage.json"),
            },
            metadata={
                "source_bundle_id": run_result.bundle.bundle_id,
                "source_bundle_format": "e2e_result_bundle",
                "reproducibility_manifest": {
                    "lineage_record_count": len(run_result.lineage_records),
                    "artifact_manifest_count": len(artifacts),
                },
                "safety_case_link": "v3_safety_case.md",
            },
        )
        artifacts.update(write_v3_result_bundle(v3_bundle, output_dir=output_dir))
        certification = certify_v3_result_bundle(v3_bundle, now=timestamp)
        certification_passed = certification.certified
        artifacts.update(write_v3_result_certification(certification, output_dir=output_dir))

    status = run_result.workflow.status
    if run_result.bundle is not None and not certification_passed:
        status = "failed"
    result = V3DiscoverResult(
        status=status,
        workflow_id=run_result.workflow.workflow_id,
        workflow_type=run_result.workflow.workflow_type,
        project_id=project_id,
        mode=run_result.workflow.mode,
        output_dir=str(output_dir),
        bundle_id=run_result.bundle.bundle_id if run_result.bundle is not None else None,
        validation_passed=validation.passed,
        certification_passed=certification_passed,
        external_writes_performed=run_result.external_writes_performed,
        planned_external_writes=run_result.planned_external_writes,
        warnings=run_result.warnings,
        artifacts=artifacts,
    )
    _write_json(output_dir / "discover_result.json", result.model_dump(mode="json"))
    return result


def render_v3_discover_cli_output(result: V3DiscoverResult) -> str:
    trace = _load_trace(result)
    timeline = trace.get("steps", []) if isinstance(trace, dict) else []
    warnings = result.warnings or []
    artifacts = sorted(result.artifacts)
    lines = [
        f"V3 discover: {result.status}",
        f"Workflow: {result.workflow_id}",
        f"Project: {result.project_id}",
        f"Mode: {result.mode}",
        f"Output: {result.output_dir}",
        "",
        "Progress",
        "1. Project created.",
        "2. Disease resolved.",
        "3. Targets retrieved.",
        "4. Candidates ranked.",
        "5. Literature summarized.",
        "6. Generated hypotheses created.",
        "7. Review workspace created.",
        "8. Portfolio/campaign drafted.",
        (
            "9. Result bundle certified."
            if result.certification_passed
            else "9. Result bundle certification failed."
        ),
        "10. Human review required for generated hypotheses.",
        "",
        "Step timeline",
        *_timeline_lines(timeline),
        "",
        "Approvals needed",
        *_approval_lines(result),
        "",
        "Current agent/subagent activity",
        *_agent_activity_lines(trace),
        "",
        "Artifacts produced",
        *[f"- {artifact}: {result.artifacts[artifact]}" for artifact in artifacts],
        "",
        "Warnings and partial success",
        f"- Status: {result.status}",
        f"- Validation passed: {result.validation_passed}",
        f"- Certification passed: {result.certification_passed}",
        f"- External writes performed: {result.external_writes_performed}",
        f"- Planned external writes: {result.planned_external_writes}",
        *([f"- Warning: {warning}" for warning in warnings] or ["- Warnings: none"]),
        "",
        "Recovery suggestions",
        *_recovery_lines(result),
        "",
        "What you have",
        *_what_you_have_lines(result),
        "",
        "What this does not prove",
        "- This is not clinical validation.",
        "- This is not biomedical evidence of binding, activity, safety, or efficacy.",
        (
            "- This provides no medical advice, treatment guidance, dosing guidance, "
            "lab protocol, or synthesis instruction."
        ),
        "",
        "Recommended human review points",
        "- Review generated hypotheses before any advancement.",
        "- Review the V3 result certification findings.",
        "- Approve any external write before write_approved_live execution.",
        "- Review portfolio and campaign drafts before activation.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _approval_ids(request: V3DiscoverRequest) -> list[str]:
    if (
        request.mode == "write_approved_live"
        and request.enable_integrations
        and request.require_approval
    ):
        return ["external_write"]
    return []


def _load_trace(result: V3DiscoverResult) -> dict[str, Any]:
    trace_path = result.artifacts.get("trace.json")
    if not trace_path:
        return {}
    try:
        payload = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _timeline_lines(timeline: Any) -> list[str]:
    if not isinstance(timeline, list) or not timeline:
        return ["- No step timeline recorded."]
    lines: list[str] = []
    for step in timeline:
        if not isinstance(step, dict):
            continue
        index = step.get("step_index", "?")
        name = step.get("step_name") or step.get("step_type") or "unknown step"
        status = step.get("status", "unknown")
        required = "required" if step.get("required") is True else "optional"
        lines.append(f"- {index}. {name}: {status} ({required})")
    return lines or ["- No step timeline recorded."]


def _approval_lines(result: V3DiscoverResult) -> list[str]:
    if result.mode == "write_approved_live" and result.planned_external_writes:
        return ["- External write approval required before execution."]
    return ["- No approvals needed for current mocked/dry-run/read-only output."]


def _agent_activity_lines(trace: dict[str, Any]) -> list[str]:
    session = trace.get("runtime_agent_session", {}) if isinstance(trace, dict) else {}
    plan = trace.get("codex_plan", {}) if isinstance(trace, dict) else {}
    orchestration = trace.get("agent_orchestration", {}) if isinstance(trace, dict) else {}
    planned_tool_count = (
        len(plan.get("approved_tools", [])) if isinstance(plan, dict) else 0
    )
    subagents = orchestration.get("subagents", []) if isinstance(orchestration, dict) else []
    subagent_names: list[str] = []
    for subagent in subagents:
        if not isinstance(subagent, dict):
            continue
        subagent_name = subagent.get("subagent_name")
        if isinstance(subagent_name, str):
            subagent_names.append(subagent_name)
    return [
        f"- Runtime agent session: {session.get('session_id', 'not recorded')}",
        f"- Codex autonomy: {session.get('autonomy', 'not recorded')}",
        f"- Approved tool registry only: {session.get('approved_tool_registry_only', True)}",
        f"- Approved tools planned: {planned_tool_count}",
        f"- Coordinator: {orchestration.get('coordinator_subagent', 'not recorded')}",
        f"- Subagents: {', '.join(subagent_names) if subagent_names else 'not recorded'}",
    ]


def _recovery_lines(result: V3DiscoverResult) -> list[str]:
    if result.status == "succeeded":
        return [
            "- No recovery needed for this run.",
            "- Re-run with --json for machine-readable status if automating.",
        ]
    suggestions = [
        "- Inspect warnings and v3_result_certification.json findings.",
        "- Re-run in mocked or dry_run mode after resolving failed gates.",
    ]
    if result.external_writes_performed or result.planned_external_writes:
        suggestions.append("- Add human approval before any write_approved_live external write.")
    return suggestions


def _what_you_have_lines(result: V3DiscoverResult) -> list[str]:
    bundle = result.artifacts.get("v3_result_bundle.json") or result.artifacts.get(
        "e2e_result_bundle.json"
    )
    certification = result.artifacts.get("v3_result_certification.json")
    trace = result.artifacts.get("trace.json")
    return [
        f"- V3 result bundle: {bundle or 'not produced'}",
        f"- V3 result certification: {certification or 'not produced'}",
        f"- Runtime trace: {trace or 'not produced'}",
        f"- Artifact count: {len(result.artifacts)}",
    ]


def _governance_permissions(request: V3DiscoverRequest) -> list[str]:
    if (
        request.mode == "write_approved_live"
        and request.enable_integrations
        and request.require_approval
    ):
        return ["integration:write"]
    return []


def _antibody_plugin_ids(request: V3DiscoverRequest) -> list[str]:
    if not request.enable_antibody_generation:
        return []
    return ["approved-v3-antibody-generation-tool"]


def _safe_defaults(request: V3DiscoverRequest) -> dict[str, Any]:
    return {
        "external_writes_enabled": False,
        "campaign_activation_enabled": False,
        "codex_stage_gate_approval_enabled": False,
        "antibody_generation_enabled_by_default": False,
        "antibody_generation_enabled": request.enable_antibody_generation,
        "generated_molecule_advancement_without_review": False,
        "approved_tools_only": True,
    }


def _trace_payload(request: V3DiscoverRequest, run_result: Any) -> dict[str, Any]:
    approved_tools = [
        step.tool_name for step in run_result.steps if step.tool_name is not None
    ]
    orchestration = build_v3_default_orchestration(
        workflow_type="full_discovery_loop",
        generation_enabled=request.enable_generation,
        biologics_enabled=request.enable_biologics,
        integrations_enabled=request.enable_integrations,
    )
    return {
        "command": "molecule-ranker discover",
        "product_contract": v3_product_contract_payload(),
        "runtime_agent_session": {
            "session_id": f"runtime-{run_result.workflow.workflow_id}",
            "autonomy": request.autonomy,
            "approved_tool_registry_only": True,
        },
        "codex_plan": {
            "mode": request.autonomy,
            "planner": "codex",
            "approved_tools": approved_tools,
            "stage_gate_approval_by_codex": False,
        },
        "agent_orchestration": orchestration.model_dump(mode="json"),
        "safe_defaults": _safe_defaults(request),
        "workflow": run_result.workflow.model_dump(mode="json"),
        "steps": [step.model_dump(mode="json") for step in run_result.steps],
        "warnings": list(run_result.warnings),
        "external_writes_performed": run_result.external_writes_performed,
        "planned_external_writes": run_result.planned_external_writes,
    }


def _legacy_certification_payload(
    *,
    request: V3DiscoverRequest,
    run_result: Any,
    validation_payload: dict[str, Any],
    certified_at: datetime,
) -> dict[str, Any]:
    certified = (
        run_result.bundle is not None
        and validation_payload.get("passed") is True
        and run_result.external_writes_performed == 0
        and run_result.bundle.generated_summary.get(
            "generated_molecules_advanced_without_review"
        )
        == 0
    )
    return {
        "certification_id": f"v3-discover-cert-{uuid4().hex[:16]}",
        "workflow_id": run_result.workflow.workflow_id,
        "result_bundle_id": run_result.bundle.bundle_id if run_result.bundle else None,
        "certified": certified,
        "certification_scope": "software_autonomy_validation_not_clinical_validation",
        "product_contract": v3_product_contract_payload(),
        "safe_defaults": _safe_defaults(request),
        "checks": {
            "result_bundle_validates": validation_payload.get("passed") is True,
            "no_external_writes": run_result.external_writes_performed == 0,
            "no_campaign_activation": True,
            "no_codex_stage_gate_approval": True,
            "generated_advancement_requires_review": True,
            "approved_tools_only": True,
        },
        "validation": validation_payload,
        "limitations": [
            "Certification is software/autonomy validation only.",
            "No medical advice, treatment guidance, dosing guidance, or lab protocols.",
            "Generated assets are computational hypotheses only.",
        ],
        "certified_at": certified_at.isoformat(),
    }


def _candidates(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "source_backed_candidate_summary",
        "label": "source_backed_research_planning_candidates",
        "disease": bundle.get("disease_name"),
        "summary": bundle.get("candidate_summary", {}),
        "claims": [],
    }


def _generated_candidates(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "generated_small_molecule_hypotheses",
        "label": "computational_hypotheses_only",
        "summary": bundle.get("generated_summary", {}),
        "advanced_without_review": False,
        "review_required": True,
        "claims": [],
    }


def _biologic_candidates(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "source_backed_biologic_candidate_summary",
        "label": "source_backed_research_planning_candidates",
        "summary": bundle.get("biologics_summary", {}),
        "claims": [],
    }


def _generated_antibodies(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "generated_antibody_hypotheses",
        "label": "computational_hypotheses_only",
        "summary": bundle.get("biologics_summary", {}),
        "advanced_without_review": False,
        "review_required": True,
        "claims": [],
    }


def _developability(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "developability_triage",
        "label": "triage_not_manufacturability_claim",
        "summary": bundle.get("review_summary", {}).get("developability", {})
        or bundle.get("review_summary", {}),
    }


def _literature_evidence(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "literature_evidence_summary",
        "label": "source_backed_evidence_summary",
        "summary": bundle.get("evidence_summary", {}),
        "fabricated_evidence": 0,
    }


def _graph(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "knowledge_graph_summary",
        "label": "derived_graph_artifact_not_graph_truth",
        "workflow_id": bundle.get("workflow_id"),
        "claims": [],
    }


def _hypotheses(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "hypothesis_workspace",
        "label": "hypotheses_for_review",
        "review_required": True,
        "claims": [],
    }


def _portfolio_optimization(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "portfolio_optimization_summary",
        "label": "planning_recommendations_not_approval",
        "summary": bundle.get("campaign_summary", {}),
        "campaign_activation_enabled": False,
    }


def _campaign_plan(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "campaign_plan",
        "label": "draft_plan_requires_human_governance",
        "summary": bundle.get("campaign_summary", {}),
        "activated": False,
        "stage_gate_approved_by_codex": False,
    }


def _review_queue(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "review_queue",
        "label": "human_review_required",
        "summary": bundle.get("review_summary", {}),
        "items_require_review": True,
    }


def _evaluation_report(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "evaluation_report",
        "label": "software_workflow_evaluation",
        "summary": bundle.get("evaluation_summary", {}),
        "clinical_validation": False,
        "scientific_validation": False,
    }


def _bundle_markdown(bundle: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# V3 Result Bundle: {bundle.get('workflow_id')}",
            "",
            f"- Disease: {bundle.get('disease_name')}",
            f"- Project: {bundle.get('project_id')}",
            f"- Bundle: {bundle.get('bundle_id')}",
            "- Scope: software/autonomy validation artifact, not clinical validation.",
            "- Generated assets, when present, are computational hypotheses only.",
            "- External writes and campaign activation are disabled by default.",
            "",
        ]
    )


def _record_artifact(
    artifacts: dict[str, str],
    output_dir: Path,
    filename: str,
    payload: dict[str, Any],
) -> None:
    target = output_dir / filename
    _write_json(target, payload)
    artifacts[filename] = str(target)


def _artifact_manifest(artifacts: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": Path(filename).stem,
            "filename": filename,
            "path": path,
        }
        for filename, path in sorted(artifacts.items())
    ]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = [
    "DiscoverAutonomy",
    "DiscoverMode",
    "V3DiscoverRequest",
    "V3DiscoverResult",
    "run_v3_discover",
]
