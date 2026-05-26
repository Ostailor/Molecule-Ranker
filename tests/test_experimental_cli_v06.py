from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.cli import app


def _write_candidates(path):
    path.write_text(
        json.dumps(
            {
                "disease": {
                    "input_name": "PD",
                    "canonical_name": "Parkinson disease",
                },
                "candidates": [
                    {
                        "name": "Rasagiline",
                        "molecule_type": "small_molecule",
                        "identifiers": {"chembl": "CHEMBL887"},
                        "known_targets": ["MAOB"],
                        "score": 0.68,
                    },
                    {
                        "name": "Safinamide",
                        "molecule_type": "small_molecule",
                        "identifiers": {"chembl": "CHEMBL2103830"},
                        "known_targets": ["MAOB"],
                        "score": 0.64,
                    },
                ],
            }
        )
    )


def test_experimental_cli_import_summarize_and_prioritize(tmp_path):
    assay_path = tmp_path / "assay_results.csv"
    assay_path.write_text(
        "\n".join(
            [
                "experiment_id,assay_name,candidate_id,molecule_name,target_symbol,outcome,value,unit",
                "exp-cli,Primary screen,CHEMBL887,Rasagiline,MAOB,positive,0.9,relative_activity",
            ]
        )
        + "\n"
    )
    candidates_path = tmp_path / "candidates.json"
    db_path = tmp_path / "experiments.sqlite"
    _write_candidates(candidates_path)
    runner = CliRunner()

    validate = runner.invoke(
        app,
        ["experimental", "validate", "--input", str(assay_path), "--json"],
    )
    assert validate.exit_code == 0, validate.stdout
    assert json.loads(validate.stdout)["valid_count"] == 1

    imported = runner.invoke(
        app,
        [
            "experimental",
            "import-results",
            "--input",
            str(assay_path),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert imported.exit_code == 0, imported.stdout
    assert json.loads(imported.stdout)["imported_count"] == 1

    summary = runner.invoke(
        app,
        ["experimental", "summarize", "--db-path", str(db_path), "--json"],
    )
    assert summary.exit_code == 0, summary.stdout
    assert json.loads(summary.stdout)["outcome_counts"]["positive"] == 1

    prioritized = runner.invoke(
        app,
        [
            "experimental",
            "prioritize",
            "--db-path",
            str(db_path),
            "--candidates",
            str(candidates_path),
            "--json",
        ],
    )
    assert prioritized.exit_code == 0, prioritized.stdout
    payload = json.loads(prioritized.stdout)
    assert payload["recommendations"][0]["candidate_name"] == "Safinamide"
    assert "No lab protocol" in " ".join(payload["limitations"])
