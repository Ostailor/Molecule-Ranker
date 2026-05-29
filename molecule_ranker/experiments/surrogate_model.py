"""Optional local surrogate models for imported assay outcome datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from importlib import import_module
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.experiments.schemas import ExperimentalLearningDataset

NON_FEATURE_COLUMNS = {
    "result_id",
    "candidate_id",
    "candidate_name",
    "candidate_origin",
    "canonical_smiles",
    "inchi_key",
    "disease_name",
    "target_symbol",
    "assay_name",
    "assay_type",
    "endpoint_name",
    "endpoint_category",
    "endpoint_directionality",
    "measured_value_numeric",
    "normalized_value",
    "normalized_unit",
    "outcome_label",
    "activity_direction",
    "qc_status",
    "label",
    "label_type",
    "binary_label",
    "continuous_label",
    "safety_label",
}


@dataclass
class SurrogateModelArtifact:
    trained: bool
    model: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)
    label_kind: str | None = None
    model_card: dict[str, Any] = field(default_factory=dict)
    training_manifest: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Estimators:
    RandomForestClassifier: Any
    RandomForestRegressor: Any
    LogisticRegression: Any
    KFold: Any
    cross_val_score: Any


def train_assay_surrogate_model(
    dataset: ExperimentalLearningDataset,
    *,
    config: dict[str, Any] | None = None,
) -> SurrogateModelArtifact:
    """Train an optional local surrogate model from labeled QC-passed assay rows."""

    config = dict(config or {})
    min_count = int(config.get("min_training_result_count", 8))
    training_rows, label_kind = _training_rows(dataset)
    metadata = _base_metadata(dataset, training_rows)
    artifact_id = _model_id(dataset, training_rows)
    if len(training_rows) < min_count:
        metadata["limitations"].insert(
            0,
            f"Insufficient labeled QC-passed results for surrogate modeling: "
            f"{len(training_rows)} < {min_count}.",
        )
        return SurrogateModelArtifact(
            trained=False,
            model=None,
            metadata=metadata,
            feature_names=[],
            label_kind=label_kind,
            model_card=_model_card(dataset, metadata, [], artifact_id),
            training_manifest=_training_manifest(
                dataset,
                training_rows,
                [],
                artifact_id=artifact_id,
                config=config,
            ),
            metrics=_metrics_artifact(metadata, artifact_id),
        )

    estimators = _load_sklearn_estimators()
    if estimators is None:
        metadata["limitations"].insert(
            0,
            "scikit-learn is not installed; heuristic active learning remains in use.",
        )
        metadata["model_type"] = "unavailable"
        return SurrogateModelArtifact(
            trained=False,
            model=None,
            metadata=metadata,
            feature_names=[],
            label_kind=label_kind,
            model_card=_model_card(dataset, metadata, [], artifact_id),
            training_manifest=_training_manifest(
                dataset,
                training_rows,
                [],
                artifact_id=artifact_id,
                config=config,
            ),
            metrics=_metrics_artifact(metadata, artifact_id),
        )

    feature_names = _feature_names(training_rows)
    x = [_feature_vector(row, feature_names) for row in training_rows]
    y = [_label_value(row, label_kind) for row in training_rows]
    split = _leakage_aware_split(training_rows, config=config)
    applicability_domain = _applicability_domain(training_rows, feature_names)
    if label_kind == "binary" and len(set(y)) < 2:
        metadata["limitations"].insert(
            0,
            "Binary surrogate model requires at least two observed label classes.",
        )
        return SurrogateModelArtifact(
            trained=False,
            model=None,
            metadata=metadata,
            feature_names=feature_names,
            label_kind=label_kind,
            model_card=_model_card(dataset, metadata, feature_names, artifact_id),
            training_manifest=_training_manifest(
                dataset,
                training_rows,
                feature_names,
                artifact_id=artifact_id,
                config=config,
                split=split,
            ),
            metrics=_metrics_artifact(metadata, artifact_id),
        )

    model_type, model = _make_model(
        estimators,
        label_kind=label_kind,
        training_count=len(training_rows),
        config=config,
    )
    model.fit(x, y)
    cross_validation = _cross_validation_metadata(
        estimators,
        model,
        x,
        y,
        label_kind=label_kind,
    )
    calibration = _calibration_metadata(
        estimators,
        training_rows,
        feature_names,
        split=split,
        label_kind=label_kind,
        config=config,
    )
    metadata.update(
        {
            "model_id": artifact_id,
            "model_type": model_type,
            "features_used": feature_names,
            "label_kind": label_kind,
            "calibration_status": "uncalibrated",
            "calibration": calibration,
            "cross_validation": cross_validation,
            "applicability_domain": applicability_domain,
            "evidence_boundary": "not_experimental_evidence",
        }
    )
    return SurrogateModelArtifact(
        trained=True,
        model=model,
        metadata=metadata,
        feature_names=feature_names,
        label_kind=label_kind,
        model_card=_model_card(dataset, metadata, feature_names, artifact_id),
        training_manifest=_training_manifest(
            dataset,
            training_rows,
            feature_names,
            artifact_id=artifact_id,
            config=config,
            split=split,
        ),
        metrics=_metrics_artifact(metadata, artifact_id),
    )


def predict_assay_surrogate_outcomes(
    artifact: SurrogateModelArtifact,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return bounded predictions labeled as surrogate model estimates."""

    if not artifact.trained or artifact.model is None:
        return []
    predictions: list[dict[str, Any]] = []
    x = [_feature_vector(row, artifact.feature_names) for row in rows]
    estimates = _apply_calibration(
        _predict_estimates(artifact.model, x, artifact.label_kind),
        artifact,
    )
    for row, estimate in zip(rows, estimates, strict=True):
        applicability = _applicability_for_row(row, artifact)
        uncertainty = _prediction_uncertainty(applicability, artifact)
        predictions.append(
            {
                "artifact_kind": "prediction_artifact",
                "prediction_id": _prediction_id(artifact, row),
                "model_id": artifact.metadata.get("model_id"),
                "evidence_boundary": "not_experimental_evidence",
                "candidate_id": row.get("candidate_id"),
                "candidate_name": row.get("candidate_name"),
                "endpoint_name": row.get("endpoint_name") or artifact.metadata.get("endpoint"),
                "surrogate_model_estimate": round(_clamp(estimate), 6),
                "prediction_label": "surrogate model estimate",
                "uncertainty_score": round(uncertainty, 6),
                "applicability_domain": applicability,
                "metadata": {
                    "model_type": artifact.metadata.get("model_type"),
                    "calibration_status": artifact.metadata.get("calibration_status"),
                    "not_experimental_evidence": True,
                    "not_assay_result": True,
                    "not_biological_activity_proof": True,
                    "features_used": list(artifact.feature_names),
                },
            }
        )
    return predictions


