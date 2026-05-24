from __future__ import annotations

from molecule_ranker.agents.base import BaseAgent, PipelineContext


class NovelMoleculeAgent(BaseAgent):
    name = "NovelMoleculeAgent"

    def process(self, context: PipelineContext) -> PipelineContext:
        context.config.setdefault("warnings", []).append(
            "NovelMoleculeAgent is a V0.0 stub; novel generation is not implemented."
        )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        return "Novel molecule generation skipped; V0.0 ranks existing molecules only."

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        return {"implemented": False}
