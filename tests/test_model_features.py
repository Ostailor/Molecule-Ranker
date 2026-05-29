from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.models.features import (
    LEAKAGE_FEATURE_COLUMNS,
    featurize_model_rows,
    validate_no_feature_leakage,
)
from molecule_ranker.models.schemas import ModelFeatureSpec


def _feature_spec(
    families: list[str] | None = None,
    *,
    fingerprint_bits: int = 16,
) -> ModelFeatureSpec:
    return ModelFeatureSpec(
        feature_spec_id="feature-spec-1",
        feature_families=families
        or [
            "rdkit_descriptors",
            "morgan_fingerprint",
            "developability",
            "oracle_scores",
            "literature_counts",
            "target_context",
            "generation_metadata",
            "review_context",
        ],
        fingerprint_radius=2,
        fingerprint_bits=fingerprint_bits,
        descriptor_names=[],
        normalization="none",
    )


def test_featurizes_valid_molecules_with_expected_descriptor_columns(tmp_path: Path) -> None:
    result = featurize_model_rows(
        [
            {
                "candidate_id": "candidate-1",
                "candidate_name": "Ethanol",
                "candidate_origin": "existing",
                "canonical_smiles": "CCO",
                "target_symbol": "MAOB",
                "metadata": {
                    "developability_score": 0.7,
                    "risk_level": "low",
                    "oracle_scoring": {
                        "experiment_worthiness_score": 0.6,
                        "uncertainty": 0.2,
                        "novelty": 0.4,
                        "diversity": 0.5,
                        "seed_evidence": 0.3,
                    },
                    "literature_counts": {"supportive": 2, "mention_only": 4},
                    "target_context": {
                        "target_relevance": 0.8,
                        "disease_target_score": 0.9,
                    },
                    "review_context": {
                        "priority_bucket": "high_priority",
                        "expert_positive_feedback": True,
                    },
                },
            }
        ],
        feature_spec=_feature_spec(),
        output_dir=tmp_path,
        config={"target_hash_buckets": 4, "generator_method_hash_buckets": 4},
    )

    features = result.rows[0]["features"]

    assert features["molecular_weight"] > 0
    assert "qed" in features
    assert features["developability_score"] == 0.7
    assert features["risk_level_encoded"] == 0.25
    assert features["experiment_worthiness_score"] == 0.6
    assert features["supportive_claim_count"] == 2.0
    assert features["target_relevance"] == 0.8
    assert features["is_generated"] == 0.0
    assert features["review_priority_encoded"] == 1.0
    assert result.schema_path is not None
    assert json.loads(result.schema_path.read_text())["feature_spec_id"] == "feature-spec-1"


def test_handles_invalid_structures_by_excluding_or_marking(tmp_path: Path) -> None:
    excluded = featurize_model_rows(
        [{"candidate_id": "bad-1", "candidate_name": "Bad", "canonical_smiles": "not-a-smiles"}],
        feature_spec=_feature_spec(["rdkit_descriptors"]),
        output_dir=tmp_path / "excluded",
        config={},
    )
    marked = featurize_model_rows(
        [{"candidate_id": "bad-1", "candidate_name": "Bad", "canonical_smiles": "not-a-smiles"}],
        feature_spec=_feature_spec(["rdkit_descriptors"]),
        output_dir=tmp_path / "marked",
        config={"invalid_structure_policy": "mark"},
    )

    assert excluded.rows == []
    assert excluded.excluded_rows == [{"row_id": "bad-1", "reason": "invalid_structure"}]
    assert marked.rows[0]["features"]["structure_valid"] == 0.0
    assert "invalid_structure" in marked.rows[0]["warnings"]


def test_fingerprint_length_correct_and_schema_stable(tmp_path: Path) -> None:
    first = featurize_model_rows(
        [{"candidate_id": "candidate-1", "canonical_smiles": "CCO"}],
        feature_spec=_feature_spec(["morgan_fingerprint"], fingerprint_bits=32),
        output_dir=tmp_path / "first",
        config={},
    )
    second = featurize_model_rows(
        [{"candidate_id": "candidate-2", "canonical_smiles": "CCN"}],
        feature_spec=_feature_spec(["morgan_fingerprint"], fingerprint_bits=32),
        output_dir=tmp_path / "second",
        config={},
    )

    bit_columns = [name for name in first.feature_names if name.startswith("morgan_bit_")]

    assert len(bit_columns) == 32
    assert first.feature_names == second.feature_names
    assert first.feature_schema == second.feature_schema


def test_labels_and_result_ids_are_not_included_in_features(tmp_path: Path) -> None:
    result = featurize_model_rows(
        [
            {
                "candidate_id": "candidate-1",
                "canonical_smiles": "CCO",
                "result_id": "result-1",
                "outcome_label": "positive",
                "label": 1,
                "binary_label": 1,
                "future_result_count": 3,
            }
        ],
        feature_spec=_feature_spec(["rdkit_descriptors", "target_context"]),
        output_dir=tmp_path,
        config={},
    )

    feature_keys = set(result.rows[0]["features"])

    assert feature_keys.isdisjoint(LEAKAGE_FEATURE_COLUMNS)
    assert validate_no_feature_leakage(result.rows) == {
        "passed": True,
        "leakage_columns": [],
    }
