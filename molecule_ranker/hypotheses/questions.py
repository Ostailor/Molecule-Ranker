from __future__ import annotations

from collections.abc import Iterable, Mapping
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.hypotheses.falsification import build_falsification_criteria
from molecule_ranker.hypotheses.planner import ResearchQuestionPlanner
from molecule_ranker.hypotheses.schemas import (
    EvidenceGap,
    FalsificationCriterion,
    ResearchHypothesis,
    TestableResearchQuestion,
)
from molecule_ranker.hypotheses.validation import detect_hypothesis_guardrail_violations

__all__ = [
    "ResearchQuestionPlanner",
    "ResearchQuestionPlannerV16",
    "TestableResearchQuestion",
    "plan_research_questions",
    "plan_research_questions_for_hypotheses",
]


class ResearchQuestionPlannerV16:
    """Build high-level testable research questions for V1.6 research hypotheses."""

    def plan(
        self,
        hypotheses: Iterable[ResearchHypothesis],
        *,
        evidence_gaps_by_hypothesis: Mapping[str, list[EvidenceGap]] | None = None,
        criteria_by_hypothesis: Mapping[str, list[FalsificationCriterion]] | None = None,
    ) -> dict[str, list[TestableResearchQuestion]]:
        gaps_by_id = evidence_gaps_by_hypothesis or {}
        criteria_by_id = criteria_by_hypothesis or {}
        return {
            hypothesis.hypothesis_id: plan_research_questions(
                hypothesis,
                evidence_gaps=gaps_by_id.get(hypothesis.hypothesis_id),
                criteria=criteria_by_id.get(hypothesis.hypothesis_id),
            )
            for hypothesis in hypotheses
        }


def plan_research_questions(
    hypothesis: ResearchHypothesis,
    *,
    evidence_gaps: Iterable[EvidenceGap] | None = None,
    criteria: Iterable[FalsificationCriterion] | None = None,
) -> list[TestableResearchQuestion]:
    evidence_gap_list = list(evidence_gaps or [])
    criterion_list = list(criteria or build_falsification_criteria(hypothesis))
    if hypothesis.hypothesis_type == "generated_molecule":
        questions = [_generated_molecule_question(hypothesis, evidence_gap_list, criterion_list)]
    elif hypothesis.hypothesis_type == "assay_contradiction":
        questions = [_contradiction_question(hypothesis, evidence_gap_list, criterion_list)]
    elif hypothesis.hypothesis_type in {"scaffold_series", "developability_risk"}:
        questions = [
            _scaffold_or_developability_question(
                hypothesis,
                evidence_gap_list,
                criterion_list,
            )
        ]
    elif hypothesis.hypothesis_type == "molecule_target":
        questions = [_molecule_target_question(hypothesis, evidence_gap_list, criterion_list)]
    else:
        questions = [_generic_question(hypothesis, evidence_gap_list, criterion_list)]
    return questions


def plan_research_questions_for_hypotheses(
    hypotheses: Iterable[ResearchHypothesis],
    *,
    evidence_gaps_by_hypothesis: Mapping[str, list[EvidenceGap]] | None = None,
    criteria_by_hypothesis: Mapping[str, list[FalsificationCriterion]] | None = None,
) -> dict[str, list[TestableResearchQuestion]]:
    return ResearchQuestionPlannerV16().plan(
        hypotheses,
        evidence_gaps_by_hypothesis=evidence_gaps_by_hypothesis,
        criteria_by_hypothesis=criteria_by_hypothesis,
    )


def _molecule_target_question(
    hypothesis: ResearchHypothesis,
    gaps: list[EvidenceGap],
    criteria: list[FalsificationCriterion],
) -> TestableResearchQuestion:
    return _question(
        hypothesis,
        "target-engagement",
        "Is the candidate associated with target engagement in a high-level "
        "orthogonal target-engagement assessment?",
        question_type="target_engagement",
        category="orthogonal binding assessment",
        expected_supported=(
            "Independent high-level target-engagement evidence aligns with the "
            "source-scoped molecule-target hypothesis."
        ),
        expected_not_supported=(
            "Independent high-level target-engagement evidence is negative, "
            "ambiguous, or inconsistent with the molecule-target hypothesis."
        ),
        ambiguity_notes=[
            "Association must remain scoped to the referenced graph and assay context.",
            "Positive support would not prove clinical efficacy.",
        ],
        gaps=gaps,
        criteria=criteria,
    )


def _generated_molecule_question(
    hypothesis: ResearchHypothesis,
    gaps: list[EvidenceGap],
    criteria: list[FalsificationCriterion],
) -> TestableResearchQuestion:
    notes = [
        "Seed context alone does not support the generated molecule.",
        "Generated-molecule evidence must refer to the exact generated structure.",
    ]
    if any(gap.gap_type == "missing_direct_experimental_result" for gap in gaps):
        notes.append("No direct evidence is linked to the generated molecule.")
    return _question(
        hypothesis,
        "generated-pathway-modulation",
        "Does the generated molecule preserve the desired pathway-modulation "
        "hypothesis compared with its seed context?",
        question_type="pathway_modulation",
        category="cellular pathway modulation",
        expected_supported=(
            "High-level evidence for the exact generated molecule aligns with the "
            "intended pathway-modulation hypothesis."
        ),
        expected_not_supported=(
            "High-level evidence for the exact generated molecule is negative, "
            "ambiguous, or only supports the seed context."
        ),
        ambiguity_notes=notes,
        gaps=gaps,
        criteria=criteria,
    )


