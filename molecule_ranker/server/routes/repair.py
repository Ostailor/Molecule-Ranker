from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from molecule_ranker.agent_repair.diagnosis import FailureDiagnosisAgent
from molecule_ranker.agent_repair.executor import RepairExecutor
from molecule_ranker.agent_repair.hosted import RepairHostedStore
from molecule_ranker.agent_repair.repair_planner import RepairPlannerAgent
from molecule_ranker.agent_repair.reports import write_repair_artifacts
from molecule_ranker.agent_repair.schemas import (
    FailureDiagnosis,
    RegressionCheck,
    RepairExecution,
    RepairPlan,
)
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import UserAccount
from molecule_ranker.runtime_agents.schemas import RuntimeToolResult
from molecule_ranker.server.dependencies import current_user, platform_database

router = APIRouter(prefix="/repair", tags=["repair"])
REPAIR_PERMISSIONS = {
    "repair:read",
    "repair:diagnose",
    "repair:plan",
    "repair:execute",
    "repair:approve",
    "repair:admin",
}


class RepairDiagnoseRequest(BaseModel):
    failed_tool_result: dict[str, Any] | None = None
    failure_category: str | None = None
    failure_object_type: str = "tool_call"
    failure_object_id: str = "hosted-repair-object"
    error_summary: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairPlanRequest(BaseModel):
    diagnosis_id: str | None = None
    diagnosis: dict[str, Any] | None = None
    autonomy_level: str = "suggest_only"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairExecuteRequest(BaseModel):
    repair_plan_id: str | None = None
    repair_plan: dict[str, Any] | None = None
    mode: Literal["dry_run", "suggest_only", "execute_safe_repairs", "execute_with_approval"] = (
        "suggest_only"
    )
    approvals: list[str] = Field(default_factory=list)


class ApprovalDecisionRequest(BaseModel):
    decided_by: str | None = None
    rationale: str = ""


