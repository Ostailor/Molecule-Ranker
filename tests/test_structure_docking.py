from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from molecule_ranker.structure.docking import (
    DockingConfig,
    NullDockingEngine,
    VinaDockingEngine,
    run_docking,
)
from molecule_ranker.structure.schemas import (
    BindingSiteDefinition,
    Ligand3DPreparation,
    ReceptorPreparation,
)


def _receptor(tmp_path: Path) -> ReceptorPreparation:
    receptor_path = tmp_path / "receptor.pdbqt"
    receptor_path.write_text("RECEPTOR\n")
    return ReceptorPreparation(
        receptor_prep_id="receptor-prep-1",
        structure_id="RCSB_PDB:6LIG",
        target_symbol="LRRK2",
        input_structure_path=str(receptor_path),
        prepared_receptor_path=str(receptor_path),
        preparation_method="rdkit_basic",
        protonation_policy="input",
        kept_chains=["A"],
        removed_chains=[],
        kept_heterogens=[],
        removed_heterogens=[],
        missing_atoms_fixed=False,
        missing_hydrogens_added=False,
        missing_loops_modeled=False,
        alternate_locations_resolved=False,
        warnings=[],
        confidence=0.6,
        metadata={"docking_ready": True},
    )


def _ligand(tmp_path: Path, molecule_id: str = "mol-1") -> Ligand3DPreparation:
    ligand_path = tmp_path / f"{molecule_id}.sdf"
    ligand_path.write_text("LIGAND\n")
    return Ligand3DPreparation(
        ligand_prep_id=f"ligand-prep-{molecule_id}",
        molecule_id=molecule_id,
        molecule_name="Ligand",
        origin="existing",
        canonical_smiles="CCO",
        conformer_count=1,
        prepared_ligand_paths=[str(ligand_path)],
        charge_method="MMFF94",
        protonation_policy="input",
        stereochemistry_status="specified",
        warnings=[],
        confidence=0.7,
        metadata={"not_experimental_evidence": True},
    )


def _site() -> BindingSiteDefinition:
    return BindingSiteDefinition(
        binding_site_id="site-1",
        target_symbol="LRRK2",
        structure_id="RCSB_PDB:6LIG",
        method="user_supplied_box",
        center=[1.0, 2.0, 3.0],
        box_size=[12.0, 12.0, 12.0],
        residues=[],
        reference_ligand_id=None,
        confidence=0.6,
        warnings=[],
        metadata={"provenance": "operator_artifact:site-1"},
    )


def test_disabled_docking_no_op(tmp_path: Path) -> None:
    run = run_docking(
        _receptor(tmp_path),
        [_ligand(tmp_path)],
        _site(),
        DockingConfig(enable_structure_docking=False, docking_artifact_dir=tmp_path / "dock"),
    )

    assert run.status == "skipped"
    assert run.ligand_count == 0
    assert run.pose_count == 0
    assert run.metadata["docking_performed"] is False
    assert any("disabled" in warning.lower() for warning in run.warnings)


def test_null_docking_engine_works(tmp_path: Path) -> None:
    run = NullDockingEngine().dock(
        _receptor(tmp_path),
        [_ligand(tmp_path)],
        _site(),
        DockingConfig(enable_structure_docking=True).model_dump(),
    )

    assert run.docking_engine == "null"
    assert run.status == "skipped"
    assert run.config["enable_structure_docking"] is True
    assert run.metadata["no_evidence_item_created"] is True


def test_vina_missing_dependency_skips_or_fails_strict(tmp_path: Path) -> None:
    engine = VinaDockingEngine(dependency_loader=lambda: None)

    run = engine.dock(
        _receptor(tmp_path),
        [_ligand(tmp_path)],
        _site(),
        DockingConfig(enable_structure_docking=True).model_dump(),
    )

    assert run.status == "skipped"
    assert any("vina" in warning.lower() for warning in run.warnings)

    with pytest.raises(RuntimeError, match="AutoDock Vina"):
        engine.dock(
            _receptor(tmp_path),
            [_ligand(tmp_path)],
            _site(),
            DockingConfig(enable_structure_docking=True, strict_docking=True).model_dump(),
        )


