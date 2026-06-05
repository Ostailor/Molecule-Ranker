from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.schemas import (
    AgentAutonomyBudget,
    AgentAutonomyBudgetPeriod,
    AgentGovernanceSchema,
)

BudgetDecisionStatus = Literal["allowed", "blocked", "approval_required"]
BudgetReservationStatus = Literal["reserved", "committed", "released"]
BudgetDimension = Literal[
    "tool_calls",
    "codex_tasks",
    "runtime_minutes",
    "artifact_writes",
    "db_writes",
    "external_reads",
    "external_writes",
    "generation_jobs",
    "docking_jobs",
    "model_training_jobs",
    "campaign_replans",
    "cost_units",
]

DEFAULT_ZERO_LIMIT_DIMENSIONS = {"external_writes"}
DESTRUCTIVE_ACTION_USAGE_KEY = "destructive_actions"
DESTRUCTIVE_ACTION_LIMIT_KEY = "max_destructive_actions"
HIGH_COST_APPROVAL = "high_cost_job"
BUDGET_OVERRIDE_APPROVAL = "budget_override"

DIMENSION_LIMIT_FIELDS: dict[BudgetDimension, str] = {
    "tool_calls": "max_tool_calls",
    "codex_tasks": "max_codex_tasks",
    "runtime_minutes": "max_runtime_minutes",
    "artifact_writes": "max_artifact_writes",
    "db_writes": "max_db_writes",
    "external_reads": "max_external_reads",
    "external_writes": "max_external_writes",
    "generation_jobs": "max_generation_jobs",
    "docking_jobs": "max_docking_jobs",
    "model_training_jobs": "max_model_training_jobs",
    "campaign_replans": "max_campaign_replans",
    "cost_units": "max_cost_units",
}

HIGH_COST_JOB_DIMENSIONS: set[BudgetDimension] = {
    "generation_jobs",
    "docking_jobs",
    "model_training_jobs",
    "cost_units",
}


class BudgetImpact(BaseModel):
    tool_calls: int = Field(default=0, ge=0)
    codex_tasks: int = Field(default=0, ge=0)
    runtime_minutes: float = Field(default=0.0, ge=0)
    artifact_writes: int = Field(default=0, ge=0)
    db_writes: int = Field(default=0, ge=0)
    external_reads: int = Field(default=0, ge=0)
    external_writes: int = Field(default=0, ge=0)
    generation_jobs: int = Field(default=0, ge=0)
    docking_jobs: int = Field(default=0, ge=0)
    model_training_jobs: int = Field(default=0, ge=0)
    campaign_replans: int = Field(default=0, ge=0)
    cost_units: float = Field(default=0.0, ge=0)
    action_type: str | None = None
    destructive: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def nonzero_dimensions(self) -> dict[BudgetDimension, float]:
        return {
            dimension: float(getattr(self, dimension))
            for dimension in DIMENSION_LIMIT_FIELDS
            if float(getattr(self, dimension)) > 0
        }


class AgentBudgetDecision(BaseModel):
    status: BudgetDecisionStatus
    allowed: bool
    requires_approval: bool
    reasons: list[str] = Field(default_factory=list)
    exceeded_dimensions: list[str] = Field(default_factory=list)
    approval_required_dimensions: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    budget: AgentAutonomyBudget
    impact: BudgetImpact
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentBudgetReservation(AgentGovernanceSchema):
    reservation_id: str
    budget_id: str
    impact: BudgetImpact
    status: BudgetReservationStatus
    created_at: datetime
    committed_at: datetime | None = None
    released_at: datetime | None = None
    release_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentBudgetReservationResult(BaseModel):
    decision: AgentBudgetDecision
    reservation: AgentBudgetReservation | None = None


