from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.developability.schemas import (
    ConsensusRescoring,
    DockingAssessment,
    Ligand3DPreparationArtifact,
    PoseQualityControl,
    ProteinLigandInteractionProfile,
    StructurePreparationArtifact,
)

StructureSource = Literal["RCSB PDB", "AlphaFold DB"]
StructureKind = Literal["experimental", "predicted"]


class TargetStructureRecord(BaseModel):
    """Target structure metadata for optional structure-aware triage.

    Records intentionally contain metadata only. V0.4 does not perform docking,
    binding-site prediction, or laboratory protocol generation here.
    """

    target_symbol: str
    structure_id: str
    source: StructureSource
    structure_kind: StructureKind
    method: str | None = None
    resolution: float | None = Field(default=None, gt=0.0)
    chains: list[str] = Field(default_factory=list)
    ligands: list[str] = Field(default_factory=list)
    uniprot_accessions: list[str] = Field(default_factory=list)
    has_binding_site_annotation: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructureSelection(BaseModel):
    target_symbol: str
    selected_structure: TargetStructureRecord | None = None
    candidates: list[TargetStructureRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructureBasedReportCard(BaseModel):
    version: str = "1.3"
    target_symbol: str
    selected_structure: TargetStructureRecord | None = None
    docking_assessments: list[DockingAssessment] = Field(default_factory=list)
    optional_workflow: bool = True
    claims_boundary: dict[str, bool] = Field(default_factory=dict)
    design_loop_context: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def select_target_structure(
    records: list[TargetStructureRecord],
    *,
    target_symbol: str,
    preferred_uniprot: str | None = None,
) -> StructureSelection:
    if not records:
        return StructureSelection(
            target_symbol=target_symbol,
            candidates=[],
            warnings=[
                "No target structure metadata was available; structure-aware filtering was skipped."
            ],
            metadata={
                "selection_policy": "v1.3_conservative_structure_selection",
                "optional_structure_workflow": True,
                "candidate_count": 0,
            },
        )

    selected = sorted(
        records,
        key=lambda record: _selection_key(record, preferred_uniprot),
        reverse=True,
    )[0]
    warnings: list[str] = []
    if selected.structure_kind == "predicted":
        warnings.append(
            "Only predicted target structure metadata was available; confidence is lower."
        )
    return StructureSelection(
        target_symbol=target_symbol,
        selected_structure=selected,
        candidates=records,
        warnings=warnings,
        metadata={
            "selection_policy": "v1.3_conservative_structure_selection",
            "optional_structure_workflow": True,
            "preferred_uniprot": preferred_uniprot,
            "docking_performed": False,
            "candidate_count": len(records),
            "selection_basis": [
                "experimental_structure_preferred",
                "target_uniprot_mapping_preferred",
                "ligand_or_binding_site_annotation_preferred",
                "higher_resolution_preferred",
                "predicted_structure_lower_confidence",
            ],
            "selected_structure_kind": selected.structure_kind,
            "selected_structure_source": selected.source,
        },
    )


def build_structure_based_report_card(
    *,
    target_symbol: str,
    selected_structure: TargetStructureRecord | None = None,
    docking_assessments: list[DockingAssessment] | None = None,
    design_loop_context: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> StructureBasedReportCard:
    assessments = list(docking_assessments or [])
    warnings = _report_card_warnings(selected_structure, assessments)
    return StructureBasedReportCard(
        target_symbol=target_symbol,
        selected_structure=selected_structure,
        docking_assessments=assessments,
        optional_workflow=True,
        claims_boundary={
            "docking_scores_are_not_binding_evidence": True,
            "poses_are_not_experimental_evidence": True,
            "structure_scores_are_not_activity_evidence": True,
            "generated_molecules_remain_computational_hypotheses": True,
            "predicted_structures_are_lower_confidence_than_suitable_experimental_structures": True,
            "codex_must_not_invent_structures_poses_sites_scores_or_interactions": True,
        },
        design_loop_context=design_loop_context or {},
        warnings=warnings,
        metadata={
            "report_card_policy": "v1.3_structure_based_design_conservative_report_card",
            "docking_assessment_count": len(assessments),
            **(metadata or {}),
        },
    )


def _report_card_warnings(
    selected_structure: TargetStructureRecord | None,
    docking_assessments: list[DockingAssessment],
) -> list[str]:
    warnings = [
        "Structure workflows are optional computational triage and are not experimental evidence.",
        "Generated molecules remain computational hypotheses.",
    ]
    if selected_structure is None:
        warnings.append("No selected target structure was supplied.")
    elif selected_structure.structure_kind == "predicted":
        warnings.append(
            "Predicted structures are lower-confidence than suitable experimental structures."
        )
    for assessment in docking_assessments:
        warnings.extend(assessment.warnings)
    return sorted(set(warnings))


def _selection_key(
    record: TargetStructureRecord,
    preferred_uniprot: str | None,
) -> tuple[float, float, float, float, float]:
    experimental = 1.0 if record.structure_kind == "experimental" else 0.0
    mapped_accessions = {accession.upper() for accession in record.uniprot_accessions}
    target_mapping = (
        1.0 if preferred_uniprot and preferred_uniprot.upper() in mapped_accessions else 0.0
    )
    ligand_or_site = 1.0 if record.ligands or record.has_binding_site_annotation else 0.0
    resolution_score = _resolution_score(record.resolution)
    return (experimental, target_mapping, ligand_or_site, resolution_score, record.confidence)


def _resolution_score(resolution: float | None) -> float:
    if resolution is None:
        return 0.0
    return max(0.0, min(1.0, (4.0 - resolution) / 3.0))


__all__ = [
    "DockingAssessment",
    "ConsensusRescoring",
    "Ligand3DPreparationArtifact",
    "PoseQualityControl",
    "ProteinLigandInteractionProfile",
    "StructureBasedReportCard",
    "StructureSelection",
    "StructurePreparationArtifact",
    "TargetStructureRecord",
    "build_structure_based_report_card",
    "select_target_structure",
]
