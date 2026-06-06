from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

BiologicType = Literal[
    "monoclonal_antibody",
    "bispecific_antibody",
    "nanobody",
    "antibody_fragment",
    "protein_binder",
    "cytokine",
    "receptor_fusion",
    "peptide",
    "other",
]
BiologicOrigin = Literal["existing", "generated", "external"]
AntibodyChainType = Literal[
    "heavy",
    "light_kappa",
    "light_lambda",
    "paired_heavy_light",
    "single_domain_vhh",
    "scfv",
    "unknown",
]
AntibodySequenceSource = Literal[
    "public_database",
    "external_registry",
    "imported",
    "generated",
    "user_supplied",
]
AntibodyNumberingScheme = Literal["imgt", "chothia", "kabat", "aho", "unknown"]
AntibodyRiskLevel = Literal["low", "medium", "high", "unknown"]
AntibodyNoveltyClass = Literal[
    "known",
    "near_duplicate",
    "close_variant",
    "novel_candidate",
    "unknown",
]

ALLOWED_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
GENERATED_ANTIBODY_NO_DIRECT_EVIDENCE_WARNING = (
    "Generated antibodies are computational hypotheses only and have no direct "
    "experimental evidence unless exact imported experimental results are linked."
)


class BiologicsSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class BiologicCandidate(BiologicsSchema):
    biologic_id: str
    name: str
    biologic_type: BiologicType
    origin: BiologicOrigin
    target_symbols: list[str] = Field(default_factory=list)
    antigen_names: list[str] = Field(default_factory=list)
    disease_name: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)
    sequence_ids: list[str] = Field(default_factory=list)
    structure_ids: list[str] = Field(default_factory=list)
    evidence_item_ids: list[str] = Field(default_factory=list)
    direct_experimental_evidence: bool = False
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def generated_candidates_do_not_default_to_direct_evidence(self) -> Self:
        if self.origin == "generated" and self.direct_experimental_evidence:
            raise ValueError(
                "generated biologic candidates cannot declare direct experimental evidence"
            )
        return self


class AntibodySequence(BiologicsSchema):
    sequence_id: str
    biologic_id: str | None = None
    chain_type: AntibodyChainType
    amino_acid_sequence: str
    sequence_length: int = Field(ge=0)
    species_origin: str | None = None
    is_generated: bool = False
    parent_sequence_ids: list[str] = Field(default_factory=list)
    source: AntibodySequenceSource
    source_record_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("amino_acid_sequence")
    @classmethod
    def normalize_amino_acid_sequence(cls, value: str) -> str:
        normalized = re.sub(r"\s+", "", value).upper()
        if not normalized:
            raise ValueError("amino_acid_sequence is required")
        invalid = sorted(set(normalized) - ALLOWED_AMINO_ACIDS)
        if invalid:
            raise ValueError(
                "sequence contains unsupported amino acid codes: "
                f"{', '.join(invalid)}"
            )
        return normalized

    @model_validator(mode="after")
    def sequence_length_matches_sequence(self) -> Self:
        actual_length = len(self.amino_acid_sequence)
        if self.sequence_length != actual_length:
            raise ValueError("sequence_length must equal len(amino_acid_sequence)")
        if self.is_generated and self.source != "generated":
            raise ValueError("generated antibody sequences must use source='generated'")
        return self


class AntibodyNumbering(BiologicsSchema):
    numbering_id: str
    sequence_id: str
    scheme: AntibodyNumberingScheme
    framework_regions: dict[str, tuple[int, int]] = Field(default_factory=dict)
    cdr_regions: dict[str, tuple[int, int]] = Field(default_factory=dict)
    insertions: dict[str, Any] = Field(default_factory=dict)
    numbering_tool: str
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def region_ranges_are_ordered(self) -> Self:
        for label, region in {
            **self.framework_regions,
            **self.cdr_regions,
        }.items():
            start, end = region
            if start < 1 or end < start:
                raise ValueError(f"invalid residue range for {label}")
        return self


