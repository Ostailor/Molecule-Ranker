from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.hypotheses.schemas import ResearchHypothesis
from molecule_ranker.portfolio.schemas import PortfolioCandidate

from .schemas import (
    CampaignAuditEvent,
    CampaignBudget,
    CampaignBudgetFit,
    CampaignEvent,
    CampaignPlan,
    CampaignReplanTrigger,
    CampaignResourceEstimate,
    CampaignSlotAllocation,
    CampaignWorkPackage,
    CampaignWorkType,
    DeferredCampaignWorkPackage,
    ReviewGate,
)

_EVENT_TO_TRIGGER = {
    "assay_result_imported": "new_assay_result",
    "review_decision_recorded": "review_decision",
    "model_prediction_updated": "model_prediction_change",
    "graph_contradiction_detected": "graph_contradiction",
    "integration_data_updated": "integration_update",
}


class CampaignPlanner:
    """Deterministic V1.7 campaign planner for research-management artifacts."""

    def __init__(self, budget: CampaignBudget | None = None) -> None:
        self.budget = budget or CampaignBudget()

    def plan(
        self,
        *,
        hypotheses: Sequence[ResearchHypothesis],
        candidates: Sequence[PortfolioCandidate],
        events: Sequence[CampaignEvent] = (),
        campaign_id: str | None = None,
        title: str = "Closed-loop campaign plan",
    ) -> CampaignPlan:
        hypothesis_index = {hypothesis.hypothesis_id: hypothesis for hypothesis in hypotheses}
        packages = [
            self._build_package(candidate, hypothesis_index)
            for candidate in candidates
        ]
        ranked = sorted(
            packages,
            key=lambda package: (
                -package.priority_score,
                -package.expected_learning_value,
                package.work_package_id,
            ),
        )
        selected, deferred = self._select_under_budget(ranked)
        allocations = self._allocate_slots(selected)
        budget_fit = self._budget_fit(selected, deferred)
        resolved_campaign_id = campaign_id or _stable_id(
            "campaign",
            *(hypothesis.hypothesis_id for hypothesis in hypotheses),
            *(candidate.portfolio_candidate_id for candidate in candidates),
        )
        source_ids = sorted(
            {
                source_id
                for package in selected
                for source_id in package.provenance_ids
            }
        )
        return CampaignPlan(
            campaign_id=resolved_campaign_id,
            title=title,
            hypothesis_ids=[hypothesis.hypothesis_id for hypothesis in hypotheses],
            candidate_ids=[candidate.portfolio_candidate_id for candidate in candidates],
            work_packages=selected,
            deferred_work_packages=deferred,
            allocations=allocations,
            budget=self.budget,
            budget_fit=budget_fit,
            replan_triggers=[self._replan_trigger(event) for event in events],
            audit_trail=[
                CampaignAuditEvent(
                    event_id=f"audit-{resolved_campaign_id}",
                    event_type="campaign_plan_created",
                    summary="Deterministic campaign plan created from supplied hypotheses, "
                    "candidate artifacts, budget constraints, and events.",
                    source_artifact_ids=source_ids,
                )
            ],
            metadata={
                "deterministic_module_version": "campaign_planner.v1.7.0",
                "codex_generated_outputs": False,
                "campaign_management_artifact": True,
            },
        )

    def _build_package(
        self,
        candidate: PortfolioCandidate,
        hypothesis_index: Mapping[str, ResearchHypothesis],
    ) -> CampaignWorkPackage:
        hypothesis_ids = _candidate_hypothesis_ids(candidate, hypothesis_index)
        matched_hypotheses = [
            hypothesis_index[item] for item in hypothesis_ids if item in hypothesis_index
        ]
        resource_estimate = _resource_estimate(candidate)
        work_type = _work_type(candidate, resource_estimate)
        followup_categories = _followup_categories(candidate, work_type)
        expected_learning_value = _expected_learning_value(candidate, matched_hypotheses)
        priority_score = _priority_score(candidate, matched_hypotheses, expected_learning_value)
        status = "ready_for_review" if work_type == "expert_review" else "planned"
        provenance_ids = _candidate_provenance(candidate, matched_hypotheses)
        candidate_batches = _candidate_batches(candidate, hypothesis_ids)
        return CampaignWorkPackage(
            work_package_id=_stable_id(
                "campaign-work", candidate.portfolio_candidate_id, *hypothesis_ids
            ),
            work_type=work_type,
            title=f"Campaign work package for {candidate.portfolio_candidate_id}",
            hypothesis_ids=hypothesis_ids,
            candidate_ids=[candidate.portfolio_candidate_id],
            high_level_followup_categories=followup_categories,
            priority_score=priority_score,
            expected_learning_value=expected_learning_value,
            opportunity_cost_score=0.0,
            resource_estimate=resource_estimate,
            dependencies=_dependencies(candidate, hypothesis_ids),
            review_gate=ReviewGate(
                required=self.budget.require_human_approval,
                required_approvals=["campaign_advancement_approval"]
                if self.budget.require_human_approval
                else [],
            ),
            status=status,
            provenance_ids=provenance_ids,
            warnings=_package_warnings(candidate, resource_estimate),
            metadata={
                "candidate_origin": candidate.origin,
                "candidate_batches": candidate_batches,
                "generated_without_direct_evidence": candidate.generated_without_direct_evidence,
                "deterministic_priority": True,
                "codex_generated": False,
            },
        )

    def _select_under_budget(
        self,
        packages: Sequence[CampaignWorkPackage],
    ) -> tuple[list[CampaignWorkPackage], list[DeferredCampaignWorkPackage]]:
        selected: list[CampaignWorkPackage] = []
        deferred: list[DeferredCampaignWorkPackage] = []
        used_assay = 0
        used_review = 0
        used_compute = 0
        used_cost = 0.0

        for package in packages:
            reasons = []
            estimate = package.resource_estimate
            if (
                self.budget.max_work_packages is not None
                and len(selected) + 1 > self.budget.max_work_packages
            ):
                reasons.append("work_package_limit")
            if (
                self.budget.max_assay_slots is not None
                and used_assay + estimate.assay_slots > self.budget.max_assay_slots
            ):
                reasons.append("assay_slot_limit")
            if (
                self.budget.max_review_slots is not None
                and used_review + estimate.review_slots > self.budget.max_review_slots
            ):
                reasons.append("review_slot_limit")
            if (
                self.budget.max_computation_slots is not None
                and used_compute + estimate.computation_slots > self.budget.max_computation_slots
            ):
                reasons.append("computation_slot_limit")
            if (
                self.budget.max_total_cost is not None
                and estimate.estimated_cost is not None
                and used_cost + estimate.estimated_cost > self.budget.max_total_cost
            ):
                reasons.append("total_cost_limit")

            if reasons:
                deferred.append(_deferred(package, "budget_or_slot_limit:" + ",".join(reasons)))
                continue
            selected.append(package)
            used_assay += estimate.assay_slots
            used_review += estimate.review_slots
            used_compute += estimate.computation_slots
            used_cost += estimate.estimated_cost or 0.0

        return selected, deferred

    def _allocate_slots(
        self,
        packages: Sequence[CampaignWorkPackage],
    ) -> list[CampaignSlotAllocation]:
        allocations: list[CampaignSlotAllocation] = []
        assay_index = 0
        review_index = 0
        compute_index = 0
        for package in packages:
            estimate = package.resource_estimate
            assay_slots = []
            review_slots = []
            compute_slots = []
            for _ in range(estimate.assay_slots):
                assay_index += 1
                assay_slots.append(f"assay-slot-{assay_index}")
            for _ in range(estimate.review_slots):
                review_index += 1
                review_slots.append(f"review-slot-{review_index}")
            for _ in range(estimate.computation_slots):
                compute_index += 1
                compute_slots.append(f"compute-slot-{compute_index}")
            allocations.append(
                CampaignSlotAllocation(
                    allocation_id=f"allocation-{package.work_package_id}",
                    work_package_id=package.work_package_id,
                    assay_slot_ids=assay_slots,
                    review_slot_ids=review_slots,
                    computation_slot_ids=compute_slots,
                    allocated_cost=estimate.estimated_cost,
                    cost_provenance_ids=estimate.cost_provenance_ids,
                )
            )
        return allocations

    def _budget_fit(
        self,
        selected: Sequence[CampaignWorkPackage],
        deferred: Sequence[DeferredCampaignWorkPackage],
    ) -> CampaignBudgetFit:
        assay = sum(package.resource_estimate.assay_slots for package in selected)
        review = sum(package.resource_estimate.review_slots for package in selected)
        compute = sum(package.resource_estimate.computation_slots for package in selected)
        known_costs = [
            package.resource_estimate.estimated_cost
            for package in selected
            if package.resource_estimate.estimated_cost is not None
        ]
        unknown_cost_ids = [
            package.work_package_id
            for package in selected
            if package.resource_estimate.estimated_cost is None
        ]
        total_cost = sum(known_costs) if known_costs else None
        limiting_factors = sorted(
            {
                reason
                for item in deferred
                for reason in item.defer_reason.split(":", 1)[-1].split(",")
                if reason
            }
        )
        within_budget = not deferred and not limiting_factors
        if self.budget.max_total_cost is not None and total_cost is not None:
            within_budget = within_budget and total_cost <= self.budget.max_total_cost
        return CampaignBudgetFit(
            budget_id=self.budget.budget_id,
            within_budget=within_budget,
            work_packages_selected=len(selected),
            work_packages_deferred=len(deferred),
            assay_slots_used=assay,
            review_slots_used=review,
            computation_slots_used=compute,
            total_cost=total_cost,
            unknown_cost_work_package_ids=unknown_cost_ids,
            limiting_factors=limiting_factors,
        )

    def _replan_trigger(self, event: CampaignEvent) -> CampaignReplanTrigger:
        trigger_type = _EVENT_TO_TRIGGER[event.event_type]
        return CampaignReplanTrigger(
            trigger_id=f"replan-{event.event_id}",
            trigger_type=trigger_type,  # type: ignore[arg-type]
            source_event_ids=[event.event_id],
            linked_hypothesis_ids=event.linked_hypothesis_ids,
            linked_candidate_ids=event.linked_candidate_ids,
            rationale=(
                "Supplied campaign event changed deterministic planning inputs and requires "
                "review before campaign advancement."
            ),
            requires_human_review=self.budget.require_human_approval,
            metadata={"source_artifact_ids": event.source_artifact_ids},
        )


