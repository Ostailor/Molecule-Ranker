from __future__ import annotations

import json

import pytest

from molecule_ranker.evaluation import metrics


def _value(metric):
    assert metric.metadata["status"] == "computed"
    return metric.value


def _undefined(metric):
    assert metric.value is None
    assert metric.metadata["status"] == "undefined"
    assert metric.metadata["undefined_reason"]


def test_ranking_metrics() -> None:
    labels = [0, 1, 0, 1]
    scores = [0.1, 0.9, 0.2, 0.8]

    assert _value(metrics.top_k_hit_rate(labels, scores, k=1)) == 1.0
    assert _value(metrics.precision_at_k(labels, scores, k=2)) == 1.0
    assert _value(metrics.recall_at_k(labels, scores, k=1)) == 0.5
    assert _value(metrics.enrichment_factor_at_k(labels, scores, k=2)) == 2.0
    assert _value(metrics.ndcg_at_k(labels, scores, k=2)) == 1.0
    assert _value(metrics.mean_reciprocal_rank(labels, scores)) == 1.0
    assert _value(metrics.average_precision(labels, scores)) == 1.0


def test_classification_and_calibration_metrics() -> None:
    labels = [0, 0, 1, 1]
    scores = [0.1, 0.4, 0.8, 0.9]
    predictions = [0, 0, 1, 1]

    assert _value(metrics.roc_auc(labels, scores)) == 1.0
    assert _value(metrics.pr_auc(labels, scores)) == 1.0
    assert _value(metrics.accuracy(labels, predictions)) == 1.0
    assert _value(metrics.balanced_accuracy(labels, predictions)) == 1.0
    assert _value(metrics.precision(labels, predictions)) == 1.0
    assert _value(metrics.recall(labels, predictions)) == 1.0
    assert _value(metrics.f1(labels, predictions)) == 1.0
    assert _value(metrics.brier_score(labels, scores)) == pytest.approx(0.055)
    assert _value(metrics.expected_calibration_error(labels, scores, bins=2)) == pytest.approx(0.2)


def test_regression_metrics() -> None:
    actual = [1, 2, 3]
    predicted = [1, 2, 4]

    assert _value(metrics.mae(actual, predicted)) == pytest.approx(1 / 3)
    assert _value(metrics.rmse(actual, predicted)) == pytest.approx((1 / 3) ** 0.5)
    assert _value(metrics.r2(actual, predicted)) == pytest.approx(0.5)
    assert _value(metrics.spearman(actual, predicted)) == pytest.approx(1.0)
    assert _value(metrics.pearson(actual, predicted)) == pytest.approx(0.9819805)


def test_generation_metrics() -> None:
    assert _value(metrics.validity_rate([True, False, True])) == pytest.approx(2 / 3)
    assert _value(metrics.uniqueness_rate(["A", "B", "A"])) == pytest.approx(2 / 3)
    assert _value(metrics.novelty_rate([True, True, False])) == pytest.approx(2 / 3)
    assert _value(metrics.diversity_score([0.2, 0.6])) == pytest.approx(0.4)
    assert _value(metrics.scaffold_diversity(["s1", "s2", "s1"])) == pytest.approx(2 / 3)
    assert _value(metrics.near_duplicate_rate([0.95, 0.2, 0.91])) == pytest.approx(2 / 3)
    assert _value(metrics.retained_after_developability([True, False])) == 0.5
    assert _value(metrics.exact_result_hit_rate([True, False, True])) == pytest.approx(2 / 3)

    readiness = metrics.experiment_readiness_distribution(["ready", "review", "ready"])
    assert json.loads(str(readiness.value)) == {"ready": 2, "review": 1}
    assert readiness.metadata["distribution"] == {"ready": 2, "review": 1}


def test_portfolio_and_campaign_metrics() -> None:
    assert _value(metrics.selected_hit_rate([True, False, True])) == pytest.approx(2 / 3)
    assert _value(metrics.learning_value_realized(6, 3)) == 2.0
    assert _value(metrics.budget_utilization(25, 100)) == 0.25
    assert _value(metrics.cost_per_positive_result(1000, 4)) == 250.0
    assert _value(metrics.stop_trigger_accuracy([True, False], [True, True])) == 0.5
    assert _value(metrics.replan_latency([2, 4, 6])) == 4.0
    assert _value(metrics.review_gate_precision([1, 1, 0], [1, 0, 1])) == 0.5
    assert _value(metrics.assay_slot_efficiency(3, 6)) == 0.5


def test_guardrail_metrics() -> None:
    assert _value(metrics.forbidden_claim_rate([True, False])) == 0.5
    assert metrics.forbidden_claim_rate([True]).higher_is_better is False
    assert _value(metrics.fake_citation_rate([False, False])) == 0.0
    assert _value(metrics.fake_result_rate([True, False])) == 0.5
    assert _value(metrics.protocol_leak_rate([False, True])) == 0.5
    assert _value(metrics.grounding_rate([True, False, True])) == pytest.approx(2 / 3)
    assert _value(metrics.json_validity_rate([True, True])) == 1.0
    assert _value(metrics.guardrail_pass_rate([True, False])) == 0.5


def test_reproducibility_metrics() -> None:
    assert _value(metrics.artifact_hash_match([True, False])) == 0.5
    assert _value(metrics.deterministic_rerun_match([True, True])) == 1.0
    assert _value(metrics.config_hash_match([False, True])) == 0.5
    assert _value(metrics.seed_reproducibility([True, False, True])) == pytest.approx(2 / 3)


def test_undefined_metrics_do_not_report_misleading_zero() -> None:
    _undefined(metrics.recall_at_k([0, 0], [0.2, 0.1], k=1))
    _undefined(metrics.roc_auc([1, 1], [0.8, 0.9]))
    _undefined(metrics.precision([0, 1], [0, 0]))
    _undefined(metrics.r2([1, 1], [1, 1]))
    _undefined(metrics.validity_rate([]))
    _undefined(metrics.cost_per_positive_result(100, 0))
    _undefined(metrics.artifact_hash_match([]))
