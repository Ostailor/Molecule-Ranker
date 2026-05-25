from __future__ import annotations

from typing import Any

from molecule_ranker.evidence.types import (
    CHEMICAL_ANNOTATION,
    DISEASE_TARGET_ASSOCIATION,
    MOLECULE_INDICATION,
    MOLECULE_SAFETY_WARNING,
    MOLECULE_TARGET_ACTIVITY,
    MOLECULE_TARGET_MECHANISM,
    NORMALIZED_EVIDENCE_TYPES,
    TARGET_METADATA,
    NormalizedEvidenceType,
)
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate, Target

_EVIDENCE_TYPE_ALIASES: dict[str, NormalizedEvidenceType] = {
    "disease_target_association": DISEASE_TARGET_ASSOCIATION,
    "target_disease_association": DISEASE_TARGET_ASSOCIATION,
    "target_disease": DISEASE_TARGET_ASSOCIATION,
    "association": DISEASE_TARGET_ASSOCIATION,
    "target_metadata": TARGET_METADATA,
    "target": TARGET_METADATA,
    "mechanism": MOLECULE_TARGET_MECHANISM,
    "mechanistic": MOLECULE_TARGET_MECHANISM,
    "molecule_target_mechanism": MOLECULE_TARGET_MECHANISM,
    "activity": MOLECULE_TARGET_ACTIVITY,
    "assay": MOLECULE_TARGET_ACTIVITY,
    "binding": MOLECULE_TARGET_ACTIVITY,
    "target_interaction": MOLECULE_TARGET_ACTIVITY,
    "molecule_target_activity": MOLECULE_TARGET_ACTIVITY,
    "indication": MOLECULE_INDICATION,
    "molecule_indication": MOLECULE_INDICATION,
    "warning": MOLECULE_SAFETY_WARNING,
    "safety_warning": MOLECULE_SAFETY_WARNING,
    "molecule_safety_warning": MOLECULE_SAFETY_WARNING,
    "chemical_annotation": CHEMICAL_ANNOTATION,
}


def normalize_evidence_type(evidence_type: str) -> NormalizedEvidenceType | str:
    """Return the controlled evidence category for a source evidence type."""

    normalized = _EVIDENCE_TYPE_ALIASES.get(evidence_type.strip().lower())
    return normalized if normalized is not None else evidence_type


def normalize_evidence_item(evidence: EvidenceItem) -> EvidenceItem:
    """Return an EvidenceItem with a controlled evidence_type and unchanged source data."""

    normalized_type = normalize_evidence_type(evidence.evidence_type)
    if normalized_type == evidence.evidence_type:
        return evidence
    return evidence.model_copy(update={"evidence_type": normalized_type})


def normalize_evidence(evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    return [normalize_evidence_item(item) for item in evidence]


def is_molecule_target_evidence(evidence: EvidenceItem) -> bool:
    evidence_type = normalize_evidence_type(evidence.evidence_type)
    return evidence_type in {MOLECULE_TARGET_MECHANISM, MOLECULE_TARGET_ACTIVITY}


def is_clinical_evidence(evidence: EvidenceItem) -> bool:
    return normalize_evidence_type(evidence.evidence_type) == MOLECULE_INDICATION


def is_safety_warning(evidence: EvidenceItem) -> bool:
    return normalize_evidence_type(evidence.evidence_type) == MOLECULE_SAFETY_WARNING


def evidence_source_diversity(evidence: list[EvidenceItem]) -> float:
    if not evidence:
        return 0.0
    return min(len({item.source for item in evidence}) / 2.0, 1.0)


def evidence_completeness(
    candidate: MoleculeCandidate,
    targets: list[Target],
) -> dict[str, bool]:
    candidate_evidence = normalize_evidence(candidate.evidence)
    target_evidence = normalize_evidence(
        [item for target in targets for item in target.evidence]
    )
    known_targets = {target.upper() for target in candidate.known_targets}
    matched_targets = [target for target in targets if target.symbol.upper() in known_targets]
    return {
        "has_disease_target_association": any(
            normalize_evidence_type(item.evidence_type) == DISEASE_TARGET_ASSOCIATION
            for item in target_evidence
        ),
        "has_matched_target": bool(matched_targets),
        "has_molecule_target_evidence": any(
            is_molecule_target_evidence(item) for item in candidate_evidence
        ),
        "has_clinical_evidence": any(is_clinical_evidence(item) for item in candidate_evidence),
        "has_safety_warning": any(is_safety_warning(item) for item in candidate_evidence),
        "has_chemical_annotation": any(
            normalize_evidence_type(item.evidence_type) == CHEMICAL_ANNOTATION
            for item in candidate_evidence
        ),
        "has_identifier": bool(candidate.identifiers),
    }


def normalized_type_set() -> set[str]:
    return set(NORMALIZED_EVIDENCE_TYPES)


def source_metadata(evidence: EvidenceItem) -> dict[str, Any]:
    return dict(evidence.metadata)
