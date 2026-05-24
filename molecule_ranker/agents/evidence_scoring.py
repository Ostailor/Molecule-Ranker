from __future__ import annotations

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.scoring import TransparentEvidenceScorer


class EvidenceScoringAgent(BaseAgent):
    name = "EvidenceScoringAgent"

    def __init__(self, scorer: TransparentEvidenceScorer | None = None) -> None:
        super().__init__()
        self._scorer = scorer or TransparentEvidenceScorer()

    def process(self, context: PipelineContext) -> PipelineContext:
        top = int(context.config.get("top", 20))
        context.candidates = self._scorer.score(context.candidates, context.targets, top=top)
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        raw_count = context.config.get("MoleculeRetrievalAgent.raw_count", len(context.candidates))
        return f"Scored {raw_count} records and retained top {len(context.candidates)}."

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        return {
            "top": context.config.get("top", 20),
            "ranked_candidates": [
                {"name": candidate.name, "score": candidate.score}
                for candidate in context.candidates
            ],
        }
