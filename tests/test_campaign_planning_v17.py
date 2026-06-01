from __future__ import annotations

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from molecule_ranker.campaign import (
    CampaignBudget,
    CampaignEvent,
    CampaignPlanner,
    CampaignResourceEstimate,
    render_campaign_dashboard_html,
    render_campaign_memo_markdown,
    validate_campaign_guardrails,
)
from molecule_ranker.cli import app
from molecule_ranker.hypotheses.schemas import ResearchHypothesis
from molecule_ranker.portfolio.schemas import PortfolioCandidate


def test_v17_campaign_planner_builds_budget_fit_work_packages_and_gates() -> None:
    plan = CampaignPlanner(
        budget=CampaignBudget(
            budget_id="budget-q3",
            max_work_packages=2,
            max_assay_slots=1,
            max_review_slots=1,
            max_computation_slots=1,
            max_total_cost=2500.0,
            cost_units="USD",
        )
    ).plan(
        hypotheses=[_hypothesis("hypothesis:mechanism", priority_score=0.91)],
        candidates=[
            _candidate(
                "candidate:review",
                origin="existing",
                resource_estimate={
                    "assay_slots": 0,
                    "review_slots": 1,
                    "computation_slots": 0,
                    "estimated_cost": 500.0,
                    "cost_provenance_ids": ["finance:review-rate-card"],
                },
            ),
            _candidate(
                "candidate:assay",
                origin="existing",
                resource_estimate={
                    "assay_slots": 1,
                    "review_slots": 0,
                    "computation_slots": 0,
                    "estimated_cost": 2000.0,
                    "cost_provenance_ids": ["finance:assay-slot-estimate"],
                },
            ),
        ],
        campaign_id="campaign:v17",
    )

    assert plan.schema_version == "1.7"
    assert [package.status for package in plan.work_packages] == ["ready_for_review", "planned"]
    assert plan.budget_fit.within_budget is True
    assert plan.budget_fit.assay_slots_used == 1
    assert plan.budget_fit.review_slots_used == 1
    assert plan.budget_fit.total_cost == 2500.0
    assert plan.allocations[0].review_slot_ids == ["review-slot-1"]
    assert plan.allocations[1].assay_slot_ids == ["assay-slot-1"]
    assert all(package.review_gate.required for package in plan.work_packages)
    assert all(package.not_lab_protocol for package in plan.work_packages)
    assert all(package.provenance_ids for package in plan.work_packages)
    assert plan.deferred_work_packages == []
    assert plan.audit_trail[0].event_type == "campaign_plan_created"


def test_v17_campaign_planner_defers_under_budget_and_computes_opportunity_cost() -> None:
    plan = CampaignPlanner(
        budget=CampaignBudget(
            max_work_packages=1,
            max_assay_slots=0,
            max_review_slots=1,
            max_computation_slots=1,
        )
    ).plan(
        hypotheses=[_hypothesis("hypothesis:learning", priority_score=0.8)],
        candidates=[
            _candidate("candidate:review", origin="existing", review_status="needs_review"),
            _candidate("candidate:generated", origin="generated", uncertainty_score=0.95),
        ],
    )

    assert len(plan.work_packages) == 1
    assert plan.deferred_work_packages
    assert plan.deferred_work_packages[0].opportunity_cost_score > 0
    assert "budget_or_slot_limit" in plan.deferred_work_packages[0].defer_reason
    assert plan.budget_fit.within_budget is False


def test_v17_campaign_replan_triggers_cover_external_updates_without_inventing_outcomes() -> None:
    event = CampaignEvent(
        event_id="event:assay-import",
        event_type="assay_result_imported",
        source_artifact_ids=["assay-result:123"],
        linked_hypothesis_ids=["hypothesis:mechanism"],
        linked_candidate_ids=["candidate:review"],
        summary="Imported result changed evidence state.",
    )

    plan = CampaignPlanner().plan(
        hypotheses=[_hypothesis("hypothesis:mechanism", priority_score=0.72)],
        candidates=[_candidate("candidate:review", origin="existing")],
        events=[event],
    )

    assert plan.replan_triggers[0].trigger_type == "new_assay_result"
    assert plan.replan_triggers[0].requires_human_review is True
    assert plan.replan_triggers[0].source_event_ids == ["event:assay-import"]
    assert "activity" not in " ".join(plan.replan_triggers[0].rationale.lower().split())
    assert plan.work_packages[0].metadata["candidate_batches"][0]["source"] in {
        "hypothesis",
        "portfolio",
        "active_learning",
        "expert_review",
    }


