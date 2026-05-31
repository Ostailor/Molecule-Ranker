from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .schemas import PortfolioCandidate, PortfolioObjective


@dataclass(frozen=True)
class ObjectiveEvaluation:
    score: float
    explanation: str
    components: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "explanation": self.explanation,
            "components": dict(self.components),
        }


PortfolioObjectiveFunction = Callable[[Sequence[PortfolioCandidate]], ObjectiveEvaluation]


def default_objectives() -> list[PortfolioObjective]:
    return [
        PortfolioObjective(
            objective_id="evidence_strength",
            name="Evidence strength",
            objective_type="maximize",
            metric_name="evidence_strength",
            weight=0.16,
            direction="higher_is_better",
            hard=False,
            description=(
                "Prefer candidates with source-backed evidence and linked experimental support."
            ),
        ),
        PortfolioObjective(
            objective_id="experiment_readiness",
            name="Experiment readiness",
            objective_type="maximize",
            metric_name="experiment_readiness",
            weight=0.13,
            direction="higher_is_better",
            hard=False,
            description=(
                "Prioritize candidates with readiness signals, review progress, and no "
                "blocking risks."
            ),
        ),
        PortfolioObjective(
            objective_id="learning_value",
            name="Learning value",
            objective_type="maximize",
            metric_name="learning_value",
            weight=0.14,
            direction="higher_is_better",
            hard=False,
            description=(
                "Reserve portfolio space for informative uncertainty, evidence gaps, "
                "active-learning signals, and diversity."
            ),
        ),
        PortfolioObjective(
            objective_id="developability",
            name="Developability triage",
            objective_type="maximize",
            metric_name="developability",
            weight=0.12,
            direction="higher_is_better",
            hard=False,
            description=(
                "Prefer candidates with stronger developability scores and fewer risk annotations."
            ),
        ),
        PortfolioObjective(
            objective_id="target_coverage",
            name="Target coverage",
            objective_type="cover",
            metric_name="target_coverage",
            weight=0.10,
            direction="higher_is_better",
            hard=False,
            description="Reward selected candidates covering distinct targets.",
        ),
        PortfolioObjective(
            objective_id="scaffold_diversity",
            name="Scaffold diversity",
            objective_type="cover",
            metric_name="scaffold_diversity",
            weight=0.10,
            direction="higher_is_better",
            hard=False,
            description="Reward distinct scaffolds and chemical series.",
        ),
        PortfolioObjective(
            objective_id="mechanism_diversity",
            name="Mechanism diversity",
            objective_type="cover",
            metric_name="mechanism_diversity",
            weight=0.08,
            direction="higher_is_better",
            hard=False,
            description="Reward distinct source-backed mechanism labels.",
        ),
        PortfolioObjective(
            objective_id="correlated_risk",
            name="Correlated risk minimization",
            objective_type="minimize",
            metric_name="correlated_risk",
            weight=0.07,
            direction="higher_is_better",
            hard=False,
            description=(
                "Penalize repeated risk modes, scaffold liabilities, and shared concern "
                "annotations."
            ),
        ),
        PortfolioObjective(
            objective_id="generated_overexposure",
            name="Generated-only exposure minimization",
            objective_type="minimize",
            metric_name="generated_overexposure",
            weight=0.05,
            direction="higher_is_better",
            hard=False,
            description="Limit portfolios that rely too heavily on generated-only hypotheses.",
        ),
        PortfolioObjective(
            objective_id="experimental_followup_value",
            name="Experimental follow-up value",
            objective_type="balance",
            metric_name="experimental_followup_value",
            weight=0.05,
            direction="higher_is_better",
            hard=False,
            description="Balance readiness, uncertainty, and review priority for follow-up triage.",
        ),
    ]


