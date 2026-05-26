from __future__ import annotations

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.review.comparison import build_candidate_comparison
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.schemas import Reviewer, ReviewItem, ReviewWorkspace
from molecule_ranker.review.workspace import ReviewWorkspaceStore


def _workspace() -> ReviewWorkspace:
    existing = ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbols=["MAOB"],
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        score=0.82,
        confidence=0.76,
        evidence_summary={
            "score_breakdown": {"final_score": 0.82, "confidence": 0.76},
            "target_evidence_count": 4,
            "molecule_evidence_count": 3,
            "literature_claim_counts": {"supports": 2, "contradicts": 0, "mentions": 1},
            "safety_warning_count": 1,
            "developability_risk_level": "low",
            "generated_score": None,
        },
        literature_summary={"claim_counts": {"supports": 2, "contradicts": 0, "mentions": 1}},
        developability_summary={"risk_level": "low", "structure_available": True},
        generation_summary=None,
        risk_flags=[],
        warnings=["Research triage only."],
        priority_bucket="high_priority",
        review_status="pending",
    )
    generated = ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="generated-1",
        candidate_name="Generated-MAOB-001",
        candidate_origin="generated",
        target_symbols=["MAOB", "COMT"],
        canonical_smiles="CCOC1=CC=CC=C1",
        score=0.63,
        confidence=None,
        evidence_summary={
            "score_breakdown": None,
            "target_evidence_count": 2,
            "molecule_evidence_count": 0,
            "literature_claim_counts": {"supports": 0, "contradicts": 0, "mentions": 0},
            "safety_warning_count": 0,
            "developability_risk_level": "unknown",
            "generated_score": 0.63,
        },
        literature_summary={"claim_counts": {"supports": 0, "contradicts": 0, "mentions": 0}},
        developability_summary={"risk_level": "unknown", "structure_available": True},
        generation_summary={"generation_score": 0.63, "target_symbol": "MAOB"},
        risk_flags=["generated_no_direct_evidence"],
        warnings=["Generated molecule hypothesis; no direct activity evidence."],
        priority_bucket="needs_review",
        review_status="pending",
    )
    workspace = ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        review_items=[existing, generated],
    )
    ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=existing.review_item_id,
        reviewer=Reviewer(reviewer_id="expert-1"),
        decision="needs_more_data",
        rationale="Needs another literature check.",
        confidence=0.7,
        decision_factors=["weak_literature"],
    )
    return workspace


def test_candidate_comparison_includes_side_by_side_fields_without_winner():
    workspace = _workspace()
    comparison = build_candidate_comparison(
        workspace,
        [item.review_item_id for item in workspace.review_items],
    )

    assert comparison.comparison_id.startswith("comparison-")
    assert comparison.shared_targets == ["MAOB"]
    assert comparison.unique_targets["Generated-MAOB-001"] == ["COMT"]
    assert comparison.comparison_table[0]["candidate_name"] == "Rasagiline"
    assert comparison.comparison_table[0]["direct_evidence_count"] == 3
    assert comparison.comparison_table[0]["reviewer_decisions"] == ["needs_more_data"]
    assert comparison.comparison_table[1]["generated_no_direct_evidence"] is True
    assert "comparison for expert review" in comparison.recommendation_summary
    assert "No automatic winner" in comparison.recommendation_summary
    assert any("Generated-MAOB-001" in item for item in comparison.differentiators)


def test_review_compare_cli_outputs_json_and_readable_summary(tmp_path):
    runner = CliRunner()
    workspace = _workspace()
    db_path = tmp_path / "review.sqlite"
    ReviewWorkspaceStore(db_path).create_workspace(workspace)
    item_ids = [item.review_item_id for item in workspace.review_items]

    json_result = runner.invoke(
        app,
        [
            "review",
            "compare",
            workspace.workspace_id,
            *item_ids,
            "--db-path",
            str(db_path),
            "--json",
        ],
    )

    assert json_result.exit_code == 0, json_result.stdout
    payload = json.loads(json_result.stdout)
    assert payload["shared_targets"] == ["MAOB"]
    assert payload["comparison_table"][1]["generated_no_direct_evidence"] is True

    text_result = runner.invoke(
        app,
        [
            "review",
            "compare",
            workspace.workspace_id,
            *item_ids,
            "--db-path",
            str(db_path),
        ],
    )

    assert text_result.exit_code == 0, text_result.stdout
    assert "comparison for expert review" in text_result.stdout
    assert "Rasagiline" in text_result.stdout
    assert "Generated-MAOB-001" in text_result.stdout
