from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.agents.base import AgentExecutionError, PipelineContext
from molecule_ranker.agents.developability_assessment import DevelopabilityAssessmentAgent
from molecule_ranker.developability.schemas import ChemistryAlert, DevelopabilityAssessment
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    GenerationRun,
    NoveltyAssessment,
)
from molecule_ranker.schemas import GeneratedMoleculeHypothesis, MoleculeCandidate


def _validation(**overrides: Any) -> ChemicalValidationResult:
    payload: dict[str, Any] = {
        "valid_rdkit_mol": True,
        "sanitization_ok": True,
        "canonicalization_ok": True,
        "allowed_elements_ok": True,
        "descriptor_bounds_ok": True,
        "pains_or_alerts": [],
        "rejection_reasons": [],
        "metadata": {"rdkit_version": "test"},
    }
    payload.update(overrides)
    return ChemicalValidationResult(**payload)


def _novelty() -> NoveltyAssessment:
    return NoveltyAssessment(
        duplicate_of_existing=False,
        duplicate_of_generated=False,
        max_similarity_to_existing=0.4,
        nearest_existing_name="seed",
        max_similarity_to_seed=0.6,
        nearest_seed_name="seed",
        novelty_class="novel_analog",
        metadata={},
    )


def _score() -> GeneratedMoleculeScoreBreakdown:
    return GeneratedMoleculeScoreBreakdown(
        target_conditioning_score=0.8,
        seed_evidence_score=0.7,
        novelty_score=0.6,
        diversity_score=0.6,
        chemical_validity_score=1.0,
        property_profile_score=0.7,
        literature_context_score=0.0,
        final_generation_score=0.67,
        confidence=0.5,
        explanation="Generated hypothesis scored from in-silico generation context.",
    )


def _generated(**overrides: Any) -> GeneratedMolecule:
    payload: dict[str, Any] = {
        "generated_id": "gen-1",
        "smiles": "CCO",
        "canonical_smiles": "CCO",
        "selfies": "[C][C][O]",
        "inchi_key": None,
        "generation_method": "selfies_mutation",
        "parent_seed_ids": ["seed-1"],
        "conditioned_targets": ["TEST"],
        "objective_id": "objective-1",
        "generation_round": 1,
        "descriptors": {"molecular_weight": 46.0},
        "fingerprints": {},
        "validation": _validation(),
        "novelty": _novelty(),
        "diversity_cluster": "cluster-1",
        "generation_score": 0.67,
        "score_breakdown": _score(),
        "warnings": [],
        "metadata": {},
    }
    payload.update(overrides)
    return GeneratedMolecule(**payload)


def _hypothesis(name: str = "gen-1") -> GeneratedMoleculeHypothesis:
    return GeneratedMoleculeHypothesis(
        name=name,
        canonical_smiles="CCO",
        target_symbol="TEST",
        generation_score=0.67,
        min_seed_similarity=0.4,
        max_seed_similarity=0.6,
        mean_seed_similarity=0.5,
        descriptors={},
    )


def _critical_assessment(molecule_id: str = "gen-1") -> DevelopabilityAssessment:
    return DevelopabilityAssessment(
        molecule_id=molecule_id,
        molecule_name=molecule_id,
        origin="generated",
        canonical_smiles="CCO",
        physchem=None,
        alerts=[
            ChemistryAlert(
                alert_id="critical-test-alert",
                alert_type="toxicophore",
                alert_name="Critical test alert",
                severity="critical",
                matched_smarts=None,
                description="Critical computational risk flag.",
                source="test",
                metadata={},
            )
        ],
        admet_predictions=[],
        synthesizability=None,
        docking=[],
        overall_developability_score=0.1,
        risk_summary="critical developability risk by computational triage.",
        risk_level="critical",
        confidence=0.4,
        recommendation="reject",
        warnings=["Critical risk flag requires expert review."],
        metadata={"limitations": []},
    )


def test_agent_assesses_existing_candidates():
    candidate = MoleculeCandidate(
        name="Nitro aromatic",
        molecule_type="small_molecule",
        chemical_metadata={"canonical_smiles": "O=[N+]([O-])c1ccccc1"},
        warnings=["Black Box Warning from retrieved source"],
    )
    context = PipelineContext(disease_input="test", candidates=[candidate])

    updated = DevelopabilityAssessmentAgent().run(context)

    assessment = updated.candidates[0].developability_assessment
    assert assessment is not None
    assert assessment.structure_available is True
    assert updated.candidates[0].chemical_metadata["developability_assessment"]
    assert updated.config["developability_run"].assessed_existing_count == 1
    assert updated.traces[-1].metadata["assessed_existing_count"] == 1
    assert "expert review" in " ".join(updated.candidates[0].warnings).lower()


