from __future__ import annotations

from datetime import UTC, datetime, timedelta

from molecule_ranker.agent_governance import (
    AgentAutonomyBudget,
    AgentAutonomyBudgetManager,
    BudgetImpact,
)
from molecule_ranker.agent_governance.schemas import AgentAutonomyBudgetPeriod

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_budget_usage_increments_after_action_commit() -> None:
    manager = AgentAutonomyBudgetManager(budgets=[_budget(max_tool_calls=3)])

    decision = manager.record_usage(
        "budget-1",
        BudgetImpact(tool_calls=1, cost_units=2.5),
        now=NOW,
    )

    assert decision.status == "allowed"
    report = manager.report_budget_usage("budget-1")
    assert report.usage["tool_calls"] == 1
    assert report.usage["cost_units"] == 2.5
    assert report.remaining["tool_calls"] == 2


def test_budget_exceeded_blocks_and_recommends_pause() -> None:
    manager = AgentAutonomyBudgetManager(
        budgets=[_budget(max_tool_calls=1, current_usage={"tool_calls": 1})]
    )

    decision = manager.check_budget(
        "budget-1",
        BudgetImpact(tool_calls=1),
        now=NOW,
    )

    assert decision.status == "blocked"
    assert decision.allowed is False
    assert decision.exceeded_dimensions == ["tool_calls"]
    assert decision.metadata["recommended_run_control"] == "pause"


def test_budget_reservation_commit_and_release() -> None:
    manager = AgentAutonomyBudgetManager(budgets=[_budget(max_tool_calls=3)])

    reserved = manager.reserve_budget(
        "budget-1",
        BudgetImpact(tool_calls=1),
        reservation_id="reservation-1",
        now=NOW,
    )
    assert reserved.reservation is not None
    assert reserved.decision.status == "allowed"
    assert manager.report_budget_usage("budget-1").reserved_usage["tool_calls"] == 1

    committed_budget = manager.commit_budget("reservation-1", committed_at=NOW)
    assert committed_budget.current_usage["tool_calls"] == 1.0
    assert manager.report_budget_usage("budget-1").reserved_usage.get("tool_calls", 0) == 0

    released = manager.reserve_budget(
        "budget-1",
        BudgetImpact(tool_calls=1),
        reservation_id="reservation-2",
        now=NOW,
    )
    assert released.reservation is not None
    manager.release_budget("reservation-2", reason="Action cancelled.", released_at=NOW)
    report = manager.report_budget_usage("budget-1")
    assert report.usage["tool_calls"] == 1
    assert report.reserved_usage.get("tool_calls", 0) == 0


def test_budget_period_reset_clears_usage() -> None:
    manager = AgentAutonomyBudgetManager(
        budgets=[
            _budget(
                period="daily",
                max_tool_calls=2,
                current_usage={"tool_calls": 2},
                reset_at=NOW - timedelta(minutes=1),
            )
        ]
    )

    reset = manager.reset_expired_budgets(now=NOW)

    assert len(reset) == 1
    report = manager.report_budget_usage("budget-1")
    assert report.usage == {}
    assert report.reset_at is not None
    assert report.reset_at > NOW


def test_external_write_blocked_by_default_zero_budget() -> None:
    manager = AgentAutonomyBudgetManager(budgets=[_budget(max_external_writes=None)])

    decision = manager.check_budget(
        "budget-1",
        BudgetImpact(external_writes=1),
        now=NOW,
    )

    assert decision.status == "blocked"
    assert decision.exceeded_dimensions == ["external_writes"]


def test_high_cost_job_requires_approval() -> None:
    manager = AgentAutonomyBudgetManager(budgets=[_budget(max_cost_units=100.0)])

    decision = manager.check_budget(
        "budget-1",
        BudgetImpact(cost_units=85.0),
        now=NOW,
    )

    assert decision.status == "approval_required"
    assert decision.required_approvals == ["high_cost_job"]

    approved = manager.record_usage(
        "budget-1",
        BudgetImpact(cost_units=85.0),
        approvals={"high_cost_job"},
        now=NOW,
    )
    assert approved.status == "allowed"
    assert manager.report_budget_usage("budget-1").usage["cost_units"] == 85.0


def _budget(
    *,
    period: AgentAutonomyBudgetPeriod = "daily",
    max_tool_calls: int | None = None,
    max_codex_tasks: int | None = None,
    max_runtime_minutes: float | None = None,
    max_artifact_writes: int | None = None,
    max_db_writes: int | None = None,
    max_external_reads: int | None = None,
    max_external_writes: int | None = 0,
    max_generation_jobs: int | None = None,
    max_docking_jobs: int | None = None,
    max_model_training_jobs: int | None = None,
    max_campaign_replans: int | None = None,
    max_cost_units: float | None = None,
    current_usage: dict[str, object] | None = None,
    reset_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
) -> AgentAutonomyBudget:
    return AgentAutonomyBudget(
        budget_id="budget-1",
        org_id="org-1",
        project_id="project-1",
        campaign_id="campaign-1",
        agent_id="agent-1",
        period=period,
        max_tool_calls=max_tool_calls,
        max_codex_tasks=max_codex_tasks,
        max_runtime_minutes=max_runtime_minutes,
        max_artifact_writes=max_artifact_writes,
        max_db_writes=max_db_writes,
        max_external_reads=max_external_reads,
        max_external_writes=max_external_writes,
        max_generation_jobs=max_generation_jobs,
        max_docking_jobs=max_docking_jobs,
        max_model_training_jobs=max_model_training_jobs,
        max_campaign_replans=max_campaign_replans,
        max_cost_units=max_cost_units,
        current_usage=current_usage or {},
        reset_at=reset_at,
        enabled=True,
        metadata=metadata or {},
    )