def maximize_evidence_strength(
    candidates: Sequence[PortfolioCandidate],
) -> ObjectiveEvaluation:
    values = [_candidate_evidence_strength(candidate) for candidate in candidates]
    linked_fraction = _fraction(
        sum(candidate.direct_experimental_evidence for candidate in candidates),
        len(candidates),
    )
    evidence_coverage = _fraction(
        sum(
            candidate.evidence_score is not None or candidate.experimental_support_score is not None
            for candidate in candidates
        ),
        len(candidates),
    )
    score = _mean(values)
    return _evaluation(
        score,
        (
            "Scores source-backed evidence and exact linked experimental-support signals "
            "without making outcome claims."
        ),
        {
            "mean_candidate_signal": score,
            "linked_experimental_fraction": linked_fraction,
            "evidence_coverage": evidence_coverage,
        },
    )


def maximize_experiment_readiness(
    candidates: Sequence[PortfolioCandidate],
) -> ObjectiveEvaluation:
    values = [_candidate_experiment_readiness(candidate) for candidate in candidates]
    no_blocking_fraction = _fraction(
        sum(not candidate.blocking_risks for candidate in candidates),
        len(candidates),
    )
    review_fraction = _fraction(
        sum(_review_status_score(candidate.review_status) >= 0.6 for candidate in candidates),
        len(candidates),
    )
    score = _mean(values)
    return _evaluation(
        score,
        (
            "Balances readiness fields, review state, and blocking-risk annotations for "
            "prioritization only."
        ),
        {
            "mean_candidate_signal": score,
            "no_blocking_risk_fraction": no_blocking_fraction,
            "review_progress_fraction": review_fraction,
        },
    )


def maximize_learning_value(candidates: Sequence[PortfolioCandidate]) -> ObjectiveEvaluation:
    uncertainty = _mean(_value(candidate.uncertainty_score) for candidate in candidates)
    evidence_gap = _fraction(
        sum(
            candidate.evidence_score is None
            and candidate.experimental_support_score is None
            and not candidate.direct_experimental_evidence
            for candidate in candidates
        ),
        len(candidates),
    )
    active_learning = _mean(_active_learning_signal(candidate) for candidate in candidates)
    diversity = _portfolio_diversity_signal(candidates)
    score = 0.35 * uncertainty + 0.25 * evidence_gap + 0.20 * active_learning + 0.20 * diversity
    return _evaluation(
        score,
        (
            "Rewards uncertainty, explicit evidence gaps, active-learning metadata, and "
            "portfolio diversity as learning signals."
        ),
        {
            "mean_uncertainty": uncertainty,
            "evidence_gap_fraction": evidence_gap,
            "active_learning_signal": active_learning,
            "diversity_signal": diversity,
        },
    )


def maximize_developability(candidates: Sequence[PortfolioCandidate]) -> ObjectiveEvaluation:
    scores = [_candidate_developability(candidate) for candidate in candidates]
    missing_fraction = _fraction(
        sum(candidate.developability_score is None for candidate in candidates),
        len(candidates),
    )
    risk_annotation_rate = _fraction(
        sum(bool(candidate.risk_flags or candidate.blocking_risks) for candidate in candidates),
        len(candidates),
    )
    score = _mean(scores)
    return _evaluation(
        score,
        (
            "Combines developability scores with risk-annotation penalties; it does not "
            "assert downstream suitability."
        ),
        {
            "mean_candidate_signal": score,
            "missing_developability_fraction": missing_fraction,
            "risk_annotation_rate": risk_annotation_rate,
        },
    )


def maximize_target_coverage(candidates: Sequence[PortfolioCandidate]) -> ObjectiveEvaluation:
    target_counts = Counter(
        target for candidate in candidates for target in set(candidate.target_symbols)
    )
    denominator = sum(1 for candidate in candidates if candidate.target_symbols)
    score = _fraction(len(target_counts), denominator)
    return _evaluation(
        score,
        "Rewards broad representation of distinct target annotations across selected candidates.",
        {
            "distinct_targets": float(len(target_counts)),
            "target_annotated_candidates": float(denominator),
            "duplicate_target_assignments": float(
                sum(max(0, count - 1) for count in target_counts.values())
            ),
        },
    )


