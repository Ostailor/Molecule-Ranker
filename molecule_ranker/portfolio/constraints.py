from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .schemas import PortfolioCandidate, PortfolioConstraint, ResourceBudget

STRICT_REVIEW_STATUSES = {
    "approved",
    "expert_approved",
    "reviewed",
    "triaged",
    "ready",
}


def default_constraints() -> list[PortfolioConstraint]:
    return [
        PortfolioConstraint(
            constraint_id="max-candidates-per-target",
            name="Maximum candidates per target",
            constraint_type="max_candidates_per_target",
            value=3,
            hard=False,
            violation_action="penalize",
            description="Avoid target over-concentration in selected research portfolios.",
        ),
        PortfolioConstraint(
            constraint_id="min-scaffold-diversity",
            name="Minimum scaffold diversity",
            constraint_type="min_scaffold_diversity",
            value=0.4,
            hard=False,
            violation_action="warn",
            description="Warn when selected candidates are concentrated in few scaffolds.",
        ),
        PortfolioConstraint(
            constraint_id="max-generated-fraction",
            name="Maximum generated-only fraction",
            constraint_type="max_generated_fraction",
            value=0.6,
            hard=False,
            violation_action="penalize",
            description="Limit over-reliance on generated-only computational hypotheses.",
        ),
        PortfolioConstraint(
            constraint_id="require-generated-review",
            name="Generated hypothesis review",
            constraint_type="require_review_approval_for_generated",
            value=True,
            hard=False,
            violation_action="require_review",
            description="Generated hypotheses require stricter human review tracking.",
        ),
        PortfolioConstraint(
            constraint_id="exclude-critical-developability-risk",
            name="Exclude critical developability risks",
            constraint_type="exclude_critical_developability_risk",
            value=True,
            hard=True,
            violation_action="reject",
            description=(
                "Reject candidates with critical developability risk annotations from "
                "automatic selection."
            ),
        ),
        PortfolioConstraint(
            constraint_id="exclude-failed-qc-only",
            name="Exclude failed-QC-only candidates",
            constraint_type="exclude_failed_qc_only_candidates",
            value=True,
            hard=True,
            violation_action="reject",
            description="Reject candidates whose linked imported evidence records all failed QC.",
        ),
        PortfolioConstraint(
            constraint_id="no-external-write-without-permission",
            name="No external write without permission",
            constraint_type="no_external_write_without_permission",
            value=True,
            hard=True,
            violation_action="reject",
            description=(
                "Block portfolio actions that would write to external systems without permission."
            ),
        ),
    ]


def group_constraints(
    constraints: Sequence[PortfolioConstraint],
) -> dict[str, list[PortfolioConstraint]]:
    grouped: dict[str, list[PortfolioConstraint]] = defaultdict(list)
    for constraint in constraints:
        grouped[_canonical_constraint_type(constraint.constraint_type)].append(constraint)
    return grouped


def constraint_allows_candidate(
    candidate: PortfolioCandidate,
    *,
    selected_count: int,
    generated_count: int,
    target_counts: Counter[str],
    series_counts: Counter[str],
    constraints: Mapping[str, Sequence[PortfolioConstraint]],
    max_count: int,
    selected_candidates: Sequence[PortfolioCandidate] = (),
    budget: ResourceBudget | None = None,
) -> bool:
    return not _candidate_hard_violations(
        candidate,
        selected_count=selected_count,
        generated_count=generated_count,
        target_counts=target_counts,
        series_counts=series_counts,
        constraints=constraints,
        max_count=max_count,
        selected_candidates=selected_candidates,
        budget=budget,
    )