def _resource_estimate(candidate: PortfolioCandidate) -> CampaignResourceEstimate:
    raw = candidate.metadata.get("campaign_resource_estimate", {})
    if isinstance(raw, CampaignResourceEstimate):
        return raw
    if isinstance(raw, Mapping) and raw:
        return CampaignResourceEstimate.model_validate(dict(raw))
    if candidate.origin == "generated" and not candidate.direct_experimental_evidence:
        return CampaignResourceEstimate(review_slots=1)
    if candidate.uncertainty_score is not None and candidate.uncertainty_score > 0.8:
        return CampaignResourceEstimate(computation_slots=1, review_slots=1)
    return CampaignResourceEstimate(review_slots=1)


def _work_type(
    candidate: PortfolioCandidate,
    estimate: CampaignResourceEstimate,
) -> CampaignWorkType:
    if candidate.origin == "generated" and not _review_approved(candidate):
        return "expert_review"
    if estimate.assay_slots > 0:
        return "assay_slot_allocation"
    if estimate.computation_slots > 0 and estimate.review_slots == 0:
        return "computation_slot_allocation"
    return "expert_review"


def _followup_categories(candidate: PortfolioCandidate, work_type: str) -> list[str]:
    raw = candidate.metadata.get("campaign_followup_categories")
    if isinstance(raw, Sequence) and not isinstance(raw, str) and raw:
        return [str(item) for item in raw]
    if work_type == "assay_slot_allocation":
        return ["high-level assay category allocation", "decision-gate evidence review"]
    if work_type == "computation_slot_allocation":
        return ["model uncertainty review", "graph consistency review"]
    if candidate.origin == "generated":
        return ["generated hypothesis review", "evidence provenance review"]
    return ["expert review", "campaign decision-gate review"]


