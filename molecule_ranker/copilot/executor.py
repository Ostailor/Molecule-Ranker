from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.copilot.policy import CoPilotPolicyEngine, PolicyDecision
from molecule_ranker.copilot.schemas import (
    ActionResultStatus,
    CoPilotAction,
    CoPilotActionResult,
)

AuditWriter = Callable[[dict[str, Any]], None]

_SUPPORTED_ACTIONS = {
    "summarize_status",
    "create_replan_draft",
    "run_campaign_replan",
    "create_review_request",
    "create_followup_request",
    "run_graph_refresh",
    "run_contradiction_scan",
    "run_portfolio_reoptimization",
    "run_active_learning_update",
    "run_evaluation_update",
    "run_repair_workflow",
    "notify_user",
    "pause_campaign",
    "create_support_bundle",
}

_FORBIDDEN_PAYLOAD_TERMS = (
    "protocol",
    "synthesis",
    "synthesize",
    "dosing",
    "dose",
    "patient guidance",
    "wet-lab",
    "wet lab",
)


class CoPilotExecutor:
    def __init__(
        self,
        *,
        runtime_tool_registry: Any,
        policy_engine: CoPilotPolicyEngine | None = None,
        approval_requester: Any | None = None,
        artifact_validator: Any | None = None,
        guardrails: Any | None = None,
        repair_workflow: Any | None = None,
        audit_writer: AuditWriter | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime_tool_registry = runtime_tool_registry
        self.policy_engine = policy_engine or CoPilotPolicyEngine()
        self.approval_requester = approval_requester
        self.artifact_validator = artifact_validator
        self.guardrails = guardrails
        self.repair_workflow = repair_workflow
        self.audit_writer = audit_writer
        self._now = now or (lambda: datetime.now(UTC))

    def execute(
        self,
        action: CoPilotAction,
        *,
        autonomy_level: str = "execute_safe_actions",
        user_policy: dict[str, object] | None = None,
        project_policy: dict[str, object] | None = None,
        org_policy: dict[str, object] | None = None,
        campaign_policy: dict[str, object] | None = None,
    ) -> CoPilotActionResult:
        requested_action_type = str(
            action.metadata.get("requested_action_type", action.action_type)
        )
        if requested_action_type == "approve_stage_gate":
            result = self._result(
                action,
                status="blocked_by_policy",
                summary="Co-pilot cannot approve stage gate decisions.",
            )
            self._audit(action, "blocked_by_policy", result=result)
            return result
        if self._payload_contains_forbidden_details(action):
            result = self._result(
                action,
                status="blocked_by_policy",
                summary="Action payload contains protocol/synthesis/dosing details.",
            )
            self._audit(action, "blocked_by_policy", result=result)
            return result
        policy_decision = self.policy_engine.evaluate_action(
            action_type=requested_action_type,
            autonomy_level=autonomy_level,
            metadata={**action.metadata, **action.tool_args},
            user_policy=user_policy,
            project_policy=project_policy,
            org_policy=org_policy,
            campaign_policy=campaign_policy,
        )
        if policy_decision.blocked:
            result = self._result(
                action,
                status="blocked_by_policy",
                summary=policy_decision.reason,
            )
            self._audit(action, "blocked_by_policy", result=result)
            return result
        if policy_decision.requires_approval or action.requires_approval:
            result = self._approval_required(action, policy_decision)
            self._audit(action, "approval_required", result=result)
            return result
        guardrail_failures = self._run_guardrails(action)
        if guardrail_failures:
            result = self._guardrail_failed(action, guardrail_failures)
            self._audit(action, "guardrail_failed", result=result)
            return result
        self._audit(action, "guardrails_passed")
        if action.action_type not in _SUPPORTED_ACTIONS:
            result = self._result(
                action,
                status="blocked_by_policy",
                summary=f"Unsupported co-pilot action: {action.action_type}.",
            )
            self._audit(action, "blocked_by_policy", result=result)
            return result
        tool_result = self.runtime_tool_registry.execute(
            action.tool_name or action.action_type,
            action.tool_args,
        )
        artifact_warnings = self._validate_artifacts(
            list(map(str, tool_result.get("artifact_ids", []))),
            action=action,
        )
        result = self._result(
            action,
            status="succeeded",
            summary=str(tool_result.get("summary", "Action executed.")),
            artifact_ids=list(map(str, tool_result.get("artifact_ids", []))),
            job_ids=list(map(str, tool_result.get("job_ids", []))),
            warnings=artifact_warnings,
        )
        self._audit(action, "succeeded", result=result)
        return result

    def _approval_required(
        self,
        action: CoPilotAction,
        policy_decision: PolicyDecision,
    ) -> CoPilotActionResult:
        approval_request_id = None
        if self.approval_requester is not None:
            approval_request_id = self.approval_requester.request_approval(
                action,
                policy_decision.reason,
            )
        return self._result(
            action,
            status="approval_required",
            summary=policy_decision.reason,
            metadata={"approval_request_id": approval_request_id},
        )

    def _run_guardrails(self, action: CoPilotAction) -> list[str]:
        if self.guardrails is None:
            return []
        failures = self.guardrails.check(action)
        return [str(failure) for failure in failures]

    def _guardrail_failed(
        self,
        action: CoPilotAction,
        failures: list[str],
    ) -> CoPilotActionResult:
        repair_artifacts: list[str] = []
        repair_summary = ""
        failure_summary = "; ".join(failures)
        if action.metadata.get("safe_repair") and self.repair_workflow is not None:
            repair_result = self.repair_workflow.repair(action, failure_summary)
            repair_artifacts = list(map(str, repair_result.get("artifact_ids", [])))
            repair_summary = str(repair_result.get("summary", ""))
        return self._result(
            action,
            status="guardrail_failed",
            summary=failure_summary,
            artifact_ids=repair_artifacts,
            warnings=[repair_summary] if repair_summary else [],
        )

    def _validate_artifacts(
        self,
        artifact_ids: list[str],
        *,
        action: CoPilotAction,
    ) -> list[str]:
        if self.artifact_validator is None or not artifact_ids:
            return []
        warnings = self.artifact_validator.validate(artifact_ids, action=action)
        return [str(warning) for warning in warnings]

    def _payload_contains_forbidden_details(self, action: CoPilotAction) -> bool:
        payload = json.dumps(
            {
                "tool_args": action.tool_args,
                "metadata": action.metadata,
            },
            default=str,
            sort_keys=True,
        ).lower()
        return any(term in payload for term in _FORBIDDEN_PAYLOAD_TERMS)

    def _result(
        self,
        action: CoPilotAction,
        *,
        status: ActionResultStatus,
        summary: str,
        artifact_ids: list[str] | None = None,
        job_ids: list[str] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CoPilotActionResult:
        return CoPilotActionResult(
            result_id=f"result-{action.copilot_action_id}",
            copilot_action_id=action.copilot_action_id,
            status=status,
            artifact_ids=artifact_ids or [],
            job_ids=job_ids or [],
            summary=summary,
            warnings=warnings or [],
            created_at=self._now(),
            metadata=metadata or {},
        )

    def _audit(
        self,
        action: CoPilotAction,
        transition: str,
        *,
        result: CoPilotActionResult | None = None,
    ) -> None:
        if self.audit_writer is None:
            return
        self.audit_writer(
            {
                "action_id": action.copilot_action_id,
                "campaign_id": action.campaign_id,
                "transition": transition,
                "result_status": result.status if result is not None else None,
                "created_at": self._now(),
            }
        )


CampaignReplanExecutor = CoPilotExecutor