def _contradiction_question(
    hypothesis: ResearchHypothesis,
    gaps: list[EvidenceGap],
    criteria: list[FalsificationCriterion],
) -> TestableResearchQuestion:
    return _question(
        hypothesis,
        "contradiction-resolution",
        "Is the apparent literature/model disagreement resolved by an assay result "
        "in the same endpoint context?",
        question_type="contradiction_resolution",
        category="expert review",
        expected_supported=(
            "Endpoint-scoped evidence clarifies which side of the contradiction is "
            "better supported."
        ),
        expected_not_supported=(
            "Endpoint-scoped evidence remains inconsistent or insufficient to resolve "
            "the contradiction."
        ),
        ambiguity_notes=[
            "Resolution changes hypothesis status but does not prove the broader hypothesis.",
            "Assay endpoint and provenance context must match before comparing records.",
        ],
        gaps=gaps,
        criteria=criteria,
    )


def _scaffold_or_developability_question(
    hypothesis: ResearchHypothesis,
    gaps: list[EvidenceGap],
    criteria: list[FalsificationCriterion],
) -> TestableResearchQuestion:
    return _question(
        hypothesis,
        "scaffold-developability-risk",
        "Is the scaffold-associated developability risk recurrent across related "
        "series members?",
        question_type="developability",
        category="developability triage",
        expected_supported=(
            "High-level developability context suggests the risk recurs across "
            "related series members."
        ),
        expected_not_supported=(
            "High-level developability context suggests the risk is isolated, "
            "ambiguous, or not linked to the series."
        ),
        ambiguity_notes=[
            "A series-level pattern does not validate or invalidate every analog.",
            "Risk interpretation should remain scoped to linked graph evidence.",
        ],
        gaps=gaps,
        criteria=criteria,
    )


def _generic_question(
    hypothesis: ResearchHypothesis,
    gaps: list[EvidenceGap],
    criteria: list[FalsificationCriterion],
) -> TestableResearchQuestion:
    return _question(
        hypothesis,
        "evidence-gap-closure",
        "Would high-level graph-backed evidence reduce uncertainty for this hypothesis?",
        question_type="evidence_gap_closure",
        category="expert review",
        expected_supported="Linked evidence reduces uncertainty for the scoped hypothesis.",
        expected_not_supported="Linked evidence remains missing, ambiguous, or contradictory.",
        ambiguity_notes=[
            "The hypothesis remains a planning object and should not be treated as evidence."
        ],
        gaps=gaps,
        criteria=criteria,
    )


def _question(
    hypothesis: ResearchHypothesis,
    question_key: str,
    question_text: str,
    *,
    question_type: str,
    category: str,
    expected_supported: str,
    expected_not_supported: str,
    ambiguity_notes: list[str],
    gaps: list[EvidenceGap],
    criteria: list[FalsificationCriterion],
) -> TestableResearchQuestion:
    text = " ".join([question_text, expected_supported, expected_not_supported, *ambiguity_notes])
    if detect_hypothesis_guardrail_violations(text):
        raise ValueError("research questions must remain high-level and non-procedural")
    return TestableResearchQuestion(
        question_id=_question_id(hypothesis.hypothesis_id, question_key),
        hypothesis_id=hypothesis.hypothesis_id,
        question_text=question_text,
        question_type=question_type,  # type: ignore[arg-type]
        high_level_validation_category=category,
        linked_entity_ids=_linked_entity_ids(hypothesis),
        required_context=_required_context(hypothesis, gaps, criteria),
        expected_observation_if_supported=expected_supported,
        expected_observation_if_not_supported=expected_not_supported,
        ambiguity_notes=ambiguity_notes,
        metadata={
            "question_key": question_key,
            "high_level_only": True,
            "evidence_gap_ids": [gap.gap_id for gap in gaps],
            "evidence_gap_types": sorted({gap.gap_type for gap in gaps}),
            "falsification_criterion_ids": [
                criterion.criterion_id for criterion in criteria
            ],
            "supporting_relation_ids": hypothesis.supporting_relation_ids,
            "contradicting_relation_ids": hypothesis.contradicting_relation_ids,
        },
    )


def _question_id(hypothesis_id: str, question_key: str) -> str:
    return f"question:{uuid5(NAMESPACE_URL, hypothesis_id + ':' + question_key).hex[:16]}"


def _linked_entity_ids(hypothesis: ResearchHypothesis) -> list[str]:
    return sorted(
        {
            *hypothesis.disease_entity_ids,
            *hypothesis.target_entity_ids,
            *hypothesis.molecule_entity_ids,
            *hypothesis.generated_molecule_entity_ids,
            *hypothesis.scaffold_entity_ids,
            *hypothesis.mechanism_entity_ids,
        }
    )


def _required_context(
    hypothesis: ResearchHypothesis,
    gaps: list[EvidenceGap],
    criteria: list[FalsificationCriterion],
) -> list[str]:
    context: list[str] = [
        "Referenced graph entities and relations",
        "Source artifact provenance",
    ]
    if gaps:
        context.append("Evidence-gap summary")
    if criteria:
        context.append("Decision-focused falsification criteria")
    if hypothesis.contradicting_relation_ids:
        context.append("Contradiction relation context")
    return context
