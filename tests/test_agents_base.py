from __future__ import annotations

from typing import ClassVar

from molecule_ranker.agents.base import BaseAgent, PipelineContext


class DummyAgent(BaseAgent):
    name: ClassVar[str] = "DummyAgent"

    def process(self, context: PipelineContext) -> PipelineContext:
        context.config["dummy_ran"] = True
        return context


def test_base_agent_mutates_context_and_appends_trace():
    context = PipelineContext(disease_input="Parkinson disease")

    result = DummyAgent().run(context)

    assert result.config["dummy_ran"] is True
    assert len(result.traces) == 1
    assert result.traces[0].agent_name == "DummyAgent"
    assert result.traces[0].input_summary == "PipelineContext(disease_input='Parkinson disease')"
    assert result.traces[0].output_summary == "Agent completed successfully."
