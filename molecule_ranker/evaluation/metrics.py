from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Sequence
from typing import Any

from molecule_ranker.evaluation.schemas import EvaluationMetric, EvaluationMetricType

Number = int | float | bool


def top_k_hit_rate(
    labels: Sequence[Number],
    scores: Sequence[Number] | None = None,
    *,
    k: int,
) -> EvaluationMetric:
    ordered = _ordered_pairs(labels, scores)
    if not ordered:
        return _undefined("top_k_hit_rate", "ranking", "no_ranked_items")
    if _positive_count(labels) == 0:
        return _undefined("top_k_hit_rate", "ranking", "no_positive_labels")
    top = ordered[: max(k, 0)]
    return _metric("top_k_hit_rate", "ranking", 1.0 if any(_positive(y) for y, _ in top) else 0.0)


def precision_at_k(
    labels: Sequence[Number],
    scores: Sequence[Number] | None = None,
    *,
    k: int,
) -> EvaluationMetric:
    ordered = _ordered_pairs(labels, scores)
    if not ordered or k <= 0:
        return _undefined("precision_at_k", "ranking", "no_ranked_items_or_invalid_k")
    top = ordered[:k]
    return _metric("precision_at_k", "ranking", _positive_count([y for y, _ in top]) / len(top))


def recall_at_k(
    labels: Sequence[Number],
    scores: Sequence[Number] | None = None,
    *,
    k: int,
) -> EvaluationMetric:
    ordered = _ordered_pairs(labels, scores)
    positives = _positive_count(labels)
    if not ordered or k <= 0:
        return _undefined("recall_at_k", "ranking", "no_ranked_items_or_invalid_k")
    if positives == 0:
        return _undefined("recall_at_k", "ranking", "no_positive_labels")
    top = ordered[:k]
    return _metric("recall_at_k", "ranking", _positive_count([y for y, _ in top]) / positives)


def enrichment_factor_at_k(
    labels: Sequence[Number],
    scores: Sequence[Number] | None = None,
    *,
    k: int,
) -> EvaluationMetric:
    ordered = _ordered_pairs(labels, scores)
    positives = _positive_count(labels)
    if not ordered or k <= 0:
        return _undefined("enrichment_factor_at_k", "ranking", "no_ranked_items_or_invalid_k")
    if positives == 0:
        return _undefined("enrichment_factor_at_k", "ranking", "no_positive_labels")
    top = ordered[: min(k, len(ordered))]
    baseline_rate = positives / len(ordered)
    if baseline_rate == 0:
        return _undefined("enrichment_factor_at_k", "ranking", "zero_baseline_rate")
    observed_rate = _positive_count([y for y, _ in top]) / len(top)
    return _metric("enrichment_factor_at_k", "ranking", observed_rate / baseline_rate)


def ndcg_at_k(
    labels: Sequence[Number],
    scores: Sequence[Number] | None = None,
    *,
    k: int,
) -> EvaluationMetric:
    ordered = _ordered_pairs(labels, scores)
    if not ordered or k <= 0:
        return _undefined("ndcg_at_k", "ranking", "no_ranked_items_or_invalid_k")
    gains = [float(y) for y, _ in ordered[:k]]
    ideal = sorted([float(label) for label in labels], reverse=True)[:k]
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0:
        return _undefined("ndcg_at_k", "ranking", "zero_ideal_dcg")
    return _metric("ndcg_at_k", "ranking", _dcg(gains) / ideal_dcg)


def mean_reciprocal_rank(
    labels: Sequence[Number],
    scores: Sequence[Number] | None = None,
) -> EvaluationMetric:
    ordered = _ordered_pairs(labels, scores)
    if not ordered:
        return _undefined("mean_reciprocal_rank", "ranking", "no_ranked_items")
    for index, (label, _score) in enumerate(ordered, start=1):
        if _positive(label):
            return _metric("mean_reciprocal_rank", "ranking", 1.0 / index)
    return _undefined("mean_reciprocal_rank", "ranking", "no_positive_labels")


