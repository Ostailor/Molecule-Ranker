from __future__ import annotations

from molecule_ranker.hypotheses.falsification import build_falsification_criteria
from molecule_ranker.hypotheses.guardrails import detect_hypothesis_guardrail_violations
from molecule_ranker.hypotheses.schemas import ResearchHypothesis


def test_molecule_target_falsification_criteria_are_decision_focused() -> None:
    criteria = build_falsification_criteria(
        _hypothesis(
            hypothesis_type="molecule_target",
            target_entity_ids=["target:MAOB"],
            molecule_entity_ids=["molecule:seed"],
        )
    )
    text = " ".join(criterion.criterion_text for criterion in criteria)

    assert len(criteria) >= 3
    assert "negative" in text
    assert "selectivity" in text
    assert "increase support but not prove clinical efficacy" in text
    assert {criterion.decision_impact for criterion in criteria} >= {
        "decrease_priority",
        "increase_priority",
    }
    assert _all_high_level(criteria)


def test_generated_molecule_falsification_criteria_distinguish_seed_from_exact_structure() -> None:
    criteria = build_falsification_criteria(
        _hypothesis(
            hypothesis_type="generated_molecule",
            molecule_entity_ids=["molecule:seed"],
            generated_molecule_entity_ids=["generated_molecule:gen-1"],
        )
    )
    text = " ".join(criterion.criterion_text for criterion in criteria)

    assert "exact-structure" in text
    assert "Seed-molecule activity alone does not support the generated molecule" in text
    assert "Critical developability risk" in text
    assert any(criterion.decision_impact == "retire_hypothesis" for criterion in criteria)
    assert _all_high_level(criteria)


def test_scaffold_series_falsification_criteria_handle_series_level_uncertainty() -> None:
    criteria = build_falsification_criteria(
        _hypothesis(
            hypothesis_type="scaffold_series",
            scaffold_entity_ids=["scaffold:core-a"],
            molecule_entity_ids=["molecule:a", "molecule:b"],
        )
    )
    text = " ".join(criterion.criterion_text for criterion in criteria)

    assert "Repeated negative results across series members" in text
    assert "does not validate all analogs" in text
    assert any(criterion.decision_impact == "retire_hypothesis" for criterion in criteria)
    assert _all_high_level(criteria)


def test_contradiction_falsification_criteria_change_status_without_proving_side() -> None:
    criteria = build_falsification_criteria(
        _hypothesis(
            hypothesis_type="assay_contradiction",
            contradicting_relation_ids=["rel:negative"],
        )
    )
    text = " ".join(criterion.criterion_text for criterion in criteria)

    assert "orthogonal result resolving one side of the contradiction changes status" in text
    assert "does not prove the broader hypothesis" in text
    assert any(criterion.decision_impact == "require_more_data" for criterion in criteria)
    assert _all_high_level(criteria)


def _hypothesis(
    *,
    hypothesis_type: str,
    target_entity_ids: list[str] | None = None,
    molecule_entity_ids: list[str] | None = None,
    generated_molecule_entity_ids: list[str] | None = None,
    scaffold_entity_ids: list[str] | None = None,
    contradicting_relation_ids: list[str] | None = None,
) -> ResearchHypothesis:
    return ResearchHypothesis(
        hypothesis_id=f"hypothesis:{hypothesis_type}",
        hypothesis_type=hypothesis_type,  # type: ignore[arg-type]
        title="Hypothesis: falsification review",
        statement="Hypothesis for review: graph-backed context needs decision criteria.",
        target_entity_ids=target_entity_ids or [],
        molecule_entity_ids=molecule_entity_ids or [],
        generated_molecule_entity_ids=generated_molecule_entity_ids or [],
        scaffold_entity_ids=scaffold_entity_ids or [],
        supporting_relation_ids=["rel:support"],
        contradicting_relation_ids=contradicting_relation_ids or [],
        source_artifact_ids=["artifact:kg"],
    )


def _all_high_level(criteria) -> bool:
    return all(
        criterion.not_lab_protocol
        and criterion.metadata["high_level_only"] is True
        and criterion.metadata["decision_focused"] is True
        and not detect_hypothesis_guardrail_violations(criterion.criterion_text)
        for criterion in criteria
    )