def _candidate_hypothesis_ids(
    candidate: PortfolioCandidate,
    hypothesis_index: Mapping[str, ResearchHypothesis],
) -> list[str]:
    raw = candidate.metadata.get("hypothesis_ids")
    if isinstance(raw, Sequence) and not isinstance(raw, str):
        ids = [str(item) for item in raw]
        known = [item for item in ids if item in hypothesis_index]
        if known:
            return known
    return list(hypothesis_index)[:1]


def _expected_learning_value(
    candidate: PortfolioCandidate,
    hypotheses: Sequence[ResearchHypothesis],
) -> float:
    uncertainty = candidate.uncertainty_score if candidate.uncertainty_score is not None else 0.5
    novelty = candidate.novelty_score if candidate.novelty_score is not None else 0.5
    testability = _average([hypothesis.testability_score for hypothesis in hypotheses]) or 0.5
    evidence_gap = 1.0 - (candidate.evidence_score if candidate.evidence_score is not None else 0.5)
    return _bounded(_average([uncertainty, novelty, testability, evidence_gap]) or 0.0)


def _priority_score(
    candidate: PortfolioCandidate,
    hypotheses: Sequence[ResearchHypothesis],
    expected_learning_value: float,
) -> float:
    hypothesis_priority = _average([hypothesis.priority_score for hypothesis in hypotheses]) or 0.5
    readiness = candidate.experiment_readiness_score or 0.5
    model_score = candidate.predictive_model_score or 0.5
    risk_penalty = 0.25 if candidate.blocking_risks else 0.0
    return _bounded(
        (0.35 * hypothesis_priority)
        + (0.25 * expected_learning_value)
        + (0.20 * readiness)
        + (0.20 * model_score)
        - risk_penalty
    )


