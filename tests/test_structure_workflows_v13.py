from __future__ import annotations

from pathlib import Path

import pytest

from molecule_ranker.developability.schemas import DockingAssessment
from molecule_ranker.developability.structure import (
    ConsensusRescoring,
    Ligand3DPreparationArtifact,
    PoseQualityControl,
    ProteinLigandInteractionProfile,
    StructureBasedReportCard,
    StructurePreparationArtifact,
    TargetStructureRecord,
    build_structure_based_report_card,
    select_target_structure,
)


def _experimental_structure() -> TargetStructureRecord:
    return TargetStructureRecord(
        target_symbol="LRRK2",
        structure_id="6XYZ",
        source="RCSB PDB",
        structure_kind="experimental",
        method="X-RAY DIFFRACTION",
        resolution=2.8,
        chains=["A"],
        ligands=["ATP"],
        uniprot_accessions=["Q5S007"],
        has_binding_site_annotation=True,
        confidence=0.78,
        provenance={"source": "RCSB PDB", "entry_url": "https://data.rcsb.org/rest/v1/core/entry/6XYZ"},
    )


def _predicted_structure() -> TargetStructureRecord:
    return TargetStructureRecord(
        target_symbol="LRRK2",
        structure_id="AF-Q5S007-F1",
        source="AlphaFold DB",
        structure_kind="predicted",
        method="AlphaFold predicted model",
        uniprot_accessions=["Q5S007"],
        confidence=0.55,
        provenance={"source": "AlphaFold DB", "entry_url": "https://alphafold.ebi.ac.uk/entry/Q5S007"},
    )


def test_v13_structure_selection_is_auditable_and_prefers_suitable_experimental_structure() -> None:
    selection = select_target_structure(
        [_predicted_structure(), _experimental_structure()],
        target_symbol="LRRK2",
        preferred_uniprot="Q5S007",
    )

    assert selection.selected_structure is not None
    assert selection.selected_structure.structure_id == "6XYZ"
    assert selection.metadata["selection_policy"] == "v1.3_conservative_structure_selection"
    assert selection.metadata["optional_structure_workflow"] is True
    assert "experimental_structure_preferred" in selection.metadata["selection_basis"]
    assert selection.metadata["candidate_count"] == 2


def test_v13_docking_assessment_requires_integrity_warning_and_rejects_binding_claims() -> None:
    with pytest.raises(ValueError, match="does not prove binding"):
        DockingAssessment(
            enabled=True,
            target_symbol="LRRK2",
            structure_source="RCSB PDB",
            structure_id="6XYZ",
            ligand_id="lig-1",
            docking_engine="mock",
            docking_score=0.7,
            score_units="normalized_mock_score",
            binding_site_method="known_ligand_site",
            confidence=0.3,
            warnings=[],
        )

    with pytest.raises(ValueError, match="must not contain binding/activity claims"):
        DockingAssessment(
            enabled=False,
            target_symbol="LRRK2",
            ligand_id="lig-1",
            confidence=0.0,
            warnings=["Docking disabled by configuration."],
            metadata={"interpretation": "This pose proves binding."},
        )


def test_v13_structure_report_card_preserves_supplied_artifacts_without_evidence_claims(
    tmp_path: Path,
) -> None:
    receptor = StructurePreparationArtifact(
        artifact_id="receptor-1",
        artifact_type="prepared_receptor",
        source_structure_id="6XYZ",
        artifact_uri=str(tmp_path / "receptor.pdbqt"),
        preparation_method="external_prepared_receptor",
        preparation_tool="test-prep",
        parameters={"protonation_state_policy": "externally reviewed"},
        warnings=[],
    )
    ligand = Ligand3DPreparationArtifact(
        artifact_id="ligand-1",
        ligand_id="lig-1",
        canonical_smiles="CCO",
        artifact_uri=str(tmp_path / "ligand.pdbqt"),
        preparation_method="external_3d_ligand_preparation",
        conformer_count=3,
        selected_conformer_id="conf-2",
        warnings=[],
    )
    docking = DockingAssessment(
        enabled=True,
        target_symbol="LRRK2",
        structure_source="RCSB PDB",
        structure_id="6XYZ",
        ligand_id="lig-1",
        docking_engine="mock",
        docking_score=0.7,
        score_units="normalized_mock_score",
        binding_site_method="known_ligand_site",
        confidence=0.3,
        receptor_preparation=receptor,
        ligand_preparation=ligand,
        pose_quality_control=PoseQualityControl(
            status="pass",
            checks={"score_parsed": True, "explicit_box_used": True},
            failure_reasons=[],
            warnings=[],
        ),
        consensus_rescoring=ConsensusRescoring(
            methods=["vina", "mock_shape"],
            normalized_scores={"vina": 0.7, "mock_shape": 0.6},
            consensus_score=0.65,
            warnings=["Consensus score is computational triage only."],
        ),
        interaction_profile=ProteinLigandInteractionProfile(
            method="external_interaction_profiler",
            interactions=[
                {
                    "interaction_type": "hydrogen_bond",
                    "residue_label": "LYS1906",
                    "source": "external_profiler",
                }
            ],
            warnings=["Interactions are pose-derived computational annotations."],
        ),
        warnings=[
            "Docking score is a weak computational heuristic and does not prove binding.",
            "Pose is not experimental evidence.",
        ],
    )

    card = build_structure_based_report_card(
        target_symbol="LRRK2",
        selected_structure=_experimental_structure(),
        docking_assessments=[docking],
        design_loop_context={"round_id": "round-1", "filter_policy": "review_only"},
    )

    assert isinstance(card, StructureBasedReportCard)
    payload = card.model_dump(mode="json")
    assert payload["version"] == "1.3"
    assert payload["claims_boundary"]["docking_scores_are_not_binding_evidence"] is True
    assert payload["claims_boundary"]["poses_are_not_experimental_evidence"] is True
    assert payload["optional_workflow"] is True
    assert payload["docking_assessments"][0]["pose_quality_control"]["status"] == "pass"
    assert payload["docking_assessments"][0]["consensus_rescoring"]["consensus_score"] == 0.65
    assert payload["design_loop_context"]["filter_policy"] == "review_only"
    assert "proves binding" not in str(payload).lower()
