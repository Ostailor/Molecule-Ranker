from __future__ import annotations

from pathlib import Path

import pytest

from molecule_ranker.structure.ligand_prep import LigandPrepConfig, prepare_ligand_3d


def test_valid_smiles_produces_conformers_and_artifacts(tmp_path: Path) -> None:
    prep = prepare_ligand_3d(
        molecule_id="mol-1",
        molecule_name="Ethanol",
        origin="existing",
        canonical_smiles="CCO",
        config=LigandPrepConfig(
            ligand_conformer_count=3,
            ligand_artifact_dir=tmp_path / "ligands",
        ),
    )

    assert prep.molecule_id == "mol-1"
    assert prep.origin == "existing"
    assert prep.conformer_count >= 1
    assert prep.conformer_count <= 3
    assert len(prep.prepared_ligand_paths) == prep.conformer_count
    assert all(Path(path).exists() for path in prep.prepared_ligand_paths)
    assert prep.metadata["not_experimental_evidence"] is True
    assert prep.metadata["no_activity_inference_from_conformation"] is True


def test_invalid_smiles_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid canonical SMILES"):
        prepare_ligand_3d(
            molecule_id="bad",
            molecule_name="Bad",
            origin="existing",
            canonical_smiles="not-a-smiles",
            config=LigandPrepConfig(ligand_artifact_dir=tmp_path / "ligands"),
        )


def test_ambiguous_stereochemistry_warns(tmp_path: Path) -> None:
    prep = prepare_ligand_3d(
        molecule_id="mol-alkene",
        molecule_name="Ambiguous alkene",
        origin="existing",
        canonical_smiles="CC=CC",
        config=LigandPrepConfig(ligand_artifact_dir=tmp_path / "ligands"),
    )

    assert prep.stereochemistry_status == "ambiguous"
    assert any("stereochemistry" in warning.lower() for warning in prep.warnings)


def test_generated_molecule_origin_preserved(tmp_path: Path) -> None:
    prep = prepare_ligand_3d(
        molecule_id="gen-1",
        molecule_name="Generated 1",
        origin="generated",
        canonical_smiles="CCO",
        config=LigandPrepConfig(ligand_artifact_dir=tmp_path / "ligands"),
    )

    assert prep.origin == "generated"
    assert any("generated molecule" in warning.lower() for warning in prep.warnings)


def test_conformer_limit_respected(tmp_path: Path) -> None:
    prep = prepare_ligand_3d(
        molecule_id="mol-2",
        molecule_name="Conformer limited",
        origin="existing",
        canonical_smiles="CCCCO",
        config=LigandPrepConfig(
            ligand_conformer_count=50,
            ligand_max_attempts=10,
            max_ligands_for_docking=1,
            ligand_artifact_dir=tmp_path / "ligands",
        ),
    )

    assert prep.conformer_count <= 10
    assert prep.metadata["requested_conformer_count"] == 50
    assert prep.metadata["effective_conformer_count"] <= 10
    assert any("limited" in warning.lower() for warning in prep.warnings)