def collect_constraint_violations(
    candidates: Sequence[PortfolioCandidate],
    constraints: Sequence[PortfolioConstraint],
    budget: ResourceBudget | None = None,
) -> list[dict[str, Any]]:
    grouped = group_constraints(constraints)
    violations: list[dict[str, Any]] = []

    def add(
        constraint: PortfolioConstraint,
        *,
        candidate_id: str | None = None,
        observed: Any = None,
        limit: Any = None,
        message: str,
    ) -> None:
        violations.append(
            {
                "constraint_id": constraint.constraint_id,
                "constraint_type": _canonical_constraint_type(constraint.constraint_type),
                "candidate_id": candidate_id,
                "hard": constraint.hard,
                "action": constraint.violation_action,
                "observed": observed,
                "limit": limit,
                "message": message,
            }
        )

    for constraint in _constraints_for(grouped, "max_candidates"):
        if len(candidates) > int(constraint.value):
            add(
                constraint,
                observed=len(candidates),
                limit=int(constraint.value),
                message="Selection exceeds the configured maximum candidate count.",
            )

    for constraint in _constraints_for(grouped, "min_candidates"):
        if len(candidates) < int(constraint.value):
            add(
                constraint,
                observed=len(candidates),
                limit=int(constraint.value),
                message="Selection is below the configured minimum candidate count.",
            )

    generated_count = sum(candidate.origin == "generated" for candidate in candidates)
    existing_count = sum(candidate.origin == "existing" for candidate in candidates)
    generated_only_count = sum(
        candidate.generated_without_direct_evidence for candidate in candidates
    )

    for constraint in _constraints_for(grouped, "max_generated_candidates"):
        if generated_count > int(constraint.value):
            add(
                constraint,
                observed=generated_count,
                limit=int(constraint.value),
                message="Selection exceeds the configured generated-candidate count.",
            )

    for constraint in _constraints_for(grouped, "max_generated_fraction"):
        fraction = _fraction(generated_only_count, len(candidates))
        if fraction > float(constraint.value):
            add(
                constraint,
                observed=round(fraction, 3),
                limit=float(constraint.value),
                message="Selection is over-concentrated in generated-only hypotheses.",
            )

    for constraint in _constraints_for(grouped, "min_existing_candidates"):
        if existing_count < int(constraint.value):
            add(
                constraint,
                observed=existing_count,
                limit=int(constraint.value),
                message="Selection does not include enough existing candidates.",
            )

    for constraint in _constraints_for(grouped, "exclude_critical_developability_risk"):
        for candidate in candidates:
            if _has_critical_developability_risk(candidate):
                add(
                    constraint,
                    candidate_id=candidate.portfolio_candidate_id,
                    observed=candidate.blocking_risks or candidate.risk_flags,
                    limit="no critical developability risks",
                    message="Candidate has critical developability risk annotations.",
                )

    for constraint in _constraints_for(grouped, "exclude_failed_qc_only_candidates"):
        for candidate in candidates:
            if _failed_qc_only(candidate):
                add(
                    constraint,
                    candidate_id=candidate.portfolio_candidate_id,
                    observed=_qc_statuses(candidate),
                    limit="at least one non-failed QC status",
                    message="Candidate has only failed-QC linked imported evidence records.",
                )

    for constraint in _constraints_for(grouped, "require_review_approval_for_generated"):
        for candidate in candidates:
            if candidate.origin == "generated" and not _has_review_approval(candidate):
                add(
                    constraint,
                    candidate_id=candidate.portfolio_candidate_id,
                    observed=candidate.review_status,
                    limit=sorted(STRICT_REVIEW_STATUSES),
                    message="Generated hypothesis lacks required review approval.",
                )

    for constraint in _constraints_for(
        grouped, "require_direct_experimental_evidence_for_assay_batch"
    ):
        for candidate in candidates:
            if not candidate.direct_experimental_evidence:
                add(
                    constraint,
                    candidate_id=candidate.portfolio_candidate_id,
                    observed=False,
                    limit=True,
                    message="Assay-batch selection requires exact linked experimental evidence.",
                )

    target_counts = Counter(
        target for candidate in candidates for target in candidate.target_symbols or ["unspecified"]
    )
    for constraint in _constraints_for(grouped, "min_target_coverage"):
        distinct_targets = len({target for target in target_counts if target != "unspecified"})
        if distinct_targets < int(constraint.value):
            add(
                constraint,
                observed=distinct_targets,
                limit=int(constraint.value),
                message="Selection covers fewer distinct targets than configured.",
            )

    for constraint in _constraints_for(grouped, "max_candidates_per_target"):
        limit = int(constraint.value)
        for target, count in target_counts.items():
            if count > limit:
                add(
                    constraint,
                    observed={target: count},
                    limit=limit,
                    message="Selection exceeds the per-target concentration limit.",
                )

    for constraint in _constraints_for(grouped, "min_scaffold_diversity"):
        diversity = _scaffold_diversity(candidates)
        if diversity < float(constraint.value):
            add(
                constraint,
                observed=round(diversity, 3),
                limit=float(constraint.value),
                message="Selection has lower scaffold diversity than configured.",
            )

    for constraint in _constraints_for(grouped, "max_near_duplicate_similarity"):
        max_similarity = _max_near_duplicate_similarity(candidates)
        if max_similarity > float(constraint.value):
            add(
                constraint,
                observed=round(max_similarity, 3),
                limit=float(constraint.value),
                message="Selection includes near-duplicate similarity above the configured limit.",
            )

    for constraint in _constraints_for(grouped, "no_external_write_without_permission"):
        if constraint.value and any(
            _requires_external_write(candidate) for candidate in candidates
        ):
            add(
                constraint,
                observed=True,
                limit=False,
                message="Selection includes external-write intent without explicit permission.",
            )

    violations.extend(_budget_violations(candidates, grouped=grouped, budget=budget))
    return violations


