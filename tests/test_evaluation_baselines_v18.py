from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from molecule_ranker.evaluation import (
    BenchmarkDataset,
    EvaluationReport,
    developability_only,
    ensure_baseline_comparison,
    evidence_score_only,
    existing_only,
    experimental_support_only,
    generated_only,
    model_prediction_only,
    no_codex_summary_baseline,
    portfolio_greedy_default,
    previous_version_baseline,
    random_ranking,
    simple_baseline_for_task,
    structure_score_only,
)


def _now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _dataset(*, task_type: str = "candidate_ranking") -> BenchmarkDataset:
    as_of = _now()
    return BenchmarkDataset(
        dataset_id="baseline-fixture",
        name="Baseline fixture",
        dataset_type="synthetic_validation",
        source_artifact_ids=["fixture"],
        row_count=3,
        candidate_count=3,
        label_count=3,
        created_at=as_of,
        data_contract_version="data-contracts.v1",
        metadata={
            "task_type": task_type,
            "baseline_as_of": as_of.isoformat(),
            "rows": [
                _row(
                    row_id="row-c1",
                    candidate_id="C1",
                    evidence_score=0.9,
                    developability_score=0.2,
                    predicted_probability=0.4,
                    structure_score=0.1,
                    portfolio_score=0.3,
                    codex_summary_used=True,
                    labels=[
                        {
                            "outcome_label": "positive",
                            "imported_at": (as_of - timedelta(days=1)).isoformat(),
                        }
                    ],
                ),
                _row(
                    row_id="row-c2",
                    candidate_id="C2",
                    generated_id="G2",
                    evidence_score=0.1,
                    developability_score=0.95,
                    predicted_probability=0.98,
                    structure_score=0.8,
                    portfolio_score=0.4,
                    codex_summary_used=False,
                    labels=[
                        {
                            "outcome_label": "positive",
                            "imported_at": (as_of + timedelta(days=3)).isoformat(),
                        }
                    ],
                ),
                _row(
                    row_id="row-c3",
                    candidate_id="C3",
                    evidence_score=0.4,
                    developability_score=0.3,
                    predicted_probability=0.2,
                    structure_score=0.2,
                    portfolio_score=0.95,
                    codex_summary_used=True,
                    labels=[
                        {
                            "outcome_label": "negative",
                            "imported_at": (as_of - timedelta(days=1)).isoformat(),
                        }
                    ],
                ),
            ],
        },
    )


def _row(
    *,
    row_id: str,
    candidate_id: str,
    evidence_score: float,
    developability_score: float,
    predicted_probability: float,
    structure_score: float,
    portfolio_score: float,
    codex_summary_used: bool,
    labels: list[dict[str, Any]],
    generated_id: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "candidate_id": candidate_id,
        "evidence_score": evidence_score,
        "developability_score": developability_score,
        "predicted_probability": predicted_probability,
        "structure_score": structure_score,
        "portfolio_score": portfolio_score,
        "codex_summary_used": codex_summary_used,
    }
    if generated_id is not None:
        record["generated_id"] = generated_id
    return {
        "row_id": row_id,
        "entity_id": generated_id or candidate_id,
        "candidate_id": candidate_id,
        "is_generated": generated_id is not None,
        "record": record,
        "labels": labels,
        "provenance": {"source_artifact_id": "fixture"},
    }


def test_baseline_comparators_rank_by_their_single_signal() -> None:
    dataset = _dataset()

    assert evidence_score_only(dataset).ranked_row_ids[0] == "row-c1"
    assert developability_only(dataset).ranked_row_ids[0] == "row-c2"
    assert model_prediction_only(dataset).ranked_row_ids[0] == "row-c2"
    assert structure_score_only(dataset).ranked_row_ids[0] == "row-c2"
    assert portfolio_greedy_default(dataset).ranked_row_ids[0] == "row-c3"
    assert existing_only(dataset).ranked_row_ids[-1] == "row-c2"
    assert generated_only(dataset).ranked_row_ids[0] == "row-c2"
    assert no_codex_summary_baseline(dataset).ranked_row_ids[0] == "row-c2"


def test_random_baseline_is_deterministic_with_seed() -> None:
    dataset = _dataset()

    first = random_ranking(dataset, seed=123)
    second = random_ranking(dataset, seed=123)

    assert first.ranked_row_ids == second.ranked_row_ids
    assert first.metadata["seed"] == 123
    assert first.metadata["uses_future_labels"] is False


def test_experimental_support_baseline_ignores_future_labels() -> None:
    dataset = _dataset()

    baseline = experimental_support_only(dataset, as_of=_now())

    assert baseline.ranked_row_ids[0] == "row-c1"
    assert baseline.scores["row-c2"] == 0.0
    assert baseline.metadata["uses_future_labels"] is False


def test_previous_version_baseline_uses_frozen_artifact_order() -> None:
    dataset = _dataset()

    baseline = previous_version_baseline(
        dataset,
        frozen_artifact={"ranked_candidate_ids": ["C2", "C1"]},
    )

    assert baseline.ranked_row_ids[:2] == ["row-c2", "row-c1"]
    assert baseline.metadata["source"] == "frozen_previous_version_artifact"


def test_simple_baseline_for_task_selects_task_specific_comparators() -> None:
    assert simple_baseline_for_task(_dataset(task_type="portfolio_selection")).baseline_id == (
        "portfolio_greedy_default"
    )
    assert simple_baseline_for_task(_dataset(task_type="codex_guardrail")).baseline_id == (
        "no_codex_summary_baseline"
    )
    assert simple_baseline_for_task(_dataset(task_type="candidate_ranking")).baseline_id == (
        "random_ranking"
    )


def test_report_without_baselines_gets_simple_comparison() -> None:
    dataset = _dataset()
    report = EvaluationReport(
        evaluation_id="eval-1",
        suite_id="suite-1",
        task_id="task-1",
        dataset_id=dataset.dataset_id,
        metrics=[],
        baseline_metrics=[],
        comparisons=[],
        warnings=[],
        limitations=["Benchmark results are evaluation artifacts, not biomedical evidence."],
        created_at=_now(),
        metadata={},
    )

    updated = ensure_baseline_comparison(report, dataset)

    assert updated is not report
    assert updated.baseline_metrics[0].name == "baseline_comparison_count"
    assert updated.comparisons[0]["baseline_id"] == "random_ranking"


def test_report_with_existing_baseline_is_not_modified() -> None:
    dataset = _dataset()
    report = EvaluationReport(
        evaluation_id="eval-1",
        suite_id="suite-1",
        task_id="task-1",
        dataset_id=dataset.dataset_id,
        metrics=[],
        baseline_metrics=[],
        comparisons=[{"baseline_id": "existing"}],
        warnings=[],
        limitations=["Benchmark results are evaluation artifacts, not biomedical evidence."],
        created_at=_now(),
        metadata={},
    )

    assert ensure_baseline_comparison(report, dataset) is report
