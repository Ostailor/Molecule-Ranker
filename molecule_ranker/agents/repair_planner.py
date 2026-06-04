from __future__ import annotations

from typing import Any

from molecule_ranker.agent_repair.repair_planner import (
    RepairPlannerAgent as DeterministicRepairPlannerAgent,
)
from molecule_ranker.agent_repair.schemas import FailureDiagnosis, RepairPlan
from molecule_ranker.agents.base import BaseAgent, PipelineContext


class RepairPlannerAgent(BaseAgent):
    """Pipeline-facing wrapper for deterministic repair planning."""

    name = "RepairPlannerAgent"

    def __init__(self) -> None:
        super().__init__()
        self.planner = DeterministicRepairPlannerAgent()
        self.last_plan: RepairPlan | None = None

    def plan_repair(self, diagnosis: FailureDiagnosis, **kwargs: Any) -> RepairPlan:
        self.last_plan = self.planner.plan_repair(diagnosis, **kwargs)
        return self.last_plan

    def plan(self, diagnosis: FailureDiagnosis, **kwargs: Any) -> RepairPlan:
        return self.plan_repair(diagnosis, **kwargs)

    def process(self, context: PipelineContext) -> PipelineContext:
        diagnosis_payload = context.config.get("failure_diagnosis_result")
        if isinstance(diagnosis_payload, dict):
            diagnosis = FailureDiagnosis.model_validate(diagnosis_payload)
            self.last_plan = self.planner.plan_repair(
                diagnosis,
                runtime_session=context.config.get("runtime_session"),
                policy_engine=context.config.get("repair_policy"),
                approvals=set(context.config.get("approvals", [])),
                repair_memory=context.config.get("repair_memory"),
                user_autonomy_level=context.config.get("autonomy_level", "suggest_only"),
            )
            context.config["repair_plan"] = self.last_plan.model_dump(mode="json")
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if self.last_plan is None:
            return "No repair plan requested."
        return (
            f"Created repair plan with {len(self.last_plan.actions)} actions; "
            f"validated={self.last_plan.validated}."
        )


__all__ = ["RepairPlannerAgent"]