def _candidate_hard_violations(
    candidate: PortfolioCandidate,
    *,
    selected_count: int,
    generated_count: int,
    target_counts: Counter[str],
    series_counts: Counter[str],
    constraints: Mapping[str, Sequence[PortfolioConstraint]],
    max_count: int,
    selected_candidates: Sequence[PortfolioCandidate],
    budget: ResourceBudget | None,
) -> list[str]:
    violations: list[str] = []

    def hard_constraints(constraint_type: str) -> list[PortfolioConstraint]:
        return [
            constraint
            for constraint in _constraints_for(constraints, constraint_type)
            if _blocks_selection(constraint)
        ]

    if selected_count + 1 > max_count:
        violations.append("max_candidates")

    for constraint in hard_constraints("max_candidates"):
        if selected_count + 1 > int(constraint.value):
            violations.append(constraint.constraint_id)

    if _has_critical_developability_risk(candidate) and hard_constraints(
        "exclude_critical_developability_risk"
    ):
        violations.append("exclude_critical_developability_risk")

    if _failed_qc_only(candidate) and hard_constraints("exclude_failed_qc_only_candidates"):
        violations.append("exclude_failed_qc_only_candidates")

    if (
        candidate.origin == "generated"
        and not _has_review_approval(candidate)
        and hard_constraints("require_review_approval_for_generated")
    ):
        violations.append("require_review_approval_for_generated")

    if not candidate.direct_experimental_evidence and hard_constraints(
        "require_direct_experimental_evidence_for_assay_batch"
    ):
        violations.append("require_direct_experimental_evidence_for_assay_batch")

    projected_generated = generated_count + int(candidate.origin == "generated")
    for constraint in hard_constraints("max_generated_candidates"):
        if projected_generated > int(constraint.value):
            violations.append(constraint.constraint_id)

    for constraint in hard_constraints("max_generated_fraction"):
        projected_total = max(1, selected_count + 1)
        projected_generated_only = sum(
            selected.generated_without_direct_evidence for selected in selected_candidates
        ) + int(candidate.generated_without_direct_evidence)
        if projected_total >= min(2, max_count) and (
            projected_generated_only / projected_total > float(constraint.value)
        ):
            violations.append(constraint.constraint_id)

    for constraint in hard_constraints("max_candidates_per_target"):
        limit = int(constraint.value)
        if any(
            target_counts[target] >= limit for target in candidate.target_symbols or ["unspecified"]
        ):
            violations.append(constraint.constraint_id)

    for constraint in hard_constraints("max_candidates_per_chemical_series"):
        limit = int(constraint.value)
        if series_counts[_series_key(candidate)] >= limit:
            violations.append(constraint.constraint_id)

    for constraint in hard_constraints("max_near_duplicate_similarity"):
        limit = float(constraint.value)
        if any(_pair_similarity(candidate, selected) > limit for selected in selected_candidates):
            violations.append(constraint.constraint_id)

    if budget is not None:
        violations.extend(_candidate_budget_hard_violations(candidate, selected_candidates, budget))

    return violations