def maximize_scaffold_diversity(candidates: Sequence[PortfolioCandidate]) -> ObjectiveEvaluation:
    scaffold_keys = [
        _scaffold_key(candidate) for candidate in candidates if _scaffold_key(candidate)
    ]
    score = _fraction(len(set(scaffold_keys)), len(candidates))
    return _evaluation(
        score,
        "Rewards distinct scaffold or chemical-series annotations and exposes missing annotations.",
        {
            "distinct_scaffold_or_series": float(len(set(scaffold_keys))),
            "annotated_candidates": float(len(scaffold_keys)),
            "missing_annotation_fraction": _fraction(
                len(candidates) - len(scaffold_keys), len(candidates)
            ),
        },
    )


def maximize_mechanism_diversity(candidates: Sequence[PortfolioCandidate]) -> ObjectiveEvaluation:
    source_backed = [
        candidate
        for candidate in candidates
        if candidate.mechanism_label and _has_source_backed_support(candidate)
    ]
    mechanisms = {str(candidate.mechanism_label).lower() for candidate in source_backed}
    score = _fraction(len(mechanisms), len(source_backed))
    return _evaluation(
        score,
        (
            "Rewards distinct mechanism labels only when candidates have source-backed "
            "support signals."
        ),
        {
            "distinct_source_backed_mechanisms": float(len(mechanisms)),
            "source_backed_mechanism_candidates": float(len(source_backed)),
        },
    )


def minimize_correlated_risk(candidates: Sequence[PortfolioCandidate]) -> ObjectiveEvaluation:
    risk_modes = Counter(
        mode for candidate in candidates for mode in _candidate_risk_modes(candidate)
    )
    repeated_modes = sum(max(0, count - 1) for count in risk_modes.values())
    denominator = max(1, sum(risk_modes.values()))
    overlap_penalty = repeated_modes / denominator
    blocking_penalty = _fraction(
        sum(bool(candidate.blocking_risks) for candidate in candidates),
        len(candidates),
    )
    score = 1.0 - (0.70 * overlap_penalty + 0.30 * blocking_penalty)
    return _evaluation(
        score,
        (
            "Penalizes shared risk-mode annotations and blocking-risk overlap without "
            "characterizing candidate safety."
        ),
        {
            "risk_mode_count": float(len(risk_modes)),
            "repeated_risk_mode_fraction": _clamp(overlap_penalty),
            "blocking_risk_fraction": blocking_penalty,
        },
    )


def minimize_generated_overexposure(
    candidates: Sequence[PortfolioCandidate],
    *,
    generated_only_limit: float = 0.4,
) -> ObjectiveEvaluation:
    generated_only_fraction = _fraction(
        sum(candidate.generated_without_direct_evidence for candidate in candidates),
        len(candidates),
    )
    if generated_only_fraction <= generated_only_limit:
        score = 1.0
    else:
        score = 1.0 - (
            (generated_only_fraction - generated_only_limit)
            / max(0.001, 1.0 - generated_only_limit)
        )
    return _evaluation(
        score,
        "Penalizes over-reliance on generated-only computational hypotheses.",
        {
            "generated_without_direct_evidence_fraction": generated_only_fraction,
            "generated_only_limit": _clamp(generated_only_limit),
        },
    )


def maximize_experimental_followup_value(
    candidates: Sequence[PortfolioCandidate],
) -> ObjectiveEvaluation:
    values = [_candidate_experimental_followup_value(candidate) for candidate in candidates]
    score = _mean(values)
    return _evaluation(
        score,
        "Balances readiness, uncertainty, review priority, and evidence gaps for follow-up triage.",
        {
            "mean_candidate_signal": score,
            "mean_readiness": _mean(
                _value(candidate.experiment_readiness_score) for candidate in candidates
            ),
            "mean_uncertainty": _mean(
                _value(candidate.uncertainty_score) for candidate in candidates
            ),
            "mean_review_priority": _mean(
                _review_priority(candidate.review_status) for candidate in candidates
            ),
        },
    )


