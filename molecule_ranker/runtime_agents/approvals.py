from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.schemas import (
    ApprovalType,
    AutonomyLevel,
    RuntimeAgentAuditEvent,
    RuntimeApprovalRequest,
    RuntimeToolSpec,
)

SAFE_EXECUTION_SIDE_EFFECTS = {"none", "artifact_write"}
OBSERVE_ONLY_TOOL_NAMES = {
    "assess_developability_artifact",
    "detect_contradictions",
    "detect_staleness",
    "explain_failure",
    "list_projects",
    "query_graph",
    "run_readiness",
    "run_release_check",
    "show_project",
    "summarize_artifacts",
    "summarize_assay_results",
    "summarize_literature",
    "summarize_ranking",
}
APPROVAL_TAGS: dict[str, ApprovalType] = {
    "policy_override": "policy_override",
    "stage_gate": "stage_gate",
    "campaign_advance": "campaign_advance",
    "generated_molecule_export": "generated_molecule_export",
    "destructive_action": "destructive_action",
    "high_cost_job": "high_cost_job",
    "broad_codex_access": "broad_codex_access",
    "support_bundle_logs": "support_bundle_logs",
    "integration_sync": "integration_sync",
    "external_write": "external_write",
}
FULL_AUTO_HUMAN_ONLY_APPROVALS = {
    "campaign_advance",
    "destructive_action",
    "external_write",
    "generated_molecule_export",
    "integration_sync",
    "stage_gate",
}


class ApprovalPolicyError(ValueError):
    """Raised when an approval action violates runtime-agent policy."""


class AutonomyCheck(BaseModel):
    allowed: bool
    requires_approval: bool = False
    approval_type: ApprovalType | None = None
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    request: RuntimeApprovalRequest
    audit_event: RuntimeAgentAuditEvent


