from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from molecule_ranker.structure.receptor_prep import ReceptorPrepConfig, prepare_receptor
from molecule_ranker.structure.schemas import StructureRecord


def _structure(tmp_path: Path, **overrides: Any) -> StructureRecord:
    input_path = tmp_path / "input.pdb"
    input_path.write_text(
        "\n".join(
            [
                "HEADER    TEST",
                "ATOM      1  N   GLY A   1      11.104  13.207   9.000  1.00 20.00           N",
                "ATOM      2  N   GLY B   1      12.104  14.207  10.000  1.00 20.00           N",
                "HETATM    3  O   HOH A 201      13.104  15.207  11.000  1.00 20.00           O",
                "HETATM    4 NA    NA A 301      14.104  16.207  12.000  1.00 20.00          NA",
                "HETATM    5  C1  ATP A 401      15.104  17.207  13.000  1.00 20.00           C",
                "END",
            ]
        )
        + "\n"
    )
    payload: dict[str, Any] = {
        "structure_id": "RCSB_PDB:6XYZ",
        "source": "RCSB_PDB",
        "external_id": "6XYZ",
        "target_symbol": "LRRK2",
        "target_identifiers": {"uniprot": "Q5S007"},
        "structure_type": "experimental",
        "experimental_method": "X-ray diffraction",
        "resolution_angstrom": 1.8,
        "coverage": {"overall": 0.9},
        "chains": ["A", "B"],
        "ligands": [{"ligand_id": "ATP"}, {"ligand_id": "HOH"}, {"ligand_id": "NA"}],
        "mutations": [],
        "organism": "Homo sapiens",
        "release_date": "2020-01-02",
        "quality_metrics": {},
        "url": "https://www.rcsb.org/structure/6XYZ",
        "retrieved_at": datetime(2026, 1, 2, tzinfo=UTC),
        "metadata": {
            "input_structure_path": str(input_path),
            "sha256": "user-structure-hash",
            "alternate_locations_present": True,
        },
    }
    payload.update(overrides)
    return StructureRecord(**payload)


def test_metadata_only_receptor_prep_records_selection_without_rewriting(tmp_path: Path) -> None:
    structure = _structure(tmp_path)

    prep = prepare_receptor(
        structure,
        selected_chain_ids=["A"],
        config=ReceptorPrepConfig(
            receptor_prep_method="metadata_only",
            receptor_artifact_dir=tmp_path / "receptors",
        ),
    )

    assert prep.preparation_method == "metadata_only"
    assert prep.input_structure_path.endswith("input.pdb")
    assert prep.prepared_receptor_path is None
    assert prep.kept_chains == ["A"]
    assert prep.removed_chains == ["B"]
    assert prep.kept_heterogens == []
    assert {"HOH", "NA", "ATP"} <= set(prep.removed_heterogens)
    assert prep.missing_hydrogens_added is False
    assert prep.alternate_locations_resolved is False
    assert any("computational workflow" in warning.lower() for warning in prep.warnings)
    assert prep.metadata["docking_ready"] is False


def test_user_structure_hash_is_preserved(tmp_path: Path) -> None:
    structure = _structure(
        tmp_path,
        source="user_supplied",
        structure_type="user_supplied",
        metadata={"input_structure_path": str(tmp_path / "input.pdb"), "sha256": "abc123"},
    )

    prep = prepare_receptor(
        structure,
        selected_chain_ids=["A"],
        config=ReceptorPrepConfig(receptor_prep_method="metadata_only"),
    )

    assert prep.metadata["source_structure_sha256"] == "abc123"
    assert any("user-supplied" in warning.lower() for warning in prep.warnings)


def test_optional_pdbfixer_path_can_be_mocked(tmp_path: Path) -> None:
    structure = _structure(tmp_path)

    def fake_pdbfixer_runner(
        structure_record: StructureRecord,
        input_path: Path,
        output_path: Path,
        config: ReceptorPrepConfig,
    ) -> dict[str, Any]:
        output_path.write_text(input_path.read_text() + "REMARK HYDROGENS ADDED\n")
        return {
            "missing_atoms_fixed": True,
            "missing_hydrogens_added": True,
            "missing_loops_modeled": False,
            "alternate_locations_resolved": True,
            "warnings": ["PDBFixer mocked path used."],
        }

    prep = prepare_receptor(
        structure,
        selected_chain_ids=["A"],
        config=ReceptorPrepConfig(
            receptor_prep_method="pdbfixer_optional",
            allow_pdbfixer=True,
            keep_reference_ligand=True,
            receptor_artifact_dir=tmp_path / "receptors",
        ),
        pdbfixer_runner=fake_pdbfixer_runner,
    )

    assert prep.preparation_method == "pdbfixer_optional"
    assert prep.prepared_receptor_path is not None
    assert Path(prep.prepared_receptor_path).exists()
    assert prep.missing_atoms_fixed is True
    assert prep.missing_hydrogens_added is True
    assert prep.alternate_locations_resolved is True
    assert prep.kept_heterogens == ["ATP"]
    assert "HOH" in prep.removed_heterogens


def test_preparation_failure_warns_and_skips_docking_unless_strict(tmp_path: Path) -> None:
    structure = _structure(tmp_path)

    def failing_runner(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mock prep failed")

    prep = prepare_receptor(
        structure,
        selected_chain_ids=["A"],
        config=ReceptorPrepConfig(
            receptor_prep_method="pdbfixer_optional",
            allow_pdbfixer=True,
            strict_receptor_prep=False,
            receptor_artifact_dir=tmp_path / "receptors",
        ),
        pdbfixer_runner=failing_runner,
    )

    assert prep.prepared_receptor_path is None
    assert prep.confidence == 0.0
    assert prep.metadata["docking_ready"] is False
    assert any("mock prep failed" in warning for warning in prep.warnings)

    with pytest.raises(RuntimeError, match="mock prep failed"):
        prepare_receptor(
            structure,
            selected_chain_ids=["A"],
            config=ReceptorPrepConfig(
                receptor_prep_method="pdbfixer_optional",
                allow_pdbfixer=True,
                strict_receptor_prep=True,
                receptor_artifact_dir=tmp_path / "receptors",
            ),
            pdbfixer_runner=failing_runner,
        )


def test_unwanted_chains_removed_according_to_config(tmp_path: Path) -> None:
    structure = _structure(tmp_path)

    prep = prepare_receptor(
        structure,
        selected_chain_ids=["B"],
        config=ReceptorPrepConfig(
            receptor_prep_method="rdkit_basic",
            keep_reference_ligand=False,
            receptor_artifact_dir=tmp_path / "receptors",
        ),
    )

    assert prep.kept_chains == ["B"]
    assert prep.removed_chains == ["A"]
    assert "ATP" in prep.removed_heterogens
    assert prep.prepared_receptor_path is not None
    assert Path(prep.prepared_receptor_path).exists()
