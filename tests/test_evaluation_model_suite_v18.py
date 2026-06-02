from __future__ import annotations

import pytest

from molecule_ranker.evaluation.evaluators.models import evaluate_model_suite


def _metric(report, name: str):
    for metric in report.metrics:
        if metric.name == name:
            return metric
    raise AssertionError(f"missing metric {name}")


def _classification_predictions() -> dict[str, object]:
    return {
        "artifact_id": "classification_predictions.json",
        "predictions": [
            {
                "candidate_id": "C1",
                "prediction_score": 0.9,
                "confidence": 0.95,
                "applicability_domain": "in_domain",
                "candidate_origin": "existing",
                "split_type": "random",
                "scaffold": "s1",
                "out_of_domain_warning": False,
                "active_design_selected": True,
                "model_influenced_decision": True,
                "calibration_status": "calibrated",
            },
            {
                "candidate_id": "C2",
                "prediction_score": 0.8,
                "confidence": 0.9,
                "applicability_domain": "out_of_domain",
                "candidate_origin": "generated",
                "split_type": "scaffold",
                "scaffold": "s2",
                "out_of_domain_warning": True,
                "active_design_selected": True,
                "model_influenced_decision": True,
                "calibration_status": "uncalibrated",
                "summary_claim": "high-confidence active molecule",
            },
            {
                "candidate_id": "C3",
                "prediction_score": 0.3,
                "confidence": 0.4,
                "applicability_domain": "near_domain",
                "candidate_origin": "existing",
                "split_type": "random",
                "scaffold": "s1",
                "out_of_domain_warning": False,
                "active_design_selected": False,
                "model_influenced_decision": False,
                "calibration_status": "calibrated",
            },
            {
                "candidate_id": "C4",
                "prediction_score": 0.2,
                "confidence": 0.3,
                "applicability_domain": "out_of_domain",
                "candidate_origin": "generated",
                "split_type": "time_based",
                "scaffold": "s3",
                "out_of_domain_warning": True,
                "active_design_selected": False,
                "model_influenced_decision": False,
                "calibration_status": "calibrated",
            },
        ],
    }


def _classification_labels() -> dict[str, object]:
    return {
        "artifact_id": "assay_results.json",
        "assay_results": [
            {"candidate_id": "C1", "outcome_label": "positive", "qc_status": "passed"},
            {"candidate_id": "C2", "outcome_label": "negative", "qc_status": "passed"},
            {"candidate_id": "C3", "outcome_label": "negative", "qc_status": "passed"},
            {"candidate_id": "C4", "outcome_label": "negative", "qc_status": "passed"},
        ],
    }


def test_model_suite_evaluates_classification_calibration_domains_and_guardrails() -> None:
    report = evaluate_model_suite(
        prediction_artifacts={"classification_predictions": _classification_predictions()},
        imported_outcome_labels={"imported_assay_results": _classification_labels()},
        task_kind="classification",
        evaluation_id="model-suite",
    )

    assert _metric(report, "roc_auc").value == pytest.approx(1.0)
    assert _metric(report, "pr_auc").value == pytest.approx(1.0)
    assert _metric(report, "brier_score").value == pytest.approx(0.195)
    assert _metric(report, "expected_calibration_error").value is not None
    assert _metric(report, "calibration_status_checked").value is True
    assert _metric(report, "in_domain_roc_auc").value is None
    assert _metric(report, "out_of_domain_warning_precision").value == pytest.approx(1.0)
    assert _metric(report, "generated_mean_confidence").value == pytest.approx(0.6)
    assert _metric(report, "existing_mean_confidence").value == pytest.approx(0.675)
    assert _metric(report, "model_influenced_active_design_hit_rate").value == pytest.approx(0.5)

    guardrail = _metric(report, "uncalibrated_prediction_overclaim_guardrail")
    assert guardrail.metric_type == "guardrail"
    assert guardrail.value is False
    assert "predictions_are_not_evidence" in report.metadata["rules"]
    assert "uncalibrated_prediction_overclaim" in report.warnings


def test_model_suite_evaluates_regression_metrics() -> None:
    report = evaluate_model_suite(
        prediction_artifacts={
            "regression_predictions": {
                "artifact_id": "regression_predictions.json",
                "predictions": [
                    {"candidate_id": "R1", "predicted_value": 1.0, "split_type": "random"},
                    {"candidate_id": "R2", "predicted_value": 2.0, "split_type": "scaffold"},
                    {"candidate_id": "R3", "predicted_value": 4.0, "split_type": "time_based"},
                ],
            }
        },
        imported_outcome_labels={
            "imported_assay_results": {
                "artifact_id": "regression_labels.json",
                "assay_results": [
                    {"candidate_id": "R1", "measured_value": 1.0, "qc_status": "passed"},
                    {"candidate_id": "R2", "measured_value": 2.0, "qc_status": "passed"},
                    {"candidate_id": "R3", "measured_value": 3.0, "qc_status": "passed"},
                ],
            }
        },
        task_kind="regression",
    )

    assert _metric(report, "mae").value == pytest.approx(1 / 3)
    assert _metric(report, "rmse").value == pytest.approx((1 / 3) ** 0.5)
    assert _metric(report, "r2").value == pytest.approx(0.5)
    assert _metric(report, "spearman").value == pytest.approx(1.0)
    assert _metric(report, "pearson").value == pytest.approx(0.9819805)
    assert _metric(report, "time_split_mae").value == pytest.approx(1.0)


def test_model_suite_does_not_use_prediction_labels_as_outcomes() -> None:
    report = evaluate_model_suite(
        prediction_artifacts={
            "classification_predictions": {
                "artifact_id": "classification_predictions.json",
                "predictions": [
                    {
                        "candidate_id": "C1",
                        "prediction_score": 0.9,
                        "outcome_label": "positive",
                        "calibration_status": "calibrated",
                    }
                ],
            }
        },
        imported_outcome_labels={},
        task_kind="classification",
    )

    assert _metric(report, "roc_auc").value is None
    assert "no_imported_or_fixture_outcome_labels" in report.warnings
    assert report.metadata["rules"]["predictions_are_evidence"] is False
