from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from molecule_ranker.schemas import (
    AgentTrace,
    Disease,
    EvidenceItem,
    MoleculeCandidate,
    RankingRun,
    ScoreBreakdown,
    Target,
)


def _evidence(confidence: float = 0.8) -> EvidenceItem:
    return EvidenceItem(
        source="Open Targets",
        source_record_id="record-1",
        title="Transparent source evidence",
        url=None,
        evidence_type="mechanistic",
        summary="A concise evidence summary for a research hypothesis.",
        confidence=confidence,
        metadata={"query": "mocked test payload"},
    )


def _score(**overrides: Any) -> ScoreBreakdown:
    payload: dict[str, Any] = {
        "disease_target_relevance": 0.8,
        "molecule_target_evidence": 0.7,
        "mechanism_plausibility": 0.6,
        "clinical_precedence": 0.5,
        "safety_prior": 0.4,
        "data_quality": 0.9,
        "novelty_or_repurposing_value": 0.3,
        "final_score": 0.65,
        "confidence": 0.75,
        "explanation": "Transparent weighted evidence score.",
    }
    payload.update(overrides)
    return ScoreBreakdown(**payload)


def test_core_schema_creation_and_json_serialization():
    disease = Disease(
        input_name="PD",
        canonical_name="Parkinson disease",
        synonyms=["Parkinson's disease"],
        identifiers={"mondo": "MONDO:0005180"},
        description="Neurodegenerative disease test record.",
    )
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        identifiers={"ensembl": "ENSG_TEST", "uniprot": "P27338"},
        target_class="protein_coding",
        disease_relevance_score=0.8,
        evidence=[_evidence()],
        mechanism="Dopaminergic pathway relevance.",
        metadata={"tractability": [{"modality": "SM", "value": True}]},
    )
    score = ScoreBreakdown(
        disease_target_relevance=0.8,
        molecule_target_evidence=0.7,
        mechanism_plausibility=0.6,
        clinical_precedence=0.5,
        safety_prior=0.4,
        data_quality=0.9,
        novelty_or_repurposing_value=0.3,
        final_score=0.65,
        confidence=0.75,
        explanation="Transparent weighted evidence score.",
    )
    candidate = MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        development_status="approved_existing_molecule",
        mechanism_of_action="Selective MAO-B inhibition.",
        evidence=[_evidence()],
        chemical_metadata={"canonical_smiles": "COC1=CC=CC=C1"},
        score=score.final_score,
        score_breakdown=score,
        warnings=["Requires experimental validation."],
    )
    run = RankingRun(
        disease=disease,
        targets=[target],
        candidates=[candidate],
        traces=[
            AgentTrace(
                agent_name="EvidenceScoringAgent",
                input_summary="One molecule record",
                output_summary="One scored candidate",
                warnings=[],
                metadata={"top": 1},
            )
        ],
        limitations=["Mocked public-source evidence."],
    )

    payload = run.model_dump(mode="json")
    assert payload["disease"]["canonical_name"] == "Parkinson disease"
    assert payload["targets"][0]["identifiers"]["uniprot"] == "P27338"
    assert payload["targets"][0]["target_class"] == "protein_coding"
    assert payload["candidates"][0]["chemical_metadata"]["canonical_smiles"] == "COC1=CC=CC=C1"
    assert payload["candidates"][0]["origin"] == "existing"
    assert payload["candidates"][0]["score_breakdown"]["final_score"] == 0.65
    assert '"RankingRun"' not in run.model_dump_json()


def test_existing_candidates_default_origin_existing():
    candidate = MoleculeCandidate(
        name="Existing candidate",
        molecule_type="small_molecule",
    )

    assert candidate.origin == "existing"
    assert candidate.generation_metadata == {}


def test_generated_molecule_candidate_contract_rejects_fake_evidence():
    candidate = MoleculeCandidate(
        name="Generated candidate",
        molecule_type="generated",
        origin="generated",
        direct_evidence_available=True,
        generation_metadata={"generated_id": "gen-1"},
    )

    assert candidate.origin == "generated"
    assert candidate.direct_evidence_available is False
    assert candidate.evidence == []

    with pytest.raises(ValidationError) as error:
        MoleculeCandidate(
            name="Generated with fake evidence",
            molecule_type="generated",
            origin="generated",
            evidence=[_evidence()],
        )

    assert "must not contain EvidenceItem" in str(error.value)


@pytest.mark.parametrize(
    ("model_factory", "field_name"),
    [
        (lambda: _evidence(confidence=1.1), "confidence"),
        (
            lambda: Target(
                symbol="SNCA",
                name=None,
                disease_relevance_score=-0.1,
                evidence=[],
                mechanism=None,
            ),
            "disease_relevance_score",
        ),
        (lambda: _score(final_score=1.2), "final_score"),
        (
            lambda: MoleculeCandidate(
                name="Candidate",
                molecule_type="small_molecule",
                identifiers={},
                known_targets=[],
                development_status=None,
                mechanism_of_action=None,
                evidence=[],
                score=-0.01,
                score_breakdown=None,
                warnings=[],
            ),
            "score",
        ),
    ],
)
def test_zero_to_one_scores_are_validated(model_factory, field_name):
    with pytest.raises(ValidationError) as error:
        model_factory()

    assert field_name in str(error.value)
