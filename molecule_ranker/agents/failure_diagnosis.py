from __future__ import annotations

from typing import Any

from molecule_ranker.agent_repair.diagnosis import (
    FailureDiagnosisAgent as DeterministicFailureDiagnosisAgent,
)
from molecule_ranker.agent_repair.schemas import FailureDiagnosis
from molecule_ranker.agents.base import BaseAgent, PipelineContext


class FailureDiagnosisAgent(BaseAgent):
    """Pipeline-facing wrapper for deterministic repair failure diagnosis."""

    name = "FailureDiagnosisAgent"

    def __init__(self) -> None:
        super().__init__()
        self.diagnoser = DeterministicFailureDiagnosisAgent()
        self.last_diagnosis: FailureDiagnosis | None = None

    def diagnose(self, **kwargs: Any) -> FailureDiagnosis:
        self.last_diagnosis = self.diagnoser.diagnose(**kwargs)
        return self.last_diagnosis

    def process(self, context: PipelineContext) -> PipelineContext:
        failure_context = context.config.get("failure_diagnosis")
        if isinstance(failure_context, dict):
            self.last_diagnosis = self.diagnoser.diagnose(**failure_context)
            context.config["failure_diagnosis_result"] = self.last_diagnosis.model_dump(
                mode="json"
            )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if self.last_diagnosis is None:
            return "No failure diagnosis requested."
        return (
            f"Diagnosed {self.last_diagnosis.failure_category} for "
            f"{self.last_diagnosis.failure_object_type}."
        )


__all__ = ["FailureDiagnosisAgent"]
