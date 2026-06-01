from __future__ import annotations

from collections.abc import Iterable
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.hypotheses.schemas import FalsificationCriterion, ResearchHypothesis
from molecule_ranker.hypotheses.validation import detect_hypothesis_guardrail_violations

__all__ = [
    "FalsificationCriteriaBuilder",
    "FalsificationCriterion",
    "build_falsification_criteria",
]


class FalsificationCriteriaBuilder:
    """Build high-level, decision-focused falsification criteria for V1.6 hypotheses."""

    def build(self, hypothesis: ResearchHypothesis) -> list[FalsificationCriterion]:
        builders = {
            "molecule_target": self._molecule_target,
            "generated_molecule": self._generated_molecule,
            "scaffold_series": self._scaffold_series,
            "assay_contradiction": self._assay_contradiction,
        }
        build = builders.get(hypothesis.hypothesis_type, self._default)
        criteria = build(hypothesis)
        return _dedupe(criteria)

    def _molecule_target(
        self,
        hypothesis: ResearchHypothesis,
    ) -> list[FalsificationCriterion]:
        return [
            _criterion(
                hypothesis,
                "orthogonal-negative-target-engagement",
                (
                    "A QC-passed orthogonal target-engagement result that is negative "
                    "would reduce priority for this molecule-target hypothesis."
                ),
                evidence_type_needed="assay_result",
                would_support=False,
                would_contradict=True,
                decision_impact="decrease_priority",
            ),
            _criterion(
                hypothesis,
                "broad-nonspecific-selectivity",
                (
                    "A selectivity result showing broad nonspecific activity would "
                    "reduce confidence in the molecule-target interpretation."
                ),
                evidence_type_needed="assay_result",
                would_support=False,
                would_contradict=True,
                decision_impact="decrease_priority",
            ),
            _criterion(
                hypothesis,
                "positive-relevant-assay-context",
                (
                    "A positive result in a relevant assay context would increase "
                    "support but not prove clinical efficacy."
                ),
                evidence_type_needed="assay_result",
                would_support=True,
                would_contradict=False,
                decision_impact="increase_priority",
            ),
        ]

    def _generated_molecule(
        self,
        hypothesis: ResearchHypothesis,
    ) -> list[FalsificationCriterion]:
        return [
            _criterion(
                hypothesis,
                "exact-structure-negative",
                (
                    "An exact-structure QC-passed negative result in the intended "
                    "assay context would reduce priority for the generated molecule."
                ),
                evidence_type_needed="assay_result",
                would_support=False,
                would_contradict=True,
                decision_impact="decrease_priority",
            ),
            _criterion(
                hypothesis,
                "seed-alone-not-supportive",
                (
                    "Seed-molecule activity alone does not support the generated "
                    "molecule without exact generated-molecule evidence."
                ),
                evidence_type_needed="graph_update",
                would_support=False,
                would_contradict=True,
                decision_impact="require_more_data",
            ),
            _criterion(
                hypothesis,
                "critical-developability-risk",
                "Critical developability risk may retire the generated-molecule hypothesis.",
                evidence_type_needed="developability_assessment",
                would_support=False,
                would_contradict=True,
                decision_impact="retire_hypothesis",
            ),
        ]

    def _scaffold_series(
        self,
        hypothesis: ResearchHypothesis,
    ) -> list[FalsificationCriterion]:
        return [
            _criterion(
                hypothesis,
                "repeated-series-negatives",
                "Repeated negative results across series members may retire the series.",
                evidence_type_needed="assay_result",
                would_support=False,
                would_contradict=True,
                decision_impact="retire_hypothesis",
            ),
            _criterion(
                hypothesis,
                "single-positive-series-member",
                (
                    "A single positive result may support further exploration but does "
                    "not validate all analogs."
                ),
                evidence_type_needed="assay_result",
                would_support=True,
                would_contradict=False,
                decision_impact="increase_priority",
            ),
        ]

    def _assay_contradiction(
        self,
        hypothesis: ResearchHypothesis,
    ) -> list[FalsificationCriterion]:
        return [
            _criterion(
                hypothesis,
                "orthogonal-contradiction-resolution",
                (
                    "An orthogonal result resolving one side of the contradiction "
                    "changes status but does not prove the broader hypothesis."
                ),
                evidence_type_needed="assay_result",
                would_support=True,
                would_contradict=True,
                decision_impact="require_more_data",
            ),
            _criterion(
                hypothesis,
                "provenance-review-adjudication",
                (
                    "Expert review that identifies a provenance or context mismatch "
                    "would update the contradiction interpretation."
                ),
                evidence_type_needed="review_decision",
                would_support=False,
                would_contradict=True,
                decision_impact="change_mechanism",
            ),
        ]

    def _default(self, hypothesis: ResearchHypothesis) -> list[FalsificationCriterion]:
        return [
            _criterion(
                hypothesis,
                "source-backed-graph-update",
                (
                    "A source-backed graph update that contradicts the referenced "
                    "relationship would require priority review."
                ),
                evidence_type_needed="graph_update",
                would_support=False,
                would_contradict=True,
                decision_impact="require_more_data",
            )
        ]