def _candidate_provenance(
    candidate: PortfolioCandidate,
    hypotheses: Sequence[ResearchHypothesis],
) -> list[str]:
    provenance = {
        candidate.portfolio_candidate_id,
        *(
            hypothesis.source_artifact_ids[0]
            for hypothesis in hypotheses
            if hypothesis.source_artifact_ids
        ),
        *(
            hypothesis.supporting_relation_ids[0]
            for hypothesis in hypotheses
            if hypothesis.supporting_relation_ids
        ),
    }
    for key in ("source_artifact_ids", "provenance_ids", "review_record_ids"):
        raw = candidate.metadata.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, str):
            provenance.update(str(item) for item in raw)
    estimate = _resource_estimate(candidate)
    provenance.update(estimate.cost_provenance_ids)
    return sorted(provenance)


def _candidate_batches(
    candidate: PortfolioCandidate,
    hypothesis_ids: Sequence[str],
) -> list[dict[str, Any]]:
    batches = [{"source": "hypothesis", "ids": list(hypothesis_ids)}]
    if candidate.metadata.get("portfolio_selection_id"):
        batches.append(
            {"source": "portfolio", "ids": [str(candidate.metadata["portfolio_selection_id"])]}
        )
    raw_al = candidate.metadata.get("active_learning_suggestion_ids")
    if isinstance(raw_al, Sequence) and not isinstance(raw_al, str) and raw_al:
        batches.append({"source": "active_learning", "ids": [str(item) for item in raw_al]})
    raw_review = candidate.metadata.get("review_record_ids")
    if isinstance(raw_review, Sequence) and not isinstance(raw_review, str) and raw_review:
        batches.append({"source": "expert_review", "ids": [str(item) for item in raw_review]})
    return batches


def _dependencies(candidate: PortfolioCandidate, hypothesis_ids: Sequence[str]) -> list[str]:
    dependencies = [f"review hypothesis {hypothesis_id}" for hypothesis_id in hypothesis_ids]
    if candidate.origin == "generated" and not candidate.direct_experimental_evidence:
        dependencies.append("confirm generated candidate provenance before advancement")
    if candidate.review_status and not _review_approved(candidate):
        dependencies.append(f"resolve review status {candidate.review_status}")
    return dependencies


def _package_warnings(
    candidate: PortfolioCandidate,
    resource_estimate: CampaignResourceEstimate,
) -> list[str]:
    warnings = [
        "Campaign work package is a research-management artifact, not an execution instruction."
    ]
    if candidate.origin == "generated" and not candidate.direct_experimental_evidence:
        warnings.append("Generated candidate remains a computational hypothesis.")
    if resource_estimate.estimated_cost is None:
        warnings.append(
            "No cost value was supplied; budget fit uses available slot constraints only."
        )
    return warnings


def _deferred(package: CampaignWorkPackage, reason: str) -> DeferredCampaignWorkPackage:
    opportunity = _bounded(package.expected_learning_value * package.priority_score)
    return DeferredCampaignWorkPackage(
        work_package_id=package.work_package_id,
        priority_score=package.priority_score,
        expected_learning_value=package.expected_learning_value,
        opportunity_cost_score=opportunity,
        defer_reason=reason,
        candidate_ids=package.candidate_ids,
        hypothesis_ids=package.hypothesis_ids,
    )


def _review_approved(candidate: PortfolioCandidate) -> bool:
    return str(candidate.review_status or "").lower() in {
        "approved",
        "expert_approved",
        "reviewed",
        "triaged",
        "ready",
    }


def _average(values: Sequence[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, value))


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts if part is not None) or prefix
    return f"{prefix}:{uuid5(NAMESPACE_URL, raw).hex[:12]}"
