from __future__ import annotations

from pathlib import Path
from typing import Any

from molecule_ranker.structure.pose_qc import PoseQCConfig, evaluate_pose_quality
from molecule_ranker.structure.schemas import BindingSiteDefinition, DockingPose


def _pose(tmp_path: Path, **overrides: Any) -> DockingPose:
    pose_path = tmp_path / "pose.pdbqt"
    pose_path.write_text("POSE\n")
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
        "docking_score": 0.62,
        "score_units": "normalized_docking_score_0_1",
        "pose_path": str(pose_path),
        "interaction_summary": {},
        "pose_quality": {},
        "confidence": 0.5,
        "warnings": ["Pose is not experimental evidence."],
        "metadata": {
            "ligand_coordinates": [[1.0, 1.0, 1.0], [2.0, 1.0, 1.0]],
            "input_heavy_atom_count": 2,
            "pose_heavy_atom_count": 2,
            "raw_docking_score": -7.0,
        },
    }
    payload.update(overrides)
    return DockingPose(**payload)


def _site() -> BindingSiteDefinition:
    return BindingSiteDefinition(
        binding_site_id="site-1",
        target_symbol="LRRK2",
        structure_id="RCSB_PDB:6LIG",
        method="user_supplied_box",
        center=[1.0, 1.0, 1.0],
        box_size=[6.0, 6.0, 6.0],
        residues=[],
        reference_ligand_id=None,
        confidence=0.6,
        warnings=[],
        metadata={"provenance": "operator_artifact:site-1"},
    )


def test_pose_outside_box_rejected(tmp_path: Path) -> None:
    pose = _pose(
        tmp_path,
        metadata={
            "ligand_coordinates": [[20.0, 20.0, 20.0]],
            "input_heavy_atom_count": 1,
            "pose_heavy_atom_count": 1,
        },
    )

    qc_pose = evaluate_pose_quality(pose, _site(), config=PoseQCConfig())

    assert qc_pose.pose_quality["status"] == "reject"
    assert qc_pose.pose_quality["checks"]["ligand_within_binding_site_box"] is False
    assert qc_pose.confidence < pose.confidence
    assert any("outside" in warning.lower() for warning in qc_pose.warnings)


def test_severe_clash_warning(tmp_path: Path) -> None:
    pose = _pose(tmp_path)

    qc_pose = evaluate_pose_quality(
        pose,
        _site(),
        config=PoseQCConfig(
            protein_coordinates=[[1.05, 1.0, 1.0]],
            severe_clash_distance_angstrom=0.2,
        ),
    )

    assert qc_pose.pose_quality["checks"]["no_severe_clashes"] is False
    assert qc_pose.pose_quality["severe_clash_count"] == 1
    assert any("clash" in warning.lower() for warning in qc_pose.warnings)


def test_missing_pose_file_warning(tmp_path: Path) -> None:
    pose = _pose(tmp_path, pose_path=str(tmp_path / "missing.pdbqt"))

    qc_pose = evaluate_pose_quality(pose, _site(), config=PoseQCConfig(expect_pose_file=True))

    assert qc_pose.pose_quality["checks"]["pose_file_exists"] is False
    assert any("pose file" in warning.lower() for warning in qc_pose.warnings)


def test_reference_rmsd_computed_with_mocked_coordinates(tmp_path: Path) -> None:
    pose = _pose(
        tmp_path,
        metadata={
            "ligand_coordinates": [[1.0, 1.0, 1.0], [2.0, 1.0, 1.0]],
            "input_heavy_atom_count": 2,
            "pose_heavy_atom_count": 2,
        },
    )

    qc_pose = evaluate_pose_quality(
        pose,
        _site(),
        config=PoseQCConfig(
            reference_ligand_coordinates=[[1.0, 1.0, 1.0], [2.0, 2.0, 1.0]],
        ),
    )

    assert qc_pose.pose_quality["checks"]["reference_rmsd_computed"] is True
    assert qc_pose.pose_quality["reference_rmsd"] == 0.707
    assert qc_pose.pose_quality["reference_rmsd_note"].startswith("Reference RMSD")


def test_confidence_lowered_on_poor_qc(tmp_path: Path) -> None:
    pose = _pose(
        tmp_path,
        docking_score=None,
        metadata={
            "ligand_coordinates": [[50.0, 50.0, 50.0]],
            "input_heavy_atom_count": 5,
            "pose_heavy_atom_count": 3,
            "raw_docking_score": 99.0,
        },
    )

    qc_pose = evaluate_pose_quality(
        pose,
        _site(),
        config=PoseQCConfig(expect_pose_file=True, reject_on_failed_required_checks=True),
    )

    assert qc_pose.pose_quality["status"] == "reject"
    assert qc_pose.pose_quality["checks"]["docking_score_present"] is False
    assert qc_pose.pose_quality["checks"]["ligand_heavy_atoms_preserved"] is False
    assert qc_pose.pose_quality["checks"]["pose_energy_sane"] is False
    assert qc_pose.confidence <= 0.1
