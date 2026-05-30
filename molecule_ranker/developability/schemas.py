from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

AlertType = Literal[
    "pains",
    "brenk",
    "reactive_functionality",
    "unstable_group",
    "toxicophore",
    "structural_liability",
    "assay_interference",
]
AlertSeverity = Literal["low", "medium", "high", "critical"]
ADMETRiskLevel = Literal["low", "medium", "high", "unknown"]
PredictionMethod = Literal["rule_based", "local_ml_model", "external_model", "unavailable"]
ApplicabilityDomain = Literal["in_domain", "out_of_domain", "unknown"]
ComplexityLevel = Literal["low", "medium", "high", "unknown"]
StartingMaterialAvailability = Literal[
    "likely_available",
    "unknown",
    "likely_unavailable",
]
DevelopabilityRiskLevel = Literal["low", "medium", "high", "critical", "unknown"]
DevelopabilityRecommendation = Literal[
    "retain",
    "deprioritize",
    "reject",
    "expert_review_required",
]
MoleculeOrigin = Literal["existing", "generated"]
PreparationArtifactType = Literal[
    "retrieved_structure",
    "prepared_receptor",
    "prepared_ligand",
    "docking_pose",
    "interaction_profile",
    "structure_report_card",
]
PoseQCStatus = Literal["pass", "warning", "fail", "not_assessed"]

_FORBIDDEN_SYNTHESIS_METADATA_KEYS = {
    "route",
    "routes",
    "route_steps",
    "synthesis_route",
    "synthetic_route",
    "retrosynthesis_route",
    "procedure",
    "procedures",
    "protocol",
    "protocols",
    "reagent",
    "reagents",
    "temperature",
    "temperatures",
    "conditions",
    "solvent",
    "solvents",
    "catalyst",
    "catalysts",
}
_FORBIDDEN_SYNTHESIS_TEXT = (
    "add ",
    "stir ",
    "heat ",
    "cool ",
    "reflux",
    "quench",
    "purify",
    "reagent",
    "temperature",
    "protocol",
    "procedure",
)


class PhysChemProfile(BaseModel):
    canonical_smiles: str
    inchi_key: str | None = None
    molecular_weight: float | None = None
    logp: float | None = None
    tpsa: float | None = None
    hbd: int | None = None
    hba: int | None = None
    rotatable_bonds: int | None = None
    aromatic_rings: int | None = None
    heavy_atom_count: int | None = None
    formal_charge: int | None = None
    fraction_csp3: float | None = Field(default=None, ge=0.0, le=1.0)
    qed: float | None = Field(default=None, ge=0.0, le=1.0)
    lipinski_violations: int = Field(ge=0)
    veber_violations: int = Field(ge=0)
    ghose_violations: int = Field(ge=0)
    egan_violations: int = Field(ge=0)
    muegge_violations: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChemistryAlert(BaseModel):
    alert_id: str
    alert_type: AlertType
    alert_name: str
    severity: AlertSeverity
    matched_smarts: str | None = None
    description: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ADMETPrediction(BaseModel):
    endpoint: str
    value: float | str | bool | None = None
    probability: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_level: ADMETRiskLevel
    model_name: str
    model_version: str | None = None
    prediction_method: PredictionMethod
    applicability_domain: ApplicabilityDomain
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SynthesizabilityAssessment(BaseModel):
    sa_score: float | None = Field(default=None, ge=0.0, le=1.0)
    retrosynthesis_available: bool = False
    route_count: int | None = Field(default=None, ge=0)
    estimated_complexity: ComplexityLevel
    starting_material_availability: StartingMaterialAvailability
    risk_level: ADMETRiskLevel
    method: str
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_actionable_synthesis_content(self) -> SynthesizabilityAssessment:
        _reject_synthesis_metadata(self.metadata)
        _reject_actionable_text([self.method, *self.warnings])
        return self


class StructurePreparationArtifact(BaseModel):
    artifact_id: str
    artifact_type: PreparationArtifactType
    source_structure_id: str
    artifact_uri: str
    preparation_method: str
    preparation_tool: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    checksum: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_protocol_content(self) -> StructurePreparationArtifact:
        _reject_synthesis_metadata(self.parameters)
        _reject_synthesis_metadata(self.metadata)
        _reject_actionable_text([self.preparation_method, *self.warnings])
        return self


class Ligand3DPreparationArtifact(BaseModel):
    artifact_id: str
    ligand_id: str
    canonical_smiles: str
    artifact_uri: str
    preparation_method: str
    conformer_count: int = Field(ge=0)
    selected_conformer_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    checksum: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_protocol_content(self) -> Ligand3DPreparationArtifact:
        _reject_synthesis_metadata(self.parameters)
        _reject_synthesis_metadata(self.metadata)
        _reject_actionable_text([self.preparation_method, *self.warnings])
        return self


