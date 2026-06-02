from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.evaluation.schemas import (
    BenchmarkDataset,
    BenchmarkDatasetType,
    BenchmarkTaskType,
)

ArtifactInput = str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]]

DATA_CONTRACT_VERSION = "data-contracts.v1"

_TASK_NAMES: dict[BenchmarkTaskType, str] = {
    "candidate_ranking": "Candidate ranking benchmark dataset",
    "molecule_generation": "Generated molecule prioritization benchmark dataset",
    "surrogate_prediction": "Surrogate model prediction benchmark dataset",
    "developability_triage": "Developability triage benchmark dataset",
    "structure_prioritization": "Structure-aware prioritization benchmark dataset",
    "portfolio_selection": "Portfolio selection benchmark dataset",
    "hypothesis_prioritization": "Hypothesis prioritization benchmark dataset",
    "campaign_planning": "Campaign planning benchmark dataset",
    "codex_guardrail": "Codex guardrail behavior benchmark dataset",
    "integration_data_quality": "Integration data quality benchmark dataset",
}

_TASK_SOURCE_FIELDS: dict[BenchmarkTaskType, tuple[str, ...]] = {
    "candidate_ranking": ("candidates", "portfolio_candidates", "ranked_candidates"),
    "molecule_generation": (
        "retained_generated_molecules",
        "generated_molecule_hypotheses",
        "generated_candidates",
        "rejected_generated_molecules",
    ),
    "surrogate_prediction": ("predictions", "model_predictions"),
    "developability_triage": ("assessments", "developability_assessments"),
    "structure_prioritization": ("structure_aware_assessments", "assessments", "candidates"),
    "portfolio_selection": (
        "selected_candidates",
        "portfolio_candidates",
        "ranked_candidates",
        "candidates",
    ),
    "hypothesis_prioritization": ("hypotheses", "ranked_hypotheses", "research_hypotheses"),
    "campaign_planning": ("work_packages", "candidate_batches", "stage_gates"),
    "codex_guardrail": ("guardrail_cases", "cases", "findings", "summaries"),
    "integration_data_quality": (
        "records",
        "fixtures",
        "external_records",
        "mappings",
        "sync_records",
        "entities",
    ),
}

_ASSAY_SOURCE_HINTS = (
    "assay",
    "imported_assay",
    "experimental_evidence",
    "experimental_result",
    "outcome",
    "label",
)
_MODEL_PREDICTION_HINTS = ("model_prediction", "model_predictions", "prediction_set")
_FAILED_QC_STATUSES = {"fail", "failed", "qc_failed", "rejected", "invalid"}
_PASS_QC_STATUSES = {"pass", "passed", "qc_passed", "accepted", "valid", "ok"}


@dataclass(frozen=True)
class ArtifactSource:
    artifact_id: str
    payload: Any
    source_name: str


def build_benchmark_dataset(
    *,
    task_type: BenchmarkTaskType,
    sources: Mapping[str, ArtifactInput],
    dataset_id: str | None = None,
    name: str | None = None,
    dataset_type: BenchmarkDatasetType | None = None,
    data_contract_version: str = DATA_CONTRACT_VERSION,
    include_failed_qc_labels: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> BenchmarkDataset:
    """Build an aggregate benchmark dataset with row provenance metadata.

    The returned schema intentionally stores aggregate counts at top level. Row-level
    source provenance, labels, warnings, and exclusions are preserved under
    ``metadata``.
    """

    normalized = _normalize_sources(sources)
    rows = _task_rows(task_type, normalized)
    label_records = _label_records(normalized)
    label_index = _label_index(label_records)
    excluded_labels: list[dict[str, Any]] = []
    warnings = [
        "benchmark_dataset_is_not_biomedical_evidence",
        "model_predictions_are_never_used_as_outcome_labels",
    ]
    allow_failed_qc = include_failed_qc_labels or bool(
        (metadata or {}).get("evaluates_qc_handling")
    )
    for row in rows:
        labels = _matching_labels(row, label_index)
        accepted = []
        for label in labels:
            exclusion_reason = _label_exclusion_reason(
                row,
                label,
                allow_failed_qc=allow_failed_qc,
            )
            if exclusion_reason is not None:
                excluded_labels.append(
                    {
                        "row_id": row["row_id"],
                        "label_artifact_id": label["source_artifact_id"],
                        "label_source_record_id": label.get("source_record_id"),
                        "reason": exclusion_reason,
                    }
                )
                continue
            accepted.append(label)
        row["labels"] = accepted

    label_count = sum(len(row["labels"]) for row in rows)
    candidate_ids = {
        str(row["entity_id"])
        for row in rows
        if row.get("entity_id") is not None and str(row["entity_id"])
    }
    source_artifact_ids = [source.artifact_id for source in normalized]
    resolved_metadata = {
        **dict(metadata or {}),
        "task_type": task_type,
        "rows": rows,
        "label_artifact_ids": sorted({label["source_artifact_id"] for label in label_records}),
        "excluded_labels": excluded_labels,
        "warnings": warnings,
        "label_rules": {
            "default_sources": "synthetic_or_user_imported",
            "failed_qc_excluded": not allow_failed_qc,
            "model_predictions_as_labels": False,
            "generated_label_matching": "exact_linked_imported_result_only",
        },
    }
    return BenchmarkDataset(
        dataset_id=dataset_id or f"{task_type}-benchmark-dataset",
        name=name or _TASK_NAMES[task_type],
        dataset_type=dataset_type or _infer_dataset_type(task_type, normalized),
        source_artifact_ids=source_artifact_ids,
        row_count=len(rows),
        candidate_count=len(candidate_ids),
        label_count=label_count,
        created_at=datetime.now(UTC),
        data_contract_version=data_contract_version,
        metadata=resolved_metadata,
    )


def build_candidate_ranking_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="candidate_ranking", sources=sources, **kwargs)


