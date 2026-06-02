from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.evaluation.prospective import (
    evaluate_prospective_run,
    freeze_prospective_run,
    import_prospective_outcomes,
)


def _metric(report, name: str):
    for metric in report.metrics:
        if metric.name == name:
            return metric
    raise AssertionError(f"missing metric {name}")


def _predictions() -> dict[str, object]:
    return {
        "artifact_id": "ranked_candidates.json",
        "candidate_rankings": [
            {
                "candidate_id": "C1",
                "rank": 1,
                "score": 0.91,
                "selected": True,
                "canonical_smiles": "CCN",
            },
            {
                "generated_id": "G1",
                "rank": 2,
                "score": 0.82,
                "selected": True,
                "canonical_smiles": "CCO",
                "seed_candidate_id": "C-seed",
            },
        ],
    }


def _outcomes(imported_at: datetime) -> dict[str, object]:
    timestamp = imported_at.isoformat()
    return {
        "artifact_id": "assay_results.json",
        "assay_results": [
            {
                "candidate_id": "C1",
                "outcome_label": "positive",
                "qc_status": "passed",
                "imported_at": timestamp,
                "source_record_id": "result-c1",
            },
            {
                "generated_id": "G1",
                "canonical_smiles": "CCO",
                "outcome_label": "positive",
                "qc_status": "passed",
                "imported_at": timestamp,
                "source_record_id": "result-g1",
            },
            {
                "candidate_id": "C2",
                "outcome_label": "negative",
                "qc_status": "failed",
                "imported_at": timestamp,
                "source_record_id": "failed-qc-result",
            },
        ],
    }


def test_freeze_before_outcome_is_valid_and_evaluated(tmp_path: Path) -> None:
    frozen_at = datetime(2026, 1, 1, 12, tzinfo=UTC)

    run, frozen = freeze_prospective_run(
        task_id="candidate-ranking",
        predictions=_predictions(),
        output_dir=tmp_path,
        frozen_at=frozen_at,
    )
    imported = import_prospective_outcomes(
        tmp_path,
        outcomes=_outcomes(datetime(2026, 1, 2, 12, tzinfo=UTC)),
    )
    report = evaluate_prospective_run(tmp_path)

    assert run.status == "awaiting_outcomes"
    assert frozen.prediction_count == 2
    assert imported.status == "outcomes_imported"
    assert report.metadata["prospective_status"] == "evaluated"
    assert _metric(report, "prospective_validation_valid").value is True
    assert _metric(report, "prospective_exact_hit_rate").value == pytest.approx(1.0)
    assert _metric(report, "failed_qc_outcome_exclusion_count").value == 1.0
    assert "Prospective validation analytics are not clinical validation." in report.limitations


def test_outcome_before_freeze_invalidates_run(tmp_path: Path) -> None:
    freeze_prospective_run(
        task_id="candidate-ranking",
        predictions=_predictions(),
        output_dir=tmp_path,
        frozen_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )

    imported = import_prospective_outcomes(
        tmp_path,
        outcomes=_outcomes(datetime(2025, 12, 31, 12, tzinfo=UTC)),
    )

    assert imported.status == "invalid"
    assert imported.frozen_before_outcomes is False
    assert "outcome_before_prediction_freeze" in imported.warnings


def test_prediction_hash_mismatch_invalidates_prospective_run(tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.json"
    predictions_path.write_text(json.dumps(_predictions()), encoding="utf-8")
    freeze_prospective_run(
        task_id="candidate-ranking",
        predictions=predictions_path,
        output_dir=tmp_path / "prospective",
        frozen_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    modified = _predictions()
    modified["candidate_rankings"][0]["score"] = 0.12  # type: ignore[index]
    predictions_path.write_text(json.dumps(modified), encoding="utf-8")
    import_prospective_outcomes(
        tmp_path / "prospective",
        outcomes=_outcomes(datetime(2026, 1, 2, 12, tzinfo=UTC)),
    )

    report = evaluate_prospective_run(tmp_path / "prospective")

    assert report.metadata["prospective_status"] == "invalid"
    assert _metric(report, "prospective_validation_valid").value is False
    assert "prediction_hash_mismatch" in report.warnings


def test_prospective_report_files_are_generated(tmp_path: Path) -> None:
    freeze_prospective_run(
        task_id="portfolio-selection",
        predictions=_predictions(),
        output_dir=tmp_path,
        frozen_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    import_prospective_outcomes(
        tmp_path,
        outcomes=_outcomes(datetime(2026, 1, 2, 12, tzinfo=UTC)),
    )

    evaluate_prospective_run(tmp_path)

    assert (tmp_path / "prospective_validation_report.json").exists()
    assert (tmp_path / "prospective_validation_report.md").exists()
    report_payload = json.loads((tmp_path / "prospective_validation_report.json").read_text())
    assert report_payload["limitations"]


def test_prospective_cli_freeze_import_and_evaluate(tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.json"
    outcomes_path = tmp_path / "outcomes.json"
    run_dir = tmp_path / "run"
    predictions_path.write_text(json.dumps(_predictions()), encoding="utf-8")
    outcomes_path.write_text(
        json.dumps(_outcomes(datetime(2026, 1, 2, 12, tzinfo=UTC))),
        encoding="utf-8",
    )
    runner = CliRunner()

    frozen = runner.invoke(
        app,
        [
            "eval",
            "prospective",
            "freeze",
            "--predictions",
            str(predictions_path),
            "--output-dir",
            str(run_dir),
            "--task-id",
            "candidate-ranking",
            "--frozen-at",
            "2026-01-01T12:00:00+00:00",
            "--json",
        ],
    )
    imported = runner.invoke(
        app,
        [
            "eval",
            "prospective",
            "import-outcomes",
            "--run-dir",
            str(run_dir),
            "--outcomes",
            str(outcomes_path),
            "--json",
        ],
    )
    evaluated = runner.invoke(
        app,
        ["eval", "prospective", "evaluate", "--run-dir", str(run_dir), "--json"],
    )

    assert frozen.exit_code == 0, frozen.output
    assert imported.exit_code == 0, imported.output
    assert evaluated.exit_code == 0, evaluated.output
    payload = json.loads(evaluated.output)
    assert payload["report"]["metadata"]["prospective_status"] == "evaluated"
    assert (run_dir / "prospective_validation_report.json").exists()
