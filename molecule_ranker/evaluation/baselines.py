from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.evaluation.schemas import (
    BenchmarkDataset,
    EvaluationMetric,
    EvaluationReport,
)

BaselineArtifact = Mapping[str, Any] | Sequence[Any]


@dataclass(frozen=True)
class BaselineResult:
    baseline_id: str
    name: str
    ranked_row_ids: list[str]
    scores: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_comparison(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "name": self.name,
            "ranked_row_ids": self.ranked_row_ids,
            "scores": self.scores,
            "metadata": self.metadata,
        }


def random_ranking(dataset: BenchmarkDataset, *, seed: int = 18) -> BaselineResult:
    row_ids = [_row_id(row) for row in _dataset_rows(dataset)]
    random.Random(seed).shuffle(row_ids)
    return BaselineResult(
        baseline_id="random_ranking",
        name="Random ranking",
        ranked_row_ids=row_ids,
        scores={row_id: float(len(row_ids) - index) for index, row_id in enumerate(row_ids)},
        metadata={"seed": seed, "uses_future_labels": False},
    )


def evidence_score_only(dataset: BenchmarkDataset) -> BaselineResult:
    return _score_baseline(
        dataset,
        baseline_id="evidence_score_only",
        name="Evidence score only",
        keys=("evidence_score", "transparent_evidence_score", "source_backed_score"),
    )


def developability_only(dataset: BenchmarkDataset) -> BaselineResult:
    return _score_baseline(
        dataset,
        baseline_id="developability_only",
        name="Developability only",
        keys=("developability_score", "overall_developability_score", "admet_score"),
        inverse_keys=("developability_risk_score", "risk_score"),
    )


def experimental_support_only(
    dataset: BenchmarkDataset,
    *,
    as_of: datetime | None = None,
) -> BaselineResult:
    resolved_as_of = as_of or _metadata_datetime(dataset, "baseline_as_of")
    rows = _dataset_rows(dataset)
    scores = {
        _row_id(row): _experimental_support_score(row, as_of=resolved_as_of)
        for row in rows
    }
    return BaselineResult(
        baseline_id="experimental_support_only",
        name="Experimental support only",
        ranked_row_ids=_ranked_ids(rows, scores),
        scores=scores,
        metadata={
            "uses_future_labels": False,
            "as_of": resolved_as_of.isoformat() if resolved_as_of else None,
            "label_source": "accepted_row_labels_before_as_of",
        },
    )


def model_prediction_only(dataset: BenchmarkDataset) -> BaselineResult:
    return _score_baseline(
        dataset,
        baseline_id="model_prediction_only",
        name="Model prediction only",
        keys=(
            "predicted_probability",
            "prediction_score",
            "model_score",
            "surrogate_score",
            "score",
        ),
    )


def structure_score_only(dataset: BenchmarkDataset) -> BaselineResult:
    return _score_baseline(
        dataset,
        baseline_id="structure_score_only",
        name="Structure score only",
        keys=("structure_score", "pose_score", "binding_score", "pocket_fit_score"),
        inverse_keys=("docking_score", "binding_energy"),
    )


def existing_only(dataset: BenchmarkDataset) -> BaselineResult:
    rows = _dataset_rows(dataset)
    scores = {_row_id(row): 0.0 if _is_generated(row) else 1.0 for row in rows}
    return BaselineResult(
        baseline_id="existing_only",
        name="Existing molecules only",
        ranked_row_ids=_ranked_ids(rows, scores),
        scores=scores,
        metadata={"uses_future_labels": False},
    )


def generated_only(dataset: BenchmarkDataset) -> BaselineResult:
    rows = _dataset_rows(dataset)
    scores = {_row_id(row): 1.0 if _is_generated(row) else 0.0 for row in rows}
    return BaselineResult(
        baseline_id="generated_only",
        name="Generated molecules only",
        ranked_row_ids=_ranked_ids(rows, scores),
        scores=scores,
        metadata={"uses_future_labels": False},
    )


def portfolio_greedy_default(dataset: BenchmarkDataset) -> BaselineResult:
    rows = _dataset_rows(dataset)
    scores = {
        _row_id(row): (
            _numeric_value(row, ("portfolio_score", "selection_score", "utility_score"))
            or _weighted_default_score(row)
        )
        for row in rows
    }
    return BaselineResult(
        baseline_id="portfolio_greedy_default",
        name="Portfolio greedy default",
        ranked_row_ids=_ranked_ids(rows, scores),
        scores=scores,
        metadata={
            "uses_future_labels": False,
            "strategy": "portfolio_score_or_evidence_developability_structure_average",
        },
    )


def no_codex_summary_baseline(dataset: BenchmarkDataset) -> BaselineResult:
    rows = _dataset_rows(dataset)
    scores = {_row_id(row): 0.0 if _codex_summary_used(row) else 1.0 for row in rows}
    return BaselineResult(
        baseline_id="no_codex_summary_baseline",
        name="No Codex summary baseline",
        ranked_row_ids=_ranked_ids(rows, scores),
        scores=scores,
        metadata={"uses_future_labels": False, "codex_summary_used": False},
    )