def _training_rows(
    dataset: ExperimentalLearningDataset,
) -> tuple[list[dict[str, Any]], str | None]:
    binary_rows = [
        row
        for row in dataset.rows
        if row.get("qc_status") == "passed" and row.get("binary_label") in {0, 1}
    ]
    if binary_rows:
        return binary_rows, "binary"
    continuous_rows = [
        row
        for row in dataset.rows
        if row.get("qc_status") == "passed" and _is_number(row.get("continuous_label"))
    ]
    if continuous_rows:
        return continuous_rows, "continuous"
    return [], None


def _make_model(
    estimators: _Estimators,
    *,
    label_kind: str | None,
    training_count: int,
    config: dict[str, Any],
) -> tuple[str, Any]:
    small_threshold = int(config.get("small_dataset_threshold", 12))
    random_state = int(config.get("random_state", 13))
    if label_kind == "binary" and training_count <= small_threshold:
        return (
            "LogisticRegression",
            estimators.LogisticRegression(max_iter=1000, random_state=random_state),
        )
    if label_kind == "binary":
        return (
            "RandomForestClassifier",
            estimators.RandomForestClassifier(n_estimators=100, random_state=random_state),
        )
    return (
        "RandomForestRegressor",
        estimators.RandomForestRegressor(n_estimators=100, random_state=random_state),
    )


