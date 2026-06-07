from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from molecule_ranker.product.schemas import PilotUser, UsageLimit

UsageAction = Literal[
    "create_project",
    "run_discovery",
    "generate_hypotheses",
    "export_result",
    "codex_task",
    "storage_write",
]

HIGH_INTERNAL_LIMIT = 1_000_000
ADMIN_BYPASS_PLANS = {"admin", "free_internal"}

DEFAULT_USAGE_LIMITS: tuple[UsageLimit, ...] = (
    UsageLimit(
        plan="free_internal",
        max_projects=HIGH_INTERNAL_LIMIT,
        max_runs_per_month=HIGH_INTERNAL_LIMIT,
        max_codex_tasks_per_month=HIGH_INTERNAL_LIMIT,
        max_generated_hypotheses_per_run=HIGH_INTERNAL_LIMIT,
        max_result_bundle_exports_per_month=HIGH_INTERNAL_LIMIT,
        max_storage_mb=HIGH_INTERNAL_LIMIT,
        metadata={"description": "admin/development plan with high internal limits"},
    ),
    UsageLimit(
        plan="pilot",
        max_projects=10,
        max_runs_per_month=50,
        max_codex_tasks_per_month=500,
        max_generated_hypotheses_per_run=100,
        max_result_bundle_exports_per_month=100,
        max_storage_mb=1000,
        metadata={
            "intended_future_monthly_price_usd": 100,
            "pricing_copy_finalized": False,
            "stripe_integrated": False,
        },
    ),
    UsageLimit(
        plan="admin",
        max_projects=HIGH_INTERNAL_LIMIT,
        max_runs_per_month=HIGH_INTERNAL_LIMIT,
        max_codex_tasks_per_month=HIGH_INTERNAL_LIMIT,
        max_generated_hypotheses_per_run=HIGH_INTERNAL_LIMIT,
        max_result_bundle_exports_per_month=HIGH_INTERNAL_LIMIT,
        max_storage_mb=HIGH_INTERNAL_LIMIT,
        metadata={"description": "internal admin plan"},
    ),
)

ACTION_LIMIT_FIELDS: dict[UsageAction, str] = {
    "create_project": "max_projects",
    "run_discovery": "max_runs_per_month",
    "generate_hypotheses": "max_generated_hypotheses_per_run",
    "export_result": "max_result_bundle_exports_per_month",
    "codex_task": "max_codex_tasks_per_month",
    "storage_write": "max_storage_mb",
}


@dataclass(frozen=True)
class UsageCheck:
    allowed: bool
    plan: str
    action: UsageAction
    used: int
    limit: int
    remaining: int | None
    error: str | None = None


class UsageLimitExceeded(RuntimeError):
    def __init__(self, check: UsageCheck) -> None:
        message = check.error or (
            f"usage limit exceeded for action {check.action}: used {check.used} of "
            f"{check.limit} on plan {check.plan}"
        )
        super().__init__(message)
        self.check = check


def default_usage_limits() -> list[UsageLimit]:
    return [limit.model_copy(deep=True) for limit in DEFAULT_USAGE_LIMITS]


def get_plan_limits(plan: str) -> UsageLimit:
    for limit in DEFAULT_USAGE_LIMITS:
        if limit.plan == plan:
            return limit.model_copy(deep=True)
    raise KeyError(f"unknown pilot plan: {plan}")


def usage_limit_for_plan(plan: str) -> UsageLimit:
    return get_plan_limits(plan)


def check_usage_allowed(user: PilotUser, action: UsageAction) -> UsageCheck:
    plan = user.plan
    limits = get_plan_limits(plan)
    limit = int(getattr(limits, ACTION_LIMIT_FIELDS[action]))
    used = _current_usage(user, action)

    if plan in ADMIN_BYPASS_PLANS:
        return UsageCheck(
            allowed=True,
            plan=plan,
            action=action,
            used=used,
            limit=limit,
            remaining=None,
        )

    if used >= limit:
        return UsageCheck(
            allowed=False,
            plan=plan,
            action=action,
            used=used,
            limit=limit,
            remaining=0,
            error=(
                f"usage limit reached for {action}: plan {plan} allows {limit}, "
                f"current usage is {used}"
            ),
        )

    return UsageCheck(
        allowed=True,
        plan=plan,
        action=action,
        used=used,
        limit=limit,
        remaining=limit - used,
    )


def record_usage_event(
    user: PilotUser,
    action: UsageAction,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    check = check_usage_allowed(user, action)
    if not check.allowed:
        raise UsageLimitExceeded(check)

    usage = _usage_state(user)
    amount = _usage_amount(action, metadata or {})
    usage["counts"][action] = int(usage["counts"].get(action, 0)) + amount
    event = {
        "action": action,
        "amount": amount,
        "plan": user.plan,
        "metadata": dict(metadata or {}),
    }
    usage["events"].append(event)
    return event


def usage_summary(user: PilotUser) -> dict[str, Any]:
    limits = get_plan_limits(user.plan)
    usage = _usage_state(user)
    counts = {action: int(usage["counts"].get(action, 0)) for action in ACTION_LIMIT_FIELDS}
    limit_values = {
        action: int(getattr(limits, field_name))
        for action, field_name in ACTION_LIMIT_FIELDS.items()
    }
    remaining = {
        action: None
        if user.plan in ADMIN_BYPASS_PLANS
        else max(limit_values[action] - counts[action], 0)
        for action in ACTION_LIMIT_FIELDS
    }
    return {
        "user_id": user.user_id,
        "plan": user.plan,
        "admin_bypass": user.plan in ADMIN_BYPASS_PLANS,
        "usage": counts,
        "limits": limit_values,
        "remaining": remaining,
        "events_count": len(usage["events"]),
    }


def _usage_state(user: PilotUser) -> dict[str, Any]:
    state = user.metadata.setdefault("usage", {})
    state.setdefault("counts", {})
    state.setdefault("events", [])
    return state


def _current_usage(user: PilotUser, action: UsageAction) -> int:
    return int(_usage_state(user)["counts"].get(action, 0))


def _usage_amount(action: UsageAction, metadata: dict[str, Any]) -> int:
    if action == "storage_write":
        return int(metadata.get("storage_mb", metadata.get("amount", 1)))
    return int(metadata.get("amount", 1))


__all__ = [
    "ACTION_LIMIT_FIELDS",
    "ADMIN_BYPASS_PLANS",
    "DEFAULT_USAGE_LIMITS",
    "HIGH_INTERNAL_LIMIT",
    "UsageAction",
    "UsageCheck",
    "UsageLimitExceeded",
    "check_usage_allowed",
    "default_usage_limits",
    "get_plan_limits",
    "record_usage_event",
    "usage_limit_for_plan",
    "usage_summary",
]
