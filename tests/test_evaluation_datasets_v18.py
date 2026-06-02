from __future__ import annotations

from collections.abc import Callable
from typing import Any

from molecule_ranker.evaluation import (
    build_campaign_planning_dataset,
    build_candidate_ranking_dataset,
    build_codex_guardrail_dataset,
    build_developability_triage_dataset,
    build_generated_molecule_prioritization_dataset,
    build_hypothesis_prioritization_dataset,
    build_integration_data_quality_dataset,
    build_portfolio_selection_dataset,
    build_structure_prioritization_dataset,
    build_surrogate_prediction_dataset,
)
from molecule_ranker.evaluation.schemas import BenchmarkDataset


def _assay_label(candidate_id: str, *, qc_status: str = "passed") -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "outcome_label": "positive",
        "qc_status": qc_status,
        "source_record_id": f"assay:{candidate_id}",
    }


def _dataset_rows(dataset: BenchmarkDataset) -> list[dict[str, Any]]:
    rows = dataset.metadata["rows"]
    assert isinstance(rows, list)
    return rows


def test_candidate_ranking_dataset_uses_imported_labels_and_excludes_failed_qc() -> None:
    dataset = build_candidate_ranking_dataset(
        {
            "candidates": {
                "artifact_id": "candidates.json",
                "candidates": [{"candidate_id": "C1"}, {"candidate_id": "C2"}],
            },
            "imported_assay_results": {
                "artifact_id": "assay_results.json",
                "assay_results": [
                    _assay_label("C1"),
                    _assay_label("C2", qc_status="failed"),
                ],
            },
        }
    )

    assert dataset.row_count == 2
    assert dataset.candidate_count == 2
    assert dataset.label_count == 1
    assert dataset.source_artifact_ids == ["candidates.json", "assay_results.json"]
    assert dataset.metadata["excluded_labels"][0]["reason"] == "failed_qc"
    assert _dataset_rows(dataset)[0]["provenance"]["source_artifact_id"] == "candidates.json"


def test_generated_dataset_requires_exact_linked_imported_result() -> None:
    dataset = build_generated_molecule_prioritization_dataset(
        {
            "generated_candidates": {
                "artifact_id": "generated_candidates.json",
                "retained_generated_molecules": [
                    {"generated_id": "G1", "candidate_name": "Generated One"},
                    {"generated_id": "G2", "candidate_name": "Generated Two"},
                ],
            },
            "imported_assay_results": {
                "artifact_id": "assay_results.json",
                "assay_results": [
                    {
                        "generated_id": "G1",
                        "outcome_label": "positive",
                        "qc_status": "passed",
                        "source_record_id": "assay:G1",
                    },
                    {
                        "candidate_name": "Generated Two",
                        "outcome_label": "positive",
                        "qc_status": "passed",
                        "source_record_id": "assay:name-only",
                    },
                ],
            },
        }
    )

    assert dataset.row_count == 2
    assert dataset.label_count == 1
    labeled_rows = [row for row in _dataset_rows(dataset) if row["labels"]]
    assert labeled_rows[0]["entity_id"] == "G1"


def test_generated_dataset_does_not_accept_synthetic_fixture_labels() -> None:
    dataset = build_generated_molecule_prioritization_dataset(
        {
            "generated_candidates": {
                "artifact_id": "generated_candidates.json",
                "generated_molecule_hypotheses": [{"generated_id": "G1"}],
            },
            "synthetic_validation_fixture": {
                "artifact_id": "synthetic_labels.json",
                "synthetic": True,
                "labels": [{"generated_id": "G1", "outcome_label": "positive"}],
            },
        }
    )

    assert dataset.label_count == 0
    assert dataset.metadata["excluded_labels"][0]["reason"] == (
        "generated_requires_exact_imported_assay_result"
    )


def test_surrogate_prediction_dataset_does_not_use_predictions_as_labels() -> None:
    dataset = build_surrogate_prediction_dataset(
        {
            "model_predictions": {
                "artifact_id": "model_predictions.json",
                "model_predictions": [
                    {
                        "candidate_id": "C1",
                        "predicted_probability": 0.8,
                        "model_version": "fixture",
                    }
                ],
            },
            "imported_assay_results": {
                "artifact_id": "assay_results.json",
                "assay_results": [_assay_label("C1")],
            },
        }
    )

    assert dataset.row_count == 1
    assert dataset.label_count == 1
    assert _dataset_rows(dataset)[0]["source_artifact_id"] == "model_predictions.json"


def test_developability_triage_dataset_builds_from_assessments() -> None:
    dataset = build_developability_triage_dataset(
        {
            "developability": {
                "artifact_id": "developability.json",
                "assessments": [{"candidate_id": "C1", "risk_level": "medium"}],
            },
            "imported_assay_results": {"assay_results": [_assay_label("C1")]},
        }
    )

    assert dataset.row_count == 1
    assert dataset.label_count == 1


