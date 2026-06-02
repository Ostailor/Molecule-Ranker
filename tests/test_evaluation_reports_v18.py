from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.evaluation.reports import (
    REQUIRED_REPORT_DISCLAIMERS,
    write_benchmark_suite_report,
    write_decision_quality_report,
    write_guardrail_benchmark_report,
    write_longitudinal_trend_report,
    write_prospective_validation_report,
    write_reproducibility_report,
)
from molecule_ranker.evaluation.schemas import (
    BenchmarkDataset,
    BenchmarkSplit,
    BenchmarkTask,
    DecisionQualityReport,
    EvaluationMetric,
    EvaluationReport,
    ReproducibilityManifest,
)


def _now() -> datetime:
    return datetime(2026, 1, 1, 12, tzinfo=UTC)


def _metric() -> EvaluationMetric:
    return EvaluationMetric(
        metric_id="precision-at-1",
        name="precision_at_1",
        metric_type="ranking",
        value=0.5,
        confidence_interval={"low": 0.25, "high": 0.75},
        higher_is_better=True,
        metadata={"uncertainty": "synthetic fixture interval"},
    )


def _report() -> EvaluationReport:
    return EvaluationReport(
        evaluation_id="eval-report-fixture",
        suite_id="suite-1",
        task_id="task-1",
        dataset_id="dataset-1",
        split_id="split-1",
        prediction_set_id="predictions-1",
        metrics=[_metric()],
        baseline_metrics=[
            EvaluationMetric(
                metric_id="random-baseline",
                name="random_ranking",
                metric_type="ranking",
                value=0.25,
                higher_is_better=True,
            )
        ],
        comparisons=[{"baseline_id": "random_ranking", "delta": 0.25}],
        warnings=["synthetic_fixture_only"],
        limitations=["Outcome labels are limited to imported results or benchmark fixtures."],
        created_at=_now(),
        metadata={
            "task_definition": {"objective": "Evaluate ranking behavior."},
            "dataset_provenance": {"source_artifact_ids": ["assay-fixture"]},
            "split": {"split_type": "prospective"},
            "guardrail_results": {"guardrail_pass_rate": 1.0},
        },
    )


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        task_id="task-1",
        suite_id="suite-1",
        name="Ranking fixture",
        task_type="candidate_ranking",
        endpoint_name="fixture endpoint",
        disease_name="fixture disease",
        target_symbol="FIX1",
        objective="Evaluate ranking behavior.",
        input_artifact_ids=["rankings"],
        label_artifact_ids=["assay-fixture"],
        metric_ids=["precision-at-1"],
    )


def _dataset() -> BenchmarkDataset:
    return BenchmarkDataset(
        dataset_id="dataset-1",
        name="Fixture dataset",
        dataset_type="synthetic_validation",
        source_artifact_ids=["assay-fixture"],
        row_count=2,
        candidate_count=2,
        label_count=2,
        created_at=_now(),
        data_contract_version="data-contracts.v1",
    )


def _split() -> BenchmarkSplit:
    return BenchmarkSplit(
        split_id="split-1",
        dataset_id="dataset-1",
        split_type="prospective",
        train_ids=[],
        validation_ids=[],
        test_ids=["C1", "C2"],
        frozen_at=_now(),
        leakage_checks={"same_inchikey_across_train_test": False},
    )


def test_all_named_reports_are_generated(tmp_path: Path) -> None:
    report = _report()
    decision = DecisionQualityReport(
        report_id="decision-report-1",
        project_id="project-1",
        campaign_id="campaign-1",
        decision_artifact_ids=["decision-artifact"],
        outcome_artifact_ids=["outcome-artifact"],
        metrics=[_metric()],
        decision_summary={"selected_count": 1},
        lessons=["Use imported outcomes only."],
        warnings=["review_required"],
        created_at=_now(),
    )
    manifest = ReproducibilityManifest(
        manifest_id="manifest-1",
        run_id="run-1",
        suite_id="suite-1",
        code_version="1.8.0",
        artifact_contract_version="artifacts.v1",
        config_hash="sha256:config",
        input_artifact_hashes={"input": "sha256:input"},
        output_artifact_hashes={"output": "sha256:output"},
        random_seeds={"split": 18},
        dependency_summary={"lock_hash": "sha256:lock"},
        created_at=_now(),
    )

    paths = [
        write_benchmark_suite_report(
            report,
            tmp_path,
            task=_task(),
            dataset=_dataset(),
            split=_split(),
        ),
        write_prospective_validation_report(report, tmp_path),
        write_decision_quality_report(decision, tmp_path),
        write_guardrail_benchmark_report(report, tmp_path),
        write_reproducibility_report(manifest, tmp_path),
        write_longitudinal_trend_report(
            {
                "trend_id": "trend-1",
                "task_definition": {"objective": "Track ranking over time."},
                "metrics": [{"name": "precision_at_1", "value": 0.5}],
                "baselines": [{"name": "random_ranking", "value": 0.25}],
                "guardrail_results": {"guardrail_pass_rate": 1.0},
            },
            tmp_path,
        ),
    ]

    assert {path.name for path in paths} == {
        "benchmark_suite_report.md",
        "prospective_validation_report.md",
        "decision_quality_report.md",
        "guardrail_benchmark_report.md",
        "reproducibility_report.md",
        "longitudinal_trend_report.md",
    }
    for path in paths:
        assert path.exists()
        text = path.read_text()
        assert "## Task Definition" in text
        assert "## Dataset And Source Provenance" in text
        assert "## Baselines" in text
        assert "## Metrics" in text
        assert "## Confidence And Uncertainty" in text
        assert "## Limitations" in text
        assert "## Guardrail Results" in text
        assert "## Interpretation Guidance" in text


def test_report_baselines_and_limitations_are_included(tmp_path: Path) -> None:
    path = write_benchmark_suite_report(_report(), tmp_path, task=_task(), dataset=_dataset())
    text = path.read_text()

    assert "random_ranking" in text
    assert "Outcome labels are limited to imported results or benchmark fixtures." in text
    for disclaimer in REQUIRED_REPORT_DISCLAIMERS:
        assert disclaimer in text


def test_reports_do_not_emit_forbidden_overclaim_text(tmp_path: Path) -> None:
    path = write_prospective_validation_report(_report(), tmp_path)
    text = path.read_text().lower()

    forbidden_phrases = [
        "proves active",
        "proves safe",
        "proves effective",
        "clinically validated",
        "synthesis protocol:",
        "lab protocol:",
        "dosing recommendation:",
    ]
    assert not any(phrase in text for phrase in forbidden_phrases)
