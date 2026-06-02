from __future__ import annotations

import json
import re
from typing import Any

EVALUATION_CODEX_TASK_TYPES = {
    "summarize_evaluation_report",
    "explain_metric_changes",
    "draft_benchmark_limitations",
    "summarize_prospective_validation",
    "explain_guardrail_failures",
    "draft_decision_quality_lessons",
}

EVALUATION_REQUIRED_REF_PREFIXES = {
    "evaluation_id": "evaluation_id",
    "task_id": "task_id",
    "dataset_id": "dataset_id",
    "split_id": "split_id",
    "metric_id": "metric IDs",
    "artifact_id": "artifact IDs",
}

FORBIDDEN_EVALUATION_OVERCLAIMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bclinical(?:ly)? validat(?:e|ed|ion)\b", re.I), "clinical validation"),
    (
        re.compile(
            r"\bproof\s+of\s+(?:efficacy|safety|activity|synthesizability)\b",
            re.I,
        ),
        "proof of efficacy, safety, activity, or synthesizability",
    ),
    (re.compile(r"\b(?:proves?|proved)\s+(?:active|safe|effective)\b", re.I), "proof claim"),
    (
        re.compile(r"\b(?:created|added|generated)\s+(?:evidence|assay results?|labels?)\b", re.I),
        "evidence creation claim",
    ),
    (
        re.compile(r"\b(?:altered|changed|modified)\s+(?:metrics?|benchmark results?)\b", re.I),
        "result alteration claim",
    ),
)

NO_GUARDRAIL_FAILURE_PATTERNS = (
    re.compile(r"\bno\s+guardrail\s+failures?\b", re.I),
    re.compile(r"\bguardrails?\s+(?:all\s+)?pass(?:ed)?\b", re.I),
)


def validate_evaluation_codex_output(
    result: Any,
    *,
    allowed_artifact_refs: set[str],
) -> Any:
    """Apply evaluation-specific Codex output guardrails.

    Codex is allowed to explain evaluation artifacts, but it cannot create or edit
    benchmark facts. The validator therefore checks citations against identifiers
    extracted from supplied artifacts and makes recorded guardrail failures sticky.
    """

    if str(result.task_type) not in EVALUATION_CODEX_TASK_TYPES:
        return result

    payload = _payload(result)
    text = _result_text(result, payload)
    allowed_lower = {ref.lower() for ref in allowed_artifact_refs}
    warnings = [
        *_detect_missing_required_citations(text, payload, allowed_lower),
        *_detect_unbacked_metric_ids(payload, allowed_lower),
        *_detect_unbacked_artifact_ids(payload, allowed_lower),
        *_detect_hidden_guardrail_failures(text, allowed_lower),
        *_detect_forbidden_evaluation_overclaims(text),
    ]
    warnings = _dedupe(warnings)
    if not warnings:
        return result

    existing = list(result.guardrail_warnings)
    for warning in warnings:
        if warning not in existing:
            existing.append(warning)
    return result.model_copy(update={"status": "guardrail_failed", "guardrail_warnings": existing})


def _payload(result: Any) -> dict[str, Any]:
    if isinstance(result.output_json, dict):
        return result.output_json
    text = result.output_text or result.stdout
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _result_text(result: Any, payload: dict[str, Any]) -> str:
    parts = [result.output_text or "", result.stdout or ""]
    if payload:
        parts.append(json.dumps(payload, sort_keys=True))
    return "\n".join(part for part in parts if part)