def build_generated_molecule_prioritization_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="molecule_generation", sources=sources, **kwargs)


def build_surrogate_prediction_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="surrogate_prediction", sources=sources, **kwargs)


def build_developability_triage_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="developability_triage", sources=sources, **kwargs)


def build_structure_prioritization_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="structure_prioritization", sources=sources, **kwargs)


def build_portfolio_selection_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="portfolio_selection", sources=sources, **kwargs)


def build_hypothesis_prioritization_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="hypothesis_prioritization", sources=sources, **kwargs)


def build_campaign_planning_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="campaign_planning", sources=sources, **kwargs)


def build_codex_guardrail_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="codex_guardrail", sources=sources, **kwargs)


def build_integration_data_quality_dataset(
    sources: Mapping[str, ArtifactInput],
    **kwargs: Any,
) -> BenchmarkDataset:
    return build_benchmark_dataset(task_type="integration_data_quality", sources=sources, **kwargs)


def _normalize_sources(sources: Mapping[str, ArtifactInput]) -> list[ArtifactSource]:
    normalized = []
    for source_name, value in sources.items():
        payload = _load_payload(value)
        artifact_id = _artifact_id(source_name, value, payload)
        normalized.append(
            ArtifactSource(
                artifact_id=artifact_id,
                payload=payload,
                source_name=source_name,
            )
        )
    return normalized


def _load_payload(value: ArtifactInput) -> Any:
    if isinstance(value, str | Path):
        path = Path(value)
        if path.suffix.lower() == ".csv":
            with path.open(newline="") as handle:
                return list(csv.DictReader(handle))
        return json.loads(path.read_text())
    if isinstance(value, Mapping):
        return dict(value)
    return [dict(item) for item in value]


def _artifact_id(source_name: str, value: ArtifactInput, payload: Any) -> str:
    if isinstance(payload, Mapping):
        metadata = payload.get("metadata")
        candidates = (
            payload.get("artifact_id"),
            payload.get("artifactId"),
            payload.get("id"),
            metadata.get("artifact_id") if isinstance(metadata, Mapping) else None,
        )
        for candidate in candidates:
            if candidate:
                return str(candidate)
    if isinstance(value, str | Path):
        return Path(value).name
    return source_name


def _task_rows(
    task_type: BenchmarkTaskType,
    sources: Sequence[ArtifactSource],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        if _is_label_source(source) and task_type not in {
            "codex_guardrail",
            "integration_data_quality",
        }:
            continue
        if _is_model_prediction_source(source) and task_type != "surrogate_prediction":
            continue
        records = _records_for_task(task_type, source.payload)
        for index, record in enumerate(records):
            normalized_record = _record_payload(record)
            entity_id = _entity_id(
                normalized_record,
                task_type,
                fallback=f"{source.artifact_id}:{index}",
            )
            row = {
                "row_id": f"{source.artifact_id}:{index}",
                "entity_id": entity_id,
                "candidate_id": _candidate_id(normalized_record),
                "source_artifact_id": source.artifact_id,
                "source_name": source.source_name,
                "source_index": index,
                "source_record_id": _source_record_id(normalized_record, fallback=entity_id),
                "is_generated": _is_generated_record(normalized_record, task_type),
                "provenance": _row_provenance(normalized_record, source),
                "record": normalized_record,
            }
            rows.append(row)
    return rows


def _records_for_task(task_type: BenchmarkTaskType, payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    if task_type == "campaign_planning":
        records = _records_from_fields(payload, _TASK_SOURCE_FIELDS[task_type])
        campaign_plan = payload.get("campaign_plan")
        if isinstance(campaign_plan, Mapping):
            records.extend(_records_from_fields(campaign_plan, _TASK_SOURCE_FIELDS[task_type]))
        return records
    return _records_from_fields(payload, _TASK_SOURCE_FIELDS[task_type])


def _records_from_fields(
    payload: Mapping[str, Any],
    fields: Sequence[str],
) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for field in fields:
        value = payload.get(field)
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, Mapping))
        elif isinstance(value, Mapping):
            records.append(value)
    if not records and _looks_like_record(payload):
        records.append(payload)
    return records


