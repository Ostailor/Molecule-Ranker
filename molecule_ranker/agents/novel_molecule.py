from __future__ import annotations

from molecule_ranker.agents.base import BaseAgent, PipelineContext

STUB_MESSAGE = (
    "Novel molecule generation is not implemented in V0.1. This agent is a "
    "placeholder for future target-conditioned molecular generation."
)

FUTURE_INPUTS = [
    "resolved disease",
    "evidence-backed targets",
    "desired mechanism",
    "constraints",
]

FUTURE_OUTPUTS = [
    "generated MoleculeCandidate objects backed by explicit model outputs and "
    "filtering metadata",
]

FUTURE_FILTERS = [
    "synthetic accessibility",
    "ADMET",
    "toxicity",
    "novelty",
    "patentability",
    "docking score",
    "target selectivity",
]


class NovelMoleculeAgent(BaseAgent):
    name = "NovelMoleculeAgent"

    def process(self, context: PipelineContext) -> PipelineContext:
        context.config.setdefault("warnings", []).append(STUB_MESSAGE)
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        return STUB_MESSAGE

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        return {
            "implemented": False,
            "v0_1_behavior": "generation disabled; candidate list is left unchanged",
            "future_interface": {
                "inputs": FUTURE_INPUTS,
                "outputs": FUTURE_OUTPUTS,
                "filters": FUTURE_FILTERS,
            },
        }