def average_precision(
    labels: Sequence[Number],
    scores: Sequence[Number] | None = None,
) -> EvaluationMetric:
    ordered = _ordered_pairs(labels, scores)
    positives = _positive_count(labels)
    if not ordered:
        return _undefined("average_precision", "ranking", "no_ranked_items")
    if positives == 0:
        return _undefined("average_precision", "ranking", "no_positive_labels")
    precision_sum = 0.0
    hits = 0
    for index, (label, _score) in enumerate(ordered, start=1):
        if _positive(label):
            hits += 1
            precision_sum += hits / index
    return _metric("average_precision", "ranking", precision_sum / positives)


def roc_auc(labels: Sequence[Number], scores: Sequence[Number]) -> EvaluationMetric:
    pairs = _binary_score_pairs(labels, scores)
    if pairs is None:
        return _undefined("roc_auc", "classification", "labels_and_scores_length_mismatch")
    positives = sum(1 for label, _score in pairs if label == 1)
    negatives = len(pairs) - positives
    if positives == 0 or negatives == 0:
        return _undefined("roc_auc", "classification", "requires_positive_and_negative_labels")
    ranks = _average_ranks([score for _label, score in pairs])
    rank_sum_positive = sum(
        rank for rank, (label, _score) in zip(ranks, pairs, strict=True) if label
    )
    auc = (rank_sum_positive - positives * (positives + 1) / 2) / (positives * negatives)
    return _metric("roc_auc", "classification", auc)


def pr_auc(labels: Sequence[Number], scores: Sequence[Number]) -> EvaluationMetric:
    result = average_precision(labels, scores)
    return result.model_copy(
        update={"metric_id": "pr_auc", "name": "pr_auc", "metric_type": "classification"}
    )


def accuracy(
    labels: Sequence[Number],
    predictions: Sequence[Number] | None = None,
    *,
    scores: Sequence[Number] | None = None,
    threshold: float = 0.5,
) -> EvaluationMetric:
    pairs = _binary_prediction_pairs(labels, predictions, scores=scores, threshold=threshold)
    if pairs is None or not pairs:
        return _undefined("accuracy", "classification", "no_labels_or_predictions")
    return _metric("accuracy", "classification", sum(y == yhat for y, yhat in pairs) / len(pairs))


def balanced_accuracy(
    labels: Sequence[Number],
    predictions: Sequence[Number] | None = None,
    *,
    scores: Sequence[Number] | None = None,
    threshold: float = 0.5,
) -> EvaluationMetric:
    pairs = _binary_prediction_pairs(labels, predictions, scores=scores, threshold=threshold)
    if pairs is None or not pairs:
        return _undefined("balanced_accuracy", "classification", "no_labels_or_predictions")
    counts = _confusion_counts(pairs)
    if counts["positive"] == 0 or counts["negative"] == 0:
        return _undefined(
            "balanced_accuracy", "classification", "requires_positive_and_negative_labels"
        )
    tpr = counts["true_positive"] / counts["positive"]
    tnr = counts["true_negative"] / counts["negative"]
    return _metric("balanced_accuracy", "classification", (tpr + tnr) / 2)


def precision(labels: Sequence[Number], predictions: Sequence[Number]) -> EvaluationMetric:
    pairs = _binary_prediction_pairs(labels, predictions)
    if pairs is None or not pairs:
        return _undefined("precision", "classification", "no_labels_or_predictions")
    counts = _confusion_counts(pairs)
    if counts["predicted_positive"] == 0:
        return _undefined("precision", "classification", "no_predicted_positive_labels")
    return _metric(
        "precision",
        "classification",
        counts["true_positive"] / counts["predicted_positive"],
    )


def recall(labels: Sequence[Number], predictions: Sequence[Number]) -> EvaluationMetric:
    pairs = _binary_prediction_pairs(labels, predictions)
    if pairs is None or not pairs:
        return _undefined("recall", "classification", "no_labels_or_predictions")
    counts = _confusion_counts(pairs)
    if counts["positive"] == 0:
        return _undefined("recall", "classification", "no_positive_labels")
    return _metric("recall", "classification", counts["true_positive"] / counts["positive"])


