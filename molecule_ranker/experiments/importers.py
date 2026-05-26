from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.experiments.schemas import (
    AssayContext,
    AssayEndpoint,
    AssayResult,
)

_ENDPOINT_DEFAULTS: dict[str, tuple[str, str, str | None]] = {
    "ames": ("safety", "binary", None),
    "binding_affinity": ("potency", "lower_is_better", None),
    "cellular_activity": ("phenotypic", "higher_is_better", None),
    "cytotoxicity": ("safety", "lower_is_better", None),
    "enzymatic_inhibition": ("potency", "higher_is_better", None),
    "herg": ("safety", "lower_is_better", None),
    "hERG": ("safety", "lower_is_better", None),
    "metabolic_stability": ("developability", "higher_is_better", None),
    "permeability": ("developability", "higher_is_better", None),
    "phenotypic_readout": ("phenotypic", "neutral", None),
    "reporter_assay": ("phenotypic", "higher_is_better", None),
    "selectivity": ("selectivity", "higher_is_better", None),
    "solubility": ("developability", "higher_is_better", None),
    "target_engagement": ("target_engagement", "higher_is_better", None),
    "viability": ("safety", "higher_is_better", None),
}
_VALID_OUTCOMES = {
    "positive",
    "negative",
    "inconclusive",
    "failed_qc",
    "not_tested",
    "invalid",
}
_VALID_ACTIVITY_DIRECTIONS = {
    "active",
    "inactive",
    "toxic",
    "non_toxic",
    "improved",
    "worsened",
    "no_effect",
    "ambiguous",
    "not_applicable",
}
_VALID_QC_STATUSES = {"passed", "failed", "partial", "unknown"}
_VALID_ASSAY_TYPES = {
    "biochemical",
    "cellular",
    "phenotypic",
    "safety",
    "developability",
    "computational_validation",
    "other",
}


def import_assay_results_csv(
    path: str | Path,
    default_context: AssayContext | None = None,
    imported_by: str | None = None,
) -> list[AssayResult]:
    """Import user-supplied CSV assay results without fabricating missing identities."""

    source_path = Path(path)
    with source_path.open(newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return [
        _result_from_row(
            _clean_row(row),
            row_index=index,
            source="csv_import",
            default_context=default_context,
            imported_by=imported_by,
        )
        for index, row in enumerate(rows, start=1)
    ]


def import_assay_results_json(
    path: str | Path,
    imported_by: str | None = None,
) -> list[AssayResult]:
    """Import JSON assay results from a list or an object containing ``results``."""

    source_path = Path(path)
    raw_payload = json.loads(source_path.read_text())
    raw_results = _json_results(raw_payload)
    results: list[AssayResult] = []
    for index, raw in enumerate(raw_results, start=1):
        if not isinstance(raw, dict):
            raise ValueError("JSON assay results must be objects.")
        if "assay_context" in raw:
            payload = dict(raw)
            if imported_by is not None:
                payload["imported_by"] = imported_by
            result = AssayResult.model_validate(payload)
        else:
            result = _result_from_row(
                _clean_row(raw),
                row_index=index,
                source="json_import",
                default_context=None,
                imported_by=imported_by,
            )
        results.append(result)
    return results


def infer_endpoint(row: Mapping[str, Any]) -> AssayEndpoint:
    endpoint_name = _text(row.get("endpoint_name")) or "unknown_endpoint"
    default_category, default_directionality, default_unit = _ENDPOINT_DEFAULTS.get(
        endpoint_name,
        ("other", "neutral", None),
    )
    unit = _text(row.get("unit")) or default_unit
    return AssayEndpoint(
        endpoint_id=_slug_id("endpoint", endpoint_name, row.get("endpoint_category") or ""),
        name=endpoint_name,
        endpoint_category=_text(row.get("endpoint_category")) or default_category,  # type: ignore[arg-type]
        unit=unit,
        directionality=_text(row.get("directionality")) or default_directionality,  # type: ignore[arg-type]
        description=_text(row.get("endpoint_description")),
        metadata={},
    )


def infer_assay_context(row: Mapping[str, Any]) -> AssayContext:
    endpoint = infer_endpoint(row)
    assay_name = _text(row.get("assay_name")) or "unspecified_assay"
    assay_type = _text(row.get("assay_type")) or "other"
    if assay_type not in _VALID_ASSAY_TYPES:
        assay_type = "other"
    return AssayContext(
        assay_context_id=_slug_id("assay-context", assay_name, endpoint.name),
        assay_name=assay_name,
        assay_type=assay_type,  # type: ignore[arg-type]
        target_symbol=_text(row.get("target_symbol")),
        target_identifiers={},
        disease_name=_text(row.get("disease_name")),
        model_system=_text(row.get("model_system")),
        species=_text(row.get("species")),
        endpoint=endpoint,
        protocol_reference=_text(row.get("protocol_reference")),
        protocol_summary=_text(row.get("protocol_summary")),
        metadata={},
    )


def parse_replicate_values(value: str | Sequence[Any] | None) -> list[float]:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        parts = [part for part in re.split(r"[;,]", value) if part.strip()]
    else:
        parts = list(value)
    parsed: list[float] = []
    for part in parts:
        if part is None or str(part).strip() == "":
            continue
        try:
            parsed.append(float(part))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"replicate value is not numeric: {part}") from exc
    return parsed


