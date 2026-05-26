from __future__ import annotations

from typing import Any

from molecule_ranker.experiments.schemas import AssayResult

_POTENCY_UNIT_FACTORS_TO_NM = {
    "nm": 1.0,
    "nanomolar": 1.0,
    "um": 1_000.0,
    "µm": 1_000.0,
    "μm": 1_000.0,
    "micromolar": 1_000.0,
    "mm": 1_000_000.0,
    "millimolar": 1_000_000.0,
    "pm": 0.001,
    "picomolar": 0.001,
    "m": 1_000_000_000.0,
    "molar": 1_000_000_000.0,
}


def validate_assay_result(result: AssayResult, strict: bool) -> AssayResult:
    warnings = detect_result_warnings(result)
    if strict and warnings:
        raise ValueError("; ".join(warnings))
    return _with_warnings(result, warnings)


def normalize_assay_result(result: AssayResult) -> AssayResult:
    normalized_value = result.normalized_value
    normalized_unit = result.normalized_unit
    warnings = detect_result_warnings(result)
    if result.assay_context.endpoint.endpoint_category == "potency":
        normalized_value, normalized_unit = normalize_potency_units(
            result.measured_value_numeric,
            result.unit,
        )
        if result.measured_value_numeric is not None and normalized_value is None:
            warnings.append("could not normalize potency unit")
    elif result.measured_value_numeric is not None:
        normalized_value = result.measured_value_numeric
        normalized_unit = result.unit
    return _with_warnings(
        result.model_copy(
            update={
                "normalized_value": normalized_value,
                "normalized_unit": normalized_unit,
            }
        ),
        warnings,
    )


def normalize_potency_units(value: object, unit: object) -> tuple[float | None, str | None]:
    if value is None or unit is None:
        return None, None
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return None, None
    factor = _POTENCY_UNIT_FACTORS_TO_NM.get(str(unit).strip().lower())
    if factor is None:
        return None, None
    return numeric * factor, "nM"


def classify_outcome(result: AssayResult, endpoint_rules: dict[str, Any]) -> AssayResult:
    warnings = detect_result_warnings(result)
    value = _classification_value(result, endpoint_rules)
    metadata = dict(result.metadata)
    classification = dict(metadata.get("classification", {}))
    if result.qc_status == "failed":
        return _with_warnings(
            result.model_copy(
                update={"outcome_label": "failed_qc", "activity_direction": "ambiguous"}
            ),
            warnings,
        )
    if value is None:
        warnings.append("ambiguous measured value")
        return _with_warnings(
            result.model_copy(
                update={"outcome_label": "inconclusive", "activity_direction": "ambiguous"}
            ),
            warnings,
        )

    threshold = _threshold_for(result, endpoint_rules)
    if threshold is None:
        if result.outcome_label in {"positive", "negative"} and _is_ambiguous_without_rules(result):
            warnings.append("ambiguous measured value")
            return _with_warnings(
                result.model_copy(
                    update={"outcome_label": "inconclusive", "activity_direction": "ambiguous"}
                ),
                warnings,
            )
        return _with_warnings(result, warnings)

    threshold_value = _threshold_value_for(result, endpoint_rules, threshold)
    directionality = result.assay_context.endpoint.directionality
    category = result.assay_context.endpoint.endpoint_category
    outcome_label = result.outcome_label
    activity_direction = result.activity_direction
    if category == "safety" and "safety_concern_threshold" in endpoint_rules:
        safety_concern = value >= threshold_value
        outcome_label = "negative" if safety_concern else "positive"
        activity_direction = "toxic" if safety_concern else "non_toxic"
    elif directionality == "lower_is_better":
        outcome_label = "positive" if value <= threshold_value else "negative"
        activity_direction = "active" if outcome_label == "positive" else "inactive"
    elif directionality == "higher_is_better":
        outcome_label = "positive" if value >= threshold_value else "negative"
        activity_direction = "active" if outcome_label == "positive" else "inactive"
    elif directionality == "binary":
        outcome_label = "positive" if bool(result.measured_value) else "negative"
        activity_direction = "active" if outcome_label == "positive" else "inactive"
    else:
        outcome_label = "inconclusive"
        activity_direction = "ambiguous"

    classification.update(
        {
            "threshold": threshold,
            "threshold_value_used": threshold_value,
            "threshold_source": "endpoint_rules",
        }
    )
    metadata["classification"] = classification
    return _with_warnings(
        result.model_copy(
            update={
                "outcome_label": outcome_label,
                "activity_direction": activity_direction,
                "metadata": metadata,
            }
        ),
        warnings,
    )