def test_v17_campaign_memo_and_dashboard_are_guarded() -> None:
    plan = CampaignPlanner().plan(
        hypotheses=[_hypothesis("hypothesis:mechanism", priority_score=0.7)],
        candidates=[_candidate("candidate:review", origin="existing")],
    )

    memo = render_campaign_memo_markdown(plan)
    dashboard = render_campaign_dashboard_html(plan)

    assert "Campaign Memo" in memo
    assert "Expected learning value" in memo
    assert "Opportunity cost" in memo
    assert "Campaign dashboard" in dashboard
    assert "not a lab protocol" in dashboard
    assert validate_campaign_guardrails(memo) == []
    assert validate_campaign_guardrails(dashboard) == []


def test_campaign_artifacts_reject_protocol_details_and_source_free_cost_claims() -> None:
    with pytest.raises(ValidationError, match="cost provenance"):
        CampaignResourceEstimate(estimated_cost=1200.0)

    with pytest.raises(ValidationError, match="protocol-level details"):
        CampaignPlanner().plan(
            hypotheses=[_hypothesis("hypothesis:bad", priority_score=0.2)],
            candidates=[
                _candidate(
                    "candidate:bad",
                    origin="existing",
                    resource_estimate={"assay_slots": 1},
                    followup_categories=["incubate at 37 C"],
                )
            ],
        )


def test_campaign_cli_writes_plan_memo_and_dashboard(tmp_path) -> None:
    hypotheses_path = tmp_path / "hypotheses.json"
    candidates_path = tmp_path / "candidates.json"
    plan_path = tmp_path / "campaign_plan.json"
    memo_path = tmp_path / "campaign_memo.md"
    dashboard_path = tmp_path / "campaign_dashboard.html"
    hypotheses_path.write_text(
        '{"hypotheses": ['
        + _hypothesis("hypothesis:mechanism", priority_score=0.7).model_dump_json()
        + "]}",
        encoding="utf-8",
    )
    candidates_path.write_text(
        '{"portfolio_candidates": ['
        + _candidate("candidate:review", origin="existing").model_dump_json()
        + "]}",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "campaign",
            "plan",
            "--hypotheses",
            str(hypotheses_path),
            "--candidates",
            str(candidates_path),
            "--output",
            str(plan_path),
            "--memo-output",
            str(memo_path),
            "--dashboard-output",
            str(dashboard_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"schema_version": "1.7"' in plan_path.read_text()
    assert "Campaign Memo" in memo_path.read_text()
    assert "Campaign dashboard" in dashboard_path.read_text()


def _hypothesis(hypothesis_id: str, *, priority_score: float) -> ResearchHypothesis:
    return ResearchHypothesis(
        hypothesis_id=hypothesis_id,
        hypothesis_type="mechanism",
        title=hypothesis_id,
        statement="Graph-backed management planning hypothesis.",
        target_entity_ids=["target:T1"],
        supporting_relation_ids=["relation:support"],
        source_artifact_ids=["artifact:graph"],
        priority_score=priority_score,
        confidence=0.6,
        uncertainty_score=0.4,
        testability_score=0.7,
        status="accepted_for_planning",
    )


def _candidate(
    candidate_id: str,
    *,
    origin: str,
    resource_estimate: dict[str, object] | None = None,
    followup_categories: list[str] | None = None,
    review_status: str | None = None,
    uncertainty_score: float = 0.4,
) -> PortfolioCandidate:
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin=origin,  # type: ignore[arg-type]
        target_symbols=["T1"],
        evidence_score=0.6,
        developability_score=0.7,
        experimental_support_score=0.4,
        predictive_model_score=0.6,
        structure_score=0.5,
        experiment_readiness_score=0.7,
        uncertainty_score=uncertainty_score,
        novelty_score=0.5,
        review_status=review_status,
        direct_experimental_evidence=origin != "generated",
        metadata={
            "hypothesis_ids": ["hypothesis:mechanism"],
            "campaign_resource_estimate": resource_estimate or {},
            "campaign_followup_categories": followup_categories or [],
            "active_learning_suggestion_ids": ["al:suggestion-1"]
            if uncertainty_score > 0.8
            else [],
            "review_record_ids": ["review:record-1"] if review_status else [],
        },
    )
