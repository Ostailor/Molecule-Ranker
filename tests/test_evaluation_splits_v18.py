from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.evaluation import (
    build_candidate_ranking_dataset,
    build_external_holdout_split,
    build_project_based_split,
    build_prospective_split,
    build_random_split,
    build_scaffold_split,
    build_surrogate_prediction_dataset,
    build_time_based_split,
    recommended_split_strategy,
    validate_split_leakage,
)


def _candidate(
    candidate_id: str,
    *,
    canonical_smiles: str = "CCO",
    inchi_key: str | None = None,
    generated_id: str | None = None,
    result_date: str = "2026-01-01",
    project_id: str = "project-1",
    features: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "candidate_id": candidate_id,
        "canonical_smiles": canonical_smiles,
        "inchi_key": inchi_key or f"INCHI-{candidate_id}",
        "result_date": result_date,
        "project_id": project_id,
        "features": features or {"descriptor": 1.0},
    }
    if generated_id:
        record["generated_id"] = generated_id
    return record


def _dataset():
    return build_candidate_ranking_dataset(
        {
            "candidates": {
                "artifact_id": "candidates.json",
                "candidates": [
                    _candidate("C1", canonical_smiles="CCO", result_date="2026-01-01"),
                    _candidate("C2", canonical_smiles="CCN", result_date="2026-02-01"),
                    _candidate("C3", canonical_smiles="c1ccccc1", result_date="2026-03-01"),
                    _candidate("C4", canonical_smiles="c1ccncc1", result_date="2026-04-01"),
                    _candidate("C5", canonical_smiles="CCCl", result_date="2026-05-01"),
                ],
            },
            "imported_assay_results": {
                "artifact_id": "assay_results.json",
                "assay_results": [
                    {
                        "candidate_id": f"C{index}",
                        "outcome_label": "positive",
                        "qc_status": "passed",
                        "source_record_id": f"assay:C{index}",
                        "imported_at": f"2026-0{index}-15T00:00:00+00:00",
                    }
                    for index in range(1, 6)
                ],
            },
        },
    )


def test_random_split_is_deterministic_and_runs_leakage_checks() -> None:
    first = build_random_split(_dataset(), seed=7)
    second = build_random_split(_dataset(), seed=7)

    assert first.train_ids == second.train_ids
    assert first.validation_ids == second.validation_ids
    assert first.test_ids == second.test_ids
    assert "same_inchikey_across_train_test" in first.leakage_checks["checks"]


def test_scaffold_split_assigns_scaffold_groups_and_is_recommended_for_prediction() -> None:
    dataset = build_surrogate_prediction_dataset(
        {
            "model_predictions": {
                "artifact_id": "model_predictions.json",
                "model_predictions": [
                    _candidate("C1", canonical_smiles="c1ccccc1"),
                    _candidate("C2", canonical_smiles="c1ccncc1"),
                    _candidate("C3", canonical_smiles="CCO"),
                ],
            },
            "imported_assay_results": {
                "artifact_id": "assay_results.json",
                "assay_results": [
                    {"candidate_id": "C1", "outcome_label": "positive", "qc_status": "passed"},
                    {"candidate_id": "C2", "outcome_label": "negative", "qc_status": "passed"},
                    {"candidate_id": "C3", "outcome_label": "positive", "qc_status": "passed"},
                ],
            },
        }
    )

    split = build_scaffold_split(dataset, test_fraction=0.34, validation_fraction=0.0)

    assert split.split_type == "scaffold"
    assert recommended_split_strategy(dataset) == "scaffold"
    assert split.metadata["scaffold_group_count"] >= 1


def test_time_based_split_requires_reliable_dates_and_uses_future_as_test() -> None:
    split = build_time_based_split(_dataset(), validation_fraction=0.2, test_fraction=0.2)

    assert split.split_type == "time_based"
    assert split.test_ids == ["candidates.json:4"]
    assert split.leakage_checks["checks"]["future_result_leakage_into_train"]["passed"] is True

    bad_dataset = build_candidate_ranking_dataset(
        {"candidates": {"candidates": [{"candidate_id": "C1"}]}}
    )
    with pytest.raises(ValueError, match="requires reliable dates"):
        build_time_based_split(bad_dataset)


def test_project_and_external_holdout_splits() -> None:
    dataset = build_candidate_ranking_dataset(
        {
            "candidates": {
                "artifact_id": "candidates.json",
                "candidates": [
                    _candidate("C1", project_id="project-a"),
                    _candidate("C2", project_id="project-b"),
                ],
            }
        }
    )

    project_split = build_project_based_split(dataset, holdout_project_ids=["project-b"])
    external_split = build_external_holdout_split(
        dataset,
        holdout_row_ids=["candidates.json:0"],
    )

    assert project_split.test_ids == ["candidates.json:1"]
    assert external_split.test_ids == ["candidates.json:0"]


def test_prospective_split_requires_frozen_prediction_artifacts() -> None:
    dataset = _dataset()

    with pytest.raises(ValueError, match="frozen prediction artifacts"):
        build_prospective_split(dataset, frozen_prediction_artifact_ids=[])

    split = build_prospective_split(
        dataset,
        frozen_prediction_artifact_ids=["predictions.json"],
        outcome_label_artifact_ids=["assay_results.json"],
        frozen_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert split.split_type == "prospective"
    assert split.train_ids == []
    assert len(split.test_ids) == dataset.row_count
    assert split.metadata["predictions_frozen_before_outcomes"] is True


def test_leakage_detection_for_overlap_duplicate_and_feature_columns() -> None:
    assignments = [
        {
            "row_id": "train-1",
            "split": "train",
            "inchi_key": "DUP-INCHI",
            "generated_id": "G1",
            "canonical_smiles": "CCO",
            "assay_result_id": "assay-1",
            "result_date": "2026-03-01",
            "features": {"outcome_label": "positive"},
        },
        {
            "row_id": "test-1",
            "split": "test",
            "inchi_key": "DUP-INCHI",
            "generated_id": "G1",
            "canonical_smiles": "CCO",
            "assay_result_id": "assay-1",
            "result_date": "2026-01-01",
            "features": {},
        },
    ]

    report = validate_split_leakage(assignments, split_type="random")

    assert report["passed"] is False
    assert "same_inchikey_across_train_test" in report["failed_checks"]
    assert "same_generated_id_across_train_test" in report["failed_checks"]
    assert "same_assay_result_duplicated" in report["failed_checks"]
    assert "same_canonical_smiles_across_train_test" in report["failed_checks"]
    assert "future_result_leakage_into_train" in report["failed_checks"]
    assert "label_column_leakage_into_features" in report["failed_checks"]


def test_leakage_detection_for_generated_seed_and_post_outcome_decisions() -> None:
    assignments = [
        {
            "row_id": "generated-1",
            "split": "train",
            "generated_id": "G1",
            "source_seed_result_id": "assay-seed",
            "label_source_record_ids": ["assay-seed"],
            "features": {
                "review_decision_after_outcome": True,
                "portfolio_decision_after_outcome": True,
            },
        }
    ]

    report = validate_split_leakage(assignments, split_type="prospective")

    assert "generated_analog_labeled_from_seed_result" in report["failed_checks"]
    assert "review_decision_after_outcome_used_as_pre_outcome_feature" in report["failed_checks"]
    assert "portfolio_decision_after_outcome_used_as_pre_outcome_feature" in report[
        "failed_checks"
    ]