def f1(labels: Sequence[Number], predictions: Sequence[Number]) -> EvaluationMetric:
    precision_metric = precision(labels, predictions)
    recall_metric = recall(labels, predictions)
    if precision_metric.value is None or recall_metric.value is None:
        return _undefined("f1", "classification", "precision_or_recall_undefined")
    denominator = float(precision_metric.value) + float(recall_metric.value)
    if denominator == 0:
        return _undefined("f1", "classification", "zero_precision_and_recall")
    return _metric(
        "f1",
        "classification",
        2 * float(precision_metric.value) * float(recall_metric.value) / denominator,
    )


def brier_score(labels: Sequence[Number], probabilities: Sequence[Number]) -> EvaluationMetric:
    pairs = _binary_score_pairs(labels, probabilities)
    if pairs is None or not pairs:
        return _undefined(
            "brier_score", "calibration", "no_labels_or_probabilities", higher_is_better=False
        )
    return _metric(
        "brier_score",
        "calibration",
        sum((score - label) ** 2 for label, score in pairs) / len(pairs),
        higher_is_better=False,
    )


def expected_calibration_error(
    labels: Sequence[Number],
    probabilities: Sequence[Number],
    *,
    bins: int = 10,
) -> EvaluationMetric:
    pairs = _binary_score_pairs(labels, probabilities)
    if pairs is None or not pairs or bins <= 0:
        return _undefined(
            "expected_calibration_error",
            "calibration",
            "no_labels_or_invalid_bins",
            higher_is_better=False,
        )
    total = len(pairs)
    error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        bucket = [
            (label, score)
            for label, score in pairs
            if lower <= score < upper or (index == bins - 1 and score == 1.0)
        ]
        if not bucket:
            continue
        confidence = sum(score for _label, score in bucket) / len(bucket)
        accuracy_value = sum(label for label, _score in bucket) / len(bucket)
        error += (len(bucket) / total) * abs(confidence - accuracy_value)
    return _metric("expected_calibration_error", "calibration", error, higher_is_better=False)


def mae(actual: Sequence[Number], predicted: Sequence[Number]) -> EvaluationMetric:
    pairs = _regression_pairs(actual, predicted)
    if pairs is None or not pairs:
        return _undefined(
            "mae", "regression", "no_actual_or_predicted_values", higher_is_better=False
        )
    return _metric(
        "mae",
        "regression",
        sum(abs(y - yhat) for y, yhat in pairs) / len(pairs),
        higher_is_better=False,
    )


def rmse(actual: Sequence[Number], predicted: Sequence[Number]) -> EvaluationMetric:
    pairs = _regression_pairs(actual, predicted)
    if pairs is None or not pairs:
        return _undefined(
            "rmse", "regression", "no_actual_or_predicted_values", higher_is_better=False
        )
    return _metric(
        "rmse",
        "regression",
        math.sqrt(sum((y - yhat) ** 2 for y, yhat in pairs) / len(pairs)),
        higher_is_better=False,
    )


def r2(actual: Sequence[Number], predicted: Sequence[Number]) -> EvaluationMetric:
    pairs = _regression_pairs(actual, predicted)
    if pairs is None or len(pairs) < 2:
        return _undefined("r2", "regression", "requires_at_least_two_pairs")
    mean_actual = sum(y for y, _yhat in pairs) / len(pairs)
    total = sum((y - mean_actual) ** 2 for y, _yhat in pairs)
    if total == 0:
        return _undefined("r2", "regression", "zero_actual_variance")
    residual = sum((y - yhat) ** 2 for y, yhat in pairs)
    return _metric("r2", "regression", 1 - residual / total)


def spearman(actual: Sequence[Number], predicted: Sequence[Number]) -> EvaluationMetric:
    pairs = _regression_pairs(actual, predicted)
    if pairs is None or len(pairs) < 2:
        return _undefined("spearman", "regression", "requires_at_least_two_pairs")
    actual_ranks = _average_ranks([y for y, _yhat in pairs])
    predicted_ranks = _average_ranks([yhat for _y, yhat in pairs])
    return _correlation_metric("spearman", actual_ranks, predicted_ranks)


