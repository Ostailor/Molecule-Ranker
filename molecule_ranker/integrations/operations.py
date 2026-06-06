from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker import __version__
from molecule_ranker.integrations.schemas import DataContract
from molecule_ranker.integrations.validation import validate_record_against_contract
from molecule_ranker.runtime_agents.schemas import RuntimeActionPlan, RuntimeActionStep
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

WorkflowRunMode = Literal["mocked", "dry_run", "live"]
ExternalSyncPlanMode = Literal["read_only", "dry_run", "write_approved"]
WorkflowStatus = Literal[
    "planned",
    "running",
    "succeeded",
    "failed",
    "validation_failed",
    "approval_required",
]
RepairStatus = Literal["no_repair_needed", "repair_planned", "blocked"]

WORKFLOW_STAGES: tuple[str, ...] = (
    "intake",
    "ranking",
    "generation",
    "review",
    "experiments",
    "graph",
    "hypotheses",
    "portfolio",
    "campaign",
    "evaluation",
    "bundle",
)

SCIENTIFIC_TRUTH_ARTIFACT_TYPES = {
    "evidence_item",
    "generated_molecule",
    "graph_fact",
    "assay_result",
    "citation",
}


class IntegrationOpsModel(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must be timezone-aware")
        return value


class EndToEndWorkflowRequest(IntegrationOpsModel):
    objective: str
    disease: str | None = None
    project_id: str | None = None
    org_id: str = "default"
    user_id: str | None = None
    mode: WorkflowRunMode = "mocked"
    resume_from: dict[str, Any] | None = None
    requested_external_write: bool = False
    write_approval_id: str | None = None
    governance_permissions: list[str] = Field(default_factory=list)
    approved_tool_names: list[str] = Field(default_factory=list)
    external_records: list[dict[str, Any]] = Field(default_factory=list)
    data_contracts: dict[str, DataContract] = Field(default_factory=dict)
    output_dir: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("objective")
    @classmethod
    def require_objective(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective is required")
        return value.strip()


class ExternalSyncPlan(IntegrationOpsModel):
    plan_id: str = Field(default_factory=lambda: f"external-sync-plan-{uuid4().hex[:12]}")
    workflow_id: str
    external_system_ids: list[str] = Field(default_factory=list)
    object_types: list[str] = Field(default_factory=lambda: ["assay_results"])
    sync_mode: ExternalSyncPlanMode
    runtime_tool_name: str
    requires_human_approval: bool
    approval_id: str | None = None
    deterministic_validation_required: bool = True
    external_write_allowed: bool = False
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowArtifact(IntegrationOpsModel):
    artifact_id: str
    artifact_type: str
    stage: str
    source: str
    sha256: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_scientific_truth_artifacts(self) -> WorkflowArtifact:
        if self.artifact_type in SCIENTIFIC_TRUTH_ARTIFACT_TYPES:
            if self.metadata.get("human_reviewed") is not True:
                raise ValueError(
                    f"{self.artifact_type} artifacts require validated source data and human review"
                )
        return self


class LineageLink(IntegrationOpsModel):
    lineage_id: str = Field(default_factory=lambda: f"lineage-{uuid4().hex[:12]}")
    artifact_id: str
    internal_system: str = "molecule-ranker"
    external_system_id: str | None = None
    external_record_id: str | None = None
    relationship: str = "derived_from"
    deterministic_validation: bool
    validation_summary: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationOperationResult(IntegrationOpsModel):
    operation_id: str = Field(default_factory=lambda: f"integration-op-{uuid4().hex[:12]}")
    status: WorkflowStatus
    sync_plan: ExternalSyncPlan
    records_seen: int = 0
    records_valid: int = 0
    records_failed: int = 0
    records_skipped: int = 0
    external_write_performed: bool = False
    validation_reports: list[dict[str, Any]] = Field(default_factory=list)
    lineage_links: list[LineageLink] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegrationRepairPlan(IntegrationOpsModel):
    repair_plan_id: str = Field(default_factory=lambda: f"integration-repair-{uuid4().hex[:12]}")
    status: RepairStatus
    blocked_scientific_repair: bool = False
    actions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EndToEndResultBundle(IntegrationOpsModel):
    bundle_id: str = Field(default_factory=lambda: f"e2e-bundle-{uuid4().hex[:12]}")
    workflow_id: str
    version: str = __version__
    mode: WorkflowRunMode
    status: WorkflowStatus
    objective: str
    project_id: str | None = None
    workflow_state: dict[str, Any]
    runtime_plan: dict[str, Any]
    sync_plan: ExternalSyncPlan
    integration_result: IntegrationOperationResult
    repair_plan: IntegrationRepairPlan | None = None
    artifacts: list[WorkflowArtifact] = Field(default_factory=list)
    lineage_links: list[LineageLink] = Field(default_factory=list)
    audit_events: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    safety_constraints: dict[str, bool] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def write(self, output_dir: str | Path) -> dict[str, Path]:
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        json_path = target / "end_to_end_result_bundle.json"
        manifest_path = target / "end_to_end_result_bundle.manifest.json"
        markdown_path = target / "end_to_end_result_bundle.md"
        payload = self.model_dump(mode="json")
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "artifact_type": "end_to_end_result_bundle",
            "version": self.version,
            "workflow_id": self.workflow_id,
            "bundle_id": self.bundle_id,
            "sha256": _sha256_json(payload),
            "created_at": self.created_at.isoformat(),
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(_bundle_markdown(self), encoding="utf-8")
        return {"json": json_path, "manifest": manifest_path, "markdown": markdown_path}


class WorkflowStateMachine:
    def __init__(self) -> None:
        self.workflow_id: str | None = None
        self.current_stage = "created"
        self.completed_stages: list[str] = []
        self.status: WorkflowStatus = "planned"
        self.audit_events: list[dict[str, Any]] = []

    def start(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        self.current_stage = "intake"
        self.status = "running"
        self.completed_stages = ["intake"]
        self._audit("workflow_started", "Workflow started.")

    def advance(self, stage: str) -> None:
        if stage not in WORKFLOW_STAGES:
            raise ValueError(f"Unknown workflow stage: {stage}")
        if self.current_stage == "created":
            raise ValueError("Workflow must be started before advancing.")
        expected_index = len(self.completed_stages)
        if WORKFLOW_STAGES[expected_index] != stage:
            raise ValueError(f"Expected next stage {WORKFLOW_STAGES[expected_index]}, got {stage}.")
        self.current_stage = stage
        self.completed_stages.append(stage)
        self._audit("workflow_stage_completed", f"Completed stage {stage}.")

    def complete(self) -> None:
        self.current_stage = "completed"
        self.status = "succeeded"
        self._audit("workflow_completed", "Workflow completed.")

    def fail(self, summary: str) -> None:
        self.status = "failed"
        self._audit("workflow_failed", summary)

    def snapshot(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "current_stage": self.current_stage,
            "completed_stages": list(self.completed_stages),
            "status": self.status,
            "audit_events": list(self.audit_events),
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> WorkflowStateMachine:
        machine = cls()
        machine.workflow_id = snapshot.get("workflow_id")
        machine.current_stage = str(snapshot.get("current_stage") or "created")
        machine.completed_stages = [
            stage for stage in snapshot.get("completed_stages", []) if stage in WORKFLOW_STAGES
        ]
        machine.status = snapshot.get("status", "planned")
        machine.audit_events = list(snapshot.get("audit_events", []))
        return machine

    def _audit(self, event_type: str, summary: str) -> None:
        self.audit_events.append(
            {
                "event_id": f"workflow-audit-{uuid4().hex[:12]}",
                "workflow_id": self.workflow_id,
                "event_type": event_type,
                "timestamp": datetime.now(UTC).isoformat(),
                "summary": summary,
            }
        )


class ExternalLineageTracker:
    def __init__(self) -> None:
        self.links: list[LineageLink] = []

    def link_internal_artifact(
        self,
        *,
        artifact_id: str,
        relationship: str = "created_by_workflow",
        metadata: dict[str, Any] | None = None,
    ) -> LineageLink:
        link = LineageLink(
            artifact_id=artifact_id,
            relationship=relationship,
            deterministic_validation=True,
            validation_summary="Internal workflow artifact; no external truth asserted.",
            metadata=metadata or {},
        )
        self.links.append(link)
        return link

    def link_external_record(
        self,
        *,
        artifact_id: str,
        external_system_id: str,
        external_record_id: str,
        deterministic_validation: bool,
        validation_summary: str = "External record passed deterministic validation.",
        metadata: dict[str, Any] | None = None,
    ) -> LineageLink:
        if not deterministic_validation:
            raise ValueError("External lineage requires deterministic validation.")
        if not external_record_id.strip():
            raise ValueError("External lineage requires a source external record ID.")
        link = LineageLink(
            artifact_id=artifact_id,
            external_system_id=external_system_id,
            external_record_id=external_record_id,
            deterministic_validation=True,
            validation_summary=validation_summary,
            metadata=metadata or {},
        )
        self.links.append(link)
        return link


class ExternalSyncPlanner:
    def plan(
        self,
        request: EndToEndWorkflowRequest,
        *,
        workflow_id: str | None = None,
    ) -> ExternalSyncPlan:
        active_workflow_id = workflow_id or f"workflow-{uuid4().hex[:12]}"
        warnings = [
            "External integration results must pass deterministic validation before scoring.",
            "No external writes are performed without explicit approval and governance permission.",
        ]
        if request.mode == "live" and request.requested_external_write:
            has_permission = "integration:write" in request.governance_permissions
            if not request.write_approval_id or not has_permission:
                raise PermissionError(
                    "Live external writes require explicit approval and "
                    "integration:write governance permission."
                )
            return ExternalSyncPlan(
                workflow_id=active_workflow_id,
                sync_mode="write_approved",
                runtime_tool_name="run_sync_write_enabled",
                requires_human_approval=True,
                approval_id=request.write_approval_id,
                external_write_allowed=True,
                warnings=warnings,
            )
        if request.mode == "dry_run":
            return ExternalSyncPlan(
                workflow_id=active_workflow_id,
                sync_mode="dry_run",
                runtime_tool_name="dry_run_sync",
                requires_human_approval=False,
                warnings=warnings,
            )
        return ExternalSyncPlan(
            workflow_id=active_workflow_id,
            sync_mode="read_only",
            runtime_tool_name="health_check_integration",
            requires_human_approval=False,
            warnings=warnings,
        )


class IntegrationDryRunSimulator:
    def simulate(
        self,
        request: EndToEndWorkflowRequest,
        sync_plan: ExternalSyncPlan,
        lineage: ExternalLineageTracker,
    ) -> IntegrationOperationResult:
        validation_reports: list[dict[str, Any]] = []
        valid_records = 0
        failed_records = 0
        for record in request.external_records:
            report = _validate_external_record(record, request.data_contracts)
            validation_reports.append(report)
            if report["valid"]:
                valid_records += 1
                artifact_id = f"validated-external-record-{_sha256_json(record)[:12]}"
                external_record_id = str(
                    record.get("source_record_id") or record.get("external_record_id") or ""
                )
                if external_record_id:
                    lineage.link_external_record(
                        artifact_id=artifact_id,
                        external_system_id=str(
                            record.get("external_system_id") or "external-system"
                        ),
                        external_record_id=external_record_id,
                        deterministic_validation=True,
                        metadata={"object_type": report["object_type"]},
                    )
            else:
                failed_records += 1
        status: WorkflowStatus = "succeeded" if failed_records == 0 else "validation_failed"
        return IntegrationOperationResult(
            status=status,
            sync_plan=sync_plan,
            records_seen=len(request.external_records),
            records_valid=valid_records,
            records_failed=failed_records,
            records_skipped=0 if request.external_records else 1,
            external_write_performed=False,
            validation_reports=validation_reports,
            lineage_links=list(lineage.links),
            warnings=list(sync_plan.warnings),
            completed_at=datetime.now(UTC),
            metadata={
                "simulated": True,
                "mode": request.mode,
                "deterministic_validation_gate": True,
            },
        )


class IntegrationOpsAgent:
    def __init__(
        self,
        *,
        planner: ExternalSyncPlanner | None = None,
        simulator: IntegrationDryRunSimulator | None = None,
    ) -> None:
        self.planner = planner or ExternalSyncPlanner()
        self.simulator = simulator or IntegrationDryRunSimulator()

    def operate(
        self,
        request: EndToEndWorkflowRequest,
        *,
        workflow_id: str | None = None,
    ) -> IntegrationOperationResult:
        sync_plan = self.planner.plan(request, workflow_id=workflow_id)
        lineage = ExternalLineageTracker()
        return self.simulator.simulate(request, sync_plan, lineage)


class IntegrationRepairAgent:
    def diagnose_and_plan(self, result: IntegrationOperationResult) -> IntegrationRepairPlan:
        if result.status == "succeeded":
            return IntegrationRepairPlan(status="no_repair_needed")
        if result.records_failed:
            return IntegrationRepairPlan(
                status="repair_planned",
                actions=[
                    {
                        "action": "repair_external_contract_mapping",
                        "reason": "External records failed deterministic data-contract validation.",
                        "allowed_scope": "integration_validation_or_mapping_only",
                    }
                ],
                warnings=[
                    "Repair may update workflow validation or mapping configuration only.",
                    "Repair must not invent assay results, citations, molecules, or graph facts.",
                ],
            )
        return IntegrationRepairPlan(
            status="blocked",
            blocked_scientific_repair=True,
            warnings=["No safe deterministic integration repair is available."],
        )


class ExternalWorkflowOrchestrator:
    def __init__(self, *, registry: RuntimeToolRegistry | None = None) -> None:
        self.registry = registry or RuntimeToolRegistry.default()

    def build_plan(
        self,
        request: EndToEndWorkflowRequest,
        *,
        workflow_id: str,
    ) -> RuntimeActionPlan:
        tool_sequence = [
            "run_ranking",
            "run_generation",
            "create_review_workspace",
            "import_assay_results",
            "build_graph",
            "generate_hypotheses",
            "optimize_portfolio",
            "plan_campaign",
            "run_benchmark",
            "dry_run_sync" if request.mode != "live" else "health_check_integration",
        ]
        approved_filter = set(request.approved_tool_names)
        if approved_filter:
            tool_sequence = [tool for tool in tool_sequence if tool in approved_filter]
        steps: list[RuntimeActionStep] = []
        for index, tool_name in enumerate(tool_sequence):
            spec = self.registry.require(tool_name)
            steps.append(
                RuntimeActionStep(
                    step_id=f"e2e-step-{index + 1}",
                    plan_id=f"e2e-plan-{workflow_id}",
                    step_index=index,
                    action_type=f"v2_7_{spec.category}",
                    tool_name=tool_name,
                    tool_args={
                        "objective": request.objective,
                        "project_id": request.project_id,
                        "mode": request.mode,
                    },
                    requires_approval=spec.requires_approval_by_default,
                    approval_reason="Tool requires approval by policy."
                    if spec.requires_approval_by_default
                    else None,
                    expected_outputs=[f"{spec.category}_artifact"],
                    status="pending",
                    metadata={"v2_7_end_to_end": True},
                )
            )
        return RuntimeActionPlan(
            plan_id=f"e2e-plan-{workflow_id}",
            session_id=f"e2e-session-{workflow_id}",
            user_goal=request.objective,
            plan_summary="V2.8 governed end-to-end discovery workflow.",
            steps=steps,
            required_approvals=["external_write"]
            if request.requested_external_write
            else [],
            expected_artifacts=["end_to_end_result_bundle"],
            risk_level="medium" if request.mode != "live" else "high",
            guardrail_warnings=[
                "No medical advice, treatment guidance, dosing, lab protocols, "
                "or synthesis instructions.",
                "Codex remains in the loop only through approved tools and cannot "
                "create scientific truth.",
            ],
            created_by="deterministic_template",
            validated=True,
            metadata={
                "tool_specs": {
                    step.tool_name: {
                        "required_permissions": self.registry.require(
                            step.tool_name
                        ).required_permissions,
                        "side_effect_level": self.registry.require(
                            step.tool_name
                        ).side_effect_level,
                        "policy_tags": self.registry.require(step.tool_name).policy_tags,
                    }
                    for step in steps
                },
                "runtime_context": {
                    "org_id": request.org_id,
                    "project_id": request.project_id,
                    "user_id": request.user_id,
                    "user_permissions": request.governance_permissions,
                },
                "codex_runtime_loop": "approved_tools_only",
            },
        )


class EndToEndWorkflowRunner:
    def __init__(
        self,
        *,
        orchestrator: ExternalWorkflowOrchestrator | None = None,
        integration_agent: IntegrationOpsAgent | None = None,
        repair_agent: IntegrationRepairAgent | None = None,
    ) -> None:
        self.orchestrator = orchestrator or ExternalWorkflowOrchestrator()
        self.integration_agent = integration_agent or IntegrationOpsAgent()
        self.repair_agent = repair_agent or IntegrationRepairAgent()

    def run(
        self,
        request: EndToEndWorkflowRequest,
        *,
        output_dir: str | Path | None = None,
    ) -> EndToEndResultBundle:
        workflow_id = str(
            (request.resume_from or {}).get("workflow_id") or f"e2e-workflow-{uuid4().hex[:12]}"
        )
        state = (
            WorkflowStateMachine.from_snapshot(request.resume_from)
            if request.resume_from
            else WorkflowStateMachine()
        )
        if state.current_stage == "created":
            state.start(workflow_id)
        runtime_plan = self.orchestrator.build_plan(request, workflow_id=workflow_id)
        artifacts: list[WorkflowArtifact] = []
        lineage = ExternalLineageTracker()
        for stage in WORKFLOW_STAGES[1:]:
            if stage not in state.completed_stages:
                state.advance(stage)
            artifact = _stage_artifact(stage, request)
            artifacts.append(artifact)
            lineage.link_internal_artifact(
                artifact_id=artifact.artifact_id,
                metadata={"stage": stage, "mode": request.mode},
            )
        integration_result = self.integration_agent.operate(request, workflow_id=workflow_id)
        repair_plan = (
            self.repair_agent.diagnose_and_plan(integration_result)
            if integration_result.status != "succeeded"
            else None
        )
        state.complete()
        warnings = [
            *runtime_plan.guardrail_warnings,
            *integration_result.warnings,
        ]
        if repair_plan is not None:
            warnings.extend(repair_plan.warnings)
        bundle = EndToEndResultBundle(
            workflow_id=workflow_id,
            mode=request.mode,
            status="succeeded"
            if integration_result.status in {"succeeded", "validation_failed"}
            else "failed",
            objective=request.objective,
            project_id=request.project_id,
            workflow_state=state.snapshot(),
            runtime_plan=runtime_plan.model_dump(mode="json"),
            sync_plan=integration_result.sync_plan,
            integration_result=integration_result,
            repair_plan=repair_plan,
            artifacts=artifacts,
            lineage_links=[*lineage.links, *integration_result.lineage_links],
            audit_events=state.audit_events,
            warnings=warnings,
            safety_constraints=_safety_constraints(),
        )
        target_dir = output_dir or request.output_dir
        if target_dir is not None:
            bundle.write(target_dir)
        return bundle


def build_integration_operations_dashboard() -> dict[str, Any]:
    return {
        "version": __version__,
        "title": "Integration operations",
        "capabilities": [
            "IntegrationOpsAgent",
            "ExternalWorkflowOrchestrator",
            "EndToEndWorkflowRunner",
            "WorkflowStateMachine",
            "ExternalSyncPlanner",
            "IntegrationRepairAgent",
            "ExternalLineageTracker",
        ],
        "modes": ["mocked", "dry_run", "live"],
        "default_mode": "mocked",
        "write_policy": (
            "live external writes require explicit approval and integration:write permission"
        ),
        "workflow": list(WORKFLOW_STAGES),
        "safety_constraints": _safety_constraints(),
    }


def render_integration_operations_dashboard(dashboard: dict[str, Any] | None = None) -> str:
    payload = dashboard or build_integration_operations_dashboard()
    rows = "".join(
        f"<tr><td>{_html(stage)}</td><td>Governed deterministic handoff</td></tr>"
        for stage in payload["workflow"]
    )
    caps = "".join(f"<li>{_html(capability)}</li>" for capability in payload["capabilities"])
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Integration operations</title>"
        "<link rel=\"stylesheet\" href=\"/static/dashboard/integrations.css\">"
        "</head><body><header class=\"integration-header\"><h1>Integration operations</h1></header>"
        "<main class=\"integration-content\">"
        "<p class=\"notice\"><strong>End-to-end workflow</strong> execution is mocked or "
        "dry-run by default. Live external writes require explicit approval, governance "
        "permission, and deterministic validation before data can affect evidence or scoring.</p>"
        "<section><h2>V2.8 capabilities</h2><ul>"
        f"{caps}</ul></section>"
        "<section><h2>Workflow stages</h2><table><thead><tr><th>Stage</th><th>Policy</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></section>"
        "</main></body></html>\n"
    )


def _validate_external_record(
    record: dict[str, Any],
    contracts: dict[str, DataContract],
) -> dict[str, Any]:
    object_type = str(record.get("object_type") or "assay_results")
    contract = contracts.get(object_type)
    errors = validate_record_against_contract(record, contract) if contract is not None else []
    if not record.get("source_record_id") and not record.get("external_record_id"):
        errors.append("source_record_id or external_record_id is required for lineage")
    return {
        "object_type": object_type,
        "source_record_id": record.get("source_record_id") or record.get("external_record_id"),
        "valid": not errors,
        "errors": errors,
        "deterministic_validation": True,
    }


def _stage_artifact(stage: str, request: EndToEndWorkflowRequest) -> WorkflowArtifact:
    payload = {
        "stage": stage,
        "objective": request.objective,
        "project_id": request.project_id,
        "mode": request.mode,
        "scientific_truth": False,
    }
    digest = _sha256_json(payload)
    return WorkflowArtifact(
        artifact_id=f"e2e-{stage}-{digest[:12]}",
        artifact_type=f"workflow_{stage}",
        stage=stage,
        source="v2.8_end_to_end_runner",
        sha256=digest,
        metadata={
            "scientific_truth": False,
            "placeholder_only": True,
            "human_governed": True,
        },
    )


def _safety_constraints() -> dict[str, bool]:
    return {
        "no_medical_advice": True,
        "no_patient_treatment_guidance": True,
        "no_dosing_guidance": True,
        "no_lab_protocols": True,
        "no_synthesis_instructions": True,
        "no_fabricated_evidence": True,
        "no_fabricated_assay_results": True,
        "no_fabricated_citations": True,
        "no_fabricated_molecules": True,
        "no_fabricated_graph_facts": True,
        "no_fabricated_external_ids": True,
        "no_codex_scientific_truth": True,
        "external_writes_require_approval": True,
        "deterministic_validation_before_scoring": True,
    }


def _sha256_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _bundle_markdown(bundle: EndToEndResultBundle) -> str:
    return (
        f"# End-to-end result bundle\n\n"
        f"- Version: {bundle.version}\n"
        f"- Workflow: {bundle.workflow_id}\n"
        f"- Mode: {bundle.mode}\n"
        f"- Status: {bundle.status}\n"
        f"- Artifacts: {len(bundle.artifacts)}\n"
        f"- Lineage links: {len(bundle.lineage_links)}\n\n"
        "This bundle is a governed workflow execution record. It does not assert "
        "medical advice, treatment guidance, lab protocols, synthesis instructions, "
        "or agent-generated scientific truth.\n"
    )


def _html(value: Any) -> str:
    from html import escape

    return escape(str(value), quote=True)


__all__ = [
    "EndToEndResultBundle",
    "EndToEndWorkflowRequest",
    "EndToEndWorkflowRunner",
    "ExternalLineageTracker",
    "ExternalSyncPlan",
    "ExternalSyncPlanner",
    "ExternalWorkflowOrchestrator",
    "IntegrationDryRunSimulator",
    "IntegrationOperationResult",
    "IntegrationOpsAgent",
    "IntegrationRepairAgent",
    "IntegrationRepairPlan",
    "LineageLink",
    "WorkflowArtifact",
    "WorkflowStateMachine",
    "build_integration_operations_dashboard",
    "render_integration_operations_dashboard",
]