def test_mocked_vina_engine_creates_docking_run_and_pose_artifacts(tmp_path: Path) -> None:
    fake_calls: list[tuple[str, Any]] = []

    class FakeVina:
        def __init__(self, *, sf_name: str = "vina", seed: int | None = None) -> None:
            fake_calls.append(("init", {"sf_name": sf_name, "seed": seed}))

        def set_receptor(self, path: str) -> None:
            fake_calls.append(("set_receptor", path))

        def set_ligand_from_file(self, path: str) -> None:
            fake_calls.append(("set_ligand_from_file", path))

        def compute_vina_maps(self, *, center: list[float], box_size: list[float]) -> None:
            fake_calls.append(("compute_vina_maps", {"center": center, "box_size": box_size}))

        def dock(self, *, exhaustiveness: int, n_poses: int) -> None:
            fake_calls.append(("dock", {"exhaustiveness": exhaustiveness, "n_poses": n_poses}))

        def energies(self, *, n_poses: int) -> list[list[float]]:
            return [[-8.2, 0.0, 0.0] for _ in range(n_poses)]

        def write_poses(self, path: str, *, n_poses: int, overwrite: bool) -> None:
            Path(path).write_text(f"POSES {n_poses} {overwrite}\n")

    artifact_dir = tmp_path / "allowed" / "dock"
    run = VinaDockingEngine(dependency_loader=lambda: FakeVina).dock(
        _receptor(tmp_path),
        [_ligand(tmp_path)],
        _site(),
        DockingConfig(
            enable_structure_docking=True,
            docking_num_poses=2,
            write_pose_files=True,
            docking_artifact_dir=artifact_dir,
            docking_random_seed=123,
        ).model_dump(),
    )

    assert run.status == "succeeded"
    assert run.docking_engine == "AutoDock Vina"
    assert run.ligand_count == 1
    assert run.pose_count == 2
    assert Path(run.artifacts["pose_mol-1"]).exists()
    assert run.metadata["raw_scores"]["mol-1"] == [-8.2, -8.2]
    assert ("dock", {"exhaustiveness": 8, "n_poses": 2}) in fake_calls


def test_docking_artifacts_written_only_inside_allowed_dir(tmp_path: Path) -> None:
    class FakeVina:
        def __init__(self, **_: Any) -> None:
            pass

        def set_receptor(self, _: str) -> None:
            pass

        def set_ligand_from_file(self, _: str) -> None:
            pass

        def compute_vina_maps(self, **_: Any) -> None:
            pass

        def dock(self, **_: Any) -> None:
            pass

        def energies(self, *, n_poses: int) -> list[list[float]]:
            return [[-7.0] for _ in range(n_poses)]

        def write_poses(self, path: str, **_: Any) -> None:
            Path(path).write_text("POSE\n")

    allowed_dir = (tmp_path / "allowed").resolve()
    run = VinaDockingEngine(dependency_loader=lambda: FakeVina).dock(
        _receptor(tmp_path),
        [_ligand(tmp_path, molecule_id="../unsafe ligand")],
        _site(),
        DockingConfig(
            enable_structure_docking=True,
            docking_artifact_dir=allowed_dir,
            write_pose_files=True,
        ).model_dump(),
    )

    artifact_paths = [Path(path).resolve() for path in run.artifacts.values()]
    assert artifact_paths
    assert all(path.is_relative_to(allowed_dir) for path in artifact_paths)
    assert all(path.exists() for path in artifact_paths)
    assert run.started_at.tzinfo is not None
    assert run.completed_at is not None and run.completed_at.tzinfo is not None
