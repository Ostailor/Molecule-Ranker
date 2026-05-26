from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.experiments.validation import (
    classify_outcome,
    detect_result_warnings,
    normalize_assay_result,
    normalize_potency_units,
    result_quality_score,
    validate_assay_result,
)


def _endpoint(
    *,
    category: str = "potency",
    directionality: str = "lower_is_better",
    unit: str | None = "uM",
) -> AssayEndpoint:
    return AssayEndpoint(
        endpoint_id="endpoint-1",
        name="binding_affinity",
        endpoint_category=category,  # type: ignore[arg-type]
        unit=unit,
        directionality=directionality,  # type: ignore[arg-type]
    )


def _result(
    *,
    value: float | str | bool | None = 0.25,
    unit: str | None = "uM",
    outcome_label: str = "inconclusive",
    activity_direction: str = "ambiguous",
    qc_status: str = "passed",
    confidence: float = 0.8,
    endpoint: AssayEndpoint | None = None,
    candidate_name: str = "Rasagiline",
) -> AssayResult:
    measured_numeric = (
        value if isinstance(value, float | int) and not isinstance(value, bool) else None
    )
    context = AssayContext(
        assay_context_id="context-1",
        assay_name="Binding screen",
        assay_type="biochemical",
        target_symbol="MAOB",
        endpoint=endpoint or _endpoint(unit=unit),
    )
    return AssayResult(
        result_id="result-1",
        candidate_name=candidate_name,
        candidate_origin="existing",
        target_symbol="MAOB",
        assay_context=context,
        measured_value=value,
        measured_value_numeric=float(measured_numeric) if measured_numeric is not None else None,
        unit=unit,
        normalized_value=None,
        normalized_unit=None,
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        confidence=confidence,
        qc_status=qc_status,  # type: ignore[arg-type]
        source="csv_import",
        imported_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def test_normalize_potency_units_converts_common_units_to_nm():
    assert normalize_potency_units(0.25, "uM") == (250.0, "nM")
    assert normalize_potency_units(1.5, "µM") == (1500.0, "nM")
    assert normalize_potency_units(2.0, "mM") == (2_000_000.0, "nM")
    assert normalize_potency_units(42.0, "nM") == (42.0, "nM")
    assert normalize_potency_units("ambiguous", "uM") == (None, None)
    assert normalize_potency_units(5.0, "relative_activity") == (None, None)


def test_normalize_assay_result_preserves_original_and_sets_normalized_fields():
    result = _result(value=0.25, unit="uM")

    normalized = normalize_assay_result(result)

    assert normalized.measured_value == 0.25
    assert normalized.unit == "uM"
    assert normalized.normalized_value == 250.0
    assert normalized.normalized_unit == "nM"


def test_threshold_based_classification_uses_configured_rules_only():
    result = normalize_assay_result(_result(value=0.25, unit="uM"))

    classified = classify_outcome(
        result,
        {"positive_threshold": 500.0, "threshold_unit": "nM"},
    )

    assert classified.outcome_label == "positive"
    assert classified.activity_direction == "active"
    assert classified.metadata["classification"]["threshold_source"] == "endpoint_rules"


def test_safety_threshold_can_classify_toxic_from_configured_rules():
    endpoint = _endpoint(category="safety", directionality="higher_is_better", unit="%")
    result = normalize_assay_result(_result(value=70.0, unit="%", endpoint=endpoint))

    classified = classify_outcome(
        result,
        {"safety_concern_threshold": 50.0, "threshold_unit": "%"},
    )

    assert classified.outcome_label == "negative"
    assert classified.activity_direction == "toxic"


def test_failed_qc_lowers_quality_score_and_records_warning():
    failed = _result(qc_status="failed", outcome_label="failed_qc", confidence=0.9)
    partial = _result(qc_status="partial", confidence=0.9)

    assert result_quality_score(failed) == 0.0
    assert result_quality_score(partial) < result_quality_score(_result(confidence=0.9))
    assert "failed_qc results must not improve candidate scores" in detect_result_warnings(failed)


def test_ambiguous_results_become_inconclusive_without_threshold_rules():
    result = _result(
        value="ambiguous",
        unit="uM",
        outcome_label="positive",
        activity_direction="active",
    )

    normalized = normalize_assay_result(result)
    classified = classify_outcome(normalized, {})

    assert classified.outcome_label == "inconclusive"
    assert classified.activity_direction == "ambiguous"
    assert "ambiguous measured value" in classified.metadata["warnings"]


def test_strict_mode_raises_on_incomplete_required_fields():
    result = _result(candidate_name="")

    with pytest.raises(ValueError, match="candidate_name is required"):
        validate_assay_result(result, strict=True)

    non_strict = validate_assay_result(result, strict=False)
    assert "candidate_name is required" in non_strict.metadata["warnings"]
