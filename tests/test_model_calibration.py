from __future__ import annotations

from molecule_ranker.models.calibration import (
    calibrate_classifier_probabilities,
    calibrate_regression_predictions,
    estimate_prediction_uncertainty,
)


def test_classifier_calibration_status_set_when_enough_validation_data() -> None:
    result = calibrate_classifier_probabilities(
        probabilities=[0.05, 0.15, 0.25, 0.35, 0.65, 0.75, 0.85, 0.95],
        labels=[0, 0, 0, 0, 1, 1, 1, 1],
        config={"min_calibration_rows": 6, "n_bins": 4},
    )

    assert result.calibration_status == "calibrated"
    assert result.metrics["brier_score"] >= 0.0
    assert result.metrics["expected_calibration_error"] >= 0.0
    assert result.metadata["raw_probabilities_calibrated"] is False


def test_insufficient_classifier_calibration_marked_uncalibrated() -> None:
    result = calibrate_classifier_probabilities(
        probabilities=[0.2, 0.8],
        labels=[0, 1],
        config={"min_calibration_rows": 5},
    )

    assert result.calibration_status == "uncalibrated"
    assert "insufficient_calibration_data" in result.warnings
    assert result.metrics["validation_row_count"] == 2


def test_ece_and_brier_are_computed_when_possible() -> None:
    result = calibrate_classifier_probabilities(
        probabilities=[0.1, 0.4, 0.6, 0.9],
        labels=[0, 0, 1, 1],
        config={"min_calibration_rows": 4, "n_bins": 2},
    )

    assert result.metrics["brier_score"] == 0.085
    assert result.metrics["expected_calibration_error"] == 0.25
    assert result.metrics["reliability_curve"]


def test_regression_insufficient_calibration_marked_uncalibrated() -> None:
    result = calibrate_regression_predictions(
        predictions=[1.0, 2.0],
        labels=[1.2, 1.9],
        config={"min_calibration_rows": 5},
    )

    assert result.calibration_status == "uncalibrated"
    assert "insufficient_calibration_data" in result.warnings
    assert result.prediction_interval is None


def test_out_of_domain_increases_uncertainty() -> None:
    in_domain = estimate_prediction_uncertainty(
        feature_vector=[0.0, 0.0],
        training_feature_matrix=[[0.0, 0.0], [0.1, 0.1]],
        applicability_domain="in_domain",
        calibration_status="calibrated",
        training_row_count=40,
    )
    out_of_domain = estimate_prediction_uncertainty(
        feature_vector=[0.0, 0.0],
        training_feature_matrix=[[0.0, 0.0], [0.1, 0.1]],
        applicability_domain="out_of_domain",
        calibration_status="calibrated",
        training_row_count=40,
    )

    assert out_of_domain["uncertainty"] > in_domain["uncertainty"]
    assert out_of_domain["confidence"] < in_domain["confidence"]


def test_generated_far_from_training_set_has_low_confidence() -> None:
    result = estimate_prediction_uncertainty(
        feature_vector=[10.0, 10.0],
        training_feature_matrix=[[0.0, 0.0], [0.2, 0.2], [0.4, 0.4]],
        candidate_origin="generated",
        applicability_domain="out_of_domain",
        calibration_status="uncalibrated",
        training_row_count=3,
    )

    assert result["components"]["distance_to_training_set"] > 0.9
    assert result["uncertainty"] >= 0.8
    assert result["confidence"] <= 0.2
