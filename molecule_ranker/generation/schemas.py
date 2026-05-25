from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

GenerationObjectiveType = Literal[
    "target_conditioned_analog_generation",
    "scaffold_hopping",
    "similarity_constrained_generation",
]

NoveltyClass = Literal[
    "duplicate",
    "near_duplicate",
    "close_analog",
    "novel_analog",
    "distant",
]


class GenerationConfig(BaseModel):
    """Runtime controls for molecular generation backends."""

    generation_method: str = "selfies_mutation"
    max_seed_molecules: int = Field(default=20, ge=1)
    min_seed_score: float = Field(default=0.35, ge=0.0, le=1.0)
    min_seed_target_relevance: float = Field(default=0.25, ge=0.0, le=1.0)
    require_structure_for_seed: bool = True
    exclude_seed_with_serious_warnings: bool = False
    max_generation_objectives: int = Field(default=10, ge=1)
    min_target_relevance_for_generation: float = Field(default=0.25, ge=0.0, le=1.0)
    seed_property_margin_fraction: float = Field(default=0.15, ge=0.0)
    descriptor_bounds_warning_only: bool = False
    basic_alerts_warning_only: bool = True
    reject_basic_alerts: bool = False
    duplicate_similarity_threshold: float = Field(default=0.98, ge=0.0, le=1.0)
    near_duplicate_similarity_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    distant_similarity_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    reject_distant_generated: bool = True
    reject_distant_generated_molecules: bool = True
    diversity_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_generated_per_diversity_cluster: int = Field(default=3, ge=1)
    generation_random_seed: int | None = None
    generated_per_objective: int = Field(default=50, ge=0)
    max_retained_generated: int = Field(default=50, ge=1)
    max_generation_rounds: int = Field(default=2, ge=1)
    max_mutations_per_child: int = Field(default=4, ge=1)
    enable_crossover: bool = True
    max_generated_before_filtering: int = Field(default=1000, ge=1)
    allowed_generation_elements: list[str] = Field(
        default_factory=lambda: ["C", "H", "N", "O", "S", "P", "F", "Cl", "Br", "I"]
    )

    @model_validator(mode="after")
    def sync_generation_flags(self) -> Self:
        if self.reject_basic_alerts:
            self.basic_alerts_warning_only = False
        return self


class GenerationObjective(BaseModel):
    """Target-conditioned generation objective derived from retrieved evidence."""

    objective_id: str
    disease_name: str
    target_symbol: str
    target_name: str | None = None
    target_identifiers: dict[str, str] = Field(default_factory=dict)
    mechanism_hint: str | None = None
    seed_molecule_names: list[str] = Field(default_factory=list)
    seed_molecule_ids: list[str] = Field(default_factory=list)
    objective_type: GenerationObjectiveType
    constraints: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SeedMolecule(BaseModel):
    """Evidence-backed retrieved molecule selected as a generation seed."""

    name: str
    canonical_smiles: str
    identifiers: dict[str, str] = Field(default_factory=dict)
    known_targets: list[str] = Field(default_factory=list)
    source_candidate_name: str
    evidence_count: int = Field(ge=0)
    best_evidence_confidence: float = Field(ge=0.0, le=1.0)
    target_relevance_score: float = Field(ge=0.0, le=1.0)
    seed_selection_reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChemicalValidationResult(BaseModel):
    """Chemical validation state for a generated structure."""

    valid_rdkit_mol: bool
    sanitization_ok: bool
    canonicalization_ok: bool
    allowed_elements_ok: bool
    descriptor_bounds_ok: bool
    pains_or_alerts: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NoveltyAssessment(BaseModel):
    """Duplicate and similarity assessment against existing and generated molecules."""

    duplicate_of_existing: bool
    duplicate_of_generated: bool
    max_similarity_to_existing: float = Field(ge=0.0, le=1.0)
    nearest_existing_name: str | None = None
    max_similarity_to_seed: float = Field(ge=0.0, le=1.0)
    nearest_seed_name: str | None = None
    novelty_class: NoveltyClass
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeneratedMoleculeScoreBreakdown(BaseModel):
    """Transparent scoring components for generated molecule prioritization."""

    target_conditioning_score: float = Field(ge=0.0, le=1.0)
    seed_evidence_score: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    diversity_score: float = Field(ge=0.0, le=1.0)
    chemical_validity_score: float = Field(ge=0.0, le=1.0)
    property_profile_score: float = Field(ge=0.0, le=1.0)
    literature_context_score: float = Field(ge=0.0, le=1.0)
    final_generation_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str


class GeneratedMolecule(BaseModel):
    """Generated in-silico molecule hypothesis, not an experimentally validated result."""

    generated_id: str
    smiles: str
    canonical_smiles: str
    selfies: str | None = None
    inchi_key: str | None = None
    origin: str = "generated"
    generation_method: str
    parent_seed_ids: list[str] = Field(default_factory=list)
    conditioned_targets: list[str] = Field(default_factory=list)
    objective_id: str
    generation_round: int = Field(ge=0)
    descriptors: dict[str, float] = Field(default_factory=dict)
    fingerprints: dict[str, Any] = Field(default_factory=dict)
    validation: ChemicalValidationResult
    novelty: NoveltyAssessment | None = None
    diversity_cluster: str | None = None
    generation_score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_breakdown: GeneratedMoleculeScoreBreakdown | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_experimental_validation_claims(self) -> Self:
        forbidden_keys = {
            "experimentally_validated",
            "validated_activity",
            "wet_lab_validated",
            "clinical_validated",
            "clinical_evidence",
        }
        claimed = sorted(
            key
            for key in forbidden_keys
            if bool(self.metadata.get(key)) or key in {warning.lower() for warning in self.warnings}
        )
        if claimed:
            joined = ", ".join(claimed)
            raise ValueError(
                "GeneratedMolecule must not pretend to be experimentally validated; "
                f"remove validation claim(s): {joined}."
            )
        return self


class GenerationRun(BaseModel):
    """Complete generation artifact with objectives, seeds, outputs, and trace metadata."""

    objectives: list[GenerationObjective] = Field(default_factory=list)
    seeds: list[SeedMolecule] = Field(default_factory=list)
    generated: list[GeneratedMolecule] = Field(default_factory=list)
    retained: list[GeneratedMolecule] = Field(default_factory=list)
    rejected: list[GeneratedMolecule] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