class CDRAnnotation(BiologicsSchema):
    annotation_id: str
    sequence_id: str
    scheme: AntibodyNumberingScheme
    cdr1: str | None = None
    cdr2: str | None = None
    cdr3: str | None = None
    cdr_lengths: dict[str, int] = Field(default_factory=dict)
    unusual_motifs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def cdr_lengths_match_sequences(self) -> Self:
        for label, sequence in {
            "cdr1": self.cdr1,
            "cdr2": self.cdr2,
            "cdr3": self.cdr3,
        }.items():
            if sequence is None:
                continue
            expected = self.cdr_lengths.get(label)
            if expected is not None and expected != len(sequence):
                raise ValueError(f"cdr_lengths[{label!r}] must match CDR sequence length")
        return self


class AntigenContext(BiologicsSchema):
    antigen_context_id: str
    target_symbol: str
    antigen_name: str
    antigen_identifiers: dict[str, str] = Field(default_factory=dict)
    epitope_description: str | None = None
    epitope_source: str | None = None
    structure_context_ids: list[str] = Field(default_factory=list)
    evidence_item_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def epitope_description_requires_source(self) -> Self:
        if self.epitope_description and not self.epitope_source:
            raise ValueError("epitope_description requires epitope_source")
        return self


class AntibodyDevelopabilityAssessment(BiologicsSchema):
    assessment_id: str
    biologic_id: str
    sequence_ids: list[str] = Field(default_factory=list)
    aggregation_risk: AntibodyRiskLevel
    polyreactivity_risk: AntibodyRiskLevel
    immunogenicity_risk: AntibodyRiskLevel
    viscosity_risk: AntibodyRiskLevel
    stability_risk: AntibodyRiskLevel
    expression_risk: AntibodyRiskLevel
    sequence_liability_flags: list[str] = Field(default_factory=list)
    cdr_liability_flags: list[str] = Field(default_factory=list)
    overall_developability_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AntibodyNoveltyAssessment(BiologicsSchema):
    novelty_id: str
    biologic_id: str
    sequence_ids: list[str] = Field(default_factory=list)
    exact_sequence_match: bool
    nearest_sequence_identity: float | None = Field(default=None, ge=0.0, le=1.0)
    nearest_known_record: str | None = None
    cdr3_exact_match: bool | None = None
    cdr3_nearest_identity: float | None = Field(default=None, ge=0.0, le=1.0)
    novelty_class: AntibodyNoveltyClass
    sources_checked: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeneratedAntibodyHypothesis(BiologicsSchema):
    generated_antibody_id: str
    biologic_id: str
    design_objective_id: str
    generated_sequence_ids: list[str] = Field(default_factory=list)
    parent_sequence_ids: list[str] = Field(default_factory=list)
    generation_method: str
    antigen_context_id: str | None = None
    target_symbols: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    direct_experimental_evidence: bool = False
    no_direct_evidence_warning: str = GENERATED_ANTIBODY_NO_DIRECT_EVIDENCE_WARNING
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def generated_hypothesis_has_no_direct_evidence(self) -> Self:
        if self.direct_experimental_evidence:
            raise ValueError(
                "generated antibody hypotheses cannot declare direct experimental evidence"
            )
        return self


__all__ = [
    "ALLOWED_AMINO_ACIDS",
    "GENERATED_ANTIBODY_NO_DIRECT_EVIDENCE_WARNING",
    "AntibodyChainType",
    "AntibodyDevelopabilityAssessment",
    "AntibodyNoveltyAssessment",
    "AntibodyNumbering",
    "AntibodyNumberingScheme",
    "AntibodyRiskLevel",
    "AntibodySequence",
    "AntibodySequenceSource",
    "AntigenContext",
    "BiologicCandidate",
    "BiologicOrigin",
    "BiologicType",
    "CDRAnnotation",
    "GeneratedAntibodyHypothesis",
]