@router.post("/diagnose")
def diagnose_repair_failure(
    body: RepairDiagnoseRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_repair_permission(database, user, "repair:diagnose")
    if body.failed_tool_result is not None:
        result = RuntimeToolResult.model_validate(body.failed_tool_result)
        diagnosis = FailureDiagnosisAgent().diagnose(
            failed_tool_result=result,
            related_artifacts=body.evidence,
        )
    else:
        diagnosis = FailureDiagnosis(
            diagnosis_id=f"diagnosis-{uuid4().hex[:12]}",
            failure_object_type=cast(Any, body.failure_object_type),
            failure_object_id=body.failure_object_id,
            failure_category=cast(Any, body.failure_category or "unknown"),
            root_cause_summary=body.error_summary or "Hosted repair diagnosis requested.",
            evidence=body.evidence,
            recoverable=(body.failure_category or "unknown") != "unknown",
            repairability="human_input_required"
            if (body.failure_category or "unknown") == "unknown"
            else "automatic_with_limits",
            confidence=0.6 if body.failure_category else 0.2,
            warnings=[],
            created_at=datetime.now(UTC),
            metadata=body.metadata,
        )
    store = _store(request)
    store.save_diagnosis(diagnosis)
    return {"diagnosis": diagnosis.model_dump(mode="json")}


@router.post("/plan")
def plan_repair(
    body: RepairPlanRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_repair_permission(database, user, "repair:plan")
    store = _store(request)
    if body.diagnosis is not None:
        diagnosis = FailureDiagnosis.model_validate(body.diagnosis)
    elif body.diagnosis_id is not None:
        diagnosis = _diagnosis_or_404(store, body.diagnosis_id)
    else:
        raise HTTPException(status_code=400, detail="diagnosis_id or diagnosis is required.")
    plan = RepairPlannerAgent().plan_repair(
        diagnosis,
        runtime_session=body.metadata,
        user_autonomy_level=body.autonomy_level,
    )
    store.save_plan(plan)
    _create_plan_approvals(store, plan)
    return {
        "repair_plan": plan.model_dump(mode="json"),
        "approvals": store.list_approvals(),
    }


@router.post("/execute")
def execute_repair(
    body: RepairExecuteRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_repair_permission(database, user, "repair:execute")
    store = _store(request)
    if body.repair_plan is not None:
        plan = RepairPlan.model_validate(body.repair_plan)
    elif body.repair_plan_id is not None:
        plan = _plan_or_404(store, body.repair_plan_id)
    else:
        raise HTTPException(status_code=400, detail="repair_plan_id or repair_plan is required.")
    execution = RepairExecutor().execute(plan, mode=body.mode, approvals=set(body.approvals))
    store.save_execution(execution)
    regression_checks: list[RegressionCheck] = []
    for check_id in execution.regression_check_ids:
        check = RegressionCheck(
            regression_check_id=check_id,
            repair_execution_id=execution.repair_execution_id,
            check_type="workflow_smoke",
            passed=execution.status in {"succeeded", "queued"},
            findings=list(execution.warnings),
            artifacts_checked=list(execution.artifacts_created + execution.artifacts_modified),
            created_at=datetime.now(UTC),
            metadata={"hosted_api_record": True},
        )
        regression_checks.append(check)
        store.save_regression_check(check)
    if execution.approvals_requested:
        for approval_id in execution.approvals_requested:
            store.save_approval(
                _approval_payload(
                    approval_id=approval_id,
                    repair_plan_id=plan.repair_plan_id,
                    action_id=None,
                    status="pending",
                    reason="Repair execution requires approval.",
                )
            )
    try:
        diagnosis = store.get_diagnosis(plan.diagnosis_id)
    except KeyError:
        diagnosis = None
    artifact_paths = write_repair_artifacts(
        request.app.state.root_dir
        / ".molecule-ranker"
        / "repair"
        / "artifacts"
        / execution.repair_execution_id,
        self_evaluation=None,
        failure_diagnosis=diagnosis,
        repair_plan=plan,
        repair_execution=execution,
        regression_checks=regression_checks,
    )
    return {
        "execution": execution.model_dump(mode="json"),
        "artifacts": {key: str(path) for key, path in artifact_paths.items()},
    }


@router.get("/executions")
def list_repair_executions(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_repair_permission(database, user, "repair:read")
    return {
        "executions": [
            execution.model_dump(mode="json") for execution in _store(request).list_executions()
        ]
    }


@router.get("/executions/{execution_id}")
def get_repair_execution(
    execution_id: str,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_repair_permission(database, user, "repair:read")
    execution = _execution_or_404(_store(request), execution_id)
    return {"execution": execution.model_dump(mode="json")}


@router.get("/memory")
def get_repair_memory(
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_repair_permission(database, user, "repair:read")
    return {"memory": [record.model_dump(mode="json") for record in _store(request).list_memory()]}


@router.post("/approvals/{approval_id}/approve")
def approve_repair(
    approval_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_repair_permission(database, user, "repair:approve")
    approval = _approval_or_404(_store(request), approval_id)
    approval.update(
        {
            "status": "approved",
            "decided_by": body.decided_by or user.user_id,
            "rationale": body.rationale,
            "decided_at": datetime.now(UTC).isoformat(),
        }
    )
    _store(request).save_approval(approval)
    return {"approval": approval}


@router.post("/approvals/{approval_id}/reject")
def reject_repair(
    approval_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    user: Annotated[UserAccount, Depends(current_user)],
    database: Annotated[PlatformDatabase, Depends(platform_database)],
) -> dict[str, Any]:
    _require_repair_permission(database, user, "repair:approve")
    approval = _approval_or_404(_store(request), approval_id)
    approval.update(
        {
            "status": "rejected",
            "decided_by": body.decided_by or user.user_id,
            "rationale": body.rationale,
            "decided_at": datetime.now(UTC).isoformat(),
        }
    )
    _store(request).save_approval(approval)
    return {"approval": approval}


def _store(request: Request) -> RepairHostedStore:
    return RepairHostedStore(request.app.state.root_dir)


def _require_repair_permission(
    database: PlatformDatabase,
    user: UserAccount,
    permission: str,
) -> None:
    permissions = {str(item) for item in user.metadata.get("permissions", [])}
    if (
        user.is_admin
        or "repair:admin" in permissions
        or "*" in permissions
        or permission in permissions
        or has_permission(user, permission, database=database)
    ):
        return
    raise HTTPException(status_code=403, detail=f"Repair permission denied: {permission}")


def _diagnosis_or_404(store: RepairHostedStore, diagnosis_id: str) -> FailureDiagnosis:
    try:
        return store.get_diagnosis(diagnosis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Repair diagnosis not found.") from exc


def _plan_or_404(store: RepairHostedStore, repair_plan_id: str) -> RepairPlan:
    try:
        return store.get_plan(repair_plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Repair plan not found.") from exc


def _execution_or_404(store: RepairHostedStore, execution_id: str) -> RepairExecution:
    try:
        return store.get_execution(execution_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Repair execution not found.") from exc


def _approval_or_404(store: RepairHostedStore, approval_id: str) -> dict[str, Any]:
    try:
        return store.get_approval(approval_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Repair approval not found.") from exc


def _create_plan_approvals(store: RepairHostedStore, plan: RepairPlan) -> None:
    for action in plan.actions:
        if action.requires_approval or plan.requires_human_approval:
            store.save_approval(
                _approval_payload(
                    approval_id=f"repair-approval-{uuid4().hex[:12]}",
                    repair_plan_id=plan.repair_plan_id,
                    action_id=action.repair_action_id,
                    status="pending",
                    reason=action.approval_reason or "Repair action requires approval.",
                )
            )


def _approval_payload(
    *,
    approval_id: str,
    repair_plan_id: str,
    action_id: str | None,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "repair_plan_id": repair_plan_id,
        "repair_action_id": action_id,
        "status": status,
        "reason": reason,
        "created_at": datetime.now(UTC).isoformat(),
    }


__all__ = ["router"]
