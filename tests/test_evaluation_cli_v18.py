from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app


def _write_run_artifacts(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "candidates.json").write_text(
        json.dumps(
            {
                "artifact_id": "candidates.json",
                "candidates": [
                    {
                        "candidate_id": "C1",
                        "rank": 1,
                        "score": 0.9,
                        "canonical_smiles": "CCO",
                    },
                    {
                        "candidate_id": "C2",
                        "rank": 2,
                        "score": 0.2,
                        "canonical_smiles": "CCN",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "assay_results.json").write_text(
        json.dumps(
            {
                "artifact_id": "assay_results.json",
                "assay_results": [
                    {
                        "candidate_id": "C1",
                        "outcome_label": "positive",
                        "qc_status": "passed",
                        "imported_at": "2026-01-02T12:00:00+00:00",
                    },
                    {
                        "candidate_id": "C2",
                        "outcome_label": "negative",
                        "qc_status": "passed",
                        "imported_at": "2026-01-02T12:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_eval_cli_help_works() -> None:
    result = CliRunner().invoke(app, ["eval", "--help"])

    assert result.exit_code == 0, result.output
    assert "suite" in result.output
    assert "dataset" in result.output
    assert "guardrails" in result.output
    assert "reproducibility" in result.output


def test_eval_cli_synthetic_benchmark_runs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run_artifacts(run_dir)
    suite_path = tmp_path / "benchmark_suite.json"
    dataset_path = tmp_path / "benchmark_dataset.json"
    split_path = tmp_path / "benchmark_split.json"
    report_path = tmp_path / "evaluation_report.json"
    runner = CliRunner()

    suite = runner.invoke(
        app,
        [
            "eval",
            "suite",
            "create",
            "--name",
            "Synthetic suite",
            "--task",
            "candidate_ranking",
            "--output",
            str(suite_path),
            "--json",
        ],
    )
    dataset = runner.invoke(
        app,
        [
            "eval",
            "dataset",
            "build",
            "--from-run",
            str(run_dir),
            "--task-type",
            "candidate_ranking",
            "--output",
            str(dataset_path),
            "--json",
        ],
    )
    split = runner.invoke(
        app,
        [
            "eval",
            "split",
            "--dataset",
            str(dataset_path),
            "--split-type",
            "random",
            "--output",
            str(split_path),
            "--json",
        ],
    )
    evaluated = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--suite",
            str(suite_path),
            "--dataset",
            str(dataset_path),
            "--split",
            str(split_path),
            "--output",
            str(report_path),
            "--json",
        ],
    )

    assert suite.exit_code == 0, suite.output
    assert dataset.exit_code == 0, dataset.output
    assert split.exit_code == 0, split.output
    assert evaluated.exit_code == 0, evaluated.output
    payload = json.loads(report_path.read_text())
    assert payload["evaluation_id"] == "candidate_ranking-evaluation"
    assert payload["comparisons"][0]["baseline_id"] == "random_ranking"
    assert report_path.with_suffix(".md").exists()


def test_eval_cli_prospective_commands_work(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run_artifacts(run_dir)
    prospective_dir = tmp_path / "prospective"
    runner = CliRunner()

    frozen = runner.invoke(
        app,
        [
            "eval",
            "prospective",
            "freeze",
            "--predictions",
            str(run_dir / "candidates.json"),
            "--output-dir",
            str(prospective_dir),
            "--task-id",
            "candidate_ranking",
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
            str(prospective_dir),
            "--outcomes",
            str(run_dir / "assay_results.json"),
            "--outcome-imported-at",
            datetime(2026, 1, 2, 12, tzinfo=UTC).isoformat(),
            "--json",
        ],
    )
    evaluated = runner.invoke(
        app,
        ["eval", "prospective", "evaluate", "--run-dir", str(prospective_dir), "--json"],
    )

    assert frozen.exit_code == 0, frozen.output
    assert imported.exit_code == 0, imported.output
    assert evaluated.exit_code == 0, evaluated.output
    assert (prospective_dir / "prospective_validation_report.json").exists()


def test_eval_cli_guardrail_benchmark_catches_fixtures(tmp_path: Path) -> None:
    run_dir = tmp_path / "guardrails"
    run_dir.mkdir()
    (run_dir / "guardrail_cases.json").write_text(
        json.dumps(
            {
                "adversarial_text_fixtures": [
                    {
                        "case_id": "dosing-1",
                        "category": "dosing_patient_guidance",
                        "text": "Give the patient dose as 10 mg/kg daily.",
                        "expect_violation": True,
                    },
                    {
                        "case_id": "clean-1",
                        "category": "clean_cautious_text",
                        "text": "This ranking is an evaluation artifact, not evidence.",
                        "expect_violation": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "guardrail_benchmark_report.json"

    result = CliRunner().invoke(
        app,
        ["eval", "guardrails", "--from-run", str(run_dir), "--output", str(output), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["metadata"]["case_count"] == 2
    assert any(
        item["case_id"] == "dosing-1" and item["passed"]
        for item in payload["metadata"]["case_results"]
    )
    assert output.with_suffix(".md").exists()
