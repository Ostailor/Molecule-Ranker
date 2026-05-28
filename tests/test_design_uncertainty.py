from __future__ import annotations

from molecule_ranker.design.oracles import OracleResult, OracleStackResult
from molecule_ranker.design.uncertainty import ApplicabilityDomain, UncertaintyEstimator
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    NoveltyAssessment,
    SeedMolecule,
)
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate


def _seed(smiles: str = "CCOc1ccccc1") -> SeedMolecule:
    return SeedMolecule(
        name="Seed A",
        canonical_smiles=smiles,
        identifiers={"chembl": "CHEMBL_A"},
        known_targets=["MAOB"],
        source_candidate_name="Seed A",
        evidence_count=3,
        best_evidence_confidence=0.9,
        target_relevance_score=0.84,
        seed_selection_reason="Evidence-backed seed.",
        metadata={"literature_support_score": 0.6},
    )


def _known_candidate(smiles: str = "CCOc1ccccc1") -> MoleculeCandidate:
    return MoleculeCandidate(
        name="Known active context",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL_KNOWN"},
        known_targets=["MAOB"],
        chemical_metadata={"canonical_smiles": smiles},
        evidence=[
            EvidenceItem(
                source="ChEMBL",
                source_record_id="CHEMBL-MECH",
                title="Molecule target mechanism",
                evidence_type="mechanism",
                summary="Retrieved molecule-target evidence.",
                confidence=0.9,
            )
        ],
    )


def _generated(
    smiles: str,
    *,
    novelty_similarity: float = 0.4,
    metadata: dict | None = None,
) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id="gen-1",
        smiles=smiles,
        canonical_smiles=smiles,
        generation_method="fragment_grower",
        parent_seed_ids=["CHEMBL_A"],
        conditioned_targets=["MAOB"],
        objective_id="objective-1",
        generation_round=1,
        descriptors={"molecular_weight": 220.0, "logp": 2.5, "tpsa": 35.0},
        fingerprints={},
        validation=ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
        ),
        novelty=NoveltyAssessment(
            duplicate_of_existing=False,
            duplicate_of_generated=False,
            max_similarity_to_existing=0.35,
            max_similarity_to_seed=novelty_similarity,
            novelty_class="novel_analog",
        ),
        warnings=["in_silico_hypothesis_only"],
        metadata=metadata or {},
    )


def _oracle_stack(scores: list[float]) -> OracleStackResult:
    oracles = [
        OracleResult(
            oracle_name=f"oracle_{index}",
            score=score,
            confidence=0.6,
            risk_flags=[],
            explanation="test oracle",
        )
        for index, score in enumerate(scores)
    ]
    return OracleStackResult(
        generated_id="gen-1",
        experiment_worthiness_score=sum(scores) / len(scores),
        confidence=0.6,
        component_scores={},
        risk_flags=[],
        oracles=oracles,
        explanation="Experiment worthiness only; not predicted efficacy or binding.",
    )


def test_far_molecule_marked_out_of_domain() -> None:
    result = UncertaintyEstimator().estimate(
        candidate=_generated("CCCCCCCCCCCCCCCC", novelty_similarity=0.05),
        seeds=[_seed()],
        known_candidates=[_known_candidate()],
        oracle_result=_oracle_stack([0.5, 0.55, 0.6]),
    )

    assert result.applicability_domain == "out_of_domain"
    assert result.chemical_space_uncertainty > 0.7
    assert "out_of_domain" in result.risk_flags


def test_oracle_disagreement_increases_uncertainty() -> None:
    estimator = UncertaintyEstimator()
    agreement = estimator.estimate(
        candidate=_generated("CCOc1ccccn1"),
        seeds=[_seed()],
        known_candidates=[_known_candidate()],
        oracle_result=_oracle_stack([0.55, 0.57, 0.56]),
    )
    disagreement = estimator.estimate(
        candidate=_generated("CCOc1ccccn1"),
        seeds=[_seed()],
        known_candidates=[_known_candidate()],
        oracle_result=_oracle_stack([0.1, 0.95, 0.45]),
    )

    assert disagreement.oracle_disagreement > agreement.oracle_disagreement
    assert disagreement.overall_uncertainty > agreement.overall_uncertainty


def test_no_direct_evidence_lowers_confidence() -> None:
    no_evidence = UncertaintyEstimator().estimate(
        candidate=_generated("CCOc1ccccn1"),
        seeds=[_seed()],
        known_candidates=[_known_candidate()],
        oracle_result=_oracle_stack([0.6, 0.65, 0.7]),
    )
    imported_context = UncertaintyEstimator().estimate(
        candidate=_generated(
            "CCOc1ccccn1",
            metadata={"direct_experimental_evidence": True, "literature_context_score": 0.7},
        ),
        seeds=[_seed()],
        known_candidates=[_known_candidate()],
        oracle_result=_oracle_stack([0.6, 0.65, 0.7]),
    )

    assert no_evidence.evidence_uncertainty > imported_context.evidence_uncertainty
    assert no_evidence.confidence < imported_context.confidence


def test_uncertainty_increases_active_learning_score_but_not_efficacy_claim() -> None:
    low_uncertainty = UncertaintyEstimator().estimate(
        candidate=_generated("CCOc1ccccn1", novelty_similarity=0.65),
        seeds=[_seed()],
        known_candidates=[_known_candidate()],
        oracle_result=_oracle_stack([0.55, 0.56, 0.57]),
    )
    interesting = UncertaintyEstimator().estimate(
        candidate=_generated("CCOc1ccccn1", novelty_similarity=0.35),
        seeds=[_seed()],
        known_candidates=[_known_candidate()],
        oracle_result=_oracle_stack([0.25, 0.75, 0.55]),
    )

    assert interesting.active_learning_value > low_uncertainty.active_learning_value
    assert interesting.uncertainty_class == "interesting_uncertainty"
    explanation = interesting.explanation.lower()
    assert "active-learning" in explanation
    assert "not predicted efficacy" in explanation
    assert "not predicted binding" in explanation


def test_applicability_domain_literal_values() -> None:
    assert set(ApplicabilityDomain.__args__) == {
        "in_domain",
        "near_domain",
        "out_of_domain",
        "unknown",
    }