class AgentBudgetUsageReport(BaseModel):
    budget_id: str
    period: AgentAutonomyBudgetPeriod
    enabled: bool
    usage: dict[str, float]
    reserved_usage: dict[str, float]
    limits: dict[str, float | None]
    remaining: dict[str, float | None]
    utilization: dict[str, float | None]
    reset_at: datetime | None
    active_reservation_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAutonomyBudgetManager:
    """Check, reserve, commit, release, reset, and report V2.6 autonomy budgets."""

    def __init__(
        self,
        *,
        budgets: list[AgentAutonomyBudget] | None = None,
        reservations: list[AgentBudgetReservation] | None = None,
        high_cost_threshold: float = 0.8,
    ) -> None:
        self.budgets = list(budgets or [])
        self.reservations = list(reservations or [])
        self.high_cost_threshold = high_cost_threshold

    def check_budget(
        self,
        budget: AgentAutonomyBudget | str,
        impact: BudgetImpact | None = None,
        *,
        approvals: set[str] | None = None,
        now: datetime | None = None,
        **impact_kwargs: Any,
    ) -> AgentBudgetDecision:
        active_budget = self._resolve_budget(budget)
        active_impact = impact or BudgetImpact.model_validate(impact_kwargs)
        current_time = now or datetime.now(UTC)
        active_budget = self._reset_if_due(active_budget, now=current_time)
        approvals = approvals or set()

        if not active_budget.enabled:
            return _budget_decision(
                status="blocked",
                budget=active_budget,
                impact=active_impact,
                reasons=["Autonomy budget is disabled."],
                metadata={"recommended_run_control": "pause"},
            )

        exceeded = self._exceeded_dimensions(active_budget, active_impact)
        if exceeded:
            if (
                active_budget.metadata.get("exhaustion_action") == "require_approval"
                and BUDGET_OVERRIDE_APPROVAL not in approvals
            ):
                return _budget_decision(
                    status="approval_required",
                    budget=active_budget,
                    impact=active_impact,
                    reasons=["Budget exhaustion requires human approval."],
                    exceeded_dimensions=exceeded,
                    approval_required_dimensions=exceeded,
                    required_approvals=[BUDGET_OVERRIDE_APPROVAL],
                    metadata={"recommended_run_control": "require_approval_all_actions"},
                )
            if (
                active_budget.metadata.get("exhaustion_action") == "require_approval"
                and BUDGET_OVERRIDE_APPROVAL in approvals
            ):
                return _budget_decision(
                    status="allowed",
                    budget=active_budget,
                    impact=active_impact,
                    reasons=["Budget override approval permits execution."],
                    exceeded_dimensions=exceeded,
                    metadata={"budget_override_approved": True},
                )
            return _budget_decision(
                status="blocked",
                budget=active_budget,
                impact=active_impact,
                reasons=["Budget limit would be exceeded."],
                exceeded_dimensions=exceeded,
                metadata={"recommended_run_control": "pause"},
            )

        high_cost_dimensions = self._high_cost_dimensions(active_budget, active_impact)
        if high_cost_dimensions and HIGH_COST_APPROVAL not in approvals:
            return _budget_decision(
                status="approval_required",
                budget=active_budget,
                impact=active_impact,
                reasons=["High-cost agent action requires approval."],
                approval_required_dimensions=high_cost_dimensions,
                required_approvals=[HIGH_COST_APPROVAL],
                metadata={"recommended_run_control": "require_approval_all_actions"},
            )

        return _budget_decision(
            status="allowed",
            budget=active_budget,
            impact=active_impact,
            reasons=["Budget permits this agent action."],
        )

    def reserve_budget(
        self,
        budget: AgentAutonomyBudget | str,
        impact: BudgetImpact | None = None,
        *,
        approvals: set[str] | None = None,
        reservation_id: str | None = None,
        now: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        **impact_kwargs: Any,
    ) -> AgentBudgetReservationResult:
        current_time = now or datetime.now(UTC)
        active_impact = impact or BudgetImpact.model_validate(impact_kwargs)
        decision = self.check_budget(
            budget,
            active_impact,
            approvals=approvals,
            now=current_time,
        )
        if not decision.allowed:
            return AgentBudgetReservationResult(decision=decision, reservation=None)

        reservation = AgentBudgetReservation(
            reservation_id=reservation_id or f"agent-budget-reservation-{uuid4().hex[:12]}",
            budget_id=decision.budget.budget_id,
            impact=active_impact,
            status="reserved",
            created_at=current_time,
            metadata=metadata or {},
        )
        self.reservations.append(reservation)
        return AgentBudgetReservationResult(decision=decision, reservation=reservation)

    def commit_budget(
        self,
        reservation_id: str,
        *,
        committed_at: datetime | None = None,
    ) -> AgentAutonomyBudget:
        reservation = self._require_reservation(reservation_id)
        if reservation.status != "reserved":
            raise ValueError(f"Reservation is not active: {reservation_id}")

        budget = self._resolve_budget(reservation.budget_id)
        updated_budget = self._increment_usage(budget, reservation.impact)
        updated_reservation = reservation.model_copy(
            update={"status": "committed", "committed_at": committed_at or datetime.now(UTC)}
        )
        self._replace_budget(updated_budget)
        self._replace_reservation(updated_reservation)
        return updated_budget

    def record_usage(
        self,
        budget: AgentAutonomyBudget | str,
        impact: BudgetImpact | None = None,
        *,
        approvals: set[str] | None = None,
        now: datetime | None = None,
        **impact_kwargs: Any,
    ) -> AgentBudgetDecision:
        active_impact = impact or BudgetImpact.model_validate(impact_kwargs)
        decision = self.check_budget(
            budget,
            active_impact,
            approvals=approvals,
            now=now,
        )
        if decision.allowed:
            self._replace_budget(self._increment_usage(decision.budget, active_impact))
        return decision

    def release_budget(
        self,
        reservation_id: str,
        *,
        reason: str = "Budget reservation released.",
        released_at: datetime | None = None,
    ) -> AgentBudgetReservation:
        reservation = self._require_reservation(reservation_id)
        if reservation.status != "reserved":
            raise ValueError(f"Reservation is not active: {reservation_id}")
        updated = reservation.model_copy(
            update={
                "status": "released",
                "released_at": released_at or datetime.now(UTC),
                "release_reason": reason,
            }
        )
        self._replace_reservation(updated)
        return updated

    def reset_budget(
        self,
        budget: AgentAutonomyBudget | str,
        *,
        now: datetime | None = None,
    ) -> AgentAutonomyBudget:
        active_budget = self._resolve_budget(budget)
        current_time = now or datetime.now(UTC)
        updated = active_budget.model_copy(
            update={
                "current_usage": {},
                "reset_at": _next_reset_at(active_budget.period, current_time),
            }
        )
        self._replace_budget(updated)
        return updated

    def reset_expired_budgets(
        self,
        *,
        now: datetime | None = None,
    ) -> list[AgentAutonomyBudget]:
        current_time = now or datetime.now(UTC)
        reset: list[AgentAutonomyBudget] = []
        for budget in list(self.budgets):
            if budget.reset_at is not None and budget.reset_at <= current_time:
                reset.append(self.reset_budget(budget, now=current_time))
        return reset

    def report_budget_usage(
        self,
        budget: AgentAutonomyBudget | str,
    ) -> AgentBudgetUsageReport:
        active_budget = self._resolve_budget(budget)
        usage = _usage(active_budget)
        reserved_usage = self._reserved_usage(active_budget.budget_id)
        limits = _limits(active_budget)
        remaining: dict[str, float | None] = {}
        utilization: dict[str, float | None] = {}
        for dimension, limit in limits.items():
            spent = usage.get(dimension, 0.0) + reserved_usage.get(dimension, 0.0)
            remaining[dimension] = None if limit is None else max(limit - spent, 0.0)
            utilization[dimension] = None if limit in (None, 0) else spent / limit
        active_reservations = [
            reservation
            for reservation in self.reservations
            if reservation.budget_id == active_budget.budget_id
            and reservation.status == "reserved"
        ]
        return AgentBudgetUsageReport(
            budget_id=active_budget.budget_id,
            period=active_budget.period,
            enabled=active_budget.enabled,
            usage=usage,
            reserved_usage=reserved_usage,
            limits=limits,
            remaining=remaining,
            utilization=utilization,
            reset_at=active_budget.reset_at,
            active_reservation_count=len(active_reservations),
            metadata={
                "external_writes_default_zero": True,
                "destructive_actions_default_zero": True,
            },
        )

    def _exceeded_dimensions(
        self,
        budget: AgentAutonomyBudget,
        impact: BudgetImpact,
    ) -> list[str]:
        usage = _usage(budget)
        reserved = self._reserved_usage(budget.budget_id)
        exceeded: list[str] = []
        for dimension, delta in impact.nonzero_dimensions().items():
            limit = _limit_for_dimension(budget, dimension)
            if limit is None:
                continue
            projected = usage.get(dimension, 0.0) + reserved.get(dimension, 0.0) + delta
            if projected > limit:
                exceeded.append(dimension)

        if impact.destructive:
            limit = float(budget.metadata.get(DESTRUCTIVE_ACTION_LIMIT_KEY, 0))
            projected = (
                usage.get(DESTRUCTIVE_ACTION_USAGE_KEY, 0.0)
                + reserved.get(DESTRUCTIVE_ACTION_USAGE_KEY, 0.0)
                + 1.0
            )
            if projected > limit:
                exceeded.append(DESTRUCTIVE_ACTION_USAGE_KEY)
        return exceeded

    def _high_cost_dimensions(
        self,
        budget: AgentAutonomyBudget,
        impact: BudgetImpact,
    ) -> list[str]:
        if impact.metadata.get("high_cost") is True:
            return sorted(impact.nonzero_dimensions())
        usage = _usage(budget)
        reserved = self._reserved_usage(budget.budget_id)
        high_cost: list[str] = []
        for dimension in HIGH_COST_JOB_DIMENSIONS:
            delta = impact.nonzero_dimensions().get(dimension, 0.0)
            if delta <= 0:
                continue
            limit = _limit_for_dimension(budget, dimension)
            if limit is None or limit == 0:
                continue
            projected_utilization = (
                usage.get(dimension, 0.0) + reserved.get(dimension, 0.0) + delta
            ) / limit
            if projected_utilization >= self.high_cost_threshold:
                high_cost.append(dimension)
        return high_cost

    def _increment_usage(
        self,
        budget: AgentAutonomyBudget,
        impact: BudgetImpact,
    ) -> AgentAutonomyBudget:
        usage = _usage(budget)
        for dimension, delta in impact.nonzero_dimensions().items():
            usage[dimension] = usage.get(dimension, 0.0) + delta
        if impact.destructive:
            usage[DESTRUCTIVE_ACTION_USAGE_KEY] = (
                usage.get(DESTRUCTIVE_ACTION_USAGE_KEY, 0.0) + 1.0
            )
        return budget.model_copy(update={"current_usage": usage})

    def _reserved_usage(self, budget_id: str) -> dict[str, float]:
        usage: dict[str, float] = {}
        for reservation in self.reservations:
            if reservation.budget_id != budget_id or reservation.status != "reserved":
                continue
            for dimension, delta in reservation.impact.nonzero_dimensions().items():
                usage[dimension] = usage.get(dimension, 0.0) + delta
            if reservation.impact.destructive:
                usage[DESTRUCTIVE_ACTION_USAGE_KEY] = (
                    usage.get(DESTRUCTIVE_ACTION_USAGE_KEY, 0.0) + 1.0
                )
        return usage

    def _reset_if_due(
        self,
        budget: AgentAutonomyBudget,
        *,
        now: datetime,
    ) -> AgentAutonomyBudget:
        if budget.reset_at is not None and budget.reset_at <= now:
            return self.reset_budget(budget, now=now)
        return budget

    def _resolve_budget(self, budget: AgentAutonomyBudget | str) -> AgentAutonomyBudget:
        if isinstance(budget, AgentAutonomyBudget):
            return budget
        for item in self.budgets:
            if item.budget_id == budget:
                return item
        raise ValueError(f"Unknown autonomy budget: {budget}")

    def _replace_budget(self, updated: AgentAutonomyBudget) -> None:
        self.budgets = [
            updated if budget.budget_id == updated.budget_id else budget
            for budget in self.budgets
        ]
        if not any(budget.budget_id == updated.budget_id for budget in self.budgets):
            self.budgets.append(updated)

    def _require_reservation(self, reservation_id: str) -> AgentBudgetReservation:
        for reservation in self.reservations:
            if reservation.reservation_id == reservation_id:
                return reservation
        raise ValueError(f"Unknown budget reservation: {reservation_id}")

    def _replace_reservation(self, updated: AgentBudgetReservation) -> None:
        self.reservations = [
            updated if reservation.reservation_id == updated.reservation_id else reservation
            for reservation in self.reservations
        ]