class RuntimeApprovalController:
    """Enforce Codex runtime autonomy and approval lifecycle policy."""

    def check_tool_allowed(
        self,
        autonomy_level: AutonomyLevel,
        tool: RuntimeToolSpec,
        *,
        actor: str = "codex",
    ) -> AutonomyCheck:
        approval_type = approval_type_for_tool(tool)
        metadata = {
            "actor": actor,
            "tool_name": tool.tool_name,
            "side_effect_level": tool.side_effect_level,
            "policy_tags": tool.policy_tags,
            **_tool_package_risk_metadata(tool),
        }

        if autonomy_level == "observe_only":
            if _is_observe_only_tool(tool):
                return AutonomyCheck(
                    allowed=True,
                    reason="observe_only permits artifact inspection and summaries.",
                    metadata=metadata,
                )
            return AutonomyCheck(
                allowed=False,
                reason="observe_only blocks tool execution with writes or operational effects.",
                metadata=metadata,
            )

        if autonomy_level == "suggest_only":
            return AutonomyCheck(
                allowed=False,
                reason="suggest_only permits planning but blocks execution.",
                metadata=metadata,
            )

        if autonomy_level == "execute_safe_tools":
            if (
                approval_type is not None
                or tool.side_effect_level not in SAFE_EXECUTION_SIDE_EFFECTS
            ):
                return AutonomyCheck(
                    allowed=False,
                    reason=(
                        "execute_safe_tools allows only no-side-effect and artifact-write "
                        "tools."
                    ),
                    metadata=metadata,
                )
            return AutonomyCheck(
                allowed=True,
                reason="execute_safe_tools permits this safe registered tool.",
                metadata=metadata,
            )

        if autonomy_level == "execute_with_approval":
            if approval_type is not None:
                return AutonomyCheck(
                    allowed=False,
                    requires_approval=True,
                    approval_type=approval_type,
                    reason=f"{approval_type} requires explicit approval.",
                    metadata=metadata,
                )
            if tool.side_effect_level in SAFE_EXECUTION_SIDE_EFFECTS:
                return AutonomyCheck(
                    allowed=True,
                    reason="execute_with_approval permits this safe registered tool.",
                    metadata=metadata,
                )
            return AutonomyCheck(
                allowed=False,
                requires_approval=True,
                approval_type="execute_plan",
                reason="Risky tool class requires explicit approval.",
                metadata=metadata,
            )

        if autonomy_level == "full_auto_restricted":
            if approval_type in FULL_AUTO_HUMAN_ONLY_APPROVALS:
                return AutonomyCheck(
                    allowed=False,
                    requires_approval=True,
                    approval_type=approval_type,
                    reason=f"{approval_type} requires human approval even in full_auto_restricted.",
                    metadata=metadata,
                )
            if approval_type is not None:
                return AutonomyCheck(
                    allowed=False,
                    requires_approval=True,
                    approval_type=approval_type,
                    reason=f"{approval_type} requires explicit approval under project policy.",
                    metadata=metadata,
                )
            return AutonomyCheck(
                allowed=True,
                reason="full_auto_restricted permits this approved tool class.",
                metadata=metadata,
            )

        return AutonomyCheck(
            allowed=False,
            reason=f"Unknown autonomy level: {autonomy_level}",
            metadata=metadata,
        )

    def create_approval_request(
        self,
        *,
        session_id: str,
        plan_id: str,
        step_id: str | None,
        requested_by: str,
        approval_type: ApprovalType,
        reason: str,
        risk_summary: str,
        requested_at: datetime | None = None,
        ttl_minutes: int = 60,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeApprovalRequest:
        requested = requested_at or datetime.now(UTC)
        expires_at = requested + timedelta(minutes=ttl_minutes)
        request_metadata = dict(metadata or {})
        request_metadata["expires_at"] = expires_at.isoformat()
        request_metadata["ttl_minutes"] = ttl_minutes
        return RuntimeApprovalRequest(
            approval_id=f"runtime-approval-{uuid4().hex[:12]}",
            session_id=session_id,
            plan_id=plan_id,
            step_id=step_id,
            requested_by=requested_by,
            approval_type=approval_type,
            reason=reason,
            risk_summary=risk_summary,
            requested_at=requested,
            status="pending",
            metadata=request_metadata,
        )

    def decide(
        self,
        request: RuntimeApprovalRequest,
        *,
        decided_by: str,
        approved: bool,
        rationale: str,
        decided_at: datetime | None = None,
    ) -> ApprovalDecision:
        if request.status != "pending":
            raise ApprovalPolicyError(
                f"Only pending approval requests can be decided; got {request.status}."
            )
        if approved and _is_codex_actor(decided_by):
            raise ApprovalPolicyError("Codex cannot approve its own approval request.")

        before = request.model_dump(mode="json")
        decided = decided_at or datetime.now(UTC)
        status = "approved" if approved else "rejected"
        updated = request.model_copy(
            update={
                "status": status,
                "decided_by": decided_by,
                "decided_at": decided,
                "decision_rationale": rationale,
            }
        )
        audit_event = _audit_event(
            request=updated,
            event_type=f"runtime_approval_{status}",
            actor=decided_by,
            timestamp=decided,
            summary=f"Approval request {updated.approval_id} {status}.",
            before=before,
            after=updated.model_dump(mode="json"),
        )
        return ApprovalDecision(request=updated, audit_event=audit_event)

    def expire_if_needed(
        self,
        request: RuntimeApprovalRequest,
        *,
        now: datetime | None = None,
    ) -> ApprovalDecision:
        current_time = now or datetime.now(UTC)
        if request.status != "pending":
            return ApprovalDecision(
                request=request,
                audit_event=_audit_event(
                    request=request,
                    event_type="runtime_approval_expiration_skipped",
                    actor=None,
                    timestamp=current_time,
                    summary=(
                        f"Approval request {request.approval_id} was already "
                        f"{request.status}."
                    ),
                    before=None,
                    after=request.model_dump(mode="json"),
                ),
            )
        expires_at = _approval_expiry(request)
        if expires_at is None or current_time < expires_at:
            return ApprovalDecision(
                request=request,
                audit_event=_audit_event(
                    request=request,
                    event_type="runtime_approval_still_pending",
                    actor=None,
                    timestamp=current_time,
                    summary=f"Approval request {request.approval_id} is still pending.",
                    before=request.model_dump(mode="json"),
                    after=request.model_dump(mode="json"),
                ),
            )

        before = request.model_dump(mode="json")
        expired = request.model_copy(
            update={
                "status": "expired",
                "decided_at": current_time,
                "decision_rationale": "Approval request expired before a decision.",
            }
        )
        audit_event = _audit_event(
            request=expired,
            event_type="runtime_approval_expired",
            actor=None,
            timestamp=current_time,
            summary=f"Approval request {expired.approval_id} expired.",
            before=before,
            after=expired.model_dump(mode="json"),
        )
        return ApprovalDecision(request=expired, audit_event=audit_event)


def approval_type_for_tool(tool: RuntimeToolSpec) -> ApprovalType | None:
    tags = set(tool.policy_tags)
    for tag, approval_type in APPROVAL_TAGS.items():
        if tag in tags:
            if approval_type == "external_write" and tool.category == "integration":
                return "integration_sync"
            return approval_type
    if tool.tool_name == "generate_support_bundle":
        return "support_bundle_logs"
    if tool.side_effect_level == "external_write":
        if tool.category == "integration":
            return "integration_sync"
        return "external_write"
    if tool.requires_approval_by_default:
        return "execute_plan"
    return None


def _tool_package_risk_metadata(tool: RuntimeToolSpec) -> dict[str, Any]:
    package = tool.metadata.get("tool_package")
    tool_version = tool.metadata.get("tool_version")
    metadata: dict[str, Any] = {}
    if isinstance(package, dict):
        metadata["package_id"] = package.get("package_id")
        metadata["package_version"] = package.get("version")
        metadata["package_status"] = package.get("status")
        metadata["security_scan_status"] = package.get("security_scan_status")
        metadata["approval_status"] = package.get("approval_status")
        metadata["tool_package_risk"] = package.get("risk_level") or package.get(
            "security_risk_level"
        )
    if isinstance(tool_version, dict):
        metadata["tool_version"] = tool_version.get("version")
    return metadata


def _is_observe_only_tool(tool: RuntimeToolSpec) -> bool:
    if tool.tool_name in OBSERVE_ONLY_TOOL_NAMES:
        return True
    return (
        tool.side_effect_level == "none"
        and tool.tool_name.startswith(
            ("assess_", "detect_", "list_", "query_", "show_", "summarize_")
        )
    )


def _approval_expiry(request: RuntimeApprovalRequest) -> datetime | None:
    raw_expires_at = request.metadata.get("expires_at")
    if not isinstance(raw_expires_at, str):
        return None
    expires_at = datetime.fromisoformat(raw_expires_at)
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ApprovalPolicyError("Approval expiration timestamp must be timezone-aware.")
    return expires_at


def _is_codex_actor(actor: str) -> bool:
    return actor.strip().lower() in {"codex", "codex_cli", "codex-runtime-agent"}


def _audit_event(
    *,
    request: RuntimeApprovalRequest,
    event_type: str,
    actor: str | None,
    timestamp: datetime,
    summary: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> RuntimeAgentAuditEvent:
    return RuntimeAgentAuditEvent(
        event_id=f"runtime-audit-{uuid4().hex[:12]}",
        session_id=request.session_id,
        event_type=event_type,
        actor=actor,
        timestamp=timestamp,
        summary=summary,
        object_type="RuntimeApprovalRequest",
        object_id=request.approval_id,
        before=before,
        after=after,
        metadata={
            "approval_type": request.approval_type,
            "plan_id": request.plan_id,
            "step_id": request.step_id,
        },
    )


__all__ = [
    "ApprovalDecision",
    "ApprovalPolicyError",
    "AutonomyCheck",
    "RuntimeApprovalController",
    "approval_type_for_tool",
]
