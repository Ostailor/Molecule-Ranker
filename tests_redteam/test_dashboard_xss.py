from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.review.dashboard import generate_static_review_dashboard
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.schemas import Reviewer, ReviewItem, ReviewWorkspace


def test_reviewer_comment_xss_is_escaped_in_dashboard(tmp_path) -> None:
    item = ReviewItem(
        run_id="run-redteam",
        disease_name="Synthetic Disease",
        candidate_id="candidate-redteam",
        candidate_name="Synthetic Candidate",
        candidate_origin="existing",
        target_symbols=["SYN1"],
        canonical_smiles="CCO",
        score=0.1,
        confidence=0.1,
        evidence_summary={"items": [], "score_breakdown": {}},
        literature_summary={"items": [], "claim_counts": {}},
        developability_summary={},
        generation_summary=None,
        risk_flags=[],
        warnings=[],
        priority_bucket="needs_review",
        review_status="pending",
    )
    workspace = ReviewWorkspace(
        run_id="run-redteam",
        disease_name="Synthetic Disease",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        review_items=[item],
    )
    ReviewDecisionEngine().add_comment(
        workspace,
        review_item_id=item.review_item_id,
        reviewer=Reviewer(reviewer_id="reviewer-redteam"),
        comment_text="Reviewer comment <script>alert('redteam')</script>",
        comment_type="general",
    )

    output_dir = generate_static_review_dashboard(workspace, tmp_path / "dashboard")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in output_dir.rglob("*.html"))

    assert "<script>alert" not in combined
    assert "&lt;script&gt;alert" in combined
