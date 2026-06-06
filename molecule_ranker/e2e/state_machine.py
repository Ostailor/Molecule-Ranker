from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.e2e.schemas import (
    EndToEndWorkflow,
    EndToEndWorkflowStep,
    WorkflowLineageRecord,
)

WorkflowClock = Callable[[], datetime]

WORKFLOW_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"running"},
    "running": {
        "awaiting_approval",
        "succeeded",
        "partially_succeeded",
        "failed",
        "cancelled",
    },
    "awaiting_approval": {"running"},
    "failed": {"running"},
    "partially_succeeded": {"running"},
    "succeeded": set(),
    "cancelled": set(),
}
STEP_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running"},
    "running": {"succeeded", "failed", "skipped", "awaiting_approval"},
    "awaiting_approval": {"running"},
    "failed": {"running", "skipped"},
    "succeeded": set(),
    "skipped": set(),
}
TERMINAL_WORKFLOW_STATUSES = {"succeeded", "cancelled"}


class WorkflowStateTransitionError(RuntimeError):
    """Raised when an end-to-end workflow transition is not allowed."""


class WorkflowStateMachine:
    def __init__(
        self,
        *,
        workflow: EndToEndWorkflow,
        steps: list[EndToEndWorkflowStep],
        lineage_records: list[WorkflowLineageRecord] | None = None,
        audit_events: list[dict[str, Any]] | None = None,
        now: WorkflowClock | None = None,
    ) -> None:
        self.workflow = workflow
        self.steps = sorted(steps, key=lambda step: step.step_index)
        self.lineage_records = list(lineage_records or [])
        self.audit_events = list(audit_events or [])
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        now: WorkflowClock | None = None,
    ) -> WorkflowStateMachine:
        return cls(
            workflow=EndToEndWorkflow.model_validate(snapshot["workflow"]),
            steps=[
                EndToEndWorkflowStep.model_validate(step)
                for step in snapshot.get("steps", [])
            ],
            lineage_records=[
                WorkflowLineageRecord.model_validate(record)
                for record in snapshot.get("lineage_records", [])
            ],
            audit_events=list(snapshot.get("audit_events", [])),
            now=now,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow.model_dump(mode="json"),
            "steps": [step.model_dump(mode="json") for step in self.steps],
            "lineage_records": [
                record.model_dump(mode="json") for record in self.lineage_records
            ],
            "audit_events": list(self.audit_events),
        }

    def step_by_id(self, step_id: str) -> EndToEndWorkflowStep:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(f"Unknown workflow step: {step_id}")

    def start_workflow(self) -> EndToEndWorkflow:
        self._transition_workflow("running", reason="Workflow started.")
        self.workflow = self.workflow.model_copy(update={"started_at": self._now()})
        self._audit("workflow_started", "workflow", self.workflow.workflow_id)
        return self.workflow

    def complete_workflow(self) -> EndToEndWorkflow:
        status = "succeeded"
        if any(step.status == "failed" and not step.required for step in self.steps):
            status = "partially_succeeded"
        self._transition_workflow(status, reason="Workflow completed.")
        self.workflow = self.workflow.model_copy(update={"completed_at": self._now()})
        self._audit(f"workflow_{status}", "workflow", self.workflow.workflow_id)
        return self.workflow

    def fail_workflow(self, *, reason: str) -> EndToEndWorkflow:
        self._transition_workflow("failed", reason=reason)
        self.workflow = self.workflow.model_copy(update={"completed_at": self._now()})
        self._audit("workflow_failed", "workflow", self.workflow.workflow_id, reason=reason)
        return self.workflow

    def cancel_workflow(self, *, reason: str) -> EndToEndWorkflow:
        self._transition_workflow("cancelled", reason=reason)
        self.workflow = self.workflow.model_copy(update={"completed_at": self._now()})
        self._audit("workflow_cancelled", "workflow", self.workflow.workflow_id, reason=reason)
        return self.workflow

    def resume_workflow(self, *, reason: str) -> EndToEndWorkflow:
        if self.workflow.status == "cancelled":
            raise WorkflowStateTransitionError(
                "Workflow is cancelled and must be cloned or restarted explicitly."
            )
        if self.workflow.status not in {"failed", "partially_succeeded", "awaiting_approval"}:
            raise WorkflowStateTransitionError(
                f"Workflow status {self.workflow.status} cannot be resumed."
            )
        self._transition_workflow("running", reason=reason, resume=True)
        self.workflow = self.workflow.model_copy(update={"completed_at": None})
        self._audit("workflow_resumed", "workflow", self.workflow.workflow_id, reason=reason)
        return self.workflow

    def start_step(self, step_id: str) -> EndToEndWorkflowStep:
        self._require_workflow_can_execute()
        step = self.step_by_id(step_id)
        if step.status == "awaiting_approval":
            raise WorkflowStateTransitionError(f"Step {step_id} is awaiting approval.")
        if self._requires_approval(step):
            if step.status == "pending":
                step = self._transition_step(
                    step,
                    "running",
                    reason="Step reached approval gate.",
                )
            updated = self._transition_step(
                step,
                "awaiting_approval",
                reason="Step requires approval before execution.",
            )
            self._transition_workflow(
                "awaiting_approval",
                reason=f"Step {step.step_id} requires approval.",
            )
            self._audit("step_awaiting_approval", "step", step.step_id)
            return updated
        updated = self._transition_step(step, "running", reason="Step started.")
        self._audit("step_started", "step", step.step_id)
        return updated

    def approve_step(
        self,
        step_id: str,
        *,
        approved_by: str,
        approval_id: str,
    ) -> EndToEndWorkflowStep:
        step = self.step_by_id(step_id)
        if step.status != "awaiting_approval":
            raise WorkflowStateTransitionError(f"Step {step_id} is not awaiting approval.")
        updated_metadata = {
            **step.metadata,
            "approval_id": approval_id,
            "approved_by": approved_by,
        }
        step = step.model_copy(update={"metadata": updated_metadata})
        self._replace_step(step)
        updated = self._transition_step(
            step,
            "running",
            reason=f"Approval {approval_id} granted by {approved_by}.",
        )
        if self.workflow.status == "awaiting_approval":
            self._transition_workflow(
                "running",
                reason=f"Approval {approval_id} granted.",
            )
        self._record_lineage(
            source_object_type="approval",
            source_object_id=approval_id,
            target_object_type="step",
            target_object_id=step.step_id,
            relation_type="approved_for",
            artifact_ids=step.output_artifact_ids,
            external_record_refs=[],
            metadata={"approved_by": approved_by},
        )
        self._audit("step_approved", "step", step.step_id, reason=approval_id)
        return updated

    def complete_step(self, step_id: str) -> EndToEndWorkflowStep:
        step = self.step_by_id(step_id)
        updated = self._transition_step(step, "succeeded", reason="Step completed.")
        for artifact_id in updated.output_artifact_ids:
            self._record_lineage(
                source_object_type="step",
                source_object_id=updated.step_id,
                target_object_type="artifact",
                target_object_id=artifact_id,
                relation_type="produced",
                artifact_ids=[artifact_id],
                external_record_refs=[],
                metadata={"step_type": updated.step_type},
            )
        self._audit("step_succeeded", "step", updated.step_id)
        return updated

    def fail_step(self, step_id: str, *, reason: str) -> EndToEndWorkflowStep:
        step = self.step_by_id(step_id)
        updated = self._transition_step(step, "failed", reason=reason)
        if step.required:
            self._skip_downstream_required_steps(step, reason=reason)
            self._transition_workflow("failed", reason=reason)
            self.workflow = self.workflow.model_copy(update={"completed_at": self._now()})
        else:
            warning = f"Optional step failed: {reason}"
            updated = updated.model_copy(update={"warnings": [*updated.warnings, warning]})
            self._replace_step(updated)
        self._audit("step_failed", "step", updated.step_id, reason=reason)
        return updated

    def skip_step(
        self,
        step_id: str,
        *,
        reason: str,
        policy_allows: bool = False,
    ) -> EndToEndWorkflowStep:
        step = self.step_by_id(step_id)
        if step.required and not policy_allows:
            raise WorkflowStateTransitionError("Required step cannot be skipped without policy.")
        if step.status == "failed" and step.required:
            raise WorkflowStateTransitionError("Failed required step cannot be skipped.")
        warnings = [*step.warnings, reason] if reason else list(step.warnings)
        step = step.model_copy(update={"warnings": warnings})
        self._replace_step(step)
        updated = self._transition_step(step, "skipped", reason=reason)
        self._audit("step_skipped", "step", updated.step_id, reason=reason)
        return updated

    def retry_step(self, step_id: str, *, reason: str) -> EndToEndWorkflowStep:
        step = self.step_by_id(step_id)
        if step.status != "failed":
            raise WorkflowStateTransitionError(f"Step {step_id} is not failed.")
        updated = self._transition_step(step, "running", reason=reason)
        self._record_lineage(
            source_object_type="repair",
            source_object_id=f"retry-{step_id}",
            target_object_type="step",
            target_object_id=step_id,
            relation_type="repaired_by",
            artifact_ids=step.output_artifact_ids,
            external_record_refs=[],
            metadata={"reason": reason},
        )
        self._audit("step_retried", "step", step_id, reason=reason)
        return updated

    def _transition_workflow(
        self,
        status: str,
        *,
        reason: str,
        resume: bool = False,
    ) -> None:
        current = self.workflow.status
        if current == "cancelled" and status != "cancelled":
            raise WorkflowStateTransitionError(
                "Workflow is cancelled and must be cloned or restarted explicitly."
            )
        if status not in WORKFLOW_TRANSITIONS.get(current, set()):
            allowed_resume = (
                resume and current in {"failed", "partially_succeeded"} and status == "running"
            )
            if not allowed_resume:
                raise WorkflowStateTransitionError(
                    f"Workflow transition {current} -> {status} is not allowed: {reason}"
                )
        self.workflow = self.workflow.model_copy(update={"status": status})

    def _transition_step(
        self,
        step: EndToEndWorkflowStep,
        status: str,
        *,
        reason: str,
    ) -> EndToEndWorkflowStep:
        current = step.status
        if status not in STEP_TRANSITIONS.get(current, set()):
            raise WorkflowStateTransitionError(
                f"Step transition {current} -> {status} is not allowed: {reason}"
            )
        now = self._now()
        updates: dict[str, Any] = {"status": status}
        if status == "running":
            updates["started_at"] = step.started_at or now
            updates["completed_at"] = None
        elif status in {"succeeded", "failed", "skipped"}:
            updates["completed_at"] = now
        updated = step.model_copy(update=updates)
        self._replace_step(updated)
        return updated

    def _replace_step(self, updated: EndToEndWorkflowStep) -> None:
        self.steps = [updated if step.step_id == updated.step_id else step for step in self.steps]

    def _require_workflow_can_execute(self) -> None:
        status = self.workflow.status
        if status == "planned":
            raise WorkflowStateTransitionError("Workflow must be running before steps execute.")
        if status == "awaiting_approval":
            raise WorkflowStateTransitionError("Workflow is awaiting approval.")
        if status == "failed":
            raise WorkflowStateTransitionError("Workflow is failed and must be resumed.")
        if status == "cancelled":
            raise WorkflowStateTransitionError("Workflow is cancelled.")
        if status == "succeeded":
            raise WorkflowStateTransitionError("Workflow is already succeeded.")

    def _requires_approval(self, step: EndToEndWorkflowStep) -> bool:
        if step.step_type == "approval_gate":
            return True
        if step.metadata.get("approval_id"):
            return False
        external_write = step.metadata.get("external_write") is True
        external_write = external_write or step.metadata.get("requires_external_write") is True
        return bool(external_write)

    def _skip_downstream_required_steps(
        self,
        failed_step: EndToEndWorkflowStep,
        *,
        reason: str,
    ) -> None:
        for step in self.steps:
            if step.step_index <= failed_step.step_index or not step.required:
                continue
            if step.status != "pending":
                continue
            warning = f"Step blocked by required step failure: {failed_step.step_id}"
            updated = step.model_copy(
                update={
                    "status": "skipped",
                    "completed_at": self._now(),
                    "warnings": [*step.warnings, warning],
                }
            )
            self._replace_step(updated)
            self._record_lineage(
                source_object_type="step",
                source_object_id=failed_step.step_id,
                target_object_type="step",
                target_object_id=step.step_id,
                relation_type="blocked_by",
                artifact_ids=[],
                external_record_refs=[],
                metadata={"reason": reason},
            )

    def _record_lineage(
        self,
        *,
        source_object_type: str,
        source_object_id: str,
        target_object_type: str,
        target_object_id: str,
        relation_type: str,
        artifact_ids: list[str],
        external_record_refs: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        self.lineage_records.append(
            WorkflowLineageRecord(
                lineage_id=f"lineage-{uuid4().hex[:12]}",
                workflow_id=self.workflow.workflow_id,
                source_object_type=source_object_type,
                source_object_id=source_object_id,
                target_object_type=target_object_type,
                target_object_id=target_object_id,
                relation_type=relation_type,  # type: ignore[arg-type]
                artifact_ids=artifact_ids,
                external_record_refs=external_record_refs,
                created_at=self._now(),
                metadata=metadata,
            )
        )

    def _audit(
        self,
        event_type: str,
        object_type: str,
        object_id: str,
        *,
        reason: str | None = None,
    ) -> None:
        self.audit_events.append(
            {
                "event_id": f"e2e-audit-{uuid4().hex[:12]}",
                "workflow_id": self.workflow.workflow_id,
                "event_type": event_type,
                "timestamp": self._now().isoformat(),
                "object_type": object_type,
                "object_id": object_id,
                "reason": reason,
            }
        )


__all__ = [
    "EndToEndWorkflow",
    "EndToEndWorkflowStep",
    "WorkflowStateMachine",
    "WorkflowStateTransitionError",
]
