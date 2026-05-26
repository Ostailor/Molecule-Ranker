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
        scored_existing = self._scorer.score(existing, context.targets, top=top) if existing else []
        scored_generated = [
            self._experimental_adjust_generated(
                self._developability_adjust_generated(candidate),
                context.config,
            )
            for candidate in generated
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

    def _experimental_adjust_generated(
        self,
        candidate: MoleculeCandidate,
        config: dict[str, object],
    ) -> MoleculeCandidate:
        experimental_config = config.get("experimental_evidence")
        if not isinstance(experimental_config, dict):
            return candidate
        generated_summaries = experimental_config.get("generated_summaries")
        if not isinstance(generated_summaries, dict):
            return candidate
        summary = generated_summaries.get(candidate.name)
        if not isinstance(summary, dict):
            return candidate

        metadata = summary.get("metadata")
        direct_result_ids = []
        if isinstance(metadata, dict):
            raw_ids = metadata.get("direct_evidence_result_ids")
            if isinstance(raw_ids, list):
                direct_result_ids = [str(result_id) for result_id in raw_ids if result_id]
        positive_count = self._summary_count(summary, "positive_count")
        negative_count = self._summary_count(summary, "negative_count")
        failed_qc_count = self._summary_count(summary, "failed_qc_count")
        safety_count = len(summary.get("safety_concerns", [])) if isinstance(
            summary.get("safety_concerns"), list
        ) else 0

        base_score = float(
            candidate.score or candidate.generation_metadata.get("generation_score") or 0.0
        )
        adjusted = base_score
        generation_metadata = dict(candidate.generation_metadata)
        warnings = set(candidate.warnings)

        if positive_count > 0 and direct_result_ids:
            adjusted += min(0.08, 0.06 + 0.01 * min(positive_count - 1, 2))
            generation_metadata["experimental_direct_evidence_available"] = True
            generation_metadata["direct_experimental_result_ids"] = direct_result_ids
            warnings.add(
                "Generated molecule has exact linked imported assay evidence; "
                "this is not clinical efficacy evidence."
            )
        if negative_count > 0:
            adjusted -= min(0.10, 0.06 + 0.02 * min(negative_count - 1, 2))
            generation_metadata["experimental_negative_result_count"] = negative_count
            warnings.add("Imported negative assay evidence lowers generated-molecule priority.")
        if safety_count > 0:
            adjusted -= min(0.14, 0.08 + 0.02 * min(safety_count - 1, 3))
            generation_metadata["experimental_safety_concern_count"] = safety_count
            warnings.add("Imported safety assay evidence lowers generated-molecule priority.")
        if failed_qc_count > 0:
            generation_metadata["experimental_failed_qc_count"] = failed_qc_count
            warnings.add("Failed-QC imported assay evidence is tracked but does not add support.")

        adjusted = max(0.0, min(adjusted, 1.0))
        return candidate.model_copy(
            update={
                "score": round(adjusted, 3),
                "generation_metadata": generation_metadata,
                "warnings": sorted(warnings),
            }
        )

    def _summary_count(self, summary: dict[object, object], key: str) -> int:
        value = summary.get(key)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, str):
            try:
                return max(0, int(value))
            except ValueError:
                return 0
        return 0

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
