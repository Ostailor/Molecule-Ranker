from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.e2e.schemas import EndToEndResultBundle, WorkflowMode
from molecule_ranker.e2e.workflow_runner import (
    EndToEndWorkflowRunner,
    EndToEndWorkflowRunnerConfig,
    WorkflowRunRequest,
    WorkflowRunResult,
)
from molecule_ranker.runtime_agents.skills.base import (
    RuntimeSkillSpec,
    RuntimeSkillStepTemplate,
    _object_schema,
)

EXTERNAL_WRITE_APPROVALS = {"external_write", "integration_sync"}
GENERATED_ADVANCEMENT_APPROVAL = "generated_molecule_export"


class FullEndToEndDiscoverySkillRequest(BaseModel):
    """Inputs for the V2.9 full end-to-end discovery runtime skill."""

    mode: WorkflowMode = "mocked"
    disease_name: str | None = None
    project_id: str | None = None
    campaign_id: str | None = None
    requested_by: str | None = None
    autonomy_level: str = "governed"
    include_generation: bool = True
    include_structure_tools: bool = False
    include_model_tools: bool = False
    requested_external_write: bool = False
    generated_advancement_requested: bool = False
    approvals: list[str] = Field(default_factory=list)
    governance_permissions: list[str] = Field(default_factory=list)
    unavailable_required_data: list[str] = Field(default_factory=list)
    partial_on_live_data_unavailable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class FullEndToEndDiscoverySkillResult(BaseModel):
    """Deterministic result returned by the runtime skill helper."""

    status: str
    workflow_result: WorkflowRunResult | None = None
    bundle: EndToEndResultBundle | None = None
    required_approvals: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    external_writes_performed: int = 0
    planned_external_writes: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


FULL_END_TO_END_DISCOVERY = RuntimeSkillSpec(
    skill_name="full_end_to_end_discovery",
    description="Run the governed V2.9 full discovery loop and generate an auditable bundle.",
    input_schema=_object_schema(
        {
            "mode": {
                "type": "string",
                "enum": [
                    "mocked",
                    "dry_run",
                    "read_only_live",
                    "write_approved_live",
                ],
            },
            "disease_name": {"type": "string"},
            "project_id": {"type": "string"},
            "campaign_id": {"type": "string"},
            "include_generation": {"type": "boolean"},
            "include_structure_tools": {"type": "boolean"},
            "include_model_tools": {"type": "boolean"},
            "requested_external_write": {"type": "boolean"},
            "generated_advancement_requested": {"type": "boolean"},
        }
    ),
    default_plan_template=[
        RuntimeSkillStepTemplate(
            action_type="create_or_select_project",
            tool_name="create_project",
            expected_outputs=["project_workspace"],
            metadata={"select_existing_project_when_project_id_present": True},
        ),
        RuntimeSkillStepTemplate(
            action_type="ranking",
            tool_name="run_ranking",
            expected_outputs=["ranking_artifact"],
        ),
        RuntimeSkillStepTemplate(
            action_type="literature",
            tool_name="run_literature_update",
            expected_outputs=["literature_artifact"],
        ),
        RuntimeSkillStepTemplate(
            action_type="developability",
            tool_name="run_developability",
            expected_outputs=["developability_artifact"],
        ),
        RuntimeSkillStepTemplate(
            action_type="generation",
            tool_name="run_generation",
            expected_outputs=["generated_molecule_hypotheses"],
            optional=True,
        ),
        RuntimeSkillStepTemplate(
            action_type="structure_if_configured",
            tool_name="run_structure_validation",
            expected_outputs=["structure_validation"],
            optional=True,
        ),
        RuntimeSkillStepTemplate(
            action_type="model_if_configured",
            tool_name="run_model_validation",
            expected_outputs=["model_validation"],
            optional=True,
        ),
        RuntimeSkillStepTemplate(
            action_type="graph_build",
            tool_name="build_graph",
            expected_outputs=["knowledge_graph"],
        ),
        RuntimeSkillStepTemplate(
            action_type="hypothesis_generation",
            tool_name="generate_hypotheses",
            expected_outputs=["hypotheses"],
        ),
        RuntimeSkillStepTemplate(
            action_type="portfolio_optimization",
            tool_name="optimize_portfolio",
            expected_outputs=["portfolio_optimization"],
        ),
        RuntimeSkillStepTemplate(
            action_type="campaign_planning",
            tool_name="plan_campaign",
            approval_requirements=[
                GENERATED_ADVANCEMENT_APPROVAL,
                "campaign_advance",
                "stage_gate",
            ],
            expected_outputs=["campaign_plan"],
        ),
        RuntimeSkillStepTemplate(
            action_type="review_workspace",
            tool_name="create_review_workspace",
            expected_outputs=["review_workspace"],
        ),
        RuntimeSkillStepTemplate(
            action_type="evaluation",
            tool_name="run_benchmark",
            expected_outputs=["evaluation_report"],
        ),
        RuntimeSkillStepTemplate(
            action_type="result_bundle",
            tool_name="summarize_artifacts",
            expected_outputs=["e2e_result_bundle"],
        ),
    ],
    required_tools=[
        "create_project",
        "run_ranking",
        "run_literature_update",
        "run_developability",
        "run_generation",
        "run_structure_validation",
        "run_model_validation",
        "build_graph",
        "generate_hypotheses",
        "optimize_portfolio",
        "plan_campaign",
        "create_review_workspace",
        "run_benchmark",
        "summarize_artifacts",
    ],
    required_permissions=[
        "project:create",
        "run:create",
        "literature:update",
        "developability:run",
        "generation:run",
        "structure:run",
        "model:run",
        "graph:build",
        "hypotheses:generate",
        "portfolio:run",
        "campaign:plan",
        "review:write",
        "evaluation:run",
        "codex:run",
    ],
    approval_requirements=[],
    expected_artifacts=[
        "project_workspace",
        "ranking_artifact",
        "literature_artifact",
        "developability_artifact",
        "generated_molecule_hypotheses",
        "structure_validation",
        "model_validation",
        "knowledge_graph",
        "hypotheses",
        "portfolio_optimization",
        "campaign_plan",
        "review_workspace",
        "evaluation_report",
        "e2e_result_bundle",
    ],
    guardrails=[
        "External writes require explicit approval and governance permission.",
        "Generated molecule advancement requires human review approval.",
        "Missing live data must fail or mark the workflow partial according to config.",
        "Codex summaries cannot create biomedical evidence or fill missing workflow steps.",
        "The result bundle is a research operations summary, not scientific evidence.",
    ],
    metadata={
        "v2_7_runtime_skill": True,
        "workflow_type": "full_discovery_loop",
        "modes": ["mocked", "dry_run", "read_only_live", "write_approved_live"],
    },
)


