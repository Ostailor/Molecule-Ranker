from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.experiments.store import ExperimentalResultStore


def _endpoint() -> AssayEndpoint:
    return AssayEndpoint(
        endpoint_id="endpoint-binding-affinity",
        name="binding_affinity",
        endpoint_category="potency",
        directionality="binary",
    )


def _assay_result(index: int, *, positive: bool) -> AssayResult:
    candidate_id = f"CHEMBL{index}"
    smiles = f"CC{'C' * index}O"
    return AssayResult(
        result_id=f"result-{index}",
        candidate_id=candidate_id,
        candidate_name=f"Candidate {index}",
        candidate_origin="existing",
        canonical_smiles=smiles,
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        assay_context=AssayContext(
            assay_context_id="context-binding",
            assay_name="Binding assay",
            assay_type="biochemical",
            target_symbol="MAOB",
            disease_name="Parkinson disease",
            endpoint=_endpoint(),
        ),
        normalized_value=float(index),
        outcome_label="positive" if positive else "negative",
        activity_direction="active" if positive else "inactive",
        confidence=0.8,
        qc_status="passed",
        source="mock",
        imported_at=datetime.now(UTC),
    )


def _store_results(db_path: Path, count: int) -> None:
    store = ExperimentalResultStore(db_path)
    store.import_results(
        [_assay_result(index, positive=index % 2 == 0) for index in range(1, count + 1)]
    )


def _build_dataset(tmp_path: Path, *, count: int) -> Path:
    db_path = tmp_path / "assay.sqlite"
    output_dir = tmp_path / "dataset"
    _store_results(db_path, count)
    result = CliRunner().invoke(
        app,
        [
            "model",
            "dataset",
            "build",
            "--db-path",
            str(db_path),
            "--endpoint-name",
            "binding_affinity",
            "--target-symbol",
            "MAOB",
            "--disease-name",
            "Parkinson disease",
            "--label-type",
            "binary",
            "--output-dir",
            str(output_dir),
            "--feature-family",
            "rdkit_descriptors",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    return Path(payload["manifest"])


def _write_run_artifacts(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "name": "Candidate 1",
                        "molecule_type": "small_molecule",
                        "origin": "existing",
                        "identifiers": {"chembl": "CHEMBL1"},
                        "known_targets": ["MAOB"],
                        "chemical_metadata": {"canonical_smiles": "CCCO"},
                        "score": 0.5,
                    }
                ]
            }
        )
        + "\n"
    )
    (run_dir / "generated_candidates.json").write_text(
        json.dumps({"generated_molecule_hypotheses": []}) + "\n"
    )


def test_model_cli_help_works() -> None:
    runner = CliRunner()

    assert runner.invoke(app, ["model", "--help"]).exit_code == 0
    assert runner.invoke(app, ["model", "dataset", "build", "--help"]).exit_code == 0
    assert runner.invoke(app, ["model", "registry", "--help"]).exit_code == 0


def test_model_dataset_build_works_on_mocked_results(tmp_path: Path) -> None:
    manifest = _build_dataset(tmp_path, count=2)
    payload = json.loads(manifest.read_text())

    assert payload["row_count"] == 2
    assert payload["source_result_ids"] == ["result-1", "result-2"]
    assert payload["positive_count"] == 1
    assert payload["negative_count"] == 1


def test_model_train_skips_when_insufficient_data(tmp_path: Path) -> None:
    manifest = _build_dataset(tmp_path, count=2)
    result = CliRunner().invoke(
        app,
        [
            "model",
            "train",
            "--dataset",
            str(manifest),
            "--model-type",
            "dummy",
            "--split-strategy",
            "random",
            "--output-dir",
            str(tmp_path / "training"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "skipped_insufficient_data"
    assert payload["model_card"] is None


def test_model_train_succeeds_with_enough_synthetic_data(tmp_path: Path) -> None:
    manifest = _build_dataset(tmp_path, count=8)
    result = CliRunner().invoke(
        app,
        [
            "model",
            "train",
            "--dataset",
            str(manifest),
            "--model-type",
            "dummy",
            "--split-strategy",
            "random",
            "--output-dir",
            str(tmp_path / "training"),
            "--random-seed",
            "7",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert Path(payload["model_card"]).exists()


def test_model_predict_writes_model_predictions_json(tmp_path: Path) -> None:
    manifest = _build_dataset(tmp_path, count=8)
    train = CliRunner().invoke(
        app,
        [
            "model",
            "train",
            "--dataset",
            str(manifest),
            "--model-type",
            "dummy",
            "--split-strategy",
            "random",
            "--output-dir",
            str(tmp_path / "training"),
        ],
    )
    assert train.exit_code == 0, train.stdout
    model_card = Path(json.loads(train.stdout)["model_card"])
    run_dir = tmp_path / "results" / "parkinson-disease"
    output = tmp_path / "model_predictions.json"
    _write_run_artifacts(run_dir)

    result = CliRunner().invoke(
        app,
        [
            "model",
            "predict",
            "--model-card",
            str(model_card),
            "--from-run",
            str(run_dir),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(output.read_text())
    assert payload["artifact_type"] == "ModelPredictionArtifact"
    assert payload["predictions"]
    assert payload["predictions"][0]["warnings"]
