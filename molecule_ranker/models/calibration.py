"""Calibration and uncertainty helpers for local surrogate models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import sqrt
from statistics import mean, pstdev
from typing import Any

from molecule_ranker.models.schemas import CalibrationStatus


@dataclass(frozen=True)
class CalibrationResult:
    calibration_status: CalibrationStatus
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    calibrated_values: list[float] = field(default_factory=list)
    prediction_interval: dict[str, float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def calibrate_classifier_probabilities(
    probabilities: Sequence[float],
    labels: Sequence[int | float | bool],
    *,
    config: Mapping[str, Any] | None = None,
) -> CalibrationResult:
    config = dict(config or {})
    raw_probabilities = [_clamp(probability) for probability in probabilities]
    binary_labels = [1.0 if bool(label) else 0.0 for label in labels]
    row_count = min(len(raw_probabilities), len(binary_labels))
    raw_probabilities = raw_probabilities[:row_count]
    binary_labels = binary_labels[:row_count]
    min_rows = int(config.get("min_calibration_rows", 20) or 20)
    n_bins = max(1, int(config.get("n_bins", 10) or 10))

    metrics: dict[str, Any] = {
        "validation_row_count": row_count,
        "brier_score": _brier_score(raw_probabilities, binary_labels),
        "expected_calibration_error": _expected_calibration_error(
            raw_probabilities,
            binary_labels,
            n_bins=n_bins,
        ),
        "reliability_curve": _reliability_curve(
            raw_probabilities,
            binary_labels,
            n_bins=n_bins,
        ),
    }
    if row_count < min_rows or len(set(binary_labels)) < 2:
        return CalibrationResult(
            calibration_status="uncalibrated",
            metrics=metrics,
            warnings=["insufficient_calibration_data"],
            calibrated_values=raw_probabilities,
            metadata={
                "raw_probabilities_calibrated": False,
                "calibration_method": "none",
            },
        )

    calibrated = _histogram_calibrated_probabilities(
        raw_probabilities,
        binary_labels,
        n_bins=n_bins,
    )
    metrics["calibration_method"] = "histogram_reliability_bins"
    metrics["calibrated_brier_score"] = _brier_score(calibrated, binary_labels)
    return CalibrationResult(
        calibration_status="calibrated",
        metrics=metrics,
        warnings=[],
        calibrated_values=calibrated,
        metadata={
            "raw_probabilities_calibrated": False,
            "calibration_method": "histogram_reliability_bins",
        },
    )


def calibrate_regression_predictions(
    predictions: Sequence[float],
    labels: Sequence[float],
    *,
    config: Mapping[str, Any] | None = None,
) -> CalibrationResult:
    config = dict(config or {})
    predicted = [float(value) for value in predictions]
    observed = [float(value) for value in labels]
    row_count = min(len(predicted), len(observed))
    predicted = predicted[:row_count]
    observed = observed[:row_count]
    residuals = [label - prediction for prediction, label in zip(predicted, observed, strict=False)]
    absolute_residuals = [abs(residual) for residual in residuals]
    min_rows = int(config.get("min_calibration_rows", 20) or 20)
    confidence_level = float(config.get("confidence_level", 0.9) or 0.9)
    quantile = _quantile(absolute_residuals, confidence_level)
    metrics: dict[str, Any] = {
        "validation_row_count": row_count,
        "mean_residual": mean(residuals) if residuals else 0.0,
        "residual_std": pstdev(residuals) if len(residuals) > 1 else 0.0,
        "mean_absolute_error": mean(absolute_residuals) if absolute_residuals else 0.0,
        "conformal_abs_residual_quantile": quantile,
    }

    if row_count < min_rows:
        return CalibrationResult(
            calibration_status="uncalibrated",
            metrics=metrics,
            warnings=["insufficient_calibration_data"],
            calibrated_values=predicted,
            prediction_interval=None,
            metadata={"calibration_method": "residual_summary_only"},
        )

    interval = {
        "confidence_level": _clamp(confidence_level),
        "half_width": quantile,
        "method": "residual_conformal_placeholder",
    }
    return CalibrationResult(
        calibration_status="calibrated",
        metrics=metrics,
        warnings=[],
        calibrated_values=predicted,
        prediction_interval=interval,
        metadata={"calibration_method": "residual_conformal_placeholder"},
    )


def estimate_prediction_uncertainty(
    *,
    feature_vector: Sequence[float],
    training_feature_matrix: Sequence[Sequence[float]],
    candidate_origin: str = "existing",
    applicability_domain: str = "unknown",
    calibration_status: CalibrationStatus = "uncalibrated",
    training_row_count: int = 0,
    ensemble_predictions: Sequence[float] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(config or {})
    ensemble_uncertainty = _ensemble_uncertainty(ensemble_predictions or [])
    distance_uncertainty = _distance_uncertainty(feature_vector, training_feature_matrix, config)
    applicability_uncertainty = {
        "in_domain": 0.1,
        "near_domain": 0.35,
        "out_of_domain": 0.75,
        "unknown": 0.5,
    }.get(applicability_domain, 0.5)
    calibration_uncertainty = {
        "calibrated": 0.1,
        "uncalibrated": 0.45,
        "insufficient_data": 0.65,
        "not_applicable": 0.35,
    }[calibration_status]
    small_data_uncertainty = _small_data_uncertainty(training_row_count, config)
    generated_penalty = (
        0.2 if candidate_origin == "generated" and applicability_domain != "in_domain" else 0.0
    )

    components = {
        "model_ensemble_variance": ensemble_uncertainty,
        "distance_to_training_set": distance_uncertainty,
        "applicability_domain": applicability_uncertainty,
        "calibration": calibration_uncertainty,
        "small_data": small_data_uncertainty,
        "generated_out_of_domain_penalty": generated_penalty,
    }
    uncertainty = _clamp(
        0.2 * ensemble_uncertainty
        + 0.3 * distance_uncertainty
        + 0.2 * applicability_uncertainty
        + 0.2 * calibration_uncertainty
        + 0.1 * small_data_uncertainty
        + generated_penalty
    )
    confidence = _clamp(1.0 - uncertainty)
    return {
        "uncertainty": uncertainty,
        "confidence": confidence,
        "components": components,
        "warnings": _uncertainty_warnings(
            calibration_status=calibration_status,
            applicability_domain=applicability_domain,
            candidate_origin=candidate_origin,
            training_row_count=training_row_count,
        ),
    }


def _brier_score(probabilities: Sequence[float], labels: Sequence[float]) -> float:
    if not probabilities:
        return 0.0
    score = sum(
        (probability - label) ** 2
        for probability, label in zip(probabilities, labels, strict=False)
    ) / len(probabilities)
    return round(score, 6)


def _expected_calibration_error(
    probabilities: Sequence[float],
    labels: Sequence[float],
    *,
    n_bins: int,
) -> float:
    if not probabilities:
        return 0.0
    total = len(probabilities)
    error = 0.0
    for bin_rows in _binned_rows(probabilities, labels, n_bins=n_bins):
        if not bin_rows:
            continue
        confidence = mean(probability for probability, _label in bin_rows)
        accuracy = mean(label for _probability, label in bin_rows)
        error += (len(bin_rows) / total) * abs(accuracy - confidence)
    return round(error, 6)


def _reliability_curve(
    probabilities: Sequence[float],
    labels: Sequence[float],
    *,
    n_bins: int,
) -> list[dict[str, float]]:
    curve: list[dict[str, float]] = []
    for index, bin_rows in enumerate(_binned_rows(probabilities, labels, n_bins=n_bins)):
        lower = index / n_bins
        upper = (index + 1) / n_bins
        if not bin_rows:
            curve.append(
                {
                    "bin_lower": lower,
                    "bin_upper": upper,
                    "count": 0.0,
                    "mean_probability": 0.0,
                    "event_rate": 0.0,
                }
            )
            continue
        curve.append(
            {
                "bin_lower": lower,
                "bin_upper": upper,
                "count": float(len(bin_rows)),
                "mean_probability": mean(probability for probability, _label in bin_rows),
                "event_rate": mean(label for _probability, label in bin_rows),
            }
        )
    return curve


def _histogram_calibrated_probabilities(
    probabilities: Sequence[float],
    labels: Sequence[float],
    *,
    n_bins: int,
) -> list[float]:
    bin_rates: dict[int, float] = {}
    for index, bin_rows in enumerate(_binned_rows(probabilities, labels, n_bins=n_bins)):
        if bin_rows:
            bin_rates[index] = mean(label for _probability, label in bin_rows)
    calibrated = []
    for probability in probabilities:
        bin_index = min(int(_clamp(probability) * n_bins), n_bins - 1)
        calibrated.append(float(bin_rates.get(bin_index, probability)))
    return calibrated


def _binned_rows(
    probabilities: Sequence[float],
    labels: Sequence[float],
    *,
    n_bins: int,
) -> list[list[tuple[float, float]]]:
    bins: list[list[tuple[float, float]]] = [[] for _index in range(n_bins)]
    for probability, label in zip(probabilities, labels, strict=False):
        bin_index = min(int(_clamp(probability) * n_bins), n_bins - 1)
        bins[bin_index].append((_clamp(probability), float(label)))
    return bins


def _quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(float(value) for value in values)
    index = min(int(round((len(sorted_values) - 1) * _clamp(quantile))), len(sorted_values) - 1)
    return float(sorted_values[index])


def _ensemble_uncertainty(predictions: Sequence[float]) -> float:
    if len(predictions) < 2:
        return 0.0
    return _clamp(pstdev(float(value) for value in predictions))


def _distance_uncertainty(
    feature_vector: Sequence[float],
    training_feature_matrix: Sequence[Sequence[float]],
    config: Mapping[str, Any],
) -> float:
    if not training_feature_matrix:
        return 1.0
    distances = [
        _euclidean_distance(feature_vector, training_vector)
        for training_vector in training_feature_matrix
    ]
    min_distance = min(distances)
    scale = float(config.get("distance_uncertainty_scale", 1.0) or 1.0)
    return _clamp(min_distance / scale)


def _small_data_uncertainty(training_row_count: int, config: Mapping[str, Any]) -> float:
    enough_rows = int(config.get("small_data_reference_rows", 30) or 30)
    if enough_rows <= 0:
        return 0.0
    return _clamp(1.0 - (training_row_count / enough_rows))


def _uncertainty_warnings(
    *,
    calibration_status: CalibrationStatus,
    applicability_domain: str,
    candidate_origin: str,
    training_row_count: int,
) -> list[str]:
    warnings: list[str] = []
    if calibration_status != "calibrated":
        warnings.append("uncalibrated_prediction")
    if applicability_domain == "out_of_domain":
        warnings.append("out_of_applicability_domain")
    if candidate_origin == "generated" and applicability_domain != "in_domain":
        warnings.append("generated_molecule_out_of_domain")
    if training_row_count < 10:
        warnings.append("small_training_dataset")
    return warnings


def _euclidean_distance(left: Sequence[float], right: Sequence[float]) -> float:
    length = min(len(left), len(right))
    if length == 0:
        return 1.0
    return sqrt(sum((float(left[index]) - float(right[index])) ** 2 for index in range(length)))


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


__all__ = [
    "CalibrationResult",
    "calibrate_classifier_probabilities",
    "calibrate_regression_predictions",
    "estimate_prediction_uncertainty",
]