def run_full_end_to_end_discovery_skill(
    request: FullEndToEndDiscoverySkillRequest,
    *,
    runner: EndToEndWorkflowRunner | None = None,
    now: Callable[[], datetime] | None = None,
) -> FullEndToEndDiscoverySkillResult:
    """Execute the runtime skill through the deterministic V2.9 e2e runner."""

    required_approvals = _missing_required_approvals(request)
    if required_approvals:
        return FullEndToEndDiscoverySkillResult(
            status="awaiting_approval",
            required_approvals=required_approvals,
            warnings=[_approval_warning(required_approvals)],
            metadata={
                "mode": request.mode,
                "workflow_type": "full_discovery_loop",
                "approval_blocked_before_execution": True,
            },
        )

    active_runner = runner or EndToEndWorkflowRunner(now=now)
    workflow_result = active_runner.run(_workflow_request(request))
    planned_external_writes = workflow_result.planned_external_writes
    metadata: dict[str, Any] = {
        "mode": request.mode,
        "workflow_type": "full_discovery_loop",
        "include_generation": request.include_generation,
        "include_structure_tools": request.include_structure_tools,
        "include_model_tools": request.include_model_tools,
        "codex_fabricated_missing_steps": False,
    }

    if request.mode == "dry_run" and request.requested_external_write:
        planned_external_writes = max(planned_external_writes, 1)
        metadata["dry_run_external_write_simulated"] = True

    return FullEndToEndDiscoverySkillResult(
        status=workflow_result.workflow.status,
        workflow_result=workflow_result,
        bundle=workflow_result.bundle,
        required_approvals=[],
        warnings=workflow_result.warnings,
        external_writes_performed=workflow_result.external_writes_performed,
        planned_external_writes=planned_external_writes,
        metadata=metadata,
    )


def _workflow_request(request: FullEndToEndDiscoverySkillRequest) -> WorkflowRunRequest:
    return WorkflowRunRequest(
        workflow_type="full_discovery_loop",
        mode=request.mode,
        disease_name=request.disease_name,
        project_id=request.project_id,
        campaign_id=request.campaign_id,
        requested_by=request.requested_by,
        autonomy_level=request.autonomy_level,
        requested_external_write=request.requested_external_write,
        approvals=request.approvals,
        governance_permissions=request.governance_permissions,
        unavailable_required_data=request.unavailable_required_data,
        config=EndToEndWorkflowRunnerConfig(
            partial_on_live_data_unavailable=request.partial_on_live_data_unavailable
        ),
        metadata={
            **request.metadata,
            "runtime_skill": "full_end_to_end_discovery",
            "include_generation": request.include_generation,
            "include_structure_tools": request.include_structure_tools,
            "include_model_tools": request.include_model_tools,
            "result_bundle_is_scientific_evidence": False,
        },
    )


def _missing_required_approvals(
    request: FullEndToEndDiscoverySkillRequest,
) -> list[str]:
    approvals = set(request.approvals)
    missing: list[str] = []
    if (
        request.mode == "write_approved_live"
        and request.requested_external_write
        and not EXTERNAL_WRITE_APPROVALS.intersection(approvals)
    ):
        missing.append("external_write")
    if (
        request.generated_advancement_requested
        and GENERATED_ADVANCEMENT_APPROVAL not in approvals
    ):
        missing.append(GENERATED_ADVANCEMENT_APPROVAL)
    return missing


def _approval_warning(required_approvals: list[str]) -> str:
    return "Runtime skill requires approval before execution: " + ", ".join(
        required_approvals
    )


__all__ = [
    "FULL_END_TO_END_DISCOVERY",
    "FullEndToEndDiscoverySkillRequest",
    "FullEndToEndDiscoverySkillResult",
    "run_full_end_to_end_discovery_skill",
]
