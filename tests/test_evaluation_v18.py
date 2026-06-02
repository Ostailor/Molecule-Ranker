from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.evaluation import (
    BenchmarkDataset,
    BenchmarkSplit,
    BenchmarkSuite,
    BenchmarkTask,
    DecisionQualityReport,
    EvaluationMetric,
    EvaluationReport,
    FrozenPredictionSet,
    ProspectiveValidationRun,
    ReproducibilityManifest,
    evaluation_dashboard_summary,
    guardrail_metric,
    write_evaluation_report,
)


def _now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_v18_schema_contracts_accept_required_fields() -> None:
    suite = BenchmarkSuite(
        suite_id="suite-1",
        name="V1.8 suite",
        version="1.8",
        description="Synthetic schema contract suite.",
        tasks=["task-1"],
        created_at=_now(),
        metadata={"not_biomedical_evidence": True},
    )
    task = BenchmarkTask(
        task_id="task-1",
        suite_id=suite.suite_id,
        name="Ranking task",
        task_type="candidate_ranking",
        endpoint_name="fixture endpoint",
        disease_name="fixture disease",
        target_symbol="SYN1",
        objective="Evaluate ranking quality over a frozen fixture.",
        input_artifact_ids=["ranking-input"],
        label_artifact_ids=["assay-labels"],
        metric_ids=["metric-1"],
        metadata={},
    )
    dataset = BenchmarkDataset(
        dataset_id="dataset-1",
        name="Imported assay result fixture",
        dataset_type="imported_assay_results",
        source_artifact_ids=["assay-labels"],
        row_count=10,
        candidate_count=8,
        label_count=10,
        created_at=_now(),
        data_contract_version="data-contracts.v1",
    )
    split = BenchmarkSplit(
        split_id="split-1",
        dataset_id=dataset.dataset_id,
        split_type="prospective",
        train_ids=["a"],
        validation_ids=["b"],
        test_ids=["c"],
        frozen_at=_now(),
        leakage_checks={"same_candidate_in_train_test": False},
    )
    prediction_set = FrozenPredictionSet(
        prediction_set_id="predictions-1",
        task_id=task.task_id,
        model_or_pipeline_version="ranker.1.8.0",
        frozen_at=_now(),
        prediction_artifact_id="predictions-artifact",
        input_candidate_ids=["a", "b", "c"],
        prediction_count=3,
        outcome_labels_available=False,
        outcome_artifact_id=None,
    )

    assert suite.tasks == ["task-1"]
    assert task.task_type == "candidate_ranking"
    assert dataset.row_count == 10
    assert split.split_type == "prospective"
    assert prediction_set.outcome_labels_available is False


def test_v18_schema_literals_reject_unknown_values() -> None:
    with pytest.raises(ValidationError):
        BenchmarkTask.model_validate(
            {
                "task_id": "task-1",
                "suite_id": "suite-1",
                "name": "Bad task",
                "task_type": "clinical_validation",
                "objective": "Not allowed.",
            }
        )

    with pytest.raises(ValidationError):
        EvaluationMetric.model_validate(
            {
                "metric_id": "metric-1",
                "name": "Bad metric",
                "metric_type": "clinical",
                "value": 0.5,
                "higher_is_better": True,
            }
        )


def test_v18_timestamps_must_be_timezone_aware() -> None:
    naive = datetime(2026, 1, 1, 12, 0)

    with pytest.raises(ValidationError, match="timezone-aware"):
        BenchmarkSuite(
            suite_id="suite-1",
            name="Bad suite",
            version="1.8",
            description="Naive timestamp should fail.",
            tasks=[],
            created_at=naive,
            metadata={},
        )

    with pytest.raises(ValidationError, match="timezone-aware"):
        BenchmarkSplit(
            split_id="split-1",
            dataset_id="dataset-1",
            split_type="random",
            train_ids=[],
            validation_ids=[],
            test_ids=[],
            frozen_at=naive,
            leakage_checks={},
            metadata={},
        )


def test_metric_directionality_and_prospective_freezing_are_recorded() -> None:
    high_is_good = EvaluationMetric(
        metric_id="metric-1",
        name="precision_at_10",
        metric_type="ranking",
        value=0.75,
        confidence_interval={"low": 0.6, "high": 0.9},
        higher_is_better=True,
    )
    low_is_good = EvaluationMetric(
        metric_id="metric-2",
        name="brier_score",
        metric_type="calibration",
        value=0.12,
        confidence_interval=None,
        higher_is_better=False,
    )
    prospective = ProspectiveValidationRun(
        prospective_run_id="prospective-1",
        project_id="project-1",
        campaign_id=None,
        task_id="task-1",
        frozen_prediction_set_id="predictions-1",
        frozen_before_outcomes=True,
        outcome_imported_at=None,
        evaluation_report_id=None,
        status="awaiting_outcomes",
        warnings=[],
        metadata={},
    )

    assert high_is_good.higher_is_better is True
    assert low_is_good.higher_is_better is False
    assert prospective.frozen_before_outcomes is True
    assert prospective.status == "awaiting_outcomes"


def test_reports_decision_quality_and_reproducibility_manifest(tmp_path: Path) -> None:
    metric = guardrail_metric(metric_id="guardrail-1", name="no_overclaim", passed=True)
    evaluation = EvaluationReport(
        evaluation_id="eval-1",
        suite_id="suite-1",
        task_id="task-1",
        dataset_id="dataset-1",
        split_id="split-1",
        prediction_set_id="predictions-1",
        metrics=[metric],
        baseline_metrics=[],
        comparisons=[],
        warnings=[],
        limitations=["Benchmark results are evaluation artifacts, not biomedical evidence."],
        created_at=_now(),
        metadata={},
    )
    decision = DecisionQualityReport(
        report_id="decision-report-1",
        project_id="project-1",
        campaign_id="campaign-1",
        decision_artifact_ids=["decision-1"],
        outcome_artifact_ids=["outcome-1"],
        metrics=[metric],
        decision_summary={"selected_count": 1},
        lessons=["Use only imported outcome artifacts."],
        warnings=[],
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
        dependency_summary={"python": "3.11"},
        created_at=_now(),
    )

    json_path, markdown_path = write_evaluation_report(evaluation, tmp_path)
    dashboard = evaluation_dashboard_summary(evaluation)

    assert decision.metrics[0].metric_type == "guardrail"
    assert manifest.random_seeds == {"split": 18}
    assert json_path.exists()
    assert markdown_path.exists()
    assert dashboard["metric_count"] == 1


def test_validate_evaluation_cli_writes_schema_report(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["validate", "evaluation", "--root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.stdout
    assert '"evaluation_id": "v1-8-synthetic-evaluation"' in result.stdout
    output_dir = tmp_path / ".molecule-ranker" / "validation" / "evaluation"
    json_path = output_dir / "evaluation_report.json"
    assert json_path.exists()
    assert (output_dir / "evaluation_report.md").exists()
    payload = json.loads(json_path.read_text())
    assert payload["comparisons"][0]["baseline_id"] == "random_ranking"
