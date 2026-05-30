from __future__ import annotations

from pathlib import Path
from typing import Any

from molecule_ranker.structure.interactions import (
    InteractionProfileConfig,
    annotate_pose_interactions,
    profile_interactions,
)
from molecule_ranker.structure.schemas import DockingPose


def _pose(**metadata_overrides: Any) -> DockingPose:
    metadata: dict[str, Any] = {
        "ligand_atoms": [
            {
                "atom_id": "L1",
                "element": "O",
                "coord": [0.0, 0.0, 0.0],
                "acceptor": True,
            },
            {
                "atom_id": "L2",
                "element": "C",
                "coord": [4.0, 0.0, 0.0],
                "hydrophobic": True,
            },
            {
                "atom_id": "L3",
                "element": "N",
                "coord": [8.0, 0.0, 0.0],
                "charge": 1,
            },
        ],
        "receptor_atoms": [
            {
                "residue": "LYS1906",
                "atom_name": "NZ",
                "element": "N",
                "coord": [0.0, 0.0, 2.8],
                "donor": True,
            },
            {
                "residue": "VAL1893",
                "atom_name": "CG1",
                "element": "C",
                "coord": [4.0, 0.0, 3.7],
                "hydrophobic": True,
            },
            {
                "residue": "ASP2017",
                "atom_name": "OD1",
                "element": "O",
                "coord": [8.0, 0.0, 3.0],
                "charge": -1,
            },
        ],
    }
    metadata.update(metadata_overrides)
    return DockingPose(
        pose_id="pose-1",
        docking_run_id="dock-run-1",
        molecule_id="mol-1",
        molecule_name="Example",
        canonical_smiles="CCO",
        target_symbol="LRRK2",
        structure_id="RCSB_PDB:6LIG",
        binding_site_id="site-1",
        pose_rank=1,
        docking_score=0.62,
        score_units="normalized_docking_score_0_1",
        pose_path=str(Path("pose.pdbqt")),
        interaction_summary={},
        pose_quality={},
        confidence=0.5,
        warnings=["Pose is not experimental evidence."],
        metadata=metadata,
    )


def test_mocked_pose_gives_contact_counts() -> None:
    profile = profile_interactions(_pose(), config=InteractionProfileConfig())

    assert profile.interaction_counts["hydrogen_bond_like"] == 1
    assert profile.interaction_counts["hydrophobic_contact"] == 1
    assert profile.interaction_counts["salt_bridge_like"] == 1
    assert set(profile.key_residue_contacts) == {"ASP2017", "LYS1906", "VAL1893"}
    assert profile.metadata["method"] == "simple_geometric_heuristics"
    assert profile.metadata["not_experimental_evidence"] is True


def test_missing_coordinates_warning() -> None:
    profile = profile_interactions(
        _pose(ligand_atoms=[], receptor_atoms=[]),
        config=InteractionProfileConfig(),
    )

    assert profile.interactions == []
    assert profile.interaction_counts == {}
    assert profile.confidence == 0.0
    assert any("coordinates" in warning.lower() for warning in profile.warnings)


def test_metal_coordination_warning() -> None:
    pose = _pose(
        ligand_atoms=[
            {"atom_id": "L1", "element": "O", "coord": [0.0, 0.0, 0.0], "acceptor": True}
        ],
        receptor_atoms=[
            {
                "residue": "ZN300",
                "atom_name": "ZN",
                "element": "ZN",
                "coord": [0.0, 0.0, 2.2],
                "metal": True,
            }
        ],
    )

    profile = profile_interactions(pose, config=InteractionProfileConfig())

    assert profile.interaction_counts["metal_coordination_like"] == 1
    assert any("metal coordination" in warning.lower() for warning in profile.warnings)


def test_interaction_profile_stored_in_docking_pose() -> None:
    pose = _pose()

    annotated = annotate_pose_interactions(pose, config=InteractionProfileConfig())

    assert annotated.interaction_summary["profile_id"] == "interaction-profile-pose-1"
    assert annotated.interaction_summary["interaction_counts"]["hydrogen_bond_like"] == 1
    assert "LYS1906" in annotated.interaction_summary["key_residue_contacts"]
    assert (
        annotated.metadata["interaction_profile"]["metadata"]["not_experimental_evidence"]
        is True
    )
