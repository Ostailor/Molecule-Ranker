from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def _write_user_structure(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "ATOM      1  N   ALA A   1      11.104  13.207   9.447  1.00 20.00           N",
                "ATOM      2  CA  ALA A   1      12.560  13.307   9.447  1.00 20.00           C",
                "END",
            ]
        )
        + "\n"
    )


def _write_generated_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "generated_candidates.json").write_text(
        json.dumps(
            {
                "retained_generated_molecules": [
                    {
                        "generated_id": "GEN-1",
                        "smiles": "CCO",
                        "canonical_smiles": "CCO",
                        "selfies": None,
                        "inchi_key": "GEN1-INCHIKEY",
                        "origin": "generated",
                        "generation_method": "test",
                        "parent_seed_ids": ["seed-1"],
                        "conditioned_targets": ["MAOB"],
                        "objective_id": "objective-1",
                        "generation_round": 1,
                        "descriptors": {},
                        "fingerprints": {},
                        "validation": {
                            "valid_rdkit_mol": True,
                            "sanitization_ok": True,
                            "canonicalization_ok": True,
                            "allowed_elements_ok": True,
                            "descriptor_bounds_ok": True,
                            "pains_or_alerts": [],
                            "rejection_reasons": [],
                            "metadata": {},
                        },
                        "novelty": None,
                        "diversity_cluster": None,
                        "generation_score": 0.7,
                        "score_breakdown": None,
                        "developability_assessment": None,
                        "warnings": ["in_silico_hypothesis_only"],
                        "metadata": {},
                    }
                ]
            }
        )
        + "\n"
    )


def test_structure_cli_help_works() -> None:
    runner = CliRunner()
    for args in (
        ["structure", "--help"],
        ["structure", "find", "--help"],
        ["structure", "select", "--help"],
        ["structure", "prepare-receptor", "--help"],
        ["structure", "prepare-ligands", "--help"],
        ["structure", "define-site", "--help"],
        ["structure", "dock", "--help"],
        ["structure", "assess", "--help"],
        ["structure", "report", "--help"],
        ["structure", "benchmark", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output


def test_structure_cli_null_docking_workflow(tmp_path: Path) -> None:
    runner = CliRunner()
    structure_file = tmp_path / "input" / "receptor.pdb"
    structure_file.parent.mkdir()
    _write_user_structure(structure_file)
    run_dir = tmp_path / "run"
    _write_generated_run(run_dir)

    structures = tmp_path / "structures.json"
    result = runner.invoke(
        app,
        [
            "structure",
            "find",
            "--target-symbol",
            "MAOB",
            "--target-id",
            str(structure_file),
            "--source",
            "user",
            "--output",
            str(structures),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    structure_payload = json.loads(structures.read_text())
    structure_id = structure_payload["structures"][0]["structure_id"]

    selection = tmp_path / "structure_selection.json"
    result = runner.invoke(
        app,
        [
            "structure",
            "select",
            "--structures",
            str(structures),
            "--target-symbol",
            "MAOB",
            "--allow-user-supplied",
            "--output",
            str(selection),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    receptor = tmp_path / "receptor_preparation.json"
    result = runner.invoke(
        app,
        [
            "structure",
            "prepare-receptor",
            "--structure-id",
            structure_id,
            "--structure-file",
            str(structure_file),
            "--method",
            "metadata_only",
            "--output",
            str(receptor),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    ligands = tmp_path / "ligand_preparation.json"
    result = runner.invoke(
        app,
        [
            "structure",
            "prepare-ligands",
            "--from-run",
            str(run_dir),
            "--include-generated",
            "--max-ligands",
            "1",
            "--output",
            str(ligands),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    site = tmp_path / "binding_sites.json"
    result = runner.invoke(
        app,
        [
            "structure",
            "define-site",
            "--structure-selection",
            str(selection),
            "--method",
            "co_crystal_ligand",
            "--output",
            str(site),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    docking = tmp_path / "docking_runs.json"
    result = runner.invoke(
        app,
        [
            "structure",
            "dock",
            "--receptor",
            str(receptor),
            "--ligands",
            str(ligands),
            "--binding-site",
            str(site),
            "--engine",
            "null",
            "--max-ligands",
            "1",
            "--output",
            str(docking),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    docking_payload = json.loads(docking.read_text())
    assert docking_payload["docking_runs"][0]["docking_engine"] == "null"
    assert docking_payload["docking_runs"][0]["status"] == "skipped"

    assessments = tmp_path / "structure_aware_assessments.json"
    poses = tmp_path / "docking_poses.json"
    poses.write_text(json.dumps({"docking_poses": []}) + "\n")
    result = runner.invoke(
        app,
        [
            "structure",
            "assess",
            "--docking-runs",
            str(docking),
            "--poses",
            str(poses),
            "--output",
            str(assessments),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        json.loads(assessments.read_text())["structure_aware_assessments"][0][
            "applicability_domain"
        ]
        == "unavailable"
    )


def test_structure_cli_strict_vina_failure(tmp_path: Path) -> None:
    runner = CliRunner()
    receptor = tmp_path / "receptor_preparation.json"
    receptor.write_text(
        json.dumps(
            {
                "receptor_preparation": [
                    {
                        "receptor_prep_id": "prep-1",
                        "structure_id": "structure-1",
                        "target_symbol": "MAOB",
                        "input_structure_path": "input.pdb",
                        "prepared_receptor_path": None,
                        "preparation_method": "metadata_only",
                        "protonation_policy": "metadata_only",
                        "kept_chains": [],
                        "removed_chains": [],
                        "kept_heterogens": [],
                        "removed_heterogens": [],
                        "missing_atoms_fixed": False,
                        "missing_hydrogens_added": False,
                        "missing_loops_modeled": False,
                        "alternate_locations_resolved": False,
                        "warnings": [],
                        "confidence": 0.0,
                        "metadata": {},
                    }
                ]
            }
        )
        + "\n"
    )
    ligands = tmp_path / "ligand_preparation.json"
    ligands.write_text(json.dumps({"ligand_preparation": []}) + "\n")
    site = tmp_path / "binding_sites.json"
    site.write_text(
        json.dumps(
            {
                "binding_sites": [
                    {
                        "binding_site_id": "site-1",
                        "target_symbol": "MAOB",
                        "structure_id": "structure-1",
                        "method": "unavailable",
                        "center": None,
                        "box_size": None,
                        "residues": [],
                        "reference_ligand_id": None,
                        "confidence": 0.0,
                        "warnings": [],
                        "metadata": {},
                    }
                ]
            }
        )
        + "\n"
    )

    result = runner.invoke(
        app,
        [
            "structure",
            "dock",
            "--receptor",
            str(receptor),
            "--ligands",
            str(ligands),
            "--binding-site",
            str(site),
            "--engine",
            "vina",
            "--enable-docking",
            "--strict",
            "--output",
            str(tmp_path / "docking_runs.json"),
        ],
    )

    assert result.exit_code == 1
    assert "docking skipped" in result.output.lower()


def test_structure_cli_blocks_unsafe_artifact_paths(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "structure",
            "find",
            "--target-symbol",
            "MAOB",
            "--target-id",
            "P27338",
            "--source",
            "alphafold",
            "--output",
            str(tmp_path / "safe" / ".." / "structures.json"),
        ],
    )

    assert result.exit_code == 1
    assert "Unsafe artifact path" in result.output
