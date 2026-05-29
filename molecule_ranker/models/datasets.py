"""Assay-specific model training dataset builder."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.experiments.store import ExperimentalResultStore
from molecule_ranker.models.schemas import ModelEndpoint, ModelFeatureSpec, ModelTrainingDataset


@dataclass(frozen=True)
class ModelDatasetBuildResult:
    dataset: ModelTrainingDataset
    features: list[dict[str, Any]]
    labels: list[Any]
    feature_matrix_path: Path
    labels_path: Path
    manifest_path: Path


def build_assay_model_training_dataset(
    store: ExperimentalResultStore,
    *,
    candidates: Sequence[Any] = (),
    generated_molecules: Sequence[Any] = (),
    endpoint: ModelEndpoint,
    feature_spec: ModelFeatureSpec,
    output_dir: str | Path,
    config: Mapping[str, Any] | None = None,
) -> ModelDatasetBuildResult:
    config = dict(config or {})
    if config.get("allow_endpoint_pooling") and not config.get("pooled_endpoint_label"):
        raise ValueError("Endpoint pooling requires pooled_endpoint_label.")

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    results = store.list_results()
    candidate_index = _candidate_index(candidates)
    generated_index = _generated_index(generated_molecules)

    features: list[dict[str, Any]] = []
    labels: list[Any] = []
    source_result_ids: list[str] = []
    included_candidate_ids: list[str] = []
    excluded_result_ids: list[str] = []
    exclusion_reasons: dict[str, str] = {}
    positive_count = 0
    negative_count = 0

    for result in results:
        exclusion = _scope_exclusion(result, endpoint, config)
        if exclusion is None:
            exclusion = _quality_exclusion(result, config)
        linked = None if exclusion else _linked_candidate(result, candidate_index, generated_index)
        if exclusion is None and linked is None:
            exclusion = "candidate_not_linked"
        if exclusion is not None:
            excluded_result_ids.append(result.result_id)
            exclusion_reasons[result.result_id] = exclusion
            continue

        label = _label_for_result(result, endpoint, config)
        if label.exclusion_reason is not None:
            excluded_result_ids.append(result.result_id)
            exclusion_reasons[result.result_id] = label.exclusion_reason
            continue

        assert linked is not None
        features.append(_feature_row(result, linked, endpoint, feature_spec))
        labels.append(label.value)
        source_result_ids.append(result.result_id)
        candidate_id = linked.get("candidate_id") or result.candidate_id or result.candidate_name
        included_candidate_ids.append(str(candidate_id))
        if endpoint.label_type == "binary":
            if label.value == 1:
                positive_count += 1
            elif label.value == 0:
                negative_count += 1

    dataset_id = _dataset_id(endpoint, source_result_ids, config)
    feature_matrix_path = output_root / f"{dataset_id}_features.json"
    labels_path = output_root / f"{dataset_id}_labels.json"
    manifest_path = output_root / f"{dataset_id}_manifest.json"

    dataset = ModelTrainingDataset(
        dataset_id=dataset_id,
        endpoint=endpoint,
        created_at=datetime.now(UTC),
        source_result_ids=source_result_ids,
        included_candidate_ids=included_candidate_ids,
        excluded_result_ids=excluded_result_ids,
        exclusion_reasons=exclusion_reasons,
        feature_spec=feature_spec,
        feature_matrix_uri=str(feature_matrix_path),
        labels_uri=str(labels_path),
        row_count=len(labels),
        positive_count=positive_count if endpoint.label_type == "binary" else None,
        negative_count=negative_count if endpoint.label_type == "binary" else None,
        train_count=None,
        validation_count=None,
        test_count=None,
        metadata={
            "source_result_count": len(results),
            "included_result_count": len(source_result_ids),
            "excluded_result_count": len(excluded_result_ids),
            "qc_passed_only": not bool(config.get("include_failed_qc", False)),
            "include_inconclusive": bool(config.get("include_inconclusive", False)),
            "allow_endpoint_pooling": bool(config.get("allow_endpoint_pooling", False)),
            "pooled_endpoint_label": config.get("pooled_endpoint_label"),
            "allow_context_pooling": bool(config.get("allow_context_pooling", False)),
            "generated_requires_exact_result_linkage": True,
            "seed_results_not_used_for_generated_analogs": True,
        },
    )

    _write_json(
        feature_matrix_path,
        {
            "dataset_id": dataset.dataset_id,
            "feature_spec_id": feature_spec.feature_spec_id,
            "rows": features,
        },
    )
    _write_json(
        labels_path,
        {
            "dataset_id": dataset.dataset_id,
            "endpoint_id": endpoint.endpoint_id,
            "label_type": endpoint.label_type,
            "labels": labels,
            "source_result_ids": source_result_ids,
        },
    )
    _write_json(manifest_path, dataset.model_dump(mode="json"))
    return ModelDatasetBuildResult(
        dataset=dataset,
        features=features,
        labels=labels,
        feature_matrix_path=feature_matrix_path,
        labels_path=labels_path,
        manifest_path=manifest_path,
    )


@dataclass(frozen=True)
class _Label:
    value: Any = None
    exclusion_reason: str | None = None


def _scope_exclusion(
    result: AssayResult,
    endpoint: ModelEndpoint,
    config: Mapping[str, Any],
) -> str | None:
    result_endpoint = result.assay_context.endpoint.name
    if result_endpoint != endpoint.endpoint_name and not config.get("allow_endpoint_pooling"):
        return "endpoint_mismatch"
    if not config.get("allow_context_pooling"):
        result_target = result.target_symbol or result.assay_context.target_symbol
        result_disease = result.disease_name or result.assay_context.disease_name
        if endpoint.target_symbol and result_target != endpoint.target_symbol:
            return "target_mismatch"
        if endpoint.disease_name and result_disease != endpoint.disease_name:
            return "disease_mismatch"
    return None


def _quality_exclusion(result: AssayResult, config: Mapping[str, Any]) -> str | None:
    if result.qc_status != "passed" and not config.get("include_failed_qc", False):
        return f"qc_status_{result.qc_status}"
    if result.outcome_label == "inconclusive" and not config.get("include_inconclusive", False):
        return "inconclusive_excluded"
    if result.outcome_label in {"failed_qc", "invalid", "not_tested"}:
        return f"outcome_{result.outcome_label}"
    return None


def _label_for_result(
    result: AssayResult,
    endpoint: ModelEndpoint,
    config: Mapping[str, Any],
) -> _Label:
    if endpoint.label_type == "binary":
        if result.outcome_label == "positive":
            return _Label(1)
        if result.outcome_label == "negative":
            return _Label(0)
        return _configured_label(result.outcome_label, config) or _Label(
            exclusion_reason=f"unmapped_binary_label_{result.outcome_label}"
        )
    if endpoint.label_type == "regression":
        if isinstance(result.normalized_value, int | float) and not isinstance(
            result.normalized_value, bool
        ):
            return _Label(float(result.normalized_value))
        return _Label(exclusion_reason="missing_numeric_normalized_value")
    if endpoint.endpoint_category == "safety":
        if result.activity_direction == "toxic" or result.outcome_label == "positive":
            return _Label("toxic")
        if result.activity_direction == "non_toxic" or result.outcome_label == "negative":
            return _Label("non_toxic")
        return _configured_label(result.activity_direction, config) or _Label(
            exclusion_reason=f"unmapped_safety_label_{result.activity_direction}"
        )
    return _configured_label(result.outcome_label, config) or _Label(
        exclusion_reason=f"unsupported_label_type_{endpoint.label_type}"
    )


def _configured_label(raw_label: str, config: Mapping[str, Any]) -> _Label | None:
    mapping = config.get("label_mapping")
    if isinstance(mapping, Mapping) and raw_label in mapping:
        return _Label(mapping[raw_label])
    return None


def _candidate_index(candidates: Sequence[Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        payload = _candidate_payload(candidate, generated=False)
        for key in _identity_keys(payload):
            index[key] = payload
    return index


def _generated_index(generated_molecules: Sequence[Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for candidate in generated_molecules:
        payload = _candidate_payload(candidate, generated=True)
        for key in _identity_keys(payload):
            index[key] = payload
    return index


def _linked_candidate(
    result: AssayResult,
    candidate_index: Mapping[str, dict[str, Any]],
    generated_index: Mapping[str, dict[str, Any]],
) -> dict[str, Any] | None:
    keys = _result_identity_keys(result)
    if result.candidate_origin == "generated":
        for key in keys:
            if key in generated_index:
                return generated_index[key]
        return None
    for key in keys:
        if key in candidate_index:
            return candidate_index[key]
    return None


def _feature_row(
    result: AssayResult,
    linked: Mapping[str, Any],
    endpoint: ModelEndpoint,
    feature_spec: ModelFeatureSpec,
) -> dict[str, Any]:
    candidate_origin = str(linked.get("candidate_origin") or result.candidate_origin)
    return {
        "result_id": result.result_id,
        "candidate_id": linked.get("candidate_id") or result.candidate_id,
        "candidate_name": linked.get("candidate_name") or result.candidate_name,
        "candidate_origin": candidate_origin,
        "endpoint_id": endpoint.endpoint_id,
        "endpoint_name": endpoint.endpoint_name,
        "feature_spec_id": feature_spec.feature_spec_id,
        "has_structure": bool(result.canonical_smiles or linked.get("canonical_smiles")),
        "is_generated": 1.0 if candidate_origin == "generated" else 0.0,
        "result_confidence": float(result.confidence),
        "target_context_match": 1.0
        if endpoint.target_symbol
        and endpoint.target_symbol == (result.target_symbol or result.assay_context.target_symbol)
        else 0.0,
        "disease_context_match": 1.0
        if endpoint.disease_name
        and endpoint.disease_name == (result.disease_name or result.assay_context.disease_name)
        else 0.0,
    }


def _candidate_payload(candidate: Any, *, generated: bool) -> dict[str, Any]:
    candidate_id = (
        _value(candidate, "candidate_id")
        or _value(candidate, "generated_id")
        or _identifier(candidate, "generated")
        or _identifier(candidate, "chembl")
        or _identifier(candidate, "pubchem_cid")
    )
    name = _value(candidate, "candidate_name") or _value(candidate, "name") or candidate_id
    return {
        "candidate_id": str(candidate_id) if candidate_id else None,
        "candidate_name": str(name) if name else None,
        "candidate_origin": (
            "generated" if generated else str(_value(candidate, "origin") or "existing")
        ),
        "canonical_smiles": _optional_string(_value(candidate, "canonical_smiles")),
        "inchi_key": _optional_string(_value(candidate, "inchi_key")),
    }


def _identity_keys(payload: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("candidate_id", "candidate_name", "canonical_smiles", "inchi_key"):
        value = payload.get(field)
        if value:
            keys.add(f"{field}:{value}")
    return keys


def _result_identity_keys(result: AssayResult) -> set[str]:
    payload = {
        "candidate_id": result.metadata.get("linked_candidate_id") or result.candidate_id,
        "candidate_name": result.candidate_name,
        "canonical_smiles": result.canonical_smiles,
        "inchi_key": result.inchi_key,
    }
    return _identity_keys(payload)


def _dataset_id(
    endpoint: ModelEndpoint,
    source_result_ids: Sequence[str],
    config: Mapping[str, Any],
) -> str:
    basis = "|".join(
        [
            endpoint.endpoint_id,
            endpoint.endpoint_name,
            endpoint.target_symbol or "",
            endpoint.disease_name or "",
            str(config.get("pooled_endpoint_label") or ""),
            ",".join(source_result_ids),
        ]
    )
    return f"model-dataset-{uuid5(NAMESPACE_URL, basis).hex[:16]}"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _value(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _identifier(item: Any, key: str) -> Any:
    identifiers = _value(item, "identifiers")
    if isinstance(identifiers, Mapping):
        return identifiers.get(key)
    return None


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


__all__ = [
    "ModelDatasetBuildResult",
    "ModelTrainingDataset",
    "build_assay_model_training_dataset",
]
