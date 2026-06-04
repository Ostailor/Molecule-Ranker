from __future__ import annotations

from typing import Any

from molecule_ranker.agent_repair.regression import (
    RegressionCheckAgent as DeterministicRegressionCheckAgent,
)
from molecule_ranker.agent_repair.schemas import RegressionCheck, RepairExecution
from molecule_ranker.agents.base import BaseAgent, PipelineContext


class RegressionCheckAgent(BaseAgent):
    """Pipeline-facing wrapper for deterministic repair regression checks."""

    name = "RegressionCheckAgent"

    def __init__(self) -> None:
        super().__init__()
        self.checker = DeterministicRegressionCheckAgent()
        self.last_checks: list[RegressionCheck] = []

    def run_checks(self, **kwargs: Any) -> list[RegressionCheck]:
        self.last_checks = self.checker.run_checks(**kwargs)
        return self.last_checks

    def process(self, context: PipelineContext) -> PipelineContext:
        execution_payload = context.config.get("repair_execution_result") or context.config.get(
            "repair_execution"
        )
        if isinstance(execution_payload, dict):
            execution = RepairExecution.model_validate(execution_payload)
            self.last_checks = self.checker.run_checks(
                repair_execution=execution,
                changed_artifacts=context.config.get("changed_artifacts", []),
                changed_config=context.config.get("changed_config", {}),
                affected_workflow=context.config.get("affected_workflow", {}),
            )
            context.config["regression_checks"] = [
                check.model_dump(mode="json") for check in self.last_checks
            ]
            context.config["regression_passed"] = self.checker.required_checks_passed(
                self.last_checks
            )
            context.config["repair_execution_regression_status"] = (
                self.checker.execution_status_after_regression(execution, self.last_checks)
            )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if not self.last_checks:
            return "No repair regression checks requested."
        passed = sum(1 for check in self.last_checks if check.passed)
        return f"Repair regression checks passed {passed}/{len(self.last_checks)}."


__all__ = ["RegressionCheckAgent"]
