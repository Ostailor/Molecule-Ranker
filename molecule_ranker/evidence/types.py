from __future__ import annotations

from typing import Literal

NormalizedEvidenceType = Literal[
    "disease_target_association",
    "target_metadata",
    "molecule_target_mechanism",
    "molecule_target_activity",
    "molecule_indication",
    "molecule_safety_warning",
    "chemical_annotation",
]

DISEASE_TARGET_ASSOCIATION: NormalizedEvidenceType = "disease_target_association"
TARGET_METADATA: NormalizedEvidenceType = "target_metadata"
MOLECULE_TARGET_MECHANISM: NormalizedEvidenceType = "molecule_target_mechanism"
MOLECULE_TARGET_ACTIVITY: NormalizedEvidenceType = "molecule_target_activity"
MOLECULE_INDICATION: NormalizedEvidenceType = "molecule_indication"
MOLECULE_SAFETY_WARNING: NormalizedEvidenceType = "molecule_safety_warning"
CHEMICAL_ANNOTATION: NormalizedEvidenceType = "chemical_annotation"

NORMALIZED_EVIDENCE_TYPES: tuple[NormalizedEvidenceType, ...] = (
    DISEASE_TARGET_ASSOCIATION,
    TARGET_METADATA,
    MOLECULE_TARGET_MECHANISM,
    MOLECULE_TARGET_ACTIVITY,
    MOLECULE_INDICATION,
    MOLECULE_SAFETY_WARNING,
    CHEMICAL_ANNOTATION,
)
