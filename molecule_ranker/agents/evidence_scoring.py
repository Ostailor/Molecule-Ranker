from __future__ import annotations

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.schemas import MoleculeCandidate
from molecule_ranker.scoring import TransparentEvidenceScorer


class EvidenceScoringAgent(BaseAgent):
    name = "EvidenceScoringAgent"

    def __init__(self, scorer: TransparentEvidenceScorer | None = None) -> None:
        super().__init__()
        self._scorer = scorer or TransparentEvidenceScorer()

    def process(self, context: PipelineContext) -> PipelineContext:
        top = int(context.config.get("top", 20))
        existing = [
            candidate for candidate in context.candidates if candidate.origin != "generated"
        ]
        generated = [
            candidate for candidate in context.candidates if candidate.origin == "generated"
        ]
        scored_existing = self._scorer.score(existing, context.targets, top=top)
        scored_generated = [
            self._developability_adjust_generated(candidate) for candidate in generated
        ]
        context.candidates = sorted(
            [*scored_existing, *scored_generated],
            key=lambda candidate: candidate.score or 0.0,
            reverse=True,
        )
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

    def _developability_adjust_generated(
        self, candidate: MoleculeCandidate
    ) -> MoleculeCandidate:
        assessment = candidate.developability_assessment
        base_score = float(
            candidate.score or candidate.generation_metadata.get("generation_score") or 0.0
        )
        if assessment is None:
            return candidate
        risk_level = self._developability_risk_level(assessment)
        adjusted = 0.70 * base_score + 0.30 * assessment.developability_score
        if risk_level == "critical":
            adjusted = 0.0
        elif risk_level == "high":
            adjusted = min(adjusted, 0.45)
        adjusted = max(0.0, min(adjusted, 1.0))
        warnings = sorted(
            set(
                [
                    *candidate.warnings,
                    (
                        "Generated ranking uses generation score plus computational "
                        "developability triage."
                    ),
                    (
                        "Critical generated developability risks should be rejected by default."
                        if risk_level == "critical"
                        else ""
                    ),
                ]
            )
        )
        warnings = [warning for warning in warnings if warning]
        return candidate.model_copy(update={"score": round(adjusted, 3), "warnings": warnings})

    def _developability_risk_level(self, assessment: object) -> str:
        metadata = getattr(assessment, "metadata", {})
        if isinstance(metadata, dict):
            risk_level = str(metadata.get("risk_level") or "").lower()
            if risk_level in {"critical", "high", "medium", "low", "unknown"}:
                return risk_level
        triage = str(getattr(assessment, "triage_recommendation", "") or "")
        if triage == "high_risk_flags":
            return "high"
        if triage == "insufficient_structure":
            return "unknown"
        if triage == "review_flags":
            return "medium"
        return "low"