def _budget_violations(
    candidates: Sequence[PortfolioCandidate],
    *,
    grouped: Mapping[str, Sequence[PortfolioConstraint]],
    budget: ResourceBudget | None,
) -> list[dict[str, Any]]:
    constraints = _budget_constraints(grouped, budget)
    violations: list[dict[str, Any]] = []
    totals = _resource_totals(candidates)
    generated = sum(candidate.origin == "generated" for candidate in candidates)
    checks: list[tuple[str, Any, Any]] = [
        ("max_total_cost", totals["total_cost"], _constraint_limit(constraints, "max_total_cost")),
        (
            "max_docking_jobs",
            totals["docking_jobs"],
            _constraint_limit(constraints, "max_docking_jobs"),
        ),
        (
            "max_assay_slots",
            totals["assay_slots"],
            _constraint_limit(constraints, "max_assay_slots"),
        ),
        (
            "max_review_hours",
            totals["review_hours"],
            _constraint_limit(constraints, "max_review_hours"),
        ),
        (
            "max_generated_candidates",
            generated,
            _constraint_limit(constraints, "max_generated_candidates"),
        ),
    ]
    for constraint_type, observed, limit in checks:
        if limit is None or observed <= limit:
            continue
        constraint = _first_constraint(constraints, constraint_type)
        violations.append(
            {
                "constraint_id": constraint.constraint_id if constraint else constraint_type,
                "constraint_type": constraint_type,
                "candidate_id": None,
                "hard": True if constraint is None else constraint.hard,
                "action": "reject" if constraint is None else constraint.violation_action,
                "observed": round(observed, 3) if isinstance(observed, float) else observed,
                "limit": limit,
                "message": f"Selection exceeds configured {constraint_type} budget.",
            }
        )
    return violations


def _candidate_budget_hard_violations(
    candidate: PortfolioCandidate,
    selected_candidates: Sequence[PortfolioCandidate],
    budget: ResourceBudget,
) -> list[str]:
    projected = [*selected_candidates, candidate]
    totals = _resource_totals(projected)
    violations: list[str] = []
    checks = {
        "max_generated_candidates": (
            sum(item.origin == "generated" for item in projected),
            budget.max_generated_candidates,
        ),
        "max_total_cost": (totals["total_cost"], budget.max_total_cost),
        "max_docking_jobs": (totals["docking_jobs"], budget.max_docking_jobs),
        "max_assay_slots": (totals["assay_slots"], budget.max_assay_slots),
        "max_review_hours": (totals["review_hours"], budget.max_review_hours),
    }
    for key, (observed, limit) in checks.items():
        if limit is not None and observed > limit:
            violations.append(key)
    return violations


def _budget_constraints(
    grouped: Mapping[str, Sequence[PortfolioConstraint]],
    budget: ResourceBudget | None,
) -> dict[str, list[PortfolioConstraint]]:
    constraints = {key: list(value) for key, value in grouped.items()}
    if budget is None:
        return constraints
    budget_values = {
        "max_candidates": budget.max_candidates,
        "max_generated_candidates": budget.max_generated_candidates,
        "max_total_cost": budget.max_total_cost,
        "max_docking_jobs": budget.max_docking_jobs,
        "max_assay_slots": budget.max_assay_slots,
        "max_review_hours": budget.max_review_hours,
    }
    for constraint_type, value in budget_values.items():
        if value is None or constraints.get(constraint_type):
            continue
        constraints[constraint_type] = [
            PortfolioConstraint(
                constraint_id=f"budget-{constraint_type.replace('_', '-')}",
                name=f"Budget {constraint_type.replace('_', ' ')}",
                constraint_type=constraint_type,
                value=value,
                hard=True,
                violation_action="reject",
                description="Resource budget limit for portfolio selection.",
            )
        ]
    return constraints


def _constraint_limit(
    constraints: Mapping[str, Sequence[PortfolioConstraint]],
    constraint_type: str,
) -> Any:
    constraint = _first_constraint(constraints, constraint_type)
    return constraint.value if constraint is not None else None


def _first_constraint(
    constraints: Mapping[str, Sequence[PortfolioConstraint]],
    constraint_type: str,
) -> PortfolioConstraint | None:
    values = _constraints_for(constraints, constraint_type)
    return values[0] if values else None


def _constraints_for(
    constraints: Mapping[str, Sequence[PortfolioConstraint]],
    constraint_type: str,
) -> list[PortfolioConstraint]:
    return list(constraints.get(_canonical_constraint_type(constraint_type), []))


def _canonical_constraint_type(constraint_type: str) -> str:
    aliases = {
        "max_per_target": "max_candidates_per_target",
        "max_per_chemical_series": "max_candidates_per_chemical_series",
        "generated_limit": "max_generated_fraction",
        "generated_fraction": "max_generated_fraction",
        "exclude_critical_risk": "exclude_critical_developability_risk",
        "require_review_approval": "require_review_approval_for_generated",
        "generated_limit_count": "max_generated_candidates",
    }
    return aliases.get(constraint_type, constraint_type)