def pearson(actual: Sequence[Number], predicted: Sequence[Number]) -> EvaluationMetric:
    pairs = _regression_pairs(actual, predicted)
    if pairs is None or len(pairs) < 2:
        return _undefined("pearson", "regression", "requires_at_least_two_pairs")
    return _correlation_metric("pearson", [y for y, _yhat in pairs], [yhat for _y, yhat in pairs])


def validity_rate(valid_flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("validity_rate", "generation", valid_flags)


def uniqueness_rate(items: Sequence[str]) -> EvaluationMetric:
    if not items:
        return _undefined("uniqueness_rate", "generation", "no_generated_items")
    return _metric("uniqueness_rate", "generation", len(set(items)) / len(items))


def novelty_rate(novel_flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("novelty_rate", "generation", novel_flags)


def diversity_score(distances: Sequence[Number]) -> EvaluationMetric:
    values = _float_values(distances)
    if not values:
        return _undefined("diversity_score", "diversity", "no_pairwise_distances")
    return _metric("diversity_score", "diversity", sum(values) / len(values))


def scaffold_diversity(scaffolds: Sequence[str]) -> EvaluationMetric:
    if not scaffolds:
        return _undefined("scaffold_diversity", "diversity", "no_scaffolds")
    return _metric("scaffold_diversity", "diversity", len(set(scaffolds)) / len(scaffolds))


def near_duplicate_rate(
    values: Sequence[Number | bool], *, threshold: float = 0.9
) -> EvaluationMetric:
    if not values:
        return _undefined(
            "near_duplicate_rate",
            "generation",
            "no_similarity_or_duplicate_flags",
            higher_is_better=False,
        )
    if all(isinstance(value, bool) for value in values):
        duplicate_count = sum(1 for value in values if bool(value))
    else:
        duplicate_count = sum(1 for value in values if float(value) >= threshold)
    return _metric(
        "near_duplicate_rate", "generation", duplicate_count / len(values), higher_is_better=False
    )


def retained_after_developability(retained_flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("retained_after_developability", "generation", retained_flags)


def exact_result_hit_rate(hit_flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("exact_result_hit_rate", "generation", hit_flags)


def experiment_readiness_distribution(labels: Sequence[str]) -> EvaluationMetric:
    if not labels:
        return _undefined("experiment_readiness_distribution", "generation", "no_readiness_labels")
    distribution = dict(sorted(Counter(labels).items()))
    return _metric(
        "experiment_readiness_distribution",
        "generation",
        json.dumps(distribution, sort_keys=True),
        metadata={"distribution": distribution, "sample_count": len(labels)},
    )


def selected_hit_rate(hit_flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("selected_hit_rate", "decision_quality", hit_flags)


def learning_value_realized(realized: Number, expected: Number) -> EvaluationMetric:
    expected_value = float(expected)
    if expected_value <= 0:
        return _undefined(
            "learning_value_realized", "decision_quality", "expected_learning_value_not_positive"
        )
    return _metric("learning_value_realized", "decision_quality", float(realized) / expected_value)


def budget_utilization(spent: Number, budget: Number) -> EvaluationMetric:
    budget_value = float(budget)
    if budget_value <= 0:
        return _undefined("budget_utilization", "cost_efficiency", "budget_not_positive")
    return _metric("budget_utilization", "cost_efficiency", float(spent) / budget_value)


def cost_per_positive_result(cost: Number, positive_count: int) -> EvaluationMetric:
    if positive_count <= 0:
        return _undefined(
            "cost_per_positive_result",
            "cost_efficiency",
            "no_positive_results",
            higher_is_better=False,
        )
    return _metric(
        "cost_per_positive_result",
        "cost_efficiency",
        float(cost) / positive_count,
        higher_is_better=False,
    )


def stop_trigger_accuracy(
    predicted_stop: Sequence[bool], actual_stop: Sequence[bool]
) -> EvaluationMetric:
    return accuracy(_bools_to_ints(actual_stop), _bools_to_ints(predicted_stop)).model_copy(
        update={
            "metric_id": "stop_trigger_accuracy",
            "name": "stop_trigger_accuracy",
            "metric_type": "decision_quality",
        }
    )


def replan_latency(latencies: Sequence[Number]) -> EvaluationMetric:
    values = _float_values(latencies)
    if not values:
        return _undefined(
            "replan_latency", "cost_efficiency", "no_replan_latencies", higher_is_better=False
        )
    return _metric(
        "replan_latency", "cost_efficiency", sum(values) / len(values), higher_is_better=False
    )


def review_gate_precision(
    approved_flags: Sequence[Number], positive_labels: Sequence[Number]
) -> EvaluationMetric:
    return precision(positive_labels, approved_flags).model_copy(
        update={
            "metric_id": "review_gate_precision",
            "name": "review_gate_precision",
            "metric_type": "decision_quality",
        }
    )


def assay_slot_efficiency(positive_count: int, assay_slots: int) -> EvaluationMetric:
    if assay_slots <= 0:
        return _undefined("assay_slot_efficiency", "cost_efficiency", "no_assay_slots")
    return _metric("assay_slot_efficiency", "cost_efficiency", positive_count / assay_slots)


def forbidden_claim_rate(flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("forbidden_claim_rate", "guardrail", flags, higher_is_better=False)


def fake_citation_rate(flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("fake_citation_rate", "guardrail", flags, higher_is_better=False)


def fake_result_rate(flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("fake_result_rate", "guardrail", flags, higher_is_better=False)


def protocol_leak_rate(flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("protocol_leak_rate", "guardrail", flags, higher_is_better=False)


def grounding_rate(flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("grounding_rate", "guardrail", flags)


def json_validity_rate(flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("json_validity_rate", "guardrail", flags)


def guardrail_pass_rate(flags: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("guardrail_pass_rate", "guardrail", flags)


def artifact_hash_match(matches: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("artifact_hash_match", "reproducibility", matches)


def deterministic_rerun_match(matches: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("deterministic_rerun_match", "reproducibility", matches)


def config_hash_match(matches: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("config_hash_match", "reproducibility", matches)


def seed_reproducibility(matches: Sequence[bool]) -> EvaluationMetric:
    return _rate_metric("seed_reproducibility", "reproducibility", matches)


def _metric(
    name: str,
    metric_type: EvaluationMetricType,
    value: float | str | bool,
    *,
    higher_is_better: bool | None = True,
    metadata: dict[str, Any] | None = None,
) -> EvaluationMetric:
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type=metric_type,
        value=value,
        higher_is_better=higher_is_better,
        metadata={"status": "computed", **dict(metadata or {})},
    )


def _undefined(
    name: str,
    metric_type: EvaluationMetricType,
    reason: str,
    *,
    higher_is_better: bool | None = True,
) -> EvaluationMetric:
    return EvaluationMetric(
        metric_id=name,
        name=name,
        metric_type=metric_type,
        value=None,
        higher_is_better=higher_is_better,
        metadata={"status": "undefined", "undefined_reason": reason},
    )


def _ordered_pairs(
    labels: Sequence[Number],
    scores: Sequence[Number] | None,
) -> list[tuple[Number, float]]:
    if scores is not None and len(labels) != len(scores):
        return []
    if scores is None:
        return [(label, float(len(labels) - index)) for index, label in enumerate(labels)]
    return sorted(
        [(label, float(score)) for label, score in zip(labels, scores, strict=True)],
        key=lambda item: item[1],
        reverse=True,
    )


def _positive(label: Number) -> bool:
    return bool(label) if isinstance(label, bool) else float(label) > 0


def _positive_count(labels: Sequence[Number]) -> int:
    return sum(1 for label in labels if _positive(label))


def _dcg(gains: Sequence[float]) -> float:
    return sum((2**gain - 1) / math.log2(index + 1) for index, gain in enumerate(gains, start=1))


def _binary_score_pairs(
    labels: Sequence[Number],
    scores: Sequence[Number],
) -> list[tuple[int, float]] | None:
    if len(labels) != len(scores):
        return None
    return [
        (1 if _positive(label) else 0, float(score))
        for label, score in zip(labels, scores, strict=True)
    ]


def _binary_prediction_pairs(
    labels: Sequence[Number],
    predictions: Sequence[Number] | None,
    *,
    scores: Sequence[Number] | None = None,
    threshold: float = 0.5,
) -> list[tuple[int, int]] | None:
    if predictions is None:
        if scores is None:
            return None
        predictions = [float(score) >= threshold for score in scores]
    if len(labels) != len(predictions):
        return None
    return [
        (1 if _positive(label) else 0, 1 if _positive(prediction) else 0)
        for label, prediction in zip(labels, predictions, strict=True)
    ]


def _confusion_counts(pairs: Sequence[tuple[int, int]]) -> dict[str, int]:
    true_positive = 0
    true_negative = 0
    positive = 0
    negative = 0
    predicted_positive = 0
    for label, prediction in pairs:
        if label == 1:
            positive += 1
            if prediction == 1:
                true_positive += 1
        else:
            negative += 1
            if prediction == 0:
                true_negative += 1
        if prediction == 1:
            predicted_positive += 1
    return {
        "true_positive": true_positive,
        "true_negative": true_negative,
        "positive": positive,
        "negative": negative,
        "predicted_positive": predicted_positive,
    }


def _regression_pairs(
    actual: Sequence[Number],
    predicted: Sequence[Number],
) -> list[tuple[float, float]] | None:
    if len(actual) != len(predicted):
        return None
    return [(float(y), float(yhat)) for y, yhat in zip(actual, predicted, strict=True)]


def _average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index
        while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[index][1]:
            end += 1
        average_rank = (index + 1 + end + 1) / 2
        for cursor in range(index, end + 1):
            ranks[indexed[cursor][0]] = average_rank
        index = end + 1
    return ranks


def _correlation_metric(name: str, x: Sequence[float], y: Sequence[float]) -> EvaluationMetric:
    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)
    numerator = sum((left - mean_x) * (right - mean_y) for left, right in zip(x, y, strict=True))
    denominator_x = math.sqrt(sum((left - mean_x) ** 2 for left in x))
    denominator_y = math.sqrt(sum((right - mean_y) ** 2 for right in y))
    if denominator_x == 0 or denominator_y == 0:
        return _undefined(name, "regression", "constant_input")
    return _metric(name, "regression", numerator / (denominator_x * denominator_y))


def _rate_metric(
    name: str,
    metric_type: EvaluationMetricType,
    flags: Sequence[bool],
    *,
    higher_is_better: bool | None = True,
) -> EvaluationMetric:
    if not flags:
        return _undefined(name, metric_type, "no_observations", higher_is_better=higher_is_better)
    return _metric(
        name,
        metric_type,
        sum(1 for flag in flags if flag) / len(flags),
        higher_is_better=higher_is_better,
        metadata={"sample_count": len(flags)},
    )


def _float_values(values: Sequence[Number]) -> list[float]:
    return [float(value) for value in values]


def _bools_to_ints(values: Sequence[bool]) -> list[int]:
    return [1 if value else 0 for value in values]


__all__ = [
    "EvaluationMetric",
    "accuracy",
    "artifact_hash_match",
    "assay_slot_efficiency",
    "average_precision",
    "balanced_accuracy",
    "brier_score",
    "budget_utilization",
    "config_hash_match",
    "cost_per_positive_result",
    "deterministic_rerun_match",
    "diversity_score",
    "enrichment_factor_at_k",
    "exact_result_hit_rate",
    "expected_calibration_error",
    "experiment_readiness_distribution",
    "f1",
    "fake_citation_rate",
    "fake_result_rate",
    "forbidden_claim_rate",
    "grounding_rate",
    "guardrail_pass_rate",
    "json_validity_rate",
    "learning_value_realized",
    "mae",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "near_duplicate_rate",
    "novelty_rate",
    "pearson",
    "pr_auc",
    "precision",
    "precision_at_k",
    "protocol_leak_rate",
    "r2",
    "recall",
    "recall_at_k",
    "replan_latency",
    "retained_after_developability",
    "review_gate_precision",
    "rmse",
    "roc_auc",
    "scaffold_diversity",
    "seed_reproducibility",
    "selected_hit_rate",
    "spearman",
    "stop_trigger_accuracy",
    "top_k_hit_rate",
    "uniqueness_rate",
    "validity_rate",
]