def _cross_validation_metadata(
    estimators: _Estimators,
    model: Any,
    x: list[list[float]],
    y: list[float],
    *,
    label_kind: str | None,
) -> dict[str, Any]:
    if len(x) < 4:
        return {"performed": False, "reason": "too_few_rows"}
    if label_kind == "binary" and len(set(y)) < 2:
        return {"performed": False, "reason": "single_class"}
    try:
        folds = min(3, len(x))
        cv = estimators.KFold(n_splits=folds, shuffle=True, random_state=13)
        scoring = "roc_auc" if label_kind == "binary" else "neg_mean_squared_error"
        scores = estimators.cross_val_score(model, x, y, cv=cv, scoring=scoring)
    except Exception as exc:
        return {"performed": False, "reason": f"cross_validation_failed:{type(exc).__name__}"}
    score_values = [float(score) for score in scores]
    return {
        "performed": True,
        "method": "KFold",
        "folds": len(score_values),
        "scoring": scoring,
        "scores": [round(score, 6) for score in score_values],
        "mean_score": round(sum(score_values) / len(score_values), 6),
    }


def _base_metadata(
    dataset: ExperimentalLearningDataset,
    training_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "model_type": None,
        "training_result_count": len(training_rows),
        "endpoint": dataset.endpoint_name,
        "disease_name": dataset.disease_name,
        "target_symbol": dataset.target_symbol,
        "features_used": [],
        "cross_validation": {"performed": False, "reason": "model_not_trained"},
        "calibration_status": "uncalibrated",
        "evidence_boundary": "not_experimental_evidence",
        "limitations": [
            "Surrogate model estimates are computational prioritization signals only.",
            "Surrogate model estimates are not proof of biological activity.",
            "Surrogate model predictions are not assay results.",
            "Surrogate model predictions are not experimental evidence.",
        ],
    }


def _model_id(dataset: ExperimentalLearningDataset, rows: list[dict[str, Any]]) -> str:
    basis = "|".join(
        [
            dataset.dataset_id,
            dataset.endpoint_name,
            dataset.disease_name or "",
            dataset.target_symbol or "",
            ",".join(sorted(str(row.get("result_id") or "") for row in rows)),
        ]
    )
    return f"model-{uuid5(NAMESPACE_URL, basis).hex[:16]}"


def _model_card(
    dataset: ExperimentalLearningDataset,
    metadata: dict[str, Any],
    feature_names: list[str],
    model_id: str,
) -> dict[str, Any]:
    return {
        "artifact_kind": "model_card",
        "model_id": model_id,
        "model_type": metadata.get("model_type"),
        "endpoint_name": dataset.endpoint_name,
        "disease_name": dataset.disease_name,
        "target_symbol": dataset.target_symbol,
        "training_result_count": metadata.get("training_result_count", 0),
        "feature_count": len(feature_names),
        "evidence_boundary": "not_experimental_evidence",
        "intended_use": "assay-specific computational prioritization only",
        "not_for": [
            "biomedical evidence",
            "assay result substitution",
            "activity, safety, efficacy, binding, treatment, or cure claims",
            "patient, clinical, dosing, protocol, or synthesis guidance",
        ],
        "limitations": list(metadata.get("limitations", [])),
    }


