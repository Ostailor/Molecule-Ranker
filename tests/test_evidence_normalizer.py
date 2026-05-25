from __future__ import annotations

from molecule_ranker.evidence import (
    evidence_completeness,
    evidence_source_diversity,
    is_clinical_evidence,
    is_molecule_target_evidence,
    is_safety_warning,
    normalize_evidence_item,
    normalize_evidence_type,
)
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate, Target


def _evidence(evidence_type: str, source: str = "ChEMBL") -> EvidenceItem:
    return EvidenceItem(
        source=source,
        source_record_id="record-1",
        title="Evidence",
        evidence_type=evidence_type,
        summary="Retrieved source evidence.",
        confidence=0.8,
        metadata={"source_payload": {"field": "value"}},
    )


def test_normalize_evidence_type_maps_source_aliases_to_controlled_vocabulary():
    assert normalize_evidence_type("target_disease_association") == (
        "disease_target_association"
    )
    assert normalize_evidence_type("mechanism") == "molecule_target_mechanism"
    assert normalize_evidence_type("assay") == "molecule_target_activity"
    assert normalize_evidence_type("safety_warning") == "molecule_safety_warning"
    assert normalize_evidence_type("chemical_annotation") == "chemical_annotation"


def test_normalize_evidence_item_preserves_record_id_and_metadata():
    evidence = _evidence("mechanism")

    normalized = normalize_evidence_item(evidence)

    assert normalized.evidence_type == "molecule_target_mechanism"
    assert normalized.source_record_id == "record-1"
    assert normalized.metadata == {"source_payload": {"field": "value"}}
    assert evidence.evidence_type == "mechanism"


def test_evidence_category_helpers_use_normalized_types():
    assert is_molecule_target_evidence(_evidence("activity"))
    assert is_molecule_target_evidence(_evidence("mechanism"))
    assert not is_molecule_target_evidence(_evidence("chemical_annotation"))
    assert is_clinical_evidence(_evidence("indication"))
    assert is_safety_warning(_evidence("warning"))


def test_evidence_source_diversity_scores_unique_public_sources():
    evidence = [
        _evidence("mechanism", "ChEMBL"),
        _evidence("activity", "ChEMBL"),
        _evidence("chemical_annotation", "PubChem"),
    ]

    assert evidence_source_diversity(evidence) == 1.0
    assert evidence_source_diversity([]) == 0.0


def test_evidence_completeness_reports_candidate_and_target_dimensions():
    target = Target(
        symbol="LRRK2",
        disease_relevance_score=0.8,
        evidence=[_evidence("target_disease_association", "Open Targets")],
    )
    candidate = MoleculeCandidate(
        name="Candidate",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL1"},
        known_targets=["LRRK2"],
        evidence=[
            _evidence("mechanism"),
            _evidence("indication"),
            _evidence("safety_warning"),
            _evidence("chemical_annotation", "PubChem"),
        ],
    )

    completeness = evidence_completeness(candidate, [target])

    assert completeness == {
        "has_disease_target_association": True,
        "has_matched_target": True,
        "has_molecule_target_evidence": True,
        "has_clinical_evidence": True,
        "has_safety_warning": True,
        "has_chemical_annotation": True,
        "has_identifier": True,
    }
