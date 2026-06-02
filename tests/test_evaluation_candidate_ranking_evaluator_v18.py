from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.evaluation.baselines import random_ranking
from molecule_ranker.evaluation.evaluators.ranking import evaluate_candidate_ranking
from molecule_ranker.evaluation.schemas import BenchmarkSplit


def _now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _split() -> BenchmarkSplit:
    return BenchmarkSplit(
        split_id="ranking-split",
        dataset_id="ranking-evaluator-fixture",
        split_type="external_holdout",
        train_ids=[],
        validation_ids=[],
        test_ids=["ranked.json:0", "ranked.json:1", "ranked.json:2", "ranked.json:3"],
        frozen_at=_now(),
        leakage_checks={"passed": True, "failed_checks": []},
        metadata={},
    )


def _ranked_artifact() -> dict[str, object]:
    return {
        "artifact_id": "ranked.json",
        "candidates": [
            {
                "candidate_id": "E1",
                "rank": 1,
                "ranking_score": 0.99,
                "candidate_origin": "existing",
                "developability_score": 0.9,
                "safety_flag": False,
                "canonical_smiles": "CCO",
            },
            {
                "candidate_id": "G1",
                "generated_id": "G1",
                "rank": 2,
                "ranking_score": 0.85,
                "candidate_origin": "generated",
                "developability_score": 0.8,
                "safety_flag": False,
                "canonical_smiles": "CCN",
                "source_seed_result_id": "seed-result-1",
            },
            {
                "candidate_id": "E2",
                "rank": 3,
                "ranking_score": 0.75,
                "candidate_origin": "existing",
                "developability_score": 0.2,
                "safety_flag": True,
                "canonical_smiles": "CCC",
            },
            {
                "candidate_id": "G2",
                "generated_id": "G2",
                "rank": 4,
                "ranking_score": 0.2,
                "candidate_origin": "generated",
                "developability_score": 0.4,
                "safety_flag": False,
                "canonical_smiles": "CCF",
            },
        ],
    }


def _labels() -> dict[str, object]:
    return {
        "artifact_id": "assay_results.json",
        "assay_results": [
            {
                "candidate_id": "E1",
                "outcome_label": "positive",
                "qc_status": "passed",
                "source_record_id": "result-e1",
            },
            {
                "candidate_id": "E2",
                "outcome_label": "positive",
                "qc_status": "passed",
                "source_record_id": "result-e2",
            },
            {
                "generated_id": "G1",
                "canonical_smiles": "CCN",
                "outcome_label": "positive",
                "qc_status": "passed",
                "source_record_id": "result-g1",
            },
            {
                "generated_id": "G2",
                "canonical_smiles": "CCF",
                "outcome_label": "positive",
                "qc_status": "failed",
                "source_record_id": "failed-result-g2",
            },
            {
                "candidate_id": "E1",
                "canonical_smiles": "CCO",
                "outcome_label": "positive",
                "qc_status": "passed",
                "source_record_id": "seed-result-1",
            },
        ],
    }


def _metric_value(report, name: str):
    for metric in report.metrics:
        if metric.name == name:
            return metric.value
    raise AssertionError(f"missing metric {name}")


def test_candidate_ranking_evaluator_scores_existing_generated_and_combined_rankings() -> None:
    report = evaluate_candidate_ranking(
        ranked_candidate_artifacts={"ranked": _ranked_artifact()},
        imported_outcome_labels={"imported_assay_results": _labels()},
        split=_split(),
        baseline_comparators=[random_ranking],
        top_k=(1, 2),
        combined_ranking_enabled=True,
        evaluation_id="ranking-eval",
    )

    assert _metric_value(report, "existing_top_1_hit_rate") == 1.0
    assert _metric_value(report, "generated_top_1_hit_rate") == 1.0
    assert _metric_value(report, "combined_top_2_hit_rate") == 1.0
    assert _metric_value(report, "enrichment_over_random_at_2") == pytest.approx(4 / 3)
    assert _metric_value(report, "generated_exact_result_hit_rate") == pytest.approx(0.5)
    assert _metric_value(report, "safety_developability_false_positive_rate") == pytest.approx(
        1 / 3
    )
    assert _metric_value(report, "failed_qc_label_exclusion_count") == 1.0
    assert report.comparisons[0]["baseline_id"] == "random_ranking"
    assert "generated_requires_exact_result_match" in report.metadata["label_rules"]


def test_candidate_ranking_evaluator_does_not_count_seed_result_as_generated_success() -> None:
    labels = _labels()
    labels["assay_results"] = [
        {
            "generated_id": "G1",
            "canonical_smiles": "CCO",
            "outcome_label": "positive",
            "qc_status": "passed",
            "source_record_id": "seed-result-1",
        }
    ]

    report = evaluate_candidate_ranking(
        ranked_candidate_artifacts={"ranked": _ranked_artifact()},
        imported_outcome_labels={"imported_assay_results": labels},
        split=_split(),
        top_k=(1,),
        combined_ranking_enabled=True,
    )

    assert _metric_value(report, "generated_exact_result_hit_rate") == 0.0
    assert "generated_seed_result_not_counted" in report.warnings


def test_candidate_ranking_evaluator_ignores_model_prediction_labels() -> None:
    report = evaluate_candidate_ranking(
        ranked_candidate_artifacts={"ranked": _ranked_artifact()},
        imported_outcome_labels={
            "model_predictions": {
                "artifact_id": "model_predictions.json",
                "model_predictions": [{"candidate_id": "E1", "outcome_label": "positive"}],
            }
        },
        split=_split(),
        top_k=(1,),
    )

    assert _metric_value(report, "combined_top_1_hit_rate") is None
    assert "no_eligible_outcome_labels" in report.warnings
