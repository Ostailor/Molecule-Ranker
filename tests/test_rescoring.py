from __future__ import annotations

from typing import Any

from molecule_ranker.structure.rescoring import (
    RescoringConfig,
    score_structure_aware_assessment,
)
from molecule_ranker.structure.schemas import DockingPose, ProteinLigandInteractionProfile


def _pose(**overrides: Any) -> DockingPose:
    payload: dict[str, Any] = {
        "pose_id": "pose-1",
        "docking_run_id": "dock-run-1",
        "molecule_id": "mol-1",
        "molecule_name": "Example",
        "canonical_smiles": "CCO",
        "target_symbol": "LRRK2",
        "structure_id": "RCSB_PDB:6LIG",
        "binding_site_id": "site-1",
        "pose_rank": 1,
        "docking_score": 0.9,
        "score_units": "normalized_docking_score_0_1",
        "pose_path": "pose.pdbqt",
        "interaction_summary": {},
        "pose_quality": {"status": "pass", "checks": {}},
        "confidence": 0.45,
        "warnings": ["Pose is not experimental evidence."],
        "metadata": {"not_experimental_evidence": True},
    }
    payload.update(overrides)
    return DockingPose(**payload)


def _profile(**overrides: Any) -> ProteinLigandInteractionProfile:
    payload: dict[str, Any] = {
        "profile_id": "profile-1",
        "pose_id": "pose-1",
        "target_symbol": "LRRK2",
        "molecule_id": "mol-1",
        "interactions": [{"interaction_type": "hydrogen_bond_like", "residue": "LYS1906"}],
        "interaction_counts": {"hydrogen_bond_like": 1, "hydrophobic_contact": 1},
        "key_residue_contacts": ["LYS1906"],
        "reference_similarity": 0.7,
        "warnings": ["Interactions are not experimental evidence."],
        "confidence": 0.55,
        "metadata": {"not_experimental_evidence": True},
    }
    payload.update(overrides)
    return ProteinLigandInteractionProfile(**payload)


def test_consensus_bounded() -> None:
    assessment = score_structure_aware_assessment(
        molecule_id="mol-1",
        molecule_name="Example",
        target_symbol="LRRK2",
        ligand_origin="existing",
        structure_id="RCSB_PDB:6LIG",
        applicability_domain="suitable_experimental_structure",
        structure_selection_confidence=1.5,
        receptor_preparation_confidence=1.2,
        ligand_preparation_confidence=1.1,
        poses=[_pose(docking_score=1.0)],
        interaction_profiles=[_profile()],
        calibrated_surrogate_score=2.0,
        developability_score=1.4,
        config=RescoringConfig(),
    )

    assert 0.0 <= assessment.consensus_score <= 1.0
    assert 0.0 <= assessment.structure_score <= 1.0
    assert assessment.metadata["score_is_not_predicted_binding_affinity"] is True


def test_poor_pose_lowers_score() -> None:
    good = score_structure_aware_assessment(
        molecule_id="mol-1",
        molecule_name="Example",
        target_symbol="LRRK2",
        ligand_origin="existing",
        structure_id="RCSB_PDB:6LIG",
        applicability_domain="suitable_experimental_structure",
        poses=[_pose(pose_quality={"status": "pass"})],
        interaction_profiles=[_profile()],
    )
    poor = score_structure_aware_assessment(
        molecule_id="mol-1",
        molecule_name="Example",
        target_symbol="LRRK2",
        ligand_origin="existing",
        structure_id="RCSB_PDB:6LIG",
        applicability_domain="suitable_experimental_structure",
        poses=[_pose(pose_quality={"status": "reject"})],
        interaction_profiles=[_profile()],
    )

    assert poor.consensus_score < good.consensus_score
    assert poor.recommendation in {"deprioritize", "reject", "needs_structure_review"}
    assert any("pose" in warning.lower() for warning in poor.warnings)


def test_predicted_structure_lowers_confidence() -> None:
    experimental = score_structure_aware_assessment(
        molecule_id="mol-1",
        molecule_name="Example",
        target_symbol="LRRK2",
        ligand_origin="existing",
        structure_id="RCSB_PDB:6LIG",
        applicability_domain="suitable_experimental_structure",
        structure_selection_confidence=0.85,
        poses=[_pose()],
        interaction_profiles=[_profile()],
    )
    predicted = score_structure_aware_assessment(
        molecule_id="mol-1",
        molecule_name="Example",
        target_symbol="LRRK2",
        ligand_origin="existing",
        structure_id="AlphaFold_DB:AF-Q5S007-F1",
        applicability_domain="lower_confidence_predicted_structure",
        structure_selection_confidence=0.85,
        poses=[_pose()],
        interaction_profiles=[_profile()],
    )

    assert predicted.consensus_score < experimental.consensus_score
    assert predicted.applicability_domain == "lower_confidence_predicted_structure"
    assert any("predicted" in warning.lower() for warning in predicted.warnings)


def test_docking_only_score_not_high_confidence() -> None:
    assessment = score_structure_aware_assessment(
        molecule_id="mol-1",
        molecule_name="Docking only",
        target_symbol="LRRK2",
        ligand_origin="existing",
        structure_id="RCSB_PDB:6LIG",
        applicability_domain="suitable_experimental_structure",
        structure_selection_confidence=0.0,
        receptor_preparation_confidence=0.0,
        ligand_preparation_confidence=0.0,
        poses=[_pose(docking_score=1.0, confidence=0.2, pose_quality={})],
        interaction_profiles=[],
    )

    assert assessment.consensus_score <= 0.4
    assert assessment.recommendation == "needs_structure_review"
    assert any("docking score alone" in warning.lower() for warning in assessment.warnings)


def test_generated_molecule_caution_present() -> None:
    assessment = score_structure_aware_assessment(
        molecule_id="gen-1",
        molecule_name="Generated",
        target_symbol="LRRK2",
        ligand_origin="generated",
        structure_id="RCSB_PDB:6LIG",
        applicability_domain="suitable_experimental_structure",
        poses=[_pose(molecule_id="gen-1")],
        interaction_profiles=[_profile(molecule_id="gen-1")],
    )

    assert any("generated molecule" in warning.lower() for warning in assessment.warnings)
    assert assessment.metadata["generated_molecule_caution"] is True
    assert "computational" in assessment.explanation.lower()