def previous_version_baseline(
    dataset: BenchmarkDataset,
    frozen_artifact: BaselineArtifact | None = None,
) -> BaselineResult:
    rows = _dataset_rows(dataset)
    artifact = frozen_artifact or _previous_version_artifact(dataset)
    ordered_ids, artifact_scores = _previous_version_order(artifact)
    row_lookup = _row_lookup(rows)
    ranked: list[str] = []
    scores: dict[str, float] = {}

    for index, identifier in enumerate(ordered_ids):
        row_id = row_lookup.get(identifier)
        if row_id is None or row_id in ranked:
            continue
        ranked.append(row_id)
        scores[row_id] = artifact_scores.get(identifier, float(len(ordered_ids) - index))

    unranked_rows = [row for row in rows if _row_id(row) not in set(ranked)]
    default_scores = {_row_id(row): 0.0 for row in unranked_rows}
    ranked.extend(_ranked_ids(unranked_rows, default_scores))
    scores.update(default_scores)
    return BaselineResult(
        baseline_id="previous_version_baseline",
        name="Previous version baseline",
        ranked_row_ids=ranked,
        scores=scores,
        metadata={"uses_future_labels": False, "source": "frozen_previous_version_artifact"},
    )


def simple_baseline_for_task(
    dataset: BenchmarkDataset,
    *,
    seed: int = 18,
) -> BaselineResult:
    task_type = str(dataset.metadata.get("task_type") or "")
    if task_type == "molecule_generation":
        return generated_only(dataset)
    if task_type == "developability_triage":
        return developability_only(dataset)
    if task_type == "surrogate_prediction":
        return evidence_score_only(dataset)
    if task_type == "structure_prioritization":
        return structure_score_only(dataset)
    if task_type == "portfolio_selection":
        return portfolio_greedy_default(dataset)
    if task_type == "codex_guardrail":
        return no_codex_summary_baseline(dataset)
    return random_ranking(dataset, seed=seed)


def ensure_baseline_comparison(
    report: EvaluationReport,
    dataset: BenchmarkDataset,
    *,
    seed: int = 18,
) -> EvaluationReport:
    if report.comparisons or report.baseline_metrics:
        return report
    baseline = simple_baseline_for_task(dataset, seed=seed)
    metric = EvaluationMetric(
        metric_id="baseline_comparison_count",
        name="baseline_comparison_count",
        metric_type="decision_quality",
        value=1.0,
        higher_is_better=True,
        metadata={
            "status": "computed",
            "baseline_id": baseline.baseline_id,
            "requirement": "at_least_one_simple_baseline",
        },
    )
    return report.model_copy(
        update={
            "baseline_metrics": [metric],
            "comparisons": [baseline.as_comparison()],
        }
    )


def _score_baseline(
    dataset: BenchmarkDataset,
    *,
    baseline_id: str,
    name: str,
    keys: Sequence[str],
    inverse_keys: Sequence[str] = (),
) -> BaselineResult:
    rows = _dataset_rows(dataset)
    scores = {
        _row_id(row): _numeric_value(row, keys, inverse_keys=inverse_keys) or 0.0
        for row in rows
    }
    return BaselineResult(
        baseline_id=baseline_id,
        name=name,
        ranked_row_ids=_ranked_ids(rows, scores),
        scores=scores,
        metadata={"uses_future_labels": False, "score_fields": [*keys, *inverse_keys]},
    )


