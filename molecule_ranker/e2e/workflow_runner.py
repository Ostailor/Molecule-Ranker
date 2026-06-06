"""End-to-end governed workflow runner for V2.7 discovery operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.e2e.lineage import ExternalLineageTracker
from molecule_ranker.e2e.schemas import (
    EndToEndResultBundle,
    EndToEndWorkflow,
    EndToEndWorkflowStep,
    WorkflowLineageRecord,
    WorkflowMode,
    WorkflowStepType,
    WorkflowType,
)
from molecule_ranker.e2e.state_machine import (
    WorkflowStateMachine,
    WorkflowStateTransitionError,
)


class EndToEndWorkflowRunnerConfig(BaseModel):
    """Configuration for strict versus partial workflow completion."""

    partial_on_live_data_unavailable: bool = False


class WorkflowRunRequest(BaseModel):
    """Request to run a built-in end-to-end workflow."""

    workflow_type: WorkflowType
    mode: WorkflowMode = "mocked"
    disease_name: str | None = None
    project_id: str | None = None
    campaign_id: str | None = None
    requested_by: str | None = None
    autonomy_level: str = "governed"
    requested_external_write: bool = False
    approvals: list[str] = Field(default_factory=list)
    governance_permissions: list[str] = Field(default_factory=list)
    unavailable_required_data: list[str] = Field(default_factory=list)
    config: EndToEndWorkflowRunnerConfig = Field(
        default_factory=EndToEndWorkflowRunnerConfig
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResult(BaseModel):
    """Execution result returned by the workflow runner."""

    workflow: EndToEndWorkflow
    steps: list[EndToEndWorkflowStep]
    bundle: EndToEndResultBundle | None
    lineage_records: list[WorkflowLineageRecord]
    audit_events: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)
    external_writes_performed: int = 0
    planned_external_writes: int = 0

    def step_by_type(self, step_type: WorkflowStepType) -> EndToEndWorkflowStep:
        matching_steps = [step for step in self.steps if step.step_type == step_type]
        if not matching_steps:
            raise KeyError(f"step type not found: {step_type}")
        if step_type == "integration_sync":
            for step in matching_steps:
                if (
                    step.metadata.get("simulated_external_write")
                    or step.metadata.get("external_write")
                    or step.metadata.get("planned_external_write")
                ):
                    return step
        return matching_steps[0]


class _StepTemplate(BaseModel):
    step_name: str
    step_type: WorkflowStepType
    required: bool = True
    tool_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EndToEndWorkflowRunner:
    """Runs built-in E2E workflow templates through the state machine."""

    _WRITE_APPROVAL = "external_write"
    _WRITE_PERMISSION = "integration:write"

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def run(self, request: WorkflowRunRequest) -> WorkflowRunResult:
        workflow = self._build_workflow(request)
        steps = self._build_steps(workflow.workflow_id, request)
        machine = WorkflowStateMachine(workflow=workflow, steps=steps, now=self._now)
        warnings: list[str] = []
        external_writes_performed = 0
        planned_external_writes = self._count_planned_external_writes(steps)

        if (
            request.mode == "read_only_live"
            and request.requested_external_write
        ):
            warning = "read_only_live cannot perform external writes"
            warnings.append(warning)
            machine.start_workflow()
            machine.fail_workflow(reason=warning)
            return self._result(
                machine=machine,
                bundle=None,
                warnings=warnings,
                external_writes_performed=0,
                planned_external_writes=planned_external_writes,
            )

        try:
            machine.start_workflow()
            for step in machine.steps:
                unavailable = self._is_unavailable_required_live_data(step, request)
                if unavailable and not request.config.partial_on_live_data_unavailable:
                    warning = f"live required data unavailable for {step.step_type}"
                    warnings.append(warning)
                    machine.start_step(step.step_id)
                    machine.fail_step(step.step_id, reason=warning)
                    return self._result(
                        machine=machine,
                        bundle=None,
                        warnings=warnings,
                        external_writes_performed=external_writes_performed,
                        planned_external_writes=planned_external_writes,
                    )

                machine.start_step(step.step_id)

                current_step = machine.step_by_id(step.step_id)
                if current_step.status == "awaiting_approval":
                    if not self._has_write_authorization(request):
                        return self._result(
                            machine=machine,
                            bundle=None,
                            warnings=warnings,
                            external_writes_performed=external_writes_performed,
                            planned_external_writes=planned_external_writes,
                        )
                    machine.approve_step(
                        step.step_id,
                        approved_by=request.requested_by or "governance",
                        approval_id=self._WRITE_APPROVAL,
                    )

                if unavailable:
                    warning = f"live required data unavailable for {step.step_type}"
                    warnings.append(warning)
                    machine.fail_step(step.step_id, reason=warning)
                    continue

                current_step = machine.step_by_id(step.step_id)
                if self._should_simulate_external_write(current_step, request):
                    current_step.metadata["simulated_external_write"] = True

                if self._performs_external_write(current_step, request):
                    external_writes_performed += 1

                machine.complete_step(step.step_id)

            machine.complete_workflow()
        except WorkflowStateTransitionError as exc:
            warnings.append(str(exc))
            if machine.workflow.status not in {
                "failed",
                "cancelled",
                "succeeded",
                "partially_succeeded",
            }:
                machine.fail_workflow(reason=str(exc))
            return self._result(
                machine=machine,
                bundle=None,
                warnings=warnings,
                external_writes_performed=external_writes_performed,
                planned_external_writes=planned_external_writes,
            )

        bundle = self._build_bundle(
            workflow=machine.workflow,
            steps=machine.steps,
            request=request,
            warnings=warnings,
            external_writes_performed=external_writes_performed,
            planned_external_writes=planned_external_writes,
        )
        bundle = ExternalLineageTracker(
            workflow_id=machine.workflow.workflow_id,
            records=machine.lineage_records,
            now=self._now,
        ).include_in_bundle(bundle)
        return self._result(
            machine=machine,
            bundle=bundle,
            warnings=warnings,
            external_writes_performed=external_writes_performed,
            planned_external_writes=planned_external_writes,
        )

    def _build_workflow(self, request: WorkflowRunRequest) -> EndToEndWorkflow:
        workflow_id = request.metadata.get("workflow_id") or f"e2e-{uuid4().hex}"
        return EndToEndWorkflow(
            workflow_id=workflow_id,
            name=request.metadata.get("name")
            or request.workflow_type.replace("_", " ").title(),
            workflow_type=request.workflow_type,
            disease_name=request.disease_name,
            project_id=request.project_id,
            campaign_id=request.campaign_id,
            mode=request.mode,
            requested_by=request.requested_by,
            autonomy_level=request.autonomy_level,
            status="planned",
            created_at=self._now(),
            started_at=None,
            completed_at=None,
            metadata={
                **request.metadata,
                "runner": "EndToEndWorkflowRunner",
                "result_bundle_is_scientific_evidence": False,
            },
        )

    def _build_steps(
        self, workflow_id: str, request: WorkflowRunRequest
    ) -> list[EndToEndWorkflowStep]:
        templates = self._workflow_templates()[request.workflow_type]
        steps: list[EndToEndWorkflowStep] = []
        for index, template in enumerate(templates):
            required = template.required
            if (
                request.mode in {"read_only_live", "write_approved_live"}
                and request.config.partial_on_live_data_unavailable
                and template.step_type in request.unavailable_required_data
            ):
                required = False

            metadata = {
                **template.metadata,
                "mode": request.mode,
                "synthetic_source": request.mode == "mocked",
                "external_read_only": request.mode == "read_only_live",
            }
            if (
                request.workflow_type == "integration_sync_loop"
                and template.metadata.get("operation") == "sync"
            ):
                metadata.update(
                    self._integration_sync_metadata(request=request)
                )

            output_artifact_id = (
                f"artifact-{workflow_id}-{index}-{template.step_type}"
            )
            steps.append(
                EndToEndWorkflowStep(
                    step_id=f"{workflow_id}-step-{index}",
                    workflow_id=workflow_id,
                    step_index=index,
                    step_name=template.step_name,
                    step_type=template.step_type,
                    required=required,
                    tool_name=template.tool_name,
                    input_artifact_ids=[] if index == 0 else [steps[-1].output_artifact_ids[0]],
                    output_artifact_ids=[output_artifact_id],
                    external_system_ids=metadata.get("external_system_ids", []),
                    status="pending",
                    started_at=None,
                    completed_at=None,
                    warnings=[],
                    metadata=metadata,
                )
            )
        return steps

    def _integration_sync_metadata(
        self, request: WorkflowRunRequest
    ) -> dict[str, Any]:
        if request.mode == "dry_run":
            return {
                "planned_external_write": True,
                "simulated_external_write": True,
                "external_system_ids": ["mock-integration"],
            }
        if (
            request.mode == "write_approved_live"
            and request.requested_external_write
        ):
            return {
                "external_write": True,
                "planned_external_write": True,
                "external_system_ids": ["approved-live-integration"],
            }
        return {
            "planned_external_write": False,
            "external_system_ids": ["read-only-integration"],
        }

    def _workflow_templates(self) -> dict[WorkflowType, list[_StepTemplate]]:
        return {
            "disease_to_ranked_candidates": [
                _StepTemplate(
                    step_name="Project setup",
                    step_type="project_setup",
                    tool_name="project.setup",
                ),
                _StepTemplate(
                    step_name="Disease resolution and ranking",
                    step_type="disease_resolution",
                    tool_name="ranking.resolve_disease",
                ),
                _StepTemplate(
                    step_name="Literature retrieval",
                    step_type="literature_retrieval",
                    tool_name="literature.retrieve",
                ),
                _StepTemplate(
                    step_name="Developability review",
                    step_type="developability",
                    tool_name="developability.review",
                ),
                _StepTemplate(
                    step_name="Report bundle",
                    step_type="report_bundle",
                    tool_name="e2e.bundle",
                ),
            ],
            "disease_to_generated_hypotheses": [
                _StepTemplate(
                    step_name="Ranking",
                    step_type="disease_resolution",
                    tool_name="ranking.rank",
                ),
                _StepTemplate(
                    step_name="Generation",
                    step_type="generation",
                    tool_name="generation.plan",
                ),
                _StepTemplate(
                    step_name="Developability",
                    step_type="developability",
                    tool_name="developability.review",
                ),
                _StepTemplate(
                    step_name="Design readiness",
                    step_type="evaluation",
                    tool_name="design.readiness",
                ),
                _StepTemplate(
                    step_name="Generated report",
                    step_type="report_bundle",
                    tool_name="e2e.bundle",
                ),
            ],
            "disease_to_review_workspace": [
                _StepTemplate(
                    step_name="Ranking",
                    step_type="disease_resolution",
                    tool_name="ranking.rank",
                ),
                _StepTemplate(
                    step_name="Generation",
                    step_type="generation",
                    required=False,
                    tool_name="generation.plan",
                ),
                _StepTemplate(
                    step_name="Review workspace",
                    step_type="review_workspace",
                    tool_name="review.workspace",
                ),
                _StepTemplate(
                    step_name="Dossier handoff",
                    step_type="report_bundle",
                    tool_name="e2e.handoff",
                ),
            ],
            "disease_to_campaign_plan": [
                _StepTemplate(
                    step_name="Ranking",
                    step_type="disease_resolution",
                    tool_name="ranking.rank",
                ),
                _StepTemplate(
                    step_name="Hypotheses",
                    step_type="hypothesis_generation",
                    tool_name="hypothesis.generate",
                ),
                _StepTemplate(
                    step_name="Campaign plan",
                    step_type="campaign_planning",
                    tool_name="campaign.plan",
                ),
            ],
            "disease_to_portfolio_and_campaign": [
                _StepTemplate(
                    step_name="Ranking",
                    step_type="disease_resolution",
                    tool_name="ranking.rank",
                ),
                _StepTemplate(
                    step_name="Graph",
                    step_type="graph_build",
                    tool_name="graph.build",
                ),
                _StepTemplate(
                    step_name="Hypotheses",
                    step_type="hypothesis_generation",
                    tool_name="hypothesis.generate",
                ),
                _StepTemplate(
                    step_name="Portfolio",
                    step_type="portfolio_optimization",
                    tool_name="portfolio.optimize",
                ),
                _StepTemplate(
                    step_name="Campaign plan",
                    step_type="campaign_planning",
                    tool_name="campaign.plan",
                ),
            ],
            "full_discovery_loop": [
                _StepTemplate(
                    step_name="Create project",
                    step_type="project_setup",
                    tool_name="project.setup",
                ),
                _StepTemplate(
                    step_name="Ranking",
                    step_type="disease_resolution",
                    tool_name="ranking.rank",
                ),
                _StepTemplate(
                    step_name="Literature",
                    step_type="literature_retrieval",
                    tool_name="literature.retrieve",
                ),
                _StepTemplate(
                    step_name="Generation",
                    step_type="generation",
                    tool_name="generation.plan",
                ),
                _StepTemplate(
                    step_name="Developability",
                    step_type="developability",
                    tool_name="developability.review",
                ),
                _StepTemplate(
                    step_name="Predictive model",
                    step_type="evaluation",
                    required=False,
                    tool_name="model.predict_if_available",
                ),
                _StepTemplate(
                    step_name="Structure",
                    step_type="developability",
                    required=False,
                    tool_name="structure.assess_if_available",
                ),
                _StepTemplate(
                    step_name="Graph",
                    step_type="graph_build",
                    tool_name="graph.build",
                ),
                _StepTemplate(
                    step_name="Hypotheses",
                    step_type="hypothesis_generation",
                    tool_name="hypothesis.generate",
                ),
                _StepTemplate(
                    step_name="Portfolio",
                    step_type="portfolio_optimization",
                    tool_name="portfolio.optimize",
                ),
                _StepTemplate(
                    step_name="Campaign",
                    step_type="campaign_planning",
                    tool_name="campaign.plan",
                ),
                _StepTemplate(
                    step_name="Review",
                    step_type="review_workspace",
                    tool_name="review.workspace",
                ),
                _StepTemplate(
                    step_name="Evaluation",
                    step_type="evaluation",
                    tool_name="evaluation.review",
                ),
                _StepTemplate(
                    step_name="Result bundle",
                    step_type="report_bundle",
                    tool_name="e2e.bundle",
                ),
            ],
            "integration_sync_loop": [
                _StepTemplate(
                    step_name="Integration health",
                    step_type="integration_sync",
                    tool_name="integration.health",
                    metadata={"operation": "health"},
                ),
                _StepTemplate(
                    step_name="Dry-run sync",
                    step_type="integration_sync",
                    tool_name="integration.sync",
                    metadata={"operation": "sync"},
                ),
                _StepTemplate(
                    step_name="Mapping review",
                    step_type="review_workspace",
                    tool_name="integration.mapping_review",
                ),
                _StepTemplate(
                    step_name="Import validated data",
                    step_type="experimental_import",
                    tool_name="integration.import_validated",
                ),
                _StepTemplate(
                    step_name="Lineage update",
                    step_type="report_bundle",
                    tool_name="lineage.update",
                ),
                _StepTemplate(
                    step_name="Report",
                    step_type="report_bundle",
                    tool_name="e2e.bundle",
                ),
            ],
            "prospective_evaluation_loop": [
                _StepTemplate(
                    step_name="Evaluation setup",
                    step_type="project_setup",
                    tool_name="evaluation.setup",
                ),
                _StepTemplate(
                    step_name="Prospective evaluation",
                    step_type="evaluation",
                    tool_name="evaluation.prospective",
                ),
                _StepTemplate(
                    step_name="Report bundle",
                    step_type="report_bundle",
                    tool_name="e2e.bundle",
                ),
            ],
        }

    def _is_unavailable_required_live_data(
        self, step: EndToEndWorkflowStep, request: WorkflowRunRequest
    ) -> bool:
        return (
            request.mode in {"read_only_live", "write_approved_live"}
            and step.step_type in request.unavailable_required_data
        )

    def _has_write_authorization(self, request: WorkflowRunRequest) -> bool:
        return (
            self._WRITE_APPROVAL in request.approvals
            and self._WRITE_PERMISSION in request.governance_permissions
        )

    def _should_simulate_external_write(
        self, step: EndToEndWorkflowStep, request: WorkflowRunRequest
    ) -> bool:
        return (
            request.mode == "dry_run"
            and step.step_type == "integration_sync"
            and step.metadata.get("operation") == "sync"
        )

    def _performs_external_write(
        self, step: EndToEndWorkflowStep, request: WorkflowRunRequest
    ) -> bool:
        return (
            request.mode == "write_approved_live"
            and step.metadata.get("external_write") is True
            and self._has_write_authorization(request)
        )

    def _count_planned_external_writes(
        self, steps: list[EndToEndWorkflowStep]
    ) -> int:
        return sum(1 for step in steps if step.metadata.get("planned_external_write"))

    def _build_bundle(
        self,
        workflow: EndToEndWorkflow,
        steps: list[EndToEndWorkflowStep],
        request: WorkflowRunRequest,
        warnings: list[str],
        external_writes_performed: int,
        planned_external_writes: int,
    ) -> EndToEndResultBundle:
        succeeded_steps = [step for step in steps if step.status == "succeeded"]
        failed_steps = [step for step in steps if step.status == "failed"]
        key_artifact_ids = [
            artifact_id
            for step in succeeded_steps
            for artifact_id in step.output_artifact_ids
        ]
        return EndToEndResultBundle(
            bundle_id=f"bundle-{workflow.workflow_id}",
            workflow_id=workflow.workflow_id,
            project_id=workflow.project_id,
            disease_name=workflow.disease_name,
            result_summary=(
                f"{workflow.workflow_type} completed with status {workflow.status}. "
                "The bundle summarizes workflow outputs and is not scientific evidence."
            ),
            key_artifact_ids=key_artifact_ids,
            candidate_summary={
                "ranking_steps_completed": self._count_succeeded(
                    steps, "disease_resolution"
                ),
                "fabricated_molecules": 0,
            },
            generated_summary={
                "generation_steps_completed": self._count_succeeded(
                    steps, "generation"
                ),
                "generated_molecules_advanced_without_review": 0,
            },
            evidence_summary={
                "literature_steps_completed": self._count_succeeded(
                    steps, "literature_retrieval"
                ),
                "fabricated_evidence": 0,
            },
            review_summary={
                "review_steps_completed": self._count_succeeded(
                    steps, "review_workspace"
                )
            },
            campaign_summary={
                "campaign_steps_completed": self._count_succeeded(
                    steps, "campaign_planning"
                )
            },
            evaluation_summary={
                "evaluation_steps_completed": self._count_succeeded(
                    steps, "evaluation"
                ),
                "failed_steps": [step.step_type for step in failed_steps],
            },
            integration_summary={
                "mode": request.mode,
                "planned_external_writes": planned_external_writes,
                "external_writes_performed": external_writes_performed,
                "deterministic_validation_required": True,
            },
            limitations=[
                "End-to-end result bundle is not scientific evidence.",
                "No patient treatment, dosing, lab protocol, or synthesis guidance is provided.",
                *warnings,
            ],
            created_at=self._now(),
            metadata={
                "mode": request.mode,
                "workflow_status": workflow.status,
                "scientific_evidence": False,
                "external_writes_performed": external_writes_performed,
                "approval_ids": list(request.approvals),
            },
        )

    def _count_succeeded(
        self, steps: list[EndToEndWorkflowStep], step_type: WorkflowStepType
    ) -> int:
        return sum(
            1
            for step in steps
            if step.step_type == step_type and step.status == "succeeded"
        )

    def _result(
        self,
        machine: WorkflowStateMachine,
        bundle: EndToEndResultBundle | None,
        warnings: list[str],
        external_writes_performed: int,
        planned_external_writes: int,
    ) -> WorkflowRunResult:
        return WorkflowRunResult(
            workflow=machine.workflow,
            steps=machine.steps,
            bundle=bundle,
            lineage_records=machine.lineage_records,
            audit_events=machine.audit_events,
            warnings=warnings,
            external_writes_performed=external_writes_performed,
            planned_external_writes=planned_external_writes,
        )


__all__ = [
    "EndToEndWorkflowRunner",
    "EndToEndWorkflowRunnerConfig",
    "WorkflowRunRequest",
    "WorkflowRunResult",
]
