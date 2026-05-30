from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

StructureType = Literal["experimental", "predicted", "user_supplied", "homology_model"]
LigandOrigin = Literal["existing", "generated"]
StereochemistryStatus = Literal["specified", "unspecified", "ambiguous", "corrected"]
BindingSiteMethod = Literal[
    "co_crystal_ligand",
    "known_residues",
    "pocket_detection",
    "user_supplied_box",
    "full_protein_blind",
    "unavailable",
]
DockingStatus = Literal["queued", "running", "succeeded", "failed", "skipped"]
ApplicabilityDomain = Literal[
    "suitable_experimental_structure",
    "lower_confidence_predicted_structure",
    "weak_or_unknown_structure",
    "unavailable",
]
StructureRecommendation = Literal[
    "retain_for_review",
    "deprioritize",
    "reject",
    "needs_structure_review",
]


class StructureSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value


class StructureRecord(StructureSchema):
    structure_id: str
    source: str
    external_id: str
    target_symbol: str
    target_identifiers: dict[str, str] = Field(default_factory=dict)
    structure_type: StructureType
    experimental_method: str | None = None
    resolution_angstrom: float | None = Field(default=None, gt=0.0)
    coverage: dict[str, Any] = Field(default_factory=dict)
    chains: list[str] = Field(default_factory=list)
    ligands: list[dict[str, Any]] = Field(default_factory=list)
    mutations: list[dict[str, Any]] = Field(default_factory=list)
    organism: str | None = None
    release_date: str | None = None
    quality_metrics: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None
    retrieved_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructureSelection(StructureSchema):
    selection_id: str
    target_symbol: str
    selected_structure_id: str
    selected_chain_ids: list[str] = Field(default_factory=list)
    selection_reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    rejected_structures: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReceptorPreparation(StructureSchema):
    receptor_prep_id: str
    structure_id: str
    target_symbol: str
    input_structure_path: str
    prepared_receptor_path: str | None = None
    preparation_method: str
    protonation_policy: str
    kept_chains: list[str] = Field(default_factory=list)
    removed_chains: list[str] = Field(default_factory=list)
    kept_heterogens: list[str] = Field(default_factory=list)
    removed_heterogens: list[str] = Field(default_factory=list)
    missing_atoms_fixed: bool
    missing_hydrogens_added: bool
    missing_loops_modeled: bool
    alternate_locations_resolved: bool
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Ligand3DPreparation(StructureSchema):
    ligand_prep_id: str
    molecule_id: str
    molecule_name: str
    origin: LigandOrigin
    canonical_smiles: str
    conformer_count: int = Field(ge=0)
    prepared_ligand_paths: list[str] = Field(default_factory=list)
    charge_method: str | None = None
    protonation_policy: str
    stereochemistry_status: StereochemistryStatus
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BindingSiteDefinition(StructureSchema):
    binding_site_id: str
    target_symbol: str
    structure_id: str
    method: BindingSiteMethod
    center: list[float] | None = None
    box_size: list[float] | None = None
    residues: list[str] = Field(default_factory=list)
    reference_ligand_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("center", "box_size")
    @classmethod
    def require_three_coordinate_vector(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and len(value) != 3:
            raise ValueError("must contain exactly three coordinates")
        return value

    @field_validator("box_size")
    @classmethod
    def require_positive_box_size(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and any(item <= 0 for item in value):
            raise ValueError("box_size values must be positive")
        return value


class DockingRun(StructureSchema):
    docking_run_id: str
    target_symbol: str
    structure_id: str
    receptor_prep_id: str
    binding_site_id: str
    docking_engine: str
    docking_engine_version: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None = None
    status: DockingStatus
    ligand_count: int = Field(ge=0)
    pose_count: int = Field(ge=0)
    artifacts: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_evidence_claims(self) -> DockingRun:
        _reject_pose_or_docking_evidence_claims(
            {
                "config": self.config,
                "artifacts": self.artifacts,
                "warnings": self.warnings,
                "metadata": self.metadata,
            }
        )
        return self


class DockingPose(StructureSchema):
    pose_id: str
    docking_run_id: str
    molecule_id: str
    molecule_name: str
    canonical_smiles: str
    target_symbol: str
    structure_id: str
    binding_site_id: str
    pose_rank: int = Field(ge=1)
    docking_score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_units: str | None = None
    pose_path: str | None = None
    interaction_summary: dict[str, Any] = Field(default_factory=dict)
    pose_quality: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_evidence_claims(self) -> DockingPose:
        _reject_pose_or_docking_evidence_claims(
            {
                "interaction_summary": self.interaction_summary,
                "pose_quality": self.pose_quality,
                "warnings": self.warnings,
                "metadata": self.metadata,
            }
        )
        return self


class ProteinLigandInteractionProfile(StructureSchema):
    profile_id: str
    pose_id: str
    target_symbol: str
    molecule_id: str
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    interaction_counts: dict[str, int] = Field(default_factory=dict)
    key_residue_contacts: list[str] = Field(default_factory=list)
    reference_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("interaction_counts")
    @classmethod
    def require_non_negative_interaction_counts(
        cls,
        value: dict[str, int],
    ) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("interaction_counts must be non-negative")
        return value


class StructureAwareAssessment(StructureSchema):
    assessment_id: str
    molecule_id: str
    molecule_name: str
    target_symbol: str
    structure_id: str | None = None
    docking_pose_ids: list[str] = Field(default_factory=list)
    structure_score: float = Field(ge=0.0, le=1.0)
    pose_confidence: float = Field(ge=0.0, le=1.0)
    interaction_score: float = Field(ge=0.0, le=1.0)
    consensus_score: float = Field(ge=0.0, le=1.0)
    applicability_domain: ApplicabilityDomain
    recommendation: StructureRecommendation
    warnings: list[str] = Field(default_factory=list)
    explanation: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def _reject_pose_or_docking_evidence_claims(value: Any) -> None:
    for text in _strings(value):
        lowered = text.lower()
        if any(safe_phrase in lowered for safe_phrase in _SAFE_LIMITATION_PHRASES):
            continue
        if any(phrase in lowered for phrase in _FORBIDDEN_POSE_DOCKING_CLAIMS):
            raise ValueError(
                "Docking and pose objects must not be treated as experimental evidence"
            )


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for key, item in value.items():
            strings.extend(_strings(str(key)))
            strings.extend(_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_strings(item))
        return strings
    return []


_FORBIDDEN_POSE_DOCKING_CLAIMS = (
    " is experimental evidence",
    " are experimental evidence",
    " as experimental evidence",
    "proves binding",
    "proof of binding",
    "confirmed binding",
    "demonstrates binding",
    " is activity evidence",
    " are activity evidence",
    "confirms activity",
    "confirms inhibition",
    "confirms activation",
)

_SAFE_LIMITATION_PHRASES = (
    "not experimental evidence",
    "not proof of binding",
    "does not prove binding",
    "not activity evidence",
)


__all__ = [
    "ApplicabilityDomain",
    "BindingSiteDefinition",
    "BindingSiteMethod",
    "DockingPose",
    "DockingRun",
    "DockingStatus",
    "Ligand3DPreparation",
    "LigandOrigin",
    "ProteinLigandInteractionProfile",
    "ReceptorPreparation",
    "StereochemistryStatus",
    "StructureAwareAssessment",
    "StructureRecord",
    "StructureRecommendation",
    "StructureSelection",
    "StructureType",
]