def result_quality_score(result: AssayResult) -> float:
    if result.qc_status == "failed" or result.outcome_label == "failed_qc":
        return 0.0
    score = result.confidence
    if result.qc_status == "partial":
        score *= 0.65
    if result.outcome_label in {"inconclusive", "invalid"}:
        score *= 0.5
    if result.measured_value_numeric is None and result.measured_value not in {True, False}:
        score *= 0.75
    if detect_result_warnings(result):
        score *= 0.9
    return round(max(0.0, min(1.0, score)), 3)


def detect_result_warnings(result: AssayResult) -> list[str]:
    warnings = list(result.metadata.get("warnings", []))
    if not str(result.candidate_name or "").strip():
        warnings.append("candidate_name is required")
    if result.qc_status == "failed" or result.outcome_label == "failed_qc":
        warnings.append("failed_qc results must not improve candidate scores")
    if result.qc_status == "partial":
        warnings.append("partial QC lowers confidence")
    if (
        result.assay_context.endpoint.endpoint_category == "potency"
        and result.measured_value_numeric is not None
        and not result.unit
    ):
        warnings.append("unit is missing for numeric potency result")
    if result.measured_value_numeric is None and result.measured_value not in {None, True, False}:
        warnings.append("ambiguous measured value")
    return _dedupe(warnings)


def _classification_value(result: AssayResult, endpoint_rules: dict[str, Any]) -> float | None:
    if result.normalized_value is not None:
        return result.normalized_value
    if result.measured_value_numeric is not None:
        return result.measured_value_numeric
    if result.assay_context.endpoint.directionality == "binary" and isinstance(
        result.measured_value,
        bool,
    ):
        return 1.0 if result.measured_value else 0.0
    metadata_value = endpoint_rules.get("measured_value_numeric")
    try:
        return float(metadata_value) if metadata_value is not None else None
    except (TypeError, ValueError):
        return None


def _threshold_for(result: AssayResult, endpoint_rules: dict[str, Any]) -> float | None:
    keys = ["positive_threshold"]
    if result.assay_context.endpoint.endpoint_category == "safety":
        keys.insert(0, "safety_concern_threshold")
    for key in keys:
        if key in endpoint_rules:
            try:
                return float(endpoint_rules[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be numeric") from exc
    metadata_rules = result.metadata.get("endpoint_rules", {})
    if isinstance(metadata_rules, dict):
        return _threshold_for(result.model_copy(update={"metadata": {}}), metadata_rules)
    return None


def _threshold_value_for(
    result: AssayResult,
    endpoint_rules: dict[str, Any],
    threshold: float,
) -> float:
    threshold_unit = endpoint_rules.get("threshold_unit")
    if (
        threshold_unit
        and result.assay_context.endpoint.endpoint_category == "potency"
        and result.normalized_unit == "nM"
    ):
        normalized_threshold, normalized_unit = normalize_potency_units(threshold, threshold_unit)
        if normalized_threshold is not None and normalized_unit == result.normalized_unit:
            return normalized_threshold
    return threshold


def _is_ambiguous_without_rules(result: AssayResult) -> bool:
    return result.measured_value_numeric is None and not isinstance(result.measured_value, bool)


def _with_warnings(result: AssayResult, warnings: list[str]) -> AssayResult:
    metadata = dict(result.metadata)
    metadata["warnings"] = _dedupe(warnings)
    return result.model_copy(update={"metadata": metadata})


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
