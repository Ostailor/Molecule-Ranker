from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.experiments.store import ExperimentalResultStore


def _write_assay_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "candidate_name,candidate_id,candidate_origin,canonical_smiles,disease_name,"
                "target_symbol,assay_name,assay_type,endpoint_name,endpoint_category,"
                "measured_value,unit,outcome_label,activity_direction,qc_status,source_record_id",
                "Rasagiline,CHEMBL887,existing,C#CCN1CCC2=CC=CC=C21,Parkinson disease,"
                "MAOB,Binding assay,biochemical,binding_affinity,potency,12,nM,"
                "positive,active,passed,src-1",
            ]
        )
        + "\n"
    )


def _write_assay_json(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "candidate_name": "Safinamide",
                        "candidate_id": "CHEMBL2103830",
                        "candidate_origin": "existing",
                        "disease_name": "Parkinson disease",
                        "target_symbol": "MAOB",
                        "assay_name": "Cell assay",
                        "assay_type": "cellular",
                        "endpoint_name": "cellular_activity",
                        "endpoint_category": "phenotypic",
                        "measured_value": "0.2",
                        "unit": "relative_activity",
                        "outcome_label": "negative",
                        "activity_direction": "inactive",
                        "qc_status": "passed",
                        "source_record_id": "json-1",
                    }
                ]
            }
        )
        + "\n"
    )


def _write_run_artifacts(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "candidates.json").write_text(
        json.dumps(
            {
                "disease": {"input_name": "PD", "canonical_name": "Parkinson disease"},
                "candidates": [
                    {
                        "name": "Rasagiline",
                        "molecule_type": "small_molecule",
                        "origin": "existing",
                        "identifiers": {"chembl": "CHEMBL887"},
                        "known_targets": ["MAOB"],
                        "chemical_metadata": {
                            "canonical_smiles": "C#CCN1CCC2=CC=CC=C21"
                        },
                        "score": 0.72,
                    },
                    {
                        "name": "Safinamide",
                        "molecule_type": "small_molecule",
                        "origin": "existing",
                        "identifiers": {"chembl": "CHEMBL2103830"},
                        "known_targets": ["MAOB"],
                        "chemical_metadata": {"canonical_smiles": "CCN"},
                        "score": 0.51,
                    },
                ],
            }
        )
        + "\n"
    )
    (run_dir / "generated_candidates.json").write_text(
        json.dumps(
            {
                "generated_molecule_hypotheses": [
                    {
                        "name": "Generated-MAOB-001",
                        "canonical_smiles": "CCOc1ccccc1N",
                        "target_symbol": "MAOB",
                        "generation_score": 0.58,
                        "min_seed_similarity": 0.2,
                        "max_seed_similarity": 0.7,
                        "mean_seed_similarity": 0.5,
                        "warnings": ["Generated hypothesis; no direct activity evidence."],
                    }
                ]
            }
        )
        + "\n"
    )


def test_experiment_import_dry_run_and_import_csv(tmp_path):
    runner = CliRunner()
    csv_path = tmp_path / "results.csv"
    db_path = tmp_path / "results.sqlite"
    _write_assay_csv(csv_path)

    dry_run = runner.invoke(
        app,
        [
            "experiment",
            "import",
            str(csv_path),
            "--db-path",
            str(db_path),
            "--dry-run",
            "--json",
        ],
    )
    assert dry_run.exit_code == 0, dry_run.stdout
    assert json.loads(dry_run.stdout)["dry_run"] is True
    assert not db_path.exists()

    imported = runner.invoke(
        app,
        [
            "experiment",
            "import",
            str(csv_path),
            "--db-path",
            str(db_path),
            "--imported-by",
            "tester",
            "--json",
        ],
    )
    assert imported.exit_code == 0, imported.stdout
    payload = json.loads(imported.stdout)
    assert payload["imported_count"] == 1
    assert payload["outcome_counts"]["positive"] == 1
    store = ExperimentalResultStore(db_path)
    assert len(store.list_results()) == 1
    assert store.list_audit_events()[-1].event_type == "assay_results_imported"


def test_experiment_json_import_list_summarize_export_and_report(tmp_path):
    runner = CliRunner()
    json_path = tmp_path / "results.json"
    db_path = tmp_path / "results.sqlite"
    export_path = tmp_path / "export.json"
    run_dir = tmp_path / "results" / "parkinson-disease"
    _write_assay_json(json_path)
    _write_run_artifacts(run_dir)

    imported = runner.invoke(
        app,
        [
            "experiment",
            "import",
            str(json_path),
            "--format",
            "json",
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert imported.exit_code == 0, imported.stdout

    listed = runner.invoke(
        app,
        [
            "experiment",
            "list",
            "--db-path",
            str(db_path),
            "--target-symbol",
            "MAOB",
            "--outcome-label",
            "negative",
            "--json",
        ],
    )
    assert listed.exit_code == 0, listed.stdout
    assert json.loads(listed.stdout)["results"][0]["candidate_name"] == "Safinamide"

    summary = runner.invoke(
        app,
        [
            "experiment",
            "summarize",
            "--candidate-name",
            "Safinamide",
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert summary.exit_code == 0, summary.stdout
    assert json.loads(summary.stdout)["negative_count"] == 1

    exported = runner.invoke(
        app,
        ["experiment", "export", "--db-path", str(db_path), "--output", str(export_path)],
    )
    assert exported.exit_code == 0, exported.stdout
    assert json.loads(export_path.read_text())["results"][0]["candidate_name"] == "Safinamide"

    report = runner.invoke(
        app,
        [
            "experiment",
            "report",
            "--db-path",
            str(db_path),
            "--from-run",
            str(run_dir),
        ],
    )
    assert report.exit_code == 0, report.stdout
    assert "Experimental Result Summary" in report.stdout
    assert "Reviewer decisions remain separate" in report.stdout


def test_experiment_link_and_active_learning(tmp_path):
    runner = CliRunner()
    csv_path = tmp_path / "results.csv"
    db_path = tmp_path / "results.sqlite"
    run_dir = tmp_path / "results" / "parkinson-disease"
    _write_assay_csv(csv_path)
    _write_run_artifacts(run_dir)

    imported = runner.invoke(
        app,
        ["experiment", "import", str(csv_path), "--db-path", str(db_path), "--json"],
    )
    assert imported.exit_code == 0, imported.stdout

    linked = runner.invoke(
        app,
        [
            "experiment",
            "link",
            "--from-run",
            str(run_dir),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert linked.exit_code == 0, linked.stdout
    assert json.loads(linked.stdout)["linked_count"] == 1
    result = ExperimentalResultStore(db_path).list_results()[0]
    assert result.metadata["linked_candidate_id"] == "CHEMBL887"

    batch = runner.invoke(
        app,
        [
            "experiment",
            "active-learning",
            "--from-run",
            str(run_dir),
            "--db-path",
            str(db_path),
            "--strategy",
            "evidence_gap",
            "--batch-size",
            "2",
            "--include-generated",
            "--json",
        ],
    )
    assert batch.exit_code == 0, batch.stdout
    payload = json.loads(batch.stdout)
    assert payload["strategy"] == "evidence_gap"
    assert payload["suggestions"]
    assert sqlite3.connect(db_path).execute(
        "select count(*) from active_learning_batches"
    ).fetchone()[0] == 1