def _dataset_rows(dataset: BenchmarkDataset) -> list[Mapping[str, Any]]:
    rows = dataset.metadata.get("rows", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _row_id(row: Mapping[str, Any]) -> str:
    value = row.get("row_id") or row.get("entity_id") or row.get("candidate_id")
    return str(value)


def _record(row: Mapping[str, Any]) -> Mapping[str, Any]:
    record = row.get("record")
    return record if isinstance(record, Mapping) else {}


def _ranked_ids(rows: Sequence[Mapping[str, Any]], scores: Mapping[str, float]) -> list[str]:
    return [
        _row_id(row)
        for row in sorted(
            rows,
            key=lambda row: (-scores.get(_row_id(row), 0.0), _row_id(row)),
        )
    ]


def _numeric_value(
    row: Mapping[str, Any],
    keys: Sequence[str],
    *,
    inverse_keys: Sequence[str] = (),
) -> float | None:
    record = _record(row)
    for key in keys:
        value = _deep_get(row, key)
        if value is None:
            value = _deep_get(record, key)
        numeric = _as_float(value)
        if numeric is not None:
            return numeric
    for key in inverse_keys:
        value = _deep_get(row, key)
        if value is None:
            value = _deep_get(record, key)
        numeric = _as_float(value)
        if numeric is not None:
            return -numeric
    return None


def _deep_get(mapping: Mapping[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    for nested_key in ("scores", "metrics", "metadata", "model_outputs"):
        nested = mapping.get(nested_key)
        if isinstance(nested, Mapping) and key in nested:
            return nested[key]
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _is_generated(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    return bool(row.get("is_generated") or record.get("is_generated") or record.get("generated_id"))


def _codex_summary_used(row: Mapping[str, Any]) -> bool:
    record = _record(row)
    value = row.get("codex_summary_used", record.get("codex_summary_used"))
    if value is None:
        value = row.get("uses_codex_summary", record.get("uses_codex_summary"))
    return bool(value)


def _weighted_default_score(row: Mapping[str, Any]) -> float:
    values = [
        _numeric_value(row, ("evidence_score", "transparent_evidence_score")),
        _numeric_value(row, ("developability_score", "overall_developability_score")),
        _numeric_value(row, ("structure_score", "pose_score")),
    ]
    observed = [value for value in values if value is not None]
    if not observed:
        return 0.0
    return sum(observed) / len(observed)


def _experimental_support_score(
    row: Mapping[str, Any],
    *,
    as_of: datetime | None,
) -> float:
    labels = row.get("labels", [])
    if not isinstance(labels, list):
        return 0.0
    return sum(_label_support_score(label) for label in labels if _label_available(label, as_of))


def _label_available(label: Any, as_of: datetime | None) -> bool:
    if not isinstance(label, Mapping) or as_of is None:
        return isinstance(label, Mapping)
    label_time = _label_datetime(label)
    return label_time is None or label_time <= as_of


def _label_support_score(label: Mapping[str, Any]) -> float:
    for key in ("outcome_label", "label", "supported", "result", "status"):
        value = label.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        normalized = str(value).strip().lower()
        if normalized in {"positive", "active", "supported", "hit", "pass", "passed", "true"}:
            return 1.0
        if normalized in {"partial", "inconclusive", "mixed"}:
            return 0.5
        if normalized in {
            "negative",
            "inactive",
            "contradicted",
            "unsupported",
            "failed",
            "false",
        }:
            return 0.0
    return 1.0 if any(key in label for key in ("measured_value", "value")) else 0.0


def _label_datetime(label: Mapping[str, Any]) -> datetime | None:
    for key in (
        "imported_at",
        "outcome_imported_at",
        "result_date",
        "created_at",
        "assay_date",
        "measured_at",
    ):
        parsed = _parse_datetime(label.get(key))
        if parsed is not None:
            return parsed
    return None


def _metadata_datetime(dataset: BenchmarkDataset, key: str) -> datetime | None:
    return _parse_datetime(dataset.metadata.get(key))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _previous_version_artifact(dataset: BenchmarkDataset) -> BaselineArtifact | None:
    for key in (
        "previous_version_baseline",
        "previous_version_ranking",
        "frozen_previous_version_artifact",
    ):
        value = dataset.metadata.get(key)
        if isinstance(value, Mapping | Sequence) and not isinstance(value, str | bytes):
            return value
    return None


def _previous_version_order(
    artifact: BaselineArtifact | None,
) -> tuple[list[str], dict[str, float]]:
    if artifact is None:
        return [], {}
    if isinstance(artifact, Mapping):
        for key in (
            "ranked_row_ids",
            "row_ids",
            "ranking",
            "ranked_candidate_ids",
            "candidate_ids",
            "predictions",
        ):
            value = artifact.get(key)
            if isinstance(value, Sequence) and not isinstance(value, str | bytes):
                return _ids_and_scores_from_sequence(value)
        scalar_scores = {
            str(key): float(value)
            for key, value in artifact.items()
            if isinstance(value, int | float)
        }
        return list(scalar_scores), scalar_scores
    return _ids_and_scores_from_sequence(artifact)


def _ids_and_scores_from_sequence(items: Sequence[Any]) -> tuple[list[str], dict[str, float]]:
    ids: list[str] = []
    scores: dict[str, float] = {}
    for index, item in enumerate(items):
        identifier: str | None = None
        score: float | None = None
        if isinstance(item, Mapping):
            for key in ("row_id", "candidate_id", "entity_id", "generated_id", "id"):
                value = item.get(key)
                if value is not None:
                    identifier = str(value)
                    break
            score = _as_float(
                item.get("score")
                or item.get("rank_score")
                or item.get("prediction_score")
                or item.get("predicted_probability")
            )
        elif item is not None:
            identifier = str(item)
        if identifier is None:
            continue
        ids.append(identifier)
        scores[identifier] = score if score is not None else float(len(items) - index)
    return ids, scores


def _row_lookup(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in rows:
        row_id = _row_id(row)
        record = _record(row)
        for value in (
            row_id,
            row.get("entity_id"),
            row.get("candidate_id"),
            record.get("candidate_id"),
            record.get("generated_id"),
            record.get("hypothesis_id"),
            record.get("work_package_id"),
            record.get("record_id"),
            record.get("integration_record_id"),
        ):
            if value is not None:
                lookup[str(value)] = row_id
    return lookup


__all__ = [
    "BaselineResult",
    "developability_only",
    "ensure_baseline_comparison",
    "evidence_score_only",
    "existing_only",
    "experimental_support_only",
    "generated_only",
    "model_prediction_only",
    "no_codex_summary_baseline",
    "portfolio_greedy_default",
    "previous_version_baseline",
    "random_ranking",
    "simple_baseline_for_task",
    "structure_score_only",
]
