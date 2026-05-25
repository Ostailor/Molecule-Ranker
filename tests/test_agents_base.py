from __future__ import annotations

from typing import ClassVar

import pytest

from molecule_ranker.agents.base import AgentExecutionError, BaseAgent, PipelineContext


class DummyAgent(BaseAgent):
    name: ClassVar[str] = "DummyAgent"

    def process(self, context: PipelineContext) -> PipelineContext:
        context.config["dummy_ran"] = True
        return context


class BrokenAgent(BaseAgent):
    name: ClassVar[str] = "BrokenAgent"

    def process(self, context: PipelineContext) -> PipelineContext:
        raise ValueError("programming bug")


def test_base_agent_mutates_context_and_appends_trace():
    context = PipelineContext(disease_input="Parkinson disease")

    result = DummyAgent().run(context)

    assert result.config["dummy_ran"] is True
    assert len(result.traces) == 1
    assert result.traces[0].agent_name == "DummyAgent"
    assert result.traces[0].input_summary == "PipelineContext(disease_input='Parkinson disease')"
    assert result.traces[0].output_summary == "Agent completed successfully."


def test_base_agent_re_raises_unexpected_exceptions_after_trace():
    context = PipelineContext(disease_input="Parkinson disease")

    with pytest.raises(AgentExecutionError, match="BrokenAgent failed unexpectedly"):
        BrokenAgent().run(context)

    assert len(context.traces) == 1
    assert context.traces[0].agent_name == "BrokenAgent"
    assert "programming bug" in context.traces[0].output_summary