def _budget_decision(
    *,
    status: BudgetDecisionStatus,
    budget: AgentAutonomyBudget,
    impact: BudgetImpact,
    reasons: list[str],
    exceeded_dimensions: list[str] | None = None,
    approval_required_dimensions: list[str] | None = None,
    required_approvals: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentBudgetDecision:
    return AgentBudgetDecision(
        status=status,
        allowed=status == "allowed",
        requires_approval=status == "approval_required",
        reasons=reasons,
        exceeded_dimensions=exceeded_dimensions or [],
        approval_required_dimensions=approval_required_dimensions or [],
        required_approvals=required_approvals or [],
        budget=budget,
        impact=impact,
        metadata=metadata or {},
    )


def _usage(budget: AgentAutonomyBudget) -> dict[str, float]:
    usage: dict[str, float] = {}
    for key, value in budget.current_usage.items():
        if isinstance(value, int | float):
            usage[key] = float(value)
    return usage


def _limits(budget: AgentAutonomyBudget) -> dict[str, float | None]:
    return {
        dimension: _limit_for_dimension(budget, dimension)
        for dimension in DIMENSION_LIMIT_FIELDS
    }


def _limit_for_dimension(
    budget: AgentAutonomyBudget,
    dimension: BudgetDimension,
) -> float | None:
    raw = getattr(budget, DIMENSION_LIMIT_FIELDS[dimension])
    if raw is None and dimension in DEFAULT_ZERO_LIMIT_DIMENSIONS:
        return 0.0
    if raw is None:
        return None
    return float(cast(int | float, raw))


def _next_reset_at(
    period: AgentAutonomyBudgetPeriod,
    now: datetime,
) -> datetime | None:
    if period == "per_session" or period == "campaign_lifetime":
        return None
    if period == "daily":
        return now + timedelta(days=1)
    if period == "weekly":
        return now + timedelta(weeks=1)
    if period == "monthly":
        return now + timedelta(days=30)
    return None


__all__ = [
    "AgentAutonomyBudget",
    "AgentAutonomyBudgetManager",
    "AgentBudgetDecision",
    "AgentBudgetReservation",
    "AgentBudgetReservationResult",
    "AgentBudgetUsageReport",
    "BudgetImpact",
]