def test_agent_assesses_existing_candidate_from_inchi_structure():
    candidate = MoleculeCandidate(
        name="Ethanol",
        molecule_type="small_molecule",
        chemical_metadata={"inchi": "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"},
    )
    context = PipelineContext(disease_input="test", candidates=[candidate])

    updated = DevelopabilityAssessmentAgent().run(context)

    assessment = updated.candidates[0].developability_assessment
    assert assessment is not None
    assert assessment.structure_available is True
    assert assessment.canonical_smiles == "CCO"
    assert updated.config["developability_run"].assessments[0].physchem is not None


def test_agent_assesses_generated_molecules():
    generated = _generated()
    run = GenerationRun(generated=[generated], retained=[generated], rejected=[])
    context = PipelineContext(
        disease_input="test",
        generated_candidates=[_hypothesis()],
        config={"generation_run": run},
    )

    updated = DevelopabilityAssessmentAgent().run(context)

    updated_run = updated.config["generation_run"]
    retained = updated_run.retained[0]
    assert retained.developability_assessment is not None
    assert retained.metadata["developability_assessment"]
    assert updated.generated_candidates[0].developability_assessment is not None
    assert updated.generated_candidates[0].trace["developability_assessment"]
    assert updated.config["developability_run"].assessed_generated_count == 1


def test_agent_assesses_preexisting_rejected_generated_molecules():
    generated = _generated(
        generated_id="gen-rejected",
        validation=_validation(rejection_reasons=["distant"]),
    )
    run = GenerationRun(generated=[generated], retained=[], rejected=[generated])
    context = PipelineContext(
        disease_input="test",
        generated_candidates=[],
        config={"generation_run": run},
    )

    updated = DevelopabilityAssessmentAgent().run(context)

    updated_run = updated.config["generation_run"]
    rejected = updated_run.rejected[0]
    assert rejected.developability_assessment is not None
    assert rejected.metadata["developability_assessment"]
    assert updated_run.generated[0].developability_assessment is not None
    assert updated.config["developability_run"].assessed_generated_count == 1
    assert updated.config["developability_run"].rejected_count == 1


def test_report_only_does_not_remove_candidates():
    candidate = MoleculeCandidate(name="No structure", molecule_type="small_molecule")
    context = PipelineContext(
        disease_input="test",
        candidates=[candidate],
        config={"developability_filter_mode": "report_only"},
    )

    updated = DevelopabilityAssessmentAgent().run(context)

    assert len(updated.candidates) == 1
    assert updated.candidates[0].developability_assessment is not None
    assert updated.config["developability_run"].rejected_count == 0


def test_filter_generated_only_rejects_critical_generated_molecules(monkeypatch):
    generated = _generated()
    run = GenerationRun(generated=[generated], retained=[generated], rejected=[])
    agent = DevelopabilityAssessmentAgent()

    def critical(*args: Any, **kwargs: Any) -> DevelopabilityAssessment:
        return _critical_assessment()

    monkeypatch.setattr(agent, "_build_structured_assessment", critical)
    context = PipelineContext(
        disease_input="test",
        generated_candidates=[_hypothesis()],
        config={
            "generation_run": run,
            "developability_filter_mode": "filter_generated_only",
        },
    )

    updated = agent.run(context)

    updated_run = updated.config["generation_run"]
    assert updated_run.retained == []
    assert len(updated_run.rejected) == 1
    assert "developability_filter_failed" in updated_run.rejected[0].validation.rejection_reasons
    assert updated.generated_candidates == []
    assert updated.config["developability_run"].rejected_count == 1


def test_strict_mode_fails_on_assessment_failure(monkeypatch):
    candidate = MoleculeCandidate(
        name="Ethanol",
        molecule_type="small_molecule",
        chemical_metadata={"canonical_smiles": "CCO"},
    )
    agent = DevelopabilityAssessmentAgent()

    def fail(*args: Any, **kwargs: Any) -> DevelopabilityAssessment:
        raise ValueError("forced developability failure")

    monkeypatch.setattr(agent, "_build_structured_assessment", fail)
    context = PipelineContext(
        disease_input="test",
        candidates=[candidate],
        config={"strict_developability": True},
    )

    with pytest.raises(AgentExecutionError, match="forced developability failure"):
        agent.run(context)
