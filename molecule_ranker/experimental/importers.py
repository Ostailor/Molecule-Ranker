from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.experimental.schemas import (
    AssayImportResult,
    AssayOutcome,
    AssayResult,
    AssayResultSourceType,
    AssayResultValidationReport,
)

_FIELD_ALIASES = {
    "candidate": "candidate_id",
    "candidate_name": "molecule_name",
    "compound": "molecule_name",
    "compound_name": "molecule_name",
    "disease": "disease_name",
    "experiment": "experiment_id",
    "generated_name": "generated_molecule_name",
    "generated_molecule": "generated_molecule_name",
    "handoff_id": "validation_handoff_id",
    "molecule": "molecule_name",
    "result": "outcome",
    "target": "target_symbol",
}

_OUTCOME_ALIASES: dict[str, AssayOutcome] = {
    "active": "positive",
    "hit": "positive",
    "pass": "positive",
    "positive": "positive",
    "supported": "positive",
    "inactive": "negative",
    "miss": "negative",
    "negative": "negative",
    "no_effect": "negative",
    "not_active": "negative",
    "fail": "failed",
    "failed": "failed",
    "qc_fail": "failed",
    "technical_failure": "failed",
    "ambiguous": "inconclusive",
    "equivocal": "inconclusive",
    "inconclusive": "inconclusive",
}

_DIRECTION_ALIASES = {
    "higher": "higher_is_better",
    "higher_is_better": "higher_is_better",
    "lower": "lower_is_better",
    "lower_is_better": "lower_is_better",
    "neutral": "neutral",
    "unknown": "unknown",
}


def import_assay_results(
    input_path: str | Path,
    *,
    payload: list[dict[str, Any]] | dict[str, Any] | None = None,
    input_format: str = "auto",
    source_type: AssayResultSourceType = "user_imported_file",
) -> AssayImportResult:
    """Import CSV/JSON assay results without inferring missing outcomes."""

    path = Path(input_path)
    rows, source_format = _load_rows(path, payload=payload, input_format=input_format)
    results = [
        _normalize_row(row, source_path=str(path), source_row=index, source_type=source_type)
        for index, row in enumerate(rows, start=1)
    ]
    report = validate_assay_results(results)
    return AssayImportResult(
        source_path=str(path),
        source_format=source_format,
        results=results,
        validation_report=report,
    )


def validate_assay_results(results: Iterable[AssayResult]) -> AssayResultValidationReport:
    materialized = list(results)
    outcome_counts: dict[str, int] = {}
    row_issues: list[dict[str, Any]] = []
    for result in materialized:
        if result.outcome is not None:
            outcome_counts[result.outcome] = outcome_counts.get(result.outcome, 0) + 1
        if result.validation_issues:
            row_issues.append(
                {
                    "result_id": result.result_id,
                    "source_row": result.source_row,
                    "status": result.validation_status,
                    "issues": result.validation_issues,
                }
            )
    return AssayResultValidationReport(
        total_count=len(materialized),
        valid_count=sum(1 for result in materialized if result.validation_status == "valid"),
        incomplete_count=sum(
            1 for result in materialized if result.validation_status == "incomplete"
        ),
        invalid_count=sum(1 for result in materialized if result.validation_status == "invalid"),
        outcome_counts=dict(sorted(outcome_counts.items())),
        row_issues=row_issues,
    )


def _load_rows(
    path: Path,
    *,
    payload: list[dict[str, Any]] | dict[str, Any] | None,
    input_format: str,
) -> tuple[list[dict[str, Any]], Literal["csv", "json", "inline"]]:
    if payload is not None:
        return _rows_from_json_payload(payload), "inline"
    fmt = _detect_format(path, input_format)
    if fmt == "csv":
        with path.open(newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)], "csv"
    raw = json.loads(path.read_text())
    return _rows_from_json_payload(raw), "json"


def _rows_from_json_payload(payload: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        raw_results = payload.get("results") or payload.get("assay_results") or []
        if isinstance(raw_results, list):
            return [row for row in raw_results if isinstance(row, dict)]
    raise ValueError("Assay result JSON must be a list or an object with a results array.")


def _detect_format(path: Path, input_format: str) -> Literal["csv", "json"]:
    fmt = input_format.lower()
    if fmt == "auto":
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return "csv"
        if suffix == ".json":
            return "json"
        raise ValueError("Cannot infer assay result format; use --format csv or --format json.")
    if fmt not in {"csv", "json"}:
        raise ValueError("Assay result format must be auto, csv, or json.")
    if fmt == "csv":
        return "csv"
    return "json"


def _normalize_row(
    row: dict[str, Any],
    *,
    source_path: str,
    source_row: int,
    source_type: AssayResultSourceType,
) -> AssayResult:
    normalized: dict[str, Any] = {}
    metadata: dict[str, Any] = {"raw": row}
    issues: list[str] = []
    for key, value in row.items():
        field = _normalize_key(key)
        if field in _FIELD_ALIASES:
            field = _FIELD_ALIASES[field]
        if field == "outcome":
            outcome, outcome_issue = _normalize_outcome(value)
            normalized["outcome"] = outcome
            if outcome_issue:
                issues.append(outcome_issue)
            continue
        if field == "value":
            parsed_value, value_issue = _parse_float(value)
            normalized["value"] = parsed_value
            if value_issue:
                issues.append(value_issue)
            continue
        if field == "direction":
            normalized["direction"] = _DIRECTION_ALIASES.get(_clean(value), "unknown")
            continue
        if field in AssayResult.model_fields:
            cleaned = _clean_text_or_none(value)
            if cleaned is not None:
                normalized[field] = cleaned
        else:
            metadata[field] = value
    if any(issue.startswith("unrecognized outcome") for issue in issues):
        normalized["validation_status"] = "invalid"
    normalized["validation_issues"] = issues
    normalized["source_path"] = source_path
    normalized["source_row"] = source_row
    normalized["provenance"] = {
        "source_type": source_type,
        "source_path": source_path,
        "source_row": source_row,
    }
    normalized["metadata"] = metadata
    return AssayResult.model_validate(normalized)


def _normalize_key(key: object) -> str:
    return str(key).strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_outcome(value: object) -> tuple[AssayOutcome | None, str | None]:
    cleaned = _clean(value)
    if not cleaned:
        return None, None
    outcome = _OUTCOME_ALIASES.get(cleaned)
    if outcome is None:
        return None, f"unrecognized outcome: {value}"
    return outcome, None


def _parse_float(value: object) -> tuple[float | None, str | None]:
    cleaned = _clean_text_or_none(value)
    if cleaned is None:
        return None, None
    try:
        return float(cleaned), None
    except (TypeError, ValueError):
        return None, f"value is not numeric: {value}"


def _clean(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _clean_text_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