def _record_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    generated = record.get("generated_molecule")
    if isinstance(generated, Mapping):
        merged = dict(generated)
        merged["rejection_reasons"] = record.get("rejection_reasons", [])
        return merged
    candidate = record.get("candidate")
    if isinstance(candidate, Mapping):
        merged = dict(candidate)
        for key in ("portfolio_score", "selection_status", "rank"):
            if key in record:
                merged[key] = record[key]
        return merged
    return dict(record)


def _label_records(sources: Sequence[ArtifactSource]) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for source in sources:
        if _is_model_prediction_source(source):
            continue
        if not _is_label_source(source):
            continue
        records = _candidate_label_records(source.payload)
        for index, record in enumerate(records):
            normalized = dict(record)
            normalized["_label_source_kind"] = _label_source_kind(source)
            normalized["source_artifact_id"] = source.artifact_id
            normalized["source_index"] = index
            normalized["source_record_id"] = _source_record_id(
                normalized,
                fallback=f"{source.artifact_id}:{index}",
            )
            labels.append(normalized)
    return labels


def _candidate_label_records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    fields = (
        "assay_results",
        "results",
        "experimental_results",
        "experimental_evidence",
        "labels",
        "outcome_labels",
        "fixtures",
    )
    return _records_from_fields(payload, fields)