OBJECTIVE_FUNCTIONS: dict[str, PortfolioObjectiveFunction] = {
    "evidence_strength": maximize_evidence_strength,
    "maximize_evidence_strength": maximize_evidence_strength,
    "experiment_readiness": maximize_experiment_readiness,
    "maximize_experiment_readiness": maximize_experiment_readiness,
    "learning_value": maximize_learning_value,
    "maximize_learning_value": maximize_learning_value,
    "developability": maximize_developability,
    "maximize_developability": maximize_developability,
    "target_coverage": maximize_target_coverage,
    "maximize_target_coverage": maximize_target_coverage,
    "scaffold_diversity": maximize_scaffold_diversity,
    "maximize_scaffold_diversity": maximize_scaffold_diversity,
    "mechanism_diversity": maximize_mechanism_diversity,
    "maximize_mechanism_diversity": maximize_mechanism_diversity,
    "correlated_risk": minimize_correlated_risk,
    "minimize_correlated_risk": minimize_correlated_risk,
    "generated_overexposure": minimize_generated_overexposure,
    "minimize_generated_overexposure": minimize_generated_overexposure,
    "experimental_followup_value": maximize_experimental_followup_value,
    "maximize_experimental_followup_value": maximize_experimental_followup_value,
}


def explain_objectives(
    candidates: Sequence[PortfolioCandidate],
    objectives: Sequence[PortfolioObjective] | None = None,
) -> dict[str, dict[str, Any]]:
    selected_objectives = objectives or default_objectives()
    return {
        objective.objective_id: _evaluate_objective(candidates, objective).as_dict()
        for objective in selected_objectives
    }


def candidate_objective_score(
    candidate: PortfolioCandidate,
    objectives: Sequence[PortfolioObjective],
    *,
    weight_overrides: Mapping[str, float] | None = None,
) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for objective in objectives:
        raw_value = _candidate_metric_value(candidate, objective.metric_name)
        if objective.direction == "lower_is_better":
            value = 1.0 - raw_value
        elif objective.direction == "categorical":
            value = 1.0 if raw_value > 0 else 0.0
        else:
            value = raw_value
        weight = objective.weight * float((weight_overrides or {}).get(objective.objective_id, 1.0))
        weighted_sum += weight * value
        weight_total += weight
    if weight_total <= 0:
        return 0.0
    score = weighted_sum / weight_total
    if candidate.generated_without_direct_evidence:
        score = min(score, 0.72)
    if candidate.blocking_risks:
        score = min(score, 0.25)
    return _round_score(score)


def aggregate_objective_scores(
    candidates: Sequence[PortfolioCandidate],
    objectives: Sequence[PortfolioObjective],
) -> dict[str, float]:
    return {
        objective.objective_id: _evaluate_objective(candidates, objective).score
        for objective in objectives
    }


def _evaluate_objective(
    candidates: Sequence[PortfolioCandidate],
    objective: PortfolioObjective,
) -> ObjectiveEvaluation:
    function = OBJECTIVE_FUNCTIONS.get(objective.metric_name) or OBJECTIVE_FUNCTIONS.get(
        objective.objective_id
    )
    if function is not None:
        return function(candidates)
    if not candidates:
        return _evaluation(
            0.0,
            f"Mean value for custom metric '{objective.metric_name}'.",
            {"candidate_count": 0.0},
        )
    score = _mean(
        _candidate_metric_value(candidate, objective.metric_name) for candidate in candidates
    )
    if objective.direction == "lower_is_better":
        score = 1.0 - score
    elif objective.direction == "categorical":
        score = 1.0 if score > 0 else 0.0
    return _evaluation(
        score,
        f"Mean value for custom metric '{objective.metric_name}'.",
        {"candidate_count": float(len(candidates))},
    )