def build_falsification_criteria(
    hypothesis: ResearchHypothesis,
) -> list[FalsificationCriterion]:
    return FalsificationCriteriaBuilder().build(hypothesis)


def build_falsification_criteria_for_hypotheses(
    hypotheses: Iterable[ResearchHypothesis],
) -> dict[str, list[FalsificationCriterion]]:
    builder = FalsificationCriteriaBuilder()
    return {hypothesis.hypothesis_id: builder.build(hypothesis) for hypothesis in hypotheses}


def _criterion(
    hypothesis: ResearchHypothesis,
    criterion_key: str,
    text: str,
    *,
    evidence_type_needed: str,
    would_support: bool,
    would_contradict: bool,
    decision_impact: str,
) -> FalsificationCriterion:
    warnings = detect_hypothesis_guardrail_violations(text)
    if warnings:
        raise ValueError("falsification criteria must remain high-level and non-procedural")
    return FalsificationCriterion(
        criterion_id=_criterion_id(hypothesis.hypothesis_id, criterion_key),
        hypothesis_id=hypothesis.hypothesis_id,
        criterion_text=text,
        evidence_type_needed=evidence_type_needed,  # type: ignore[arg-type]
        would_support=would_support,
        would_contradict=would_contradict,
        decision_impact=decision_impact,  # type: ignore[arg-type]
        graph_record_ids=_graph_record_ids(hypothesis),
        metadata={
            "criterion_key": criterion_key,
            "high_level_only": True,
            "decision_focused": True,
            "not_experimental_protocol": True,
            "not_synthesis_or_dosing": True,
        },
    )


def _criterion_id(hypothesis_id: str, criterion_key: str) -> str:
    material = f"{hypothesis_id}:{criterion_key}"
    return f"criterion:{uuid5(NAMESPACE_URL, material).hex[:16]}"


def _graph_record_ids(hypothesis: ResearchHypothesis) -> list[str]:
    return sorted(
        {
            *hypothesis.disease_entity_ids,
            *hypothesis.target_entity_ids,
            *hypothesis.molecule_entity_ids,
            *hypothesis.generated_molecule_entity_ids,
            *hypothesis.scaffold_entity_ids,
            *hypothesis.mechanism_entity_ids,
            *hypothesis.supporting_relation_ids,
            *hypothesis.contradicting_relation_ids,
            *hypothesis.source_artifact_ids,
        }
    )


def _dedupe(criteria: list[FalsificationCriterion]) -> list[FalsificationCriterion]:
    deduped = {criterion.criterion_id: criterion for criterion in criteria}
    return sorted(deduped.values(), key=lambda criterion: criterion.criterion_id)
