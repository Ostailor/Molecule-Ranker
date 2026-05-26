from __future__ import annotations

from datetime import UTC, datetime

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.review.dashboard import generate_static_review_dashboard
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.schemas import Reviewer, ReviewItem, ReviewWorkspace
from molecule_ranker.review.workspace import ReviewWorkspaceStore


def _workspace() -> ReviewWorkspace:
    item = ReviewItem(
        run_id="run-1",
        disease_name="Parkinson <script>alert(1)</script>",
        candidate_id="candidate-1",
        candidate_name="Unsafe <script>alert('x')</script>",
        candidate_origin="existing",
        target_symbols=["MAOB", "<img src=x onerror=alert(1)>"],
        canonical_smiles="CCO",
        score=0.72,
        confidence=0.68,
        evidence_summary={
            "score_breakdown": {"final_score": 0.72, "confidence": 0.68},
            "target_evidence_count": 2,
            "molecule_evidence_count": 1,
            "literature_claim_counts": {"supports": 1, "contradicts": 0, "mentions": 0},
            "safety_warning_count": 1,
            "developability_risk_level": "medium",
            "items": [{"summary": "Evidence <b>summary</b>"}],
        },
        literature_summary={
            "items": [
                {
                    "title": "Citation <script>alert(2)</script>",
                    "url": "https://example.org/paper",
                    "abstract": "Full abstract should not be copied.",
                }
            ],
            "claim_counts": {"supports": 1, "contradicts": 0, "mentions": 0},
        },
        developability_summary={"risk_level": "medium", "structure_available": True},
        generation_summary=None,
        risk_flags=["safety_risk"],
        warnings=["Warning <script>alert(3)</script>"],
        priority_bucket="medium_priority",
        review_status="pending",
    )
    workspace = ReviewWorkspace(
        run_id="run-1",
        disease_name=item.disease_name,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        review_items=[item],
    )
    ReviewDecisionEngine().add_comment(
        workspace,
        review_item_id=item.review_item_id,
        reviewer=Reviewer(reviewer_id="expert-1"),
        comment_text="Comment <script>alert(4)</script>",
        comment_type="general",
    )
    return workspace


def test_static_dashboard_generates_pages_and_escapes_dangerous_html(tmp_path):
    workspace = _workspace()

    output_dir = generate_static_review_dashboard(workspace, tmp_path / "dashboard")

    expected = [
        output_dir / "index.html",
        output_dir / "queue.html",
        output_dir / "audit.html",
        output_dir / "compare.html",
        output_dir / "candidates" / f"{workspace.review_items[0].review_item_id}.html",
    ]
    for path in expected:
        assert path.exists(), path

    combined = "\n".join(path.read_text() for path in expected)
    assert "<script>alert" not in combined
    assert "&lt;script&gt;alert" in combined
    assert "Full abstract should not be copied." not in combined
    assert "https://example.org/paper" in combined
    assert "Expert triage only" in combined


def test_review_dashboard_cli_generates_static_site_from_sqlite_workspace(tmp_path):
    workspace = _workspace()
    db_path = tmp_path / "review.sqlite"
    output_dir = tmp_path / "review_dashboard"
    ReviewWorkspaceStore(db_path).create_workspace(workspace)

    result = CliRunner().invoke(
        app,
        [
            "review",
            "dashboard",
            workspace.workspace_id,
            "--db-path",
            str(db_path),
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (output_dir / "index.html").exists()
    assert (output_dir / "queue.html").exists()