def _label_index(labels: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for label in labels:
        for key in _match_keys(label, include_name=False):
            index.setdefault(key, []).append(label)
    return index


def _matching_labels(
    row: Mapping[str, Any],
    label_index: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    matched: dict[tuple[str, str], dict[str, Any]] = {}
    for key in _match_keys(row["record"], include_name=not row.get("is_generated")):
        for label in label_index.get(key, []):
            matched[(label["source_artifact_id"], label["source_record_id"])] = label
    return list(matched.values())


def _label_exclusion_reason(
    row: Mapping[str, Any],
    label: Mapping[str, Any],
    *,
    allow_failed_qc: bool,
) -> str | None:
    if _is_prediction_label(label):
        return "model_prediction_not_outcome_label"
    qc_status = _qc_status(label)
    if not allow_failed_qc and qc_status in _FAILED_QC_STATUSES:
        return "failed_qc"
    if row.get("is_generated") and label.get("_label_source_kind") != "imported_assay_result":
        return "generated_requires_exact_imported_assay_result"
    return None


def _is_label_source(source: ArtifactSource) -> bool:
    name = source.source_name.lower()
    if any(hint in name for hint in _ASSAY_SOURCE_HINTS):
        return True
    payload = source.payload
    if isinstance(payload, list):
        return any(_has_label_value(item) for item in payload if isinstance(item, Mapping))
    if isinstance(payload, Mapping):
        if any(field in payload for field in ("assay_results", "labels", "outcome_labels")):
            return True
        if payload.get("artifact_type") in {"assay_results", "experimental_evidence"}:
            return True
        return _has_label_value(payload)
    return False


def _is_model_prediction_source(source: ArtifactSource) -> bool:
    name = source.source_name.lower()
    if any(hint in name for hint in _MODEL_PREDICTION_HINTS):
        return True
    payload = source.payload
    if isinstance(payload, Mapping):
        artifact_type = str(payload.get("artifact_type") or "").lower()
        return "prediction" in artifact_type
    return False


def _label_source_kind(source: ArtifactSource) -> str:
    name = source.source_name.lower()
    if "synthetic" in name or _payload_flag(source.payload, "synthetic"):
        return "synthetic_validation_fixture"
    if any(hint in name for hint in ("assay", "experimental")):
        return "imported_assay_result"
    return "benchmark_fixture"


def _infer_dataset_type(
    task_type: BenchmarkTaskType,
    sources: Sequence[ArtifactSource],
) -> BenchmarkDatasetType:
    if task_type == "integration_data_quality":
        return "integration_fixture"
    if any("external" in source.source_name.lower() for source in sources):
        return "external_benchmark"
    if any(_payload_flag(source.payload, "synthetic") for source in sources):
        return "synthetic_validation"
    if all(_is_label_source(source) for source in sources):
        return "imported_assay_results"
    return "frozen_project_artifacts"


def _payload_flag(payload: Any, key: str) -> bool:
    if not isinstance(payload, Mapping):
        return False
    metadata = payload.get("metadata")
    metadata_value = metadata.get(key) if isinstance(metadata, Mapping) else None
    return bool(payload.get(key) or metadata_value)


def _has_label_value(record: Mapping[str, Any]) -> bool:
    return any(
        key in record
        for key in (
            "outcome_label",
            "label",
            "measured_value",
            "value",
            "supported",
            "status",
        )
    )


def _is_prediction_label(record: Mapping[str, Any]) -> bool:
    source_type = str(record.get("source_type") or record.get("artifact_type") or "").lower()
    return "prediction" in source_type or bool(
        record.get("model_id") or record.get("model_version")
    )


def _qc_status(record: Mapping[str, Any]) -> str:
    status = str(record.get("qc_status") or record.get("quality_status") or "").strip().lower()
    if status in _PASS_QC_STATUSES or status in _FAILED_QC_STATUSES:
        return status
    return status or "unknown"


def _looks_like_record(payload: Mapping[str, Any]) -> bool:
    return bool(_entity_id(payload, "candidate_ranking", fallback=""))


def _entity_id(record: Mapping[str, Any], task_type: BenchmarkTaskType, *, fallback: str) -> str:
    if task_type == "hypothesis_prioritization":
        for key in ("hypothesis_id", "research_hypothesis_id", "id"):
            if record.get(key):
                return str(record[key])
    if task_type == "campaign_planning":
        for key in ("work_package_id", "package_id", "stage_gate_id", "id"):
            if record.get(key):
                return str(record[key])
    if task_type == "integration_data_quality":
        for key in ("integration_record_id", "external_record_id", "record_id", "id"):
            if record.get(key):
                return str(record[key])
    return _candidate_id(record) or str(record.get("record_id") or record.get("id") or fallback)


def _candidate_id(record: Mapping[str, Any]) -> str | None:
    for key in ("candidate_id", "generated_id", "molecule_id", "compound_id"):
        if record.get(key):
            return str(record[key])
    return None


def _source_record_id(record: Mapping[str, Any], *, fallback: str) -> str:
    for key in ("source_record_id", "result_id", "record_id", "id"):
        if record.get(key):
            return str(record[key])
    return fallback


def _is_generated_record(record: Mapping[str, Any], task_type: BenchmarkTaskType) -> bool:
    if task_type == "molecule_generation":
        return True
    origin = str(record.get("candidate_origin") or record.get("origin") or "").lower()
    return origin == "generated" or bool(record.get("generated_id"))


def _row_provenance(record: Mapping[str, Any], source: ArtifactSource) -> dict[str, Any]:
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping):
        return dict(provenance)
    return {
        "source_artifact_id": source.artifact_id,
        "source_name": source.source_name,
        "source_record_id": _source_record_id(record, fallback=source.artifact_id),
    }


def _match_keys(record: Mapping[str, Any], *, include_name: bool) -> list[str]:
    keys = []
    for field in ("candidate_id", "generated_id", "molecule_id", "compound_id"):
        if record.get(field):
            keys.append(f"{field}:{record[field]}")
    for field in (
        "hypothesis_id",
        "research_hypothesis_id",
        "work_package_id",
        "package_id",
        "integration_record_id",
        "external_record_id",
        "record_id",
    ):
        if record.get(field):
            keys.append(f"{field}:{record[field]}")
    for field in ("inchi_key", "canonical_smiles"):
        if record.get(field):
            keys.append(f"{field}:{record[field]}")
    if include_name:
        for field in ("candidate_name", "name"):
            if record.get(field):
                keys.append(f"{field}:{record[field]}")
    return keys

__all__ = [
    "BenchmarkDataset",
    "build_benchmark_dataset",
    "build_campaign_planning_dataset",
    "build_candidate_ranking_dataset",
    "build_codex_guardrail_dataset",
    "build_developability_triage_dataset",
    "build_generated_molecule_prioritization_dataset",
    "build_hypothesis_prioritization_dataset",
    "build_integration_data_quality_dataset",
    "build_portfolio_selection_dataset",
    "build_structure_prioritization_dataset",
    "build_surrogate_prediction_dataset",
]
