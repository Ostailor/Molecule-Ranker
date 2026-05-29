"""Deterministic model feature pipeline helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from rdkit import DataStructs
from rdkit.Chem import QED, rdMolDescriptors

from molecule_ranker.generation.chemistry import (
    descriptors_from_mol,
    mol_from_smiles,
    morgan_fingerprint,
)
from molecule_ranker.models.schemas import ModelFeatureSpec

RDKIT_DESCRIPTOR_COLUMNS = [
    "molecular_weight",
    "logp",
    "tpsa",
    "hbd",
    "hba",
    "rotatable_bonds",
    "aromatic_rings",
    "heavy_atom_count",
    "formal_charge",
    "fraction_csp3",
    "qed",
]
DEVELOPABILITY_COLUMNS = [
    "developability_score",
    "risk_level_encoded",
    "alert_count",
    "admet_risk_count",
]
ORACLE_SCORE_COLUMNS = [
    "experiment_worthiness_score",
    "oracle_uncertainty",
    "novelty_score",
    "diversity_score",
    "seed_evidence_score",
]
LITERATURE_COUNT_COLUMNS = [
    "supportive_claim_count",
    "clinical_claim_count",
    "safety_claim_count",
    "contradictory_claim_count",
    "mention_only_count",
]
TARGET_CONTEXT_COLUMNS = [
    "target_relevance",
    "disease_target_score",
]
GENERATION_METADATA_COLUMNS = [
    "is_generated",
    "novelty_class_encoded",
    "distance_to_seed",
]
REVIEW_CONTEXT_COLUMNS = [
    "review_priority_encoded",
    "expert_positive_feedback",
    "expert_negative_feedback",
]
LEAKAGE_FEATURE_COLUMNS = {
    "result_id",
    "source_result_id",
    "assay_result_id",
    "outcome_label",
    "activity_direction",
    "label",
    "label_type",
    "binary_label",
    "continuous_label",
    "safety_label",
    "normalized_value",
    "measured_value",
    "measured_value_numeric",
    "future_result_count",
    "future_outcome",
}
RISK_LEVEL_ENCODING = {
    "none": 0.0,
    "unknown": 0.0,
    "low": 0.25,
    "medium": 0.5,
    "high": 0.75,
    "critical": 1.0,
}
NOVELTY_ENCODING = {
    "duplicate": 0.0,
    "near_duplicate": 0.2,
    "close_analog": 0.45,
    "novel_analog": 0.8,
    "distant": 1.0,
}
REVIEW_PRIORITY_ENCODING = {
    "reject_suggested": 0.0,
    "low_priority": 0.25,
    "needs_review": 0.5,
    "medium_priority": 0.66,
    "high_priority": 1.0,
}


@dataclass(frozen=True)
class FeatureMatrixBuildResult:
    rows: list[dict[str, Any]]
    feature_names: list[str]
    feature_schema: dict[str, Any]
    excluded_rows: list[dict[str, str]]
    schema_path: Path | None = None


def featurize_model_rows(
    rows: Sequence[Any],
    *,
    feature_spec: ModelFeatureSpec,
    output_dir: str | Path | None = None,
    config: Mapping[str, Any] | None = None,
) -> FeatureMatrixBuildResult:
    config = dict(config or {})
    feature_names = _feature_names(feature_spec, config)
    feature_schema = _feature_schema(feature_spec, feature_names)
    invalid_policy = str(config.get("invalid_structure_policy", "exclude"))
    built_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, str]] = []

    for index, item in enumerate(rows):
        row_id = _row_id(item, index)
        smiles = _optional_string(_value(item, "canonical_smiles") or _value(item, "smiles"))
        mol = mol_from_smiles(smiles) if smiles else None
        if mol is None and _requires_structure(feature_spec):
            if invalid_policy == "mark":
                features = {name: 0.0 for name in feature_names}
                features["structure_valid"] = 0.0
                built_rows.append(
                    {
                        "row_id": row_id,
                        "candidate_id": _optional_string(_value(item, "candidate_id")),
                        "candidate_name": _candidate_name(item),
                        "features": _ordered_features(features, feature_names),
                        "warnings": ["invalid_structure"],
                    }
                )
            else:
                excluded_rows.append({"row_id": row_id, "reason": "invalid_structure"})
            continue

        features = _features_for_row(item, mol, feature_spec, feature_names, config)
        built_rows.append(
            {
                "row_id": row_id,
                "candidate_id": _optional_string(
                    _value(item, "candidate_id") or _value(item, "generated_id")
                ),
                "candidate_name": _candidate_name(item),
                "features": _ordered_features(features, feature_names),
                "warnings": [],
            }
        )

    leakage = validate_no_feature_leakage(built_rows)
    feature_schema = {**feature_schema, "leakage_check": leakage}
    schema_path = None
    if output_dir is not None:
        schema_path = Path(output_dir) / f"{feature_spec.feature_spec_id}_schema.json"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(json.dumps(feature_schema, indent=2, sort_keys=True) + "\n")
    return FeatureMatrixBuildResult(
        rows=built_rows,
        feature_names=feature_names,
        feature_schema=feature_schema,
        excluded_rows=excluded_rows,
        schema_path=schema_path,
    )


def validate_no_feature_leakage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    leakage_columns: set[str] = set()
    for row in rows:
        features = row.get("features")
        if not isinstance(features, Mapping):
            continue
        leakage_columns.update(str(key) for key in features if str(key) in LEAKAGE_FEATURE_COLUMNS)
    return {"passed": not leakage_columns, "leakage_columns": sorted(leakage_columns)}


def _features_for_row(
    item: Any,
    mol: Any,
    feature_spec: ModelFeatureSpec,
    feature_names: list[str],
    config: Mapping[str, Any],
) -> dict[str, float]:
    features = {name: 0.0 for name in feature_names}
    metadata = _metadata(item)
    families = set(feature_spec.feature_families)
    if "structure_valid" in features:
        features["structure_valid"] = 1.0 if mol is not None else 0.0
    if "rdkit_descriptors" in families and mol is not None:
        features.update(_rdkit_descriptor_features(mol))
    if "morgan_fingerprint" in families and mol is not None:
        features.update(_morgan_features(mol, feature_spec))
    if "developability" in families:
        features.update(_developability_features(item, metadata))
    if "oracle_scores" in families:
        features.update(_oracle_features(metadata))
    if "literature_counts" in families:
        features.update(_literature_features(metadata))
    if "target_context" in families:
        features.update(_target_context_features(item, metadata, config))
    if "generation_metadata" in families:
        features.update(_generation_features(item, metadata, config))
    if "review_context" in families:
        features.update(_review_features(metadata, config))
    return features


def _rdkit_descriptor_features(mol: Any) -> dict[str, float]:
    descriptors = descriptors_from_mol(mol)
    descriptors["fraction_csp3"] = round(float(rdMolDescriptors.CalcFractionCSP3(mol)), 3)
    try:
        descriptors["qed"] = round(float(QED.qed(mol)), 3)
    except Exception:
        descriptors["qed"] = 0.0
    return {name: float(descriptors.get(name, 0.0) or 0.0) for name in RDKIT_DESCRIPTOR_COLUMNS}


def _morgan_features(mol: Any, feature_spec: ModelFeatureSpec) -> dict[str, float]:
    radius = feature_spec.fingerprint_radius or 2
    n_bits = feature_spec.fingerprint_bits or 2048
    bit_vector = morgan_fingerprint(mol, radius=radius, n_bits=n_bits)
    bit_string = DataStructs.BitVectToText(bit_vector)
    return {
        f"morgan_bit_{index}": 1.0 if bit_string[index] == "1" else 0.0
        for index in range(n_bits)
    }


def _developability_features(item: Any, metadata: Mapping[str, Any]) -> dict[str, float]:
    assessment = _value(item, "developability_assessment")
    assessment_metadata = _metadata(assessment)
    risk_level = metadata.get("risk_level") or assessment_metadata.get("risk_level") or "unknown"
    alerts = _list_value(metadata.get("alerts") or _value(assessment, "medicinal_chemistry_alerts"))
    admet_risks = _list_value(
        metadata.get("admet_risks") or _value(assessment, "admet_property_flags")
    )
    score = _number(
        metadata.get("developability_score"),
        _number(_value(assessment, "developability_score"), 0.0),
    )
    return {
        "developability_score": score,
        "risk_level_encoded": RISK_LEVEL_ENCODING.get(str(risk_level).lower(), 0.0),
        "alert_count": float(len(alerts)),
        "admet_risk_count": float(len(admet_risks)),
    }


def _oracle_features(metadata: Mapping[str, Any]) -> dict[str, float]:
    oracle = _mapping(metadata.get("oracle_scoring"))
    return {
        "experiment_worthiness_score": _number(
            oracle.get("experiment_worthiness_score"), 0.0
        ),
        "oracle_uncertainty": _number(
            oracle.get("uncertainty") or oracle.get("uncertainty_score"), 0.0
        ),
        "novelty_score": _number(
            oracle.get("novelty") or oracle.get("novelty_score"), 0.0
        ),
        "diversity_score": _number(
            oracle.get("diversity") or oracle.get("diversity_score"), 0.0
        ),
        "seed_evidence_score": _number(
            oracle.get("seed_evidence") or oracle.get("seed_evidence_score"), 0.0
        ),
    }


def _literature_features(metadata: Mapping[str, Any]) -> dict[str, float]:
    counts = _mapping(metadata.get("literature_counts"))
    return {
        "supportive_claim_count": _number(
            counts.get("supportive") or counts.get("supportive_claim_count"), 0.0
        ),
        "clinical_claim_count": _number(
            counts.get("clinical") or counts.get("clinical_claim_count"), 0.0
        ),
        "safety_claim_count": _number(
            counts.get("safety") or counts.get("safety_claim_count"), 0.0
        ),
        "contradictory_claim_count": _number(
            counts.get("contradictory") or counts.get("contradictory_claim_count"),
            0.0,
        ),
        "mention_only_count": _number(
            counts.get("mention_only") or counts.get("mention_only_count"), 0.0
        ),
    }


def _target_context_features(
    item: Any,
    metadata: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, float]:
    context = _mapping(metadata.get("target_context"))
    values = {
        "target_relevance": _number(context.get("target_relevance"), 0.0),
        "disease_target_score": _number(context.get("disease_target_score"), 0.0),
    }
    buckets = int(config.get("target_hash_buckets", 0) or 0)
    if buckets:
        target = str(_value(item, "target_symbol") or context.get("target_symbol") or "")
        bucket = _stable_bucket(target, buckets)
        values.update(
            {
                f"target_symbol_hash_{index}": 1.0 if index == bucket else 0.0
                for index in range(buckets)
            }
        )
    return values


def _generation_features(
    item: Any,
    metadata: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, float]:
    origin = str(_value(item, "candidate_origin") or _value(item, "origin") or "")
    method = str(_value(item, "generation_method") or metadata.get("generator_method") or "")
    novelty_class = str(_value(item, "novelty_class") or metadata.get("novelty_class") or "")
    values = {
        "is_generated": 1.0
        if origin == "generated" or bool(_value(item, "generated_id"))
        else 0.0,
        "novelty_class_encoded": NOVELTY_ENCODING.get(novelty_class, 0.0),
        "distance_to_seed": _number(metadata.get("distance_to_seed"), 0.0),
    }
    buckets = int(config.get("generator_method_hash_buckets", 0) or 0)
    if buckets:
        bucket = _stable_bucket(method, buckets)
        values.update(
            {
                f"generator_method_hash_{index}": 1.0 if index == bucket else 0.0
                for index in range(buckets)
            }
        )
    return values


def _review_features(metadata: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, float]:
    review = _mapping(metadata.get("review_context"))
    include_feedback = bool(config.get("include_expert_feedback", True))
    return {
        "review_priority_encoded": REVIEW_PRIORITY_ENCODING.get(
            str(review.get("priority_bucket") or "").lower(),
            0.0,
        ),
        "expert_positive_feedback": 1.0
        if include_feedback and bool(review.get("expert_positive_feedback"))
        else 0.0,
        "expert_negative_feedback": 1.0
        if include_feedback and bool(review.get("expert_negative_feedback"))
        else 0.0,
    }


def _feature_names(feature_spec: ModelFeatureSpec, config: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    families = feature_spec.feature_families
    if _requires_structure(feature_spec):
        names.append("structure_valid")
    if "rdkit_descriptors" in families:
        names.extend(RDKIT_DESCRIPTOR_COLUMNS)
    if "morgan_fingerprint" in families:
        n_bits = feature_spec.fingerprint_bits or 2048
        names.extend(f"morgan_bit_{index}" for index in range(n_bits))
    if "developability" in families:
        names.extend(DEVELOPABILITY_COLUMNS)
    if "oracle_scores" in families:
        names.extend(ORACLE_SCORE_COLUMNS)
    if "literature_counts" in families:
        names.extend(LITERATURE_COUNT_COLUMNS)
    if "target_context" in families:
        names.extend(TARGET_CONTEXT_COLUMNS)
        names.extend(
            f"target_symbol_hash_{index}"
            for index in range(int(config.get("target_hash_buckets", 0) or 0))
        )
    if "generation_metadata" in families:
        names.extend(GENERATION_METADATA_COLUMNS)
        names.extend(
            f"generator_method_hash_{index}"
            for index in range(int(config.get("generator_method_hash_buckets", 0) or 0))
        )
    if "review_context" in families:
        names.extend(REVIEW_CONTEXT_COLUMNS)
    return list(dict.fromkeys(name for name in names if name not in LEAKAGE_FEATURE_COLUMNS))


def _feature_schema(feature_spec: ModelFeatureSpec, feature_names: list[str]) -> dict[str, Any]:
    return {
        "feature_spec_id": feature_spec.feature_spec_id,
        "feature_families": list(feature_spec.feature_families),
        "normalization": feature_spec.normalization,
        "fingerprint_radius": feature_spec.fingerprint_radius,
        "fingerprint_bits": feature_spec.fingerprint_bits,
        "feature_names": feature_names,
        "columns": {name: "float" for name in feature_names},
        "label_columns_excluded": sorted(LEAKAGE_FEATURE_COLUMNS),
        "deterministic": True,
    }


def _ordered_features(features: Mapping[str, float], feature_names: list[str]) -> dict[str, float]:
    return {name: float(features.get(name, 0.0) or 0.0) for name in feature_names}


def _requires_structure(feature_spec: ModelFeatureSpec) -> bool:
    return any(
        family in set(feature_spec.feature_families)
        for family in {"rdkit_descriptors", "morgan_fingerprint"}
    )


def _row_id(item: Any, index: int) -> str:
    value = _value(item, "candidate_id") or _value(item, "generated_id") or _value(item, "row_id")
    return str(value or f"row-{index}")


def _candidate_name(item: Any) -> str | None:
    return _optional_string(_value(item, "candidate_name") or _value(item, "name"))


def _metadata(item: Any) -> Mapping[str, Any]:
    metadata = _value(item, "metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return default


def _value(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _stable_bucket(value: str, buckets: int) -> int:
    if buckets <= 0:
        return 0
    digest = sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % buckets


__all__ = [
    "FeatureMatrixBuildResult",
    "LEAKAGE_FEATURE_COLUMNS",
    "ModelFeatureSpec",
    "featurize_model_rows",
    "validate_no_feature_leakage",
]
