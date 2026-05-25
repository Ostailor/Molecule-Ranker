from __future__ import annotations

from molecule_ranker.developability.synthesizability import (
    FALLBACK_METHOD,
    assess_synthesizability,
    compute_complexity_flags,
    compute_sa_score,
)


def test_simple_molecule_low_complexity():
    assessment = assess_synthesizability("CCO", {})

    assert assessment.sa_score is not None
    assert 0.0 <= assessment.sa_score <= 1.0
    assert assessment.estimated_complexity == "low"
    assert assessment.risk_level == "low"
    assert assessment.starting_material_availability == "unknown"
    assert assessment.retrosynthesis_available is False
    assert assessment.route_count is None


def test_macrocycle_and_high_stereochemistry_are_high_complexity_flags():
    macrocycle_flags = compute_complexity_flags("C1CCCCCCCCCCC1")
    stereo_flags = compute_complexity_flags("C[C@H](O)[C@H](O)[C@H](O)[C@H](O)C")
    assessment = assess_synthesizability("C1CCCCCCCCCCC1", {"force_descriptor_fallback": True})

    assert "macrocycle_present" in macrocycle_flags
    assert "many_stereocenters" in stereo_flags
    assert assessment.estimated_complexity == "high"
    assert assessment.risk_level == "high"


def test_fallback_mode_is_clearly_labeled():
    assessment = assess_synthesizability("CCO", {"force_descriptor_fallback": True})

    assert assessment.method == FALLBACK_METHOD
    assert assessment.confidence < 0.5
    assert assessment.metadata["calculation_source"] == "rdkit_descriptors"
    assert "complexity_flags" in assessment.metadata


def test_compute_sa_score_is_bounded_or_unavailable():
    score = compute_sa_score("CCO")

    assert score is None or 0.0 <= score <= 1.0


def test_no_synthesis_instruction_text_appears_in_output():
    assessment = assess_synthesizability("CCO", {"force_descriptor_fallback": True})
    text = assessment.model_dump_json().lower()

    forbidden_terms = [
        "synthesis route",
        "reagent",
        "reaction condition",
        "temperature",
        "catalyst",
        "procedure",
        "protocol",
    ]
    assert not any(term in text for term in forbidden_terms)
