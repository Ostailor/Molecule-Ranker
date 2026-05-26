from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.experiments.linking import LinkingConfig, link_assay_result
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
)
from molecule_ranker.review.schemas import ReviewItem
from molecule_ranker.schemas import MoleculeCandidate


def _context() -> AssayContext:
    return AssayContext(
        assay_context_id="context-1",
        assay_name="Binding screen",
        assay_type="biochemical",
        target_symbol="MAOB",
        endpoint=AssayEndpoint(
            endpoint_id="endpoint-binding",
            name="binding_affinity",
            endpoint_category="potency",
            unit="nM",
            directionality="lower_is_better",
        ),
    )


def _result(**overrides: Any) -> AssayResult:
    payload: dict[str, Any] = {
        "result_id": "result-1",
        "candidate_id": "CHEMBL887",
        "candidate_name": "Rasagiline",
        "candidate_origin": "existing",
        "canonical_smiles": "C#CCN1CCC2=CC=CC=C21",
        "inchi_key": "RUYUTDCTDCBNSZ-UHFFFAOYSA-N",
        "target_symbol": "MAOB",
        "assay_context": _context(),
        "measured_value": 12.5,
        "measured_value_numeric": 12.5,
        "unit": "nM",
        "outcome_label": "positive",
        "activity_direction": "active",
        "confidence": 0.8,
        "qc_status": "passed",
        "source": "csv_import",
        "imported_at": datetime(2026, 1, 2, tzinfo=UTC),
    }
    payload.update(overrides)
    return AssayResult(**payload)


def _candidate(name: str = "Rasagiline", chembl: str = "CHEMBL887") -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": chembl},
        chemical_metadata={
            "canonical_smiles": "C#CCN1CCC2=CC=CC=C21",
            "inchi_key": "RUYUTDCTDCBNSZ-UHFFFAOYSA-N",
        },
    )


def _review_item() -> ReviewItem:
    return ReviewItem(
        review_item_id="review-item-1",
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        priority_bucket="high_priority",
        review_status="pending",
    )


def _generated(**overrides: Any) -> GeneratedMolecule:
    payload: dict[str, Any] = {
        "generated_id": "gen-1",
        "smiles": "C#CCN(C)CCc1ccccn1",
        "canonical_smiles": "C#CCN(C)CCc1ccccn1",
        "inchi_key": "GENERATED-INCHI-KEY",
        "generation_method": "selfies_mutation",
        "parent_seed_ids": ["CHEMBL887"],
        "conditioned_targets": ["MAOB"],
        "objective_id": "objective-1",
        "generation_round": 1,
        "validation": ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
        ),
    }
    payload.update(overrides)
    return GeneratedMolecule(**payload)


def test_links_existing_candidate_by_candidate_id_first():
    linked = link_assay_result(
        _result(candidate_id="CHEMBL887", inchi_key="DIFFERENT"),
        candidates=[_candidate()],
        review_items=[_review_item()],
    )

    assert linked.metadata["linked_candidate_id"] == "CHEMBL887"
    assert linked.metadata["linked_review_item_id"] == "review-item-1"
    assert linked.metadata["link_method"] == "candidate_id"
    assert linked.metadata["link_confidence"] == 1.0
    assert linked.metadata["matched_identifiers"]["candidate_id"] == "CHEMBL887"


def test_links_existing_candidate_by_inchi_key_when_id_absent():
    linked = link_assay_result(
        _result(candidate_id=None, candidate_name="Unlabeled", canonical_smiles=None),
        candidates=[_candidate()],
    )

    assert linked.metadata["linked_candidate_id"] == "CHEMBL887"
    assert linked.metadata["link_method"] == "inchi_key"
    assert linked.metadata["matched_identifiers"]["inchi_key"] == "RUYUTDCTDCBNSZ-UHFFFAOYSA-N"


def test_links_generated_molecule_by_generated_id_and_marks_direct_evidence_only_for_result():
    linked = link_assay_result(
        _result(
            candidate_id="gen-1",
            candidate_name="Generated analog 1",
            candidate_origin="generated",
            canonical_smiles=None,
            inchi_key=None,
        ),
        generated_molecules=[_generated()],
    )

    assert linked.metadata["linked_candidate_id"] == "gen-1"
    assert linked.metadata["link_method"] == "generated_id"
    assert linked.metadata["generated_direct_experimental_evidence"] is True
    assert linked.metadata["direct_evidence_result_id"] == "result-1"


def test_ambiguous_name_match_is_rejected_without_silent_linking():
    linked = link_assay_result(
        _result(
            candidate_id=None,
            inchi_key=None,
            canonical_smiles=None,
            candidate_name="Rasagiline",
        ),
        candidates=[
            _candidate(name="Rasagiline", chembl="CHEMBL887"),
            _candidate(name="Rasagiline", chembl="CHEMBL999"),
        ],
    )

    assert linked.metadata["linked_candidate_id"] is None
    assert linked.metadata["link_method"] == "ambiguous"
    assert "ambiguous" in linked.metadata["ambiguity_warning"]


def test_seed_result_does_not_validate_generated_analog():
    linked = link_assay_result(
        _result(candidate_id="CHEMBL887", candidate_name="Rasagiline", candidate_origin="existing"),
        candidates=[_candidate()],
        generated_molecules=[_generated()],
    )

    assert linked.metadata["linked_candidate_id"] == "CHEMBL887"
    assert linked.metadata["link_method"] == "candidate_id"
    assert linked.metadata.get("generated_direct_experimental_evidence") is not True
    assert linked.metadata.get("linked_generated_id") is None


def test_fuzzy_name_matching_requires_config_and_high_confidence():
    without_fuzzy = link_assay_result(
        _result(
            candidate_id=None,
            inchi_key=None,
            canonical_smiles=None,
            candidate_name="Rasagilin",
        ),
        candidates=[_candidate()],
    )
    with_fuzzy = link_assay_result(
        _result(
            candidate_id=None,
            inchi_key=None,
            canonical_smiles=None,
            candidate_name="Rasagilin",
        ),
        candidates=[_candidate()],
        config=LinkingConfig(allow_fuzzy_name_matching=True, fuzzy_min_confidence=0.85),
    )

    assert without_fuzzy.metadata["linked_candidate_id"] is None
    assert without_fuzzy.metadata["link_method"] == "unlinked"
    assert with_fuzzy.metadata["linked_candidate_id"] == "CHEMBL887"
    assert with_fuzzy.metadata["link_method"] == "fuzzy_name"