def _blocks_selection(constraint: PortfolioConstraint) -> bool:
    return constraint.hard and constraint.violation_action == "reject"


def _has_critical_developability_risk(candidate: PortfolioCandidate) -> bool:
    text = " ".join([*candidate.risk_flags, *candidate.blocking_risks]).lower()
    return "critical_developability_risk" in text or (
        "critical" in text and "developability" in text
    )


def _failed_qc_only(candidate: PortfolioCandidate) -> bool:
    statuses = _qc_statuses(candidate)
    return bool(statuses) and all(status in {"failed", "fail", "rejected"} for status in statuses)


def _qc_statuses(candidate: PortfolioCandidate) -> list[str]:
    statuses: list[str] = []
    for key in ("qc_statuses", "qc_status", "experimental_qc_statuses"):
        value = candidate.metadata.get(key)
        statuses.extend(str(item).lower() for item in _as_list(value) if item)
    for key in ("experimental_evidence_records", "experimental_results", "assay_results"):
        for record in _as_list(candidate.metadata.get(key)):
            if isinstance(record, Mapping) and record.get("qc_status"):
                statuses.append(str(record["qc_status"]).lower())
    return statuses


def _has_review_approval(candidate: PortfolioCandidate) -> bool:
    if candidate.review_status is None:
        return False
    normalized = candidate.review_status.lower().replace("-", "_").replace(" ", "_")
    return normalized in STRICT_REVIEW_STATUSES


def _requires_external_write(candidate: PortfolioCandidate) -> bool:
    metadata = candidate.metadata
    return bool(
        metadata.get("external_write_requested")
        or metadata.get("requires_external_write")
        or (
            metadata.get("external_write_permission") is False
            and metadata.get("external_integration_mappings")
        )
    )


def _resource_totals(candidates: Sequence[PortfolioCandidate]) -> dict[str, float]:
    return {
        "total_cost": sum(
            _metadata_number(candidate, "estimated_cost", "cost") for candidate in candidates
        ),
        "docking_jobs": sum(
            _metadata_number(candidate, "docking_jobs") for candidate in candidates
        ),
        "assay_slots": sum(_metadata_number(candidate, "assay_slots") for candidate in candidates),
        "review_hours": sum(
            _metadata_number(candidate, "review_hours") for candidate in candidates
        ),
    }


def _metadata_number(candidate: PortfolioCandidate, *keys: str) -> float:
    for key in keys:
        value = candidate.metadata.get(key)
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float):
            return float(value)
    return 0.0


def _scaffold_diversity(candidates: Sequence[PortfolioCandidate]) -> float:
    if not candidates:
        return 0.0
    scaffold_keys = {
        _scaffold_key(candidate) for candidate in candidates if _scaffold_key(candidate)
    }
    return len(scaffold_keys) / len(candidates)


def _max_near_duplicate_similarity(candidates: Sequence[PortfolioCandidate]) -> float:
    max_similarity = 0.0
    for index, candidate in enumerate(candidates):
        for other in candidates[index + 1 :]:
            max_similarity = max(max_similarity, _pair_similarity(candidate, other))
    return max_similarity


def _pair_similarity(candidate: PortfolioCandidate, other: PortfolioCandidate) -> float:
    pair_key = f"{other.portfolio_candidate_id}:similarity"
    reverse_pair_key = f"{candidate.portfolio_candidate_id}:similarity"
    for metadata, key in (
        (candidate.metadata, pair_key),
        (other.metadata, reverse_pair_key),
    ):
        similarities = metadata.get("pairwise_similarity")
        if isinstance(similarities, Mapping) and isinstance(similarities.get(key), int | float):
            return float(similarities[key])
    candidate_similarity = _metadata_number(candidate, "near_duplicate_similarity")
    other_similarity = _metadata_number(other, "near_duplicate_similarity")
    return max(candidate_similarity, other_similarity)


def _scaffold_key(candidate: PortfolioCandidate) -> str | None:
    return candidate.scaffold_id or candidate.chemical_series_id


def _series_key(candidate: PortfolioCandidate) -> str:
    return candidate.chemical_series_id or candidate.scaffold_id or "unspecified"


def _fraction(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, float(numerator) / float(denominator)))


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]