def test_structure_prioritization_dataset_builds_from_structure_assessments() -> None:
    dataset = build_structure_prioritization_dataset(
        {
            "structure_aware_assessments": {
                "artifact_id": "structure_aware_assessments.json",
                "structure_aware_assessments": [{"candidate_id": "C1", "pose_qc": "pass"}],
            },
            "imported_assay_results": {"assay_results": [_assay_label("C1")]},
        }
    )

    assert dataset.row_count == 1
    assert dataset.label_count == 1


def test_portfolio_selection_dataset_builds_from_selected_candidates() -> None:
    dataset = build_portfolio_selection_dataset(
        {
            "portfolio_optimization": {
                "artifact_id": "portfolio_optimization.json",
                "selected_candidates": [{"candidate_id": "C1", "selection_status": "selected"}],
            },
            "imported_assay_results": {"assay_results": [_assay_label("C1")]},
        }
    )

    assert dataset.row_count == 1
    assert dataset.label_count == 1


def test_hypothesis_prioritization_dataset_builds_from_hypotheses() -> None:
    dataset = build_hypothesis_prioritization_dataset(
        {
            "hypotheses": {
                "artifact_id": "hypotheses.json",
                "hypotheses": [{"hypothesis_id": "H1", "priority_score": 0.7}],
            },
            "synthetic_validation_fixture": {
                "artifact_id": "hypothesis_labels.json",
                "synthetic": True,
                "labels": [{"hypothesis_id": "H1", "label": "supported"}],
            },
        }
    )

    assert dataset.dataset_type == "synthetic_validation"
    assert dataset.row_count == 1
    assert dataset.label_count == 1


def test_campaign_planning_dataset_builds_from_plan_work_packages() -> None:
    dataset = build_campaign_planning_dataset(
        {
            "campaign_plan": {
                "artifact_id": "campaign_plan.json",
                "work_packages": [{"work_package_id": "WP1", "package_type": "review"}],
            },
            "synthetic_validation_fixture": {
                "artifact_id": "campaign_outcomes.json",
                "synthetic": True,
                "labels": [{"work_package_id": "WP1", "outcome_label": "completed"}],
            },
        }
    )

    assert dataset.row_count == 1
    assert dataset.label_count == 1


def test_codex_guardrail_dataset_builds_from_guardrail_cases() -> None:
    dataset = build_codex_guardrail_dataset(
        {
            "synthetic_validation_fixture": {
                "artifact_id": "codex_guardrails.json",
                "synthetic": True,
                "guardrail_cases": [
                    {"record_id": "case-1", "category": "overclaim", "expected": "blocked"}
                ],
            }
        }
    )

    assert dataset.dataset_type == "synthetic_validation"
    assert dataset.row_count == 1


def test_integration_data_quality_dataset_builds_from_external_fixtures() -> None:
    dataset = build_integration_data_quality_dataset(
        {
            "external_integration_fixture": {
                "artifact_id": "integration_fixture.json",
                "fixtures": [
                    {
                        "integration_record_id": "EXT1",
                        "source_system": "fixture",
                        "data_quality_issue": False,
                    }
                ],
            }
        }
    )

    assert dataset.dataset_type == "integration_fixture"
    assert dataset.row_count == 1
    assert _dataset_rows(dataset)[0]["entity_id"] == "EXT1"


def test_all_task_helpers_return_benchmark_dataset() -> None:
    cases: list[tuple[Callable[..., BenchmarkDataset], dict[str, object]]] = [
        (
            build_candidate_ranking_dataset,
            {"candidates": {"candidates": [{"candidate_id": "C1"}]}},
        ),
        (
            build_generated_molecule_prioritization_dataset,
            {"generated_candidates": {"generated_molecule_hypotheses": [{"generated_id": "G1"}]}},
        ),
        (
            build_surrogate_prediction_dataset,
            {"model_predictions": {"model_predictions": [{"candidate_id": "C1"}]}},
        ),
        (
            build_developability_triage_dataset,
            {"developability": {"assessments": [{"candidate_id": "C1"}]}},
        ),
        (
            build_structure_prioritization_dataset,
            {"structure": {"structure_aware_assessments": [{"candidate_id": "C1"}]}},
        ),
        (
            build_portfolio_selection_dataset,
            {"portfolio": {"selected_candidates": [{"candidate_id": "C1"}]}},
        ),
        (
            build_hypothesis_prioritization_dataset,
            {"hypotheses": {"hypotheses": [{"hypothesis_id": "H1"}]}},
        ),
        (
            build_campaign_planning_dataset,
            {"campaign": {"work_packages": [{"work_package_id": "WP1"}]}},
        ),
        (
            build_codex_guardrail_dataset,
            {"codex": {"guardrail_cases": [{"record_id": "case-1"}]}},
        ),
        (
            build_integration_data_quality_dataset,
            {"integration": {"records": [{"integration_record_id": "EXT1"}]}},
        ),
    ]

    for builder, sources in cases:
        dataset = builder(sources)
        assert isinstance(dataset, BenchmarkDataset)
        assert dataset.row_count == 1
