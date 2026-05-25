from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.developability.schemas import DockingAssessment

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
            metadata={"selection_policy": "v0.4_metadata_only_structure_selection"},
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
            "selection_policy": "v0.4_metadata_only_structure_selection",
            "preferred_uniprot": preferred_uniprot,
            "docking_performed": False,
            "selection_basis": [
                "experimental_structure_preferred",
                "target_uniprot_mapping_preferred",
                "ligand_or_binding_site_annotation_preferred",
                "higher_resolution_preferred",
                "predicted_structure_lower_confidence",
            ],
        },
    )


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
    "StructureSelection",
    "TargetStructureRecord",
    "select_target_structure",
]