def _candidate_metric_value(candidate: PortfolioCandidate, metric_name: str) -> float:
    proxy_functions: dict[str, Callable[[PortfolioCandidate], float]] = {
        "evidence_strength": _candidate_evidence_strength,
        "maximize_evidence_strength": _candidate_evidence_strength,
        "experiment_readiness": _candidate_experiment_readiness,
        "maximize_experiment_readiness": _candidate_experiment_readiness,
        "learning_value": _candidate_learning_value,
        "maximize_learning_value": _candidate_learning_value,
        "developability": _candidate_developability,
        "maximize_developability": _candidate_developability,
        "target_coverage": lambda candidate: 1.0 if candidate.target_symbols else 0.0,
        "maximize_target_coverage": lambda candidate: 1.0 if candidate.target_symbols else 0.0,
        "scaffold_diversity": lambda candidate: 1.0 if _scaffold_key(candidate) else 0.0,
        "maximize_scaffold_diversity": lambda candidate: 1.0 if _scaffold_key(candidate) else 0.0,
        "mechanism_diversity": lambda candidate: (
            1.0 if candidate.mechanism_label and _has_source_backed_support(candidate) else 0.0
        ),
        "maximize_mechanism_diversity": lambda candidate: (
            1.0 if candidate.mechanism_label and _has_source_backed_support(candidate) else 0.0
        ),
        "correlated_risk": lambda candidate: 1.0 - _candidate_risk_burden(candidate),
        "minimize_correlated_risk": lambda candidate: 1.0 - _candidate_risk_burden(candidate),
        "generated_overexposure": lambda candidate: (
            0.0 if candidate.generated_without_direct_evidence else 1.0
        ),
        "minimize_generated_overexposure": lambda candidate: (
            0.0 if candidate.generated_without_direct_evidence else 1.0
        ),
        "experimental_followup_value": _candidate_experimental_followup_value,
        "maximize_experimental_followup_value": _candidate_experimental_followup_value,
    }
    proxy = proxy_functions.get(metric_name)
    if proxy is not None:
        return _clamp(proxy(candidate))
    value: Any = getattr(candidate, metric_name, None)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return _clamp(float(value))
    return 0.0


def _candidate_evidence_strength(candidate: PortfolioCandidate) -> float:
    evidence = _value(candidate.evidence_score)
    experimental = _value(candidate.experimental_support_score)
    return _clamp(0.60 * evidence + 0.40 * experimental)


def _candidate_experiment_readiness(candidate: PortfolioCandidate) -> float:
    readiness = _value(candidate.experiment_readiness_score)
    review = _review_status_score(candidate.review_status)
    no_blocking = 0.0 if candidate.blocking_risks else 1.0
    score = 0.65 * readiness + 0.25 * review + 0.10 * no_blocking
    if candidate.blocking_risks:
        score = min(score, 0.35)
    return _clamp(score)


def _candidate_learning_value(candidate: PortfolioCandidate) -> float:
    uncertainty = _value(candidate.uncertainty_score)
    evidence_gap = float(
        candidate.evidence_score is None
        and candidate.experimental_support_score is None
        and not candidate.direct_experimental_evidence
    )
    active_learning = _active_learning_signal(candidate)
    diversity_annotation = float(bool(candidate.target_symbols) or bool(_scaffold_key(candidate)))
    return _clamp(
        0.40 * uncertainty
        + 0.25 * evidence_gap
        + 0.25 * active_learning
        + 0.10 * diversity_annotation
    )


def _candidate_developability(candidate: PortfolioCandidate) -> float:
    developability = _value(candidate.developability_score, missing=0.5)
    risk_penalty = _candidate_risk_burden(candidate)
    return _clamp(developability - 0.55 * risk_penalty)


def _candidate_experimental_followup_value(candidate: PortfolioCandidate) -> float:
    readiness = _value(candidate.experiment_readiness_score)
    uncertainty = _value(candidate.uncertainty_score)
    review = _review_priority(candidate.review_status)
    evidence_gap = float(
        candidate.experimental_support_score is None and not candidate.direct_experimental_evidence
    )
    score = 0.40 * readiness + 0.30 * uncertainty + 0.20 * review + 0.10 * evidence_gap
    if candidate.blocking_risks:
        score = min(score, 0.35)
    return _clamp(score)