class PoseQualityControl(BaseModel):
    status: PoseQCStatus
    checks: dict[str, bool] = Field(default_factory=dict)
    failure_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_pose_evidence_claims(self) -> PoseQualityControl:
        _reject_structure_claim_metadata(self.metadata)
        return self


class ConsensusRescoring(BaseModel):
    methods: list[str] = Field(default_factory=list)
    normalized_scores: dict[str, float] = Field(default_factory=dict)
    consensus_score: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_score_evidence_claims(self) -> ConsensusRescoring:
        _reject_structure_claim_metadata(self.metadata)
        return self


class ProteinLigandInteractionProfile(BaseModel):
    method: str
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_sourced_interactions_and_reject_claims(
        self,
    ) -> ProteinLigandInteractionProfile:
        for interaction in self.interactions:
            if not str(interaction.get("source") or "").strip():
                raise ValueError("Protein-ligand interactions must include a source.")
        _reject_structure_claim_metadata(self.metadata)
        return self


class DockingAssessment(BaseModel):
    enabled: bool
    target_symbol: str
    structure_source: str | None = None
    structure_id: str | None = None
    ligand_id: str
    docking_engine: str | None = None
    docking_score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_units: str | None = None
    binding_site_method: str | None = None
    pose_file: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    receptor_preparation: StructurePreparationArtifact | None = None
    ligand_preparation: Ligand3DPreparationArtifact | None = None
    pose_quality_control: PoseQualityControl | None = None
    consensus_rescoring: ConsensusRescoring | None = None
    interaction_profile: ProteinLigandInteractionProfile | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_structure_integrity_boundaries(self) -> DockingAssessment:
        _reject_structure_claim_metadata(self.metadata)
        if self.enabled:
            warning_text = " ".join(self.warnings).lower()
            if "does not prove binding" not in warning_text:
                raise ValueError(
                    "Enabled docking assessments must state that docking does not prove binding."
                )
        if self.structure_source == "AlphaFold DB" and self.confidence > 0.55:
            raise ValueError(
                "Predicted structures must remain lower-confidence than suitable "
                "experimental structures."
            )
        return self


class DevelopabilityAssessment(BaseModel):
    molecule_id: str
    molecule_name: str
    origin: MoleculeOrigin
    canonical_smiles: str
    physchem: PhysChemProfile | None = None
    alerts: list[ChemistryAlert] = Field(default_factory=list)
    admet_predictions: list[ADMETPrediction] = Field(default_factory=list)
    synthesizability: SynthesizabilityAssessment | None = None
    docking: list[DockingAssessment] = Field(default_factory=list)
    overall_developability_score: float = Field(ge=0.0, le=1.0)
    risk_summary: str
    risk_level: DevelopabilityRiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    recommendation: DevelopabilityRecommendation
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DevelopabilityRun(BaseModel):
    enabled: bool
    assessed_existing_count: int = Field(ge=0)
    assessed_generated_count: int = Field(ge=0)
    retained_count: int = Field(ge=0)
    deprioritized_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    assessments: list[DevelopabilityAssessment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _reject_synthesis_metadata(metadata: dict[str, Any]) -> None:
    forbidden = _FORBIDDEN_SYNTHESIS_METADATA_KEYS & {key.lower() for key in metadata}
    if forbidden:
        joined = ", ".join(sorted(forbidden))
        raise ValueError(f"Synthesizability metadata must not include actionable routes: {joined}")


def _reject_actionable_text(values: list[str]) -> None:
    for value in values:
        lowered = value.lower()
        if any(term in lowered for term in _FORBIDDEN_SYNTHESIS_TEXT):
            raise ValueError("Synthesizability assessment must not include synthesis instructions")


def _reject_structure_claim_metadata(metadata: dict[str, Any]) -> None:
    for text in _metadata_strings(metadata):
        lowered = text.lower()
        if any(phrase in lowered for phrase in _FORBIDDEN_STRUCTURE_CLAIM_TEXT):
            raise ValueError("Structure workflow metadata must not contain binding/activity claims")


def _metadata_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for key, item in value.items():
            strings.extend(_metadata_strings(str(key)))
            strings.extend(_metadata_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_metadata_strings(item))
        return strings
    return []


_FORBIDDEN_STRUCTURE_CLAIM_TEXT = (
    "proves binding",
    "proof of binding",
    "confirmed binding",
    "demonstrates binding",
    "is active",
    "is safe",
    "is efficacious",
    "treats ",
    "cures ",
    "inhibits ",
    "activates ",
)