def _training_manifest(
    dataset: ExperimentalLearningDataset,
    rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    artifact_id: str,
    config: dict[str, Any],
    split: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feature_schema_hash = sha256(
        "|".join(feature_names).encode("utf-8")
    ).hexdigest()
    return {
        "artifact_kind": "training_manifest",
        "model_id": artifact_id,
        "dataset_id": dataset.dataset_id,
        "assay_scope": {
            "endpoint_name": dataset.endpoint_name,
            "disease_name": dataset.disease_name,
            "target_symbol": dataset.target_symbol,
            "allow_endpoint_pooling": bool(config.get("allow_endpoint_pooling", False)),
            "allow_context_pooling": bool(config.get("allow_context_pooling", False)),
        },
        "source_result_ids": [str(row.get("result_id")) for row in rows],
        "feature_names": list(feature_names),
        "feature_schema_hash": feature_schema_hash,
        "featurization": {
            "pipeline": "deterministic_numeric_descriptors_plus_morgan_bits",
            "missing_numeric_value": 0.0,
            "fingerprint_bits_are_binary": True,
        },
        "leakage_controls": {
            "split_unit": "candidate_or_result",
            "test_labels_excluded_from_training": True,
            "split": split or {"train_result_ids": [], "test_result_ids": []},
        },
        "labels_excluded_from_manifest": True,
        "forbidden_data": ["patient", "clinical", "dosing"],
        "evidence_boundary": "not_experimental_evidence",
    }


def _metrics_artifact(metadata: dict[str, Any], model_id: str) -> dict[str, Any]:
    return {
        "artifact_kind": "model_metrics",
        "model_id": model_id,
        "cross_validation": metadata.get("cross_validation", {"performed": False}),
        "calibration": metadata.get("calibration", {"status": "not_performed"}),
        "metrics_are_computed_not_invented": True,
        "evidence_boundary": "not_experimental_evidence",
    }


def _leakage_aware_split(
    rows: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    if len(rows) < 4:
        return {
            "method": "deterministic_holdout",
            "train_result_ids": [str(row.get("result_id")) for row in rows],
            "test_result_ids": [],
            "reason": "too_few_rows_for_holdout",
        }
    test_fraction = float(config.get("test_fraction", 0.2))
    test_count = max(1, int(round(len(rows) * min(max(test_fraction, 0.0), 0.5))))
    ordered = sorted(rows, key=lambda row: _split_key(row))
    test_rows = ordered[:test_count]
    test_ids = {str(row.get("result_id")) for row in test_rows}
    train_rows = [row for row in rows if str(row.get("result_id")) not in test_ids]
    return {
        "method": "deterministic_holdout",
        "train_result_ids": [str(row.get("result_id")) for row in train_rows],
        "test_result_ids": [str(row.get("result_id")) for row in test_rows],
    }


def _split_key(row: dict[str, Any]) -> str:
    identity = row.get("candidate_id") or row.get("inchi_key") or row.get("result_id")
    return sha256(str(identity).encode("utf-8")).hexdigest()


def _calibration_metadata(
    estimators: _Estimators,
    rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    split: dict[str, Any],
    label_kind: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    test_ids = split.get("test_result_ids") if isinstance(split, dict) else []
    train_ids = split.get("train_result_ids") if isinstance(split, dict) else []
    if isinstance(test_ids, list) and isinstance(train_ids, list) and len(test_ids) >= 2:
        train_rows = [row for row in rows if str(row.get("result_id")) in set(train_ids)]
        test_rows = [row for row in rows if str(row.get("result_id")) in set(test_ids)]
        train_y = [_label_value(row, label_kind) for row in train_rows]
        if label_kind == "binary" and len(set(train_y)) < 2:
            return {
                "status": "uncalibrated_small_dataset",
                "method": None,
                "holdout_result_count": len(test_rows),
                "reason": "training_split_single_class",
            }
        try:
            _, calibration_model = _make_model(
                estimators,
                label_kind=label_kind,
                training_count=len(train_rows),
                config=config,
            )
            train_x = [_feature_vector(row, feature_names) for row in train_rows]
            test_x = [_feature_vector(row, feature_names) for row in test_rows]
            test_y = [_label_value(row, label_kind) for row in test_rows]
            calibration_model.fit(train_x, train_y)
            estimates = _predict_estimates(calibration_model, test_x, label_kind)
        except Exception as exc:
            return {
                "status": "uncalibrated_small_dataset",
                "method": None,
                "holdout_result_count": len(test_rows),
                "reason": f"calibration_failed:{type(exc).__name__}",
            }
        paired_holdout = list(zip(test_y, estimates, strict=True))
        residuals = [observed - _clamp(estimated) for observed, estimated in paired_holdout]
        offset = sum(residuals) / len(residuals) if residuals else 0.0
        errors = [
            (_clamp(estimated + offset) - observed) ** 2
            for observed, estimated in paired_holdout
        ]
        return {
            "status": "calibrated",
            "method": "deterministic_holdout_residual_offset",
            "holdout_result_count": len(test_rows),
            "residual_offset": round(offset, 6),
            "mean_squared_calibration_error": round(sum(errors) / len(errors), 6)
            if errors
            else None,
            "note": "Calibration used held-out imported labels that were excluded from training.",
        }
    return {
        "status": "uncalibrated_small_dataset",
        "method": None,
        "holdout_result_count": len(test_ids) if isinstance(test_ids, list) else 0,
    }


def _applicability_domain(
    rows: list[dict[str, Any]],
    feature_names: list[str],
) -> dict[str, Any]:
    numeric_ranges: dict[str, dict[str, float]] = {}
    for name in feature_names:
        if name.startswith("morgan_bit_"):
            continue
        values = [_to_float(row.get(name)) for row in rows if _is_number(row.get(name))]
        if values:
            numeric_ranges[name] = {"min": min(values), "max": max(values)}
    fingerprints = [
        sorted(int(bit) for bit in row.get("morgan_fp_on_bits", []) if isinstance(bit, int))
        for row in rows
    ]
    return {
        "numeric_feature_ranges": numeric_ranges,
        "training_fingerprints": fingerprints,
        "min_similarity": 0.2,
    }


def _applicability_for_row(
    row: dict[str, Any],
    artifact: SurrogateModelArtifact,
) -> dict[str, Any]:
    domain = artifact.metadata.get("applicability_domain")
    if not isinstance(domain, dict):
        return {"status": "unknown", "score": 0.0, "reason": "domain_missing"}
    ranges = domain.get("numeric_feature_ranges")
    range_checks: list[bool] = []
    if isinstance(ranges, dict):
        for name, bounds in ranges.items():
            if not isinstance(bounds, dict) or not _is_number(row.get(name)):
                continue
            value = float(row[name])
            range_checks.append(float(bounds["min"]) <= value <= float(bounds["max"]))
    similarity = _max_fingerprint_similarity(
        row.get("morgan_fp_on_bits"),
        domain.get("training_fingerprints"),
    )
    range_score = (
        sum(1.0 for item in range_checks if item) / len(range_checks)
        if range_checks
        else 0.5
    )
    score = _clamp(0.55 * range_score + 0.45 * similarity)
    status = "inside" if score >= 0.5 else "outside"
    return {
        "status": status,
        "score": round(score, 6),
        "max_training_fingerprint_similarity": round(similarity, 6),
    }


def _max_fingerprint_similarity(raw_bits: object, raw_training: object) -> float:
    if not isinstance(raw_bits, list) or not isinstance(raw_training, list):
        return 0.0
    bits = {int(bit) for bit in raw_bits if isinstance(bit, int)}
    if not bits:
        return 0.0
    similarities: list[float] = []
    for item in raw_training:
        if not isinstance(item, list):
            continue
        training_bits = {int(bit) for bit in item if isinstance(bit, int)}
        union = bits | training_bits
        similarities.append(len(bits & training_bits) / len(union) if union else 0.0)
    return max(similarities, default=0.0)


def _prediction_uncertainty(
    applicability: dict[str, Any],
    artifact: SurrogateModelArtifact,
) -> float:
    ad_score = applicability.get("score")
    if not isinstance(ad_score, int | float):
        ad_score = 0.0
    calibration = artifact.metrics.get("calibration", {})
    calibration_penalty = 0.15 if calibration.get("status") == "calibrated" else 0.35
    return _clamp((1.0 - float(ad_score)) + calibration_penalty)


def _apply_calibration(
    estimates: list[float],
    artifact: SurrogateModelArtifact,
) -> list[float]:
    calibration = artifact.metrics.get("calibration", {})
    offset = calibration.get("residual_offset")
    if calibration.get("status") != "calibrated" or not isinstance(offset, int | float):
        return estimates
    return [_clamp(estimate + float(offset)) for estimate in estimates]


def _prediction_id(artifact: SurrogateModelArtifact, row: dict[str, Any]) -> str:
    basis = "|".join(
        [
            str(artifact.metadata.get("model_id") or ""),
            str(row.get("candidate_id") or ""),
            str(row.get("candidate_name") or ""),
            str(row.get("endpoint_name") or artifact.metadata.get("endpoint") or ""),
        ]
    )
    return f"prediction-{uuid5(NAMESPACE_URL, basis).hex[:16]}"


def _feature_names(rows: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    max_fingerprint_bit = 0
    for row in rows:
        for key, value in row.items():
            if key in NON_FEATURE_COLUMNS or key.startswith("review_"):
                continue
            if _is_number(value):
                names.add(key)
        bits = row.get("morgan_fp_on_bits")
        if isinstance(bits, list):
            numeric_bits = [int(bit) for bit in bits if isinstance(bit, int)]
            max_fingerprint_bit = max([max_fingerprint_bit, *numeric_bits], default=0)
    fingerprint_bits = [f"morgan_bit_{index}" for index in range(max_fingerprint_bit + 1)]
    return sorted(names) + fingerprint_bits


def _feature_vector(row: dict[str, Any], feature_names: list[str]) -> list[float]:
    on_bits = set()
    raw_bits = row.get("morgan_fp_on_bits")
    if isinstance(raw_bits, list):
        on_bits = {int(bit) for bit in raw_bits if isinstance(bit, int)}
    values: list[float] = []
    for name in feature_names:
        if name.startswith("morgan_bit_"):
            bit = int(name.rsplit("_", 1)[-1])
            values.append(1.0 if bit in on_bits else 0.0)
            continue
        values.append(_to_float(row.get(name)))
    return values


def _label_value(row: dict[str, Any], label_kind: str | None) -> float:
    key = "binary_label" if label_kind == "binary" else "continuous_label"
    return _to_float(row.get(key))


def _predict_estimates(model: Any, x: list[list[float]], label_kind: str | None) -> list[float]:
    if label_kind == "binary" and hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(x)
        return [float(probability[-1]) for probability in probabilities]
    raw_predictions = model.predict(x)
    return [float(value) for value in raw_predictions]


def _load_sklearn_estimators() -> _Estimators | None:
    try:
        ensemble = import_module("sklearn.ensemble")
        linear_model = import_module("sklearn.linear_model")
        model_selection = import_module("sklearn.model_selection")
    except Exception:
        return None
    return _Estimators(
        RandomForestClassifier=ensemble.RandomForestClassifier,
        RandomForestRegressor=ensemble.RandomForestRegressor,
        LogisticRegression=linear_model.LogisticRegression,
        KFold=model_selection.KFold,
        cross_val_score=model_selection.cross_val_score,
    )


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _to_float(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


__all__ = [
    "SurrogateModelArtifact",
    "predict_assay_surrogate_outcomes",
    "train_assay_surrogate_model",
]