def _review_status_score(status: str | None) -> float:
    if status is None:
        return 0.0
    normalized = status.lower().replace("-", "_").replace(" ", "_")
    if normalized in {"approved", "expert_approved", "reviewed", "triaged", "ready"}:
        return 1.0
    if normalized in {"needs_review", "pending", "queued", "in_review"}:
        return 0.55
    if normalized in {"hold", "deprioritize", "deprioritized", "reject", "rejected"}:
        return 0.1
    return 0.3


def _review_priority(status: str | None) -> float:
    if status is None:
        return 0.4
    normalized = status.lower().replace("-", "_").replace(" ", "_")
    if normalized in {"needs_review", "pending", "queued", "in_review"}:
        return 0.9
    if normalized in {"approved", "expert_approved", "reviewed", "triaged", "ready"}:
        return 0.75
    if normalized in {"hold", "deprioritize", "deprioritized", "reject", "rejected"}:
        return 0.1
    return 0.5


def _active_learning_signal(candidate: PortfolioCandidate) -> float:
    records = candidate.metadata.get("active_learning_suggestions")
    if not isinstance(records, list):
        records = [candidate.metadata.get("active_learning")]
    scores: list[float] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        score = _first_score(
            record.get("expected_information_gain"),
            record.get("acquisition_score"),
            record.get("learning_value"),
            record.get("priority_score"),
            record.get("uncertainty_score"),
            record.get("score"),
        )
        if score is not None:
            scores.append(score)
    return _mean(scores)


def _portfolio_diversity_signal(candidates: Sequence[PortfolioCandidate]) -> float:
    if not candidates:
        return 0.0
    target_score = maximize_target_coverage(candidates).score
    scaffold_score = maximize_scaffold_diversity(candidates).score
    return _clamp((target_score + scaffold_score) / 2.0)


def _candidate_risk_modes(candidate: PortfolioCandidate) -> set[str]:
    modes = {
        _normalize_mode(flag) for flag in [*candidate.risk_flags, *candidate.blocking_risks] if flag
    }
    metadata = candidate.metadata
    for key in ("scaffold_liabilities", "safety_concerns", "risk_modes"):
        value = metadata.get(key)
        if isinstance(value, list):
            modes.update(_normalize_mode(str(item)) for item in value if item)
        elif value:
            modes.add(_normalize_mode(str(value)))
    scaffold = _scaffold_key(candidate)
    if scaffold and any("liabil" in mode for mode in modes):
        modes.add(f"scaffold_liability:{scaffold.lower()}")
    return {mode for mode in modes if mode}


def _candidate_risk_burden(candidate: PortfolioCandidate) -> float:
    flag_penalty = min(0.45, 0.08 * len(candidate.risk_flags))
    blocking_penalty = min(0.45, 0.25 * len(candidate.blocking_risks))
    metadata_penalty = min(0.20, 0.05 * len(_candidate_risk_modes(candidate)))
    return _clamp(flag_penalty + blocking_penalty + metadata_penalty)


def _normalize_mode(value: str) -> str:
    return "_".join(value.lower().strip().split())


def _has_source_backed_support(candidate: PortfolioCandidate) -> bool:
    return bool(
        candidate.direct_experimental_evidence
        or candidate.evidence_score is not None
        or candidate.experimental_support_score is not None
    )


def _scaffold_key(candidate: PortfolioCandidate) -> str | None:
    return candidate.scaffold_id or candidate.chemical_series_id


def _first_score(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float):
            return _clamp(float(value))
    return None


def _value(value: float | None, *, missing: float = 0.0) -> float:
    return _clamp(float(value) if value is not None else missing)


def _mean(values: Sequence[float] | Any) -> float:
    concrete = [float(value) for value in values]
    if not concrete:
        return 0.0
    return _clamp(sum(concrete) / len(concrete))


def _fraction(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(float(numerator) / float(denominator))


def _evaluation(
    score: float,
    explanation: str,
    components: Mapping[str, float],
) -> ObjectiveEvaluation:
    return ObjectiveEvaluation(
        score=_round_score(score),
        explanation=explanation,
        components={key: _round_score(value) for key, value in components.items()},
    )


def _round_score(value: float) -> float:
    return round(_clamp(value), 3)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
