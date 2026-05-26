from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.review.metrics import compute_review_metrics
from molecule_ranker.review.schemas import (
    CandidateOrigin,
    DecisionValue,
    FollowupRequest,
    PriorityBucket,
    Reviewer,
    ReviewerComment,
    ReviewerDecision,
    ReviewItem,
    ReviewStatus,
    ReviewWorkspace,
)
from molecule_ranker.review.workspace import ReviewWorkspaceStore

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _reviewer(reviewer_id: str, role: str = "reviewer") -> Reviewer:
    return Reviewer(reviewer_id=reviewer_id, role=role)


def _item(
    candidate_id: str,
    name: str,
    *,
    origin: CandidateOrigin = "existing",
    targets: list[str] | None = None,
    priority: PriorityBucket = "medium_priority",
    status: ReviewStatus = "pending",
    risk_flags: list[str] | None = None,
) -> ReviewItem:
    return ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id=candidate_id,
        candidate_name=name,
        candidate_origin=origin,
        target_symbols=targets or ["MAOB"],
        canonical_smiles="CCO",
        score=0.7,
        confidence=0.6,
        evidence_summary={"records": 2},
        literature_summary={"claim_counts": {"supports": 1}},
        developability_summary={"risk_level": "medium"},
        generation_summary={"method": "target_conditioned"} if origin == "generated" else None,
        risk_flags=risk_flags or [],
        warnings=[],
        priority_bucket=priority,
        review_status=status,
    )


def _decision(
    item: ReviewItem,
    decision: DecisionValue,
    *,
    reviewer_id: str = "expert-1",
    created_at: datetime = FIXED_TIME,
    factors: list[str] | None = None,
) -> ReviewerDecision:
    return ReviewerDecision(
        review_item_id=item.review_item_id,
        reviewer=_reviewer(reviewer_id),
        decision=decision,
        rationale=f"{decision} rationale.",
        confidence=0.7,
        decision_factors=factors or [],
        created_at=created_at,
    )


def test_metrics_from_empty_workspace():
    workspace = ReviewWorkspace(
        run_id="run-empty",
        disease_name="Parkinson disease",
        created_at=FIXED_TIME,
    )

    metrics = compute_review_metrics(workspace)

    assert metrics.total_review_items == 0
    assert metrics.reviewed_count == 0
    assert metrics.pending_count == 0
    assert metrics.accepted_count == 0
    assert metrics.feedback_conflict_count == 0
    assert metrics.accepted_by_origin == {"existing": 0, "generated": 0}
    assert metrics.time_to_decision == {"count": 0}


def test_metrics_from_populated_workspace_counts_decisions_and_origins():
    existing = _item(
        "CHEMBL1",
        "Accepted existing",
        targets=["MAOB", "SNCA"],
        priority="high_priority",
        status="accepted",
        risk_flags=["developability_risk"],
    )
    generated = _item(
        "GEN1",
        "Accepted generated",
        origin="generated",
        targets=["MAOB"],
        priority="medium_priority",
        status="accepted",
        risk_flags=["developability_risk"],
    )
    rejected = _item(
        "CHEMBL2",
        "Rejected existing",
        targets=["LRRK2"],
        priority="reject_suggested",
        status="rejected",
        risk_flags=["safety_risk"],
    )
    needs_data = _item(
        "CHEMBL3",
        "Needs data",
        targets=["SNCA"],
        priority="needs_review",
        status="needs_more_data",
        risk_flags=["developability_risk"],
    )
    pending = _item("CHEMBL4", "Pending existing", targets=["GBA1"])
    workspace = ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=FIXED_TIME,
        review_items=[existing, generated, rejected, needs_data, pending],
        decisions=[
            _decision(
                existing,
                "accept_for_followup",
                created_at=FIXED_TIME + timedelta(hours=1),
            ),
            _decision(
                generated,
                "accept_for_followup",
                reviewer_id="expert-2",
                created_at=FIXED_TIME + timedelta(hours=2),
            ),
            _decision(
                rejected,
                "reject",
                created_at=FIXED_TIME + timedelta(hours=3),
                factors=["safety_risk", "weak_literature"],
            ),
            _decision(
                needs_data,
                "needs_more_data",
                reviewer_id="expert-2",
                created_at=FIXED_TIME + timedelta(hours=4),
            ),
        ],
        comments=[
            ReviewerComment(
                review_item_id=existing.review_item_id,
                reviewer=_reviewer("expert-1"),
                comment_text="Review comment.",
                comment_type="general",
                created_at=FIXED_TIME,
            )
        ],
        followup_requests=[
            FollowupRequest(
                review_item_id=needs_data.review_item_id,
                requested_by=_reviewer("expert-2"),
                request_type="expert_review",
                request_text="Ask for another expert review.",
                priority="medium",
                status="open",
                created_at=FIXED_TIME,
            )
        ],
    )

    metrics = compute_review_metrics(workspace)

    assert metrics.total_review_items == 5
    assert metrics.reviewed_count == 4
    assert metrics.pending_count == 1
    assert metrics.accepted_count == 2
    assert metrics.rejected_count == 1
    assert metrics.needs_more_data_count == 1
    assert metrics.accepted_by_origin == {"existing": 1, "generated": 1}
    assert metrics.accepted_by_target == {"MAOB": 2, "SNCA": 1}
    assert metrics.accepted_by_priority_bucket == {"high_priority": 1, "medium_priority": 1}
    assert metrics.rejection_reasons == {"safety_risk": 1, "weak_literature": 1}
    assert metrics.top_recurring_risk_flags[0] == {"risk_flag": "developability_risk", "count": 3}
    assert metrics.time_to_decision["count"] == 4
    assert metrics.time_to_decision["mean_seconds"] == 9000.0
    assert metrics.reviewer_activity_summary["expert-1"]["decisions"] == 2
    assert metrics.reviewer_activity_summary["expert-1"]["comments"] == 1
    assert metrics.reviewer_activity_summary["expert-2"]["followup_requests"] == 1


def test_metrics_counts_feedback_conflicts():
    item = _item("CHEMBL1", "Conflicted candidate", status="needs_more_data")
    workspace = ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=FIXED_TIME,
        review_items=[item],
        decisions=[
            _decision(item, "accept_for_followup", reviewer_id="expert-1"),
            _decision(item, "reject", reviewer_id="expert-2", factors=["safety_risk"]),
        ],
    )

    metrics = compute_review_metrics(workspace)

    assert metrics.feedback_conflict_count == 1


def test_review_metrics_cli_json(tmp_path):
    item = _item("CHEMBL1", "Accepted existing", status="accepted")
    workspace = ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=FIXED_TIME,
        review_items=[item],
        decisions=[_decision(item, "accept_for_followup")],
    )
    db_path = tmp_path / "review.sqlite"
    ReviewWorkspaceStore(db_path).create_workspace(workspace)

    result = CliRunner().invoke(
        app,
        [
            "review",
            "metrics",
            workspace.workspace_id,
            "--db-path",
            str(db_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["workspace_id"] == workspace.workspace_id
    assert payload["accepted_count"] == 1
