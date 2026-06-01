from __future__ import annotations

from molecule_ranker.hypotheses.evidence_gap import analyze_hypothesis_evidence_gaps
from molecule_ranker.hypotheses.falsification import build_falsification_criteria
from molecule_ranker.hypotheses.guardrails import detect_hypothesis_guardrail_violations
from molecule_ranker.hypotheses.questions import plan_research_questions
from molecule_ranker.hypotheses.schemas import ResearchHypothesis
from molecule_ranker.knowledge_graph.schemas import GraphEntity, GraphRelation


def test_high_level_questions_generated_with_expected_observations() -> None:
    hypothesis = _hypothesis(
        "molecule_target",
        target_entity_ids=["target:MAOB"],
        molecule_entity_ids=["molecule:seed"],
    )
    gaps = analyze_hypothesis_evidence_gaps(
        hypothesis,
        entities=_entities("target:MAOB", "molecule:seed"),
        relations=[_relation("rel:support", "molecule:seed", "targets", "target:MAOB")],
    )
    criteria = build_falsification_criteria(hypothesis)

    questions = plan_research_questions(hypothesis, evidence_gaps=gaps, criteria=criteria)

    assert questions
    first = questions[0]
    assert first.question_text.startswith("Is the candidate associated with target engagement")
    assert first.expected_observation_if_supported
    assert first.expected_observation_if_not_supported
    assert first.ambiguity_notes
    assert first.metadata["evidence_gap_ids"]
    assert first.metadata["falsification_criterion_ids"]


def test_questions_do_not_include_protocol_details() -> None:
    hypothesis = _hypothesis(
        "molecule_target",
        target_entity_ids=["target:MAOB"],
        molecule_entity_ids=["molecule:seed"],
    )

    questions = plan_research_questions(hypothesis)

    assert questions
    assert all(question.forbidden_detail_check is True for question in questions)
    assert not detect_hypothesis_guardrail_violations(
        " ".join(
            item
            for question in questions
            for item in [
                question.question_text,
                question.expected_observation_if_supported,
                question.expected_observation_if_not_supported,
                *question.ambiguity_notes,
            ]
        )
    )


def test_generated_molecule_questions_preserve_no_direct_evidence_warning() -> None:
    hypothesis = _hypothesis(
        "generated_molecule",
        molecule_entity_ids=["molecule:seed"],
        generated_molecule_entity_ids=["generated_molecule:gen-1"],
    )
    gaps = analyze_hypothesis_evidence_gaps(
        hypothesis,
        entities=_entities("molecule:seed", "generated_molecule:gen-1"),
        relations=[
            _relation(
                "rel:support",
                "generated_molecule:gen-1",
                "generated_from",
                "molecule:seed",
                relation_type="generated_lineage",
            )
        ],
    )

    questions = plan_research_questions(hypothesis, evidence_gaps=gaps)
    text = " ".join(question.question_text for question in questions)
    notes = " ".join(note for question in questions for note in question.ambiguity_notes)

    assert "preserve the desired pathway-modulation hypothesis" in text
    assert "No direct evidence is linked to the generated molecule" in notes
    assert any(
        "missing_direct_experimental_result" in question.metadata["evidence_gap_types"]
        for question in questions
    )


def test_contradiction_questions_map_to_contradictions() -> None:
    hypothesis = _hypothesis(
        "assay_contradiction",
        molecule_entity_ids=["molecule:seed"],
        contradicting_relation_ids=["rel:negative"],
        assay_result_ids=["assay_result:negative"],
    )
    gaps = analyze_hypothesis_evidence_gaps(
        hypothesis,
        entities=_entities("molecule:seed", "assay_result:negative"),
        relations=[
            _relation(
                "rel:negative",
                "molecule:seed",
                "produced_result",
                "assay_result:negative",
                relation_type="experimental",
                direction="contradictory",
            )
        ],
    )
    criteria = build_falsification_criteria(hypothesis)

    questions = plan_research_questions(hypothesis, evidence_gaps=gaps, criteria=criteria)
    contradiction = questions[0]

    assert "literature/model disagreement" in contradiction.question_text
    assert contradiction.question_type == "contradiction_resolution"
    assert contradiction.metadata["contradicting_relation_ids"] == ["rel:negative"]
    assert "contradictory_results" in contradiction.metadata["evidence_gap_types"]


def _hypothesis(
    hypothesis_type: str,
    *,
    target_entity_ids: list[str] | None = None,
    molecule_entity_ids: list[str] | None = None,
    generated_molecule_entity_ids: list[str] | None = None,
    contradicting_relation_ids: list[str] | None = None,
    assay_result_ids: list[str] | None = None,
) -> ResearchHypothesis:
    return ResearchHypothesis(
        hypothesis_id=f"hypothesis:{hypothesis_type}",
        hypothesis_type=hypothesis_type,  # type: ignore[arg-type]
        title="Hypothesis: question planning",
        statement="Hypothesis for review: graph-backed context needs research questions.",
        target_entity_ids=target_entity_ids or [],
        molecule_entity_ids=molecule_entity_ids or [],
        generated_molecule_entity_ids=generated_molecule_entity_ids or [],
        supporting_relation_ids=["rel:support"],
        contradicting_relation_ids=contradicting_relation_ids or [],
        source_artifact_ids=["artifact:kg"],
        assay_result_ids=assay_result_ids or [],
    )


def _entity(entity_id: str) -> GraphEntity:
    return GraphEntity(entity_id=entity_id, entity_type=entity_id.split(":", 1)[0], name=entity_id)


def _entities(*entity_ids: str) -> list[GraphEntity]:
    return [_entity(entity_id) for entity_id in entity_ids]


def _relation(
    relation_id: str,
    subject: str,
    predicate: str,
    object_id: str,
    *,
    relation_type: str = "evidence_backed",
    direction: str | None = "supportive",
) -> GraphRelation:
    return GraphRelation(
        relation_id=relation_id,
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=object_id,
        relation_type=relation_type,
        confidence=0.8,
        direction=direction,
        source_artifact_ids=[f"artifact:{relation_id}"],
        source_record_ids=[f"record:{relation_id}"],
    )