def parse_measured_value(value: object) -> tuple[float | str | bool | None, float | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    if isinstance(value, int | float):
        return float(value), float(value)
    text = str(value).strip()
    if not text:
        return None, None
    lowered = text.lower()
    if lowered == "true":
        return True, None
    if lowered == "false":
        return False, None
    try:
        numeric = float(text)
    except ValueError:
        return text, None
    return numeric, numeric


def normalize_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"could not parse result_date: {value}") from exc


def _result_from_row(
    row: dict[str, Any],
    *,
    row_index: int,
    source: str,
    default_context: AssayContext | None,
    imported_by: str | None,
) -> AssayResult:
    candidate_name = _text(row.get("candidate_name"))
    if candidate_name is None:
        raise ValueError(f"candidate_name is required for assay result row {row_index}")
    warnings: list[str] = []
    measured_value, measured_value_numeric = parse_measured_value(row.get("measured_value"))
    if (
        row.get("measured_value") not in {None, ""}
        and measured_value_numeric is None
        and not isinstance(measured_value, bool)
    ):
        warnings.append("measured_value is not numeric")
    assay_context = default_context or infer_assay_context(row)
    unit = _text(row.get("unit")) or assay_context.endpoint.unit
    if (
        measured_value_numeric is not None
        and assay_context.endpoint.endpoint_category == "potency"
        and unit is None
    ):
        warnings.append("unit is missing for numeric potency result")
    outcome_label = _outcome_label(row, assay_context)
    activity_direction = _normalized_choice(
        row.get("activity_direction"),
        _VALID_ACTIVITY_DIRECTIONS,
        "ambiguous" if outcome_label == "inconclusive" else "not_applicable",
    )
    qc_status = _normalized_choice(row.get("qc_status"), _VALID_QC_STATUSES, "unknown")
    result_id = _slug_id(
        source,
        row.get("source_record_id") or row_index,
        candidate_name,
        assay_context.assay_name,
        assay_context.endpoint.name,
    )
    metadata = {
        "raw_row": row,
        "warnings": warnings,
    }
    return AssayResult(
        result_id=result_id,
        candidate_id=_text(row.get("candidate_id")),
        candidate_name=candidate_name,
        candidate_origin=_text(row.get("candidate_origin")) or "unknown",  # type: ignore[arg-type]
        canonical_smiles=_text(row.get("canonical_smiles")),
        inchi_key=_text(row.get("inchi_key")),
        disease_name=_text(row.get("disease_name")) or assay_context.disease_name,
        target_symbol=_text(row.get("target_symbol")) or assay_context.target_symbol,
        assay_context=assay_context,
        measured_value=measured_value,
        measured_value_numeric=measured_value_numeric,
        unit=unit,
        relation=_text(row.get("relation")),
        normalized_value=measured_value_numeric,
        normalized_unit=unit,
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        replicate_count=_parse_int(row.get("replicate_count")),
        replicate_values=parse_replicate_values(row.get("replicate_values")),
        uncertainty=_parse_optional_float(row.get("uncertainty"), field_name="uncertainty"),
        confidence=_parse_optional_float(row.get("confidence"), field_name="confidence") or 0.5,
        qc_status=qc_status,  # type: ignore[arg-type]
        result_date=normalize_date(row.get("result_date")),
        source=source,
        source_record_id=_text(row.get("source_record_id")),
        imported_by=imported_by,
        notes=_text(row.get("notes")),
        metadata=metadata,
    )


def _json_results(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return list(payload["results"])
    raise ValueError("JSON assay result input must be a list or an object with a results list.")


def _outcome_label(row: Mapping[str, Any], context: AssayContext) -> str:
    raw = _text(row.get("outcome_label"))
    if raw:
        return raw
    if context.endpoint.directionality == "binary":
        measured_value, _numeric = parse_measured_value(row.get("measured_value"))
        if measured_value is True:
            return "positive"
        if measured_value is False:
            return "negative"
    return "inconclusive"


def _normalized_choice(value: object, valid_values: set[str], default: str) -> str:
    text = _text(value)
    if text is None:
        return default
    return text if text in valid_values else text


def _parse_int(value: object) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"replicate_count is not an integer: {value}") from exc


def _parse_optional_float(value: object, *, field_name: str) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not numeric: {value}") from exc


def _clean_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {_normalize_key(key): value for key, value in row.items()}


def _normalize_key(key: object) -> str:
    return str(key).strip().lower().replace(" ", "_").replace("-", "_")


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _slug_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = uuid5(NAMESPACE_URL, raw).hex[:12]
    slug_parts = [_slug(part) for part in parts if _text(part) is not None]
    return "-".join([_slug(prefix), *slug_parts, digest])


def _slug(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "unknown"