def _detect_missing_required_citations(
    text: str,
    payload: dict[str, Any],
    allowed_lower: set[str],
) -> list[str]:
    lowered_text = text.lower()
    warnings: list[str] = []
    for prefix, label in EVALUATION_REQUIRED_REF_PREFIXES.items():
        values = _prefixed_allowed_values(allowed_lower, prefix)
        if not values:
            continue
        if prefix == "metric_id":
            cited = {str(item).lower() for item in _list(payload.get("metric_ids"))}
            if not cited:
                warnings.append("Evaluation Codex output missing required metric IDs.")
                continue
            missing = sorted(
                value for value in values if value not in cited and value not in lowered_text
            )
            if missing:
                warnings.append(
                    "Evaluation Codex output did not cite required metric IDs: "
                    + ", ".join(missing)
                    + "."
                )
            continue
        if prefix == "artifact_id":
            cited = {str(item).lower() for item in _list(payload.get("artifact_ids"))}
            if not cited:
                warnings.append("Evaluation Codex output missing required artifact IDs.")
                continue
            missing = sorted(
                value for value in values if value not in cited and value not in lowered_text
            )
            if missing:
                warnings.append(
                    "Evaluation Codex output did not cite required artifact IDs: "
                    + ", ".join(missing)
                    + "."
                )
            continue
        if not any(value in lowered_text for value in values):
            warnings.append(f"Evaluation Codex output did not cite required {label}.")
    return warnings


def _detect_unbacked_metric_ids(payload: dict[str, Any], allowed_lower: set[str]) -> list[str]:
    allowed_metric_ids = _prefixed_allowed_values(allowed_lower, "metric_id")
    warnings: list[str] = []
    for metric_id in _list(payload.get("metric_ids")):
        lowered = str(metric_id).lower()
        if lowered not in allowed_metric_ids:
            warnings.append(f"Unbacked evaluation metric ID: {metric_id}.")
    for metric in _list(payload.get("metrics")):
        if isinstance(metric, dict) and metric.get("metric_id") is not None:
            metric_id = str(metric["metric_id"])
            if metric_id.lower() not in allowed_metric_ids:
                warnings.append(f"Unbacked evaluation metric ID: {metric_id}.")
    return warnings


def _detect_unbacked_artifact_ids(payload: dict[str, Any], allowed_lower: set[str]) -> list[str]:
    allowed_artifact_ids = _prefixed_allowed_values(allowed_lower, "artifact_id")
    warnings: list[str] = []
    for artifact_id in _list(payload.get("artifact_ids")):
        lowered = str(artifact_id).lower()
        if lowered not in allowed_artifact_ids:
            warnings.append(f"Unbacked evaluation artifact ID: {artifact_id}.")
    return warnings


def _detect_hidden_guardrail_failures(text: str, allowed_lower: set[str]) -> list[str]:
    if "evaluation_guardrail_failure:recorded" not in allowed_lower:
        return []
    if any(pattern.search(text) for pattern in NO_GUARDRAIL_FAILURE_PATTERNS):
        return ["Evaluation Codex output hid a recorded guardrail failure."]
    lowered = text.lower()
    if "guardrail" not in lowered or "fail" not in lowered:
        return ["Evaluation Codex output omitted recorded guardrail failures."]
    return []


def _detect_forbidden_evaluation_overclaims(text: str) -> list[str]:
    warnings: list[str] = []
    for pattern, label in FORBIDDEN_EVALUATION_OVERCLAIMS:
        match = pattern.search(text)
        if match and not _is_negated_constraint(text, match.start()):
            warnings.append(f"Forbidden evaluation Codex claim: {label}.")
    return warnings


def _prefixed_allowed_values(allowed_lower: set[str], prefix: str) -> set[str]:
    needle = f"{prefix}:"
    return {ref.split(":", 1)[1] for ref in allowed_lower if ref.startswith(needle)}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _is_negated_constraint(text: str, start: int) -> bool:
    window = text[max(0, start - 80) : start + 80].lower()
    negations = (
        "not ",
        "no ",
        "cannot ",
        "do not ",
        "isn't ",
        "is not ",
        "are not ",
        "without ",
    )
    return any(negation in window for negation in negations)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


__all__ = [
    "EVALUATION_CODEX_TASK_TYPES",
    "EVALUATION_REQUIRED_REF_PREFIXES",
    "validate_evaluation_codex_output",
]
