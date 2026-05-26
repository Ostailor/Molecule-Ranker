from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from molecule_ranker.review.schemas import (
    CandidateDossier,
    FollowupRequest,
    Reviewer,
    ReviewerComment,
    ReviewerDecision,
    ReviewItem,
    ReviewWorkspace,
    ValidationHandoff,
)
from molecule_ranker.review.workspace import ReviewWorkspaceStore

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _reviewer(reviewer_id: str = "reviewer-1") -> Reviewer:
    return Reviewer(
        reviewer_id=reviewer_id,
        name="Local Reviewer",
        role="medicinal_chemist",
    )


def _workspace() -> ReviewWorkspace:
    item = ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbols=["MAOB"],
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        score=0.65,
        confidence=0.75,
        evidence_summary={"records": 3},
        literature_summary={"papers": 2},
        developability_summary={"risk": "medium"},
        generation_summary=None,
        risk_flags=["developability_risk"],
        warnings=["Computational triage only."],
        priority_bucket="high_priority",
        review_status="pending",
    )
    return ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=FIXED_TIME,
        review_items=[item],
    )


def _decision(item_id: str, reviewer_id: str = "reviewer-1") -> ReviewerDecision:
    return ReviewerDecision(
        review_item_id=item_id,
        reviewer=_reviewer(reviewer_id),
        decision="needs_more_data",
        rationale="Needs independent literature follow-up.",
        confidence=0.7,
        decision_factors=["weak_literature"],
        created_at=FIXED_TIME,
    )


def test_store_creates_tables_and_workspace(tmp_path):
    db_path = tmp_path / "review.sqlite"
    store = ReviewWorkspaceStore(db_path)
    workspace = store.create_workspace(_workspace())

    loaded = store.get_workspace(workspace.workspace_id)
    summaries = store.list_workspaces()

    assert loaded.workspace_id == workspace.workspace_id
    assert loaded.review_items[0].candidate_name == "Rasagiline"
    assert loaded.review_items[0].priority_bucket == "high_priority"
    assert summaries[0].workspace_id == workspace.workspace_id
    assert summaries[0].review_item_count == 1
    assert summaries[0].pending_count == 1

    with sqlite3.connect(db_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
    assert {
        "review_workspaces",
        "review_items",
        "reviewer_decisions",
        "reviewer_comments",
        "followup_requests",
        "candidate_dossiers",
        "validation_handoffs",
        "audit_events",
    } <= table_names


def test_store_reloads_decisions_comments_followups_and_payload_tables(tmp_path):
    store = ReviewWorkspaceStore(tmp_path / "review.sqlite")
    workspace = store.create_workspace(_workspace())
    item_id = workspace.review_items[0].review_item_id
    reviewer = _reviewer()

    first_decision = _decision(item_id)
    second_decision = ReviewerDecision(
        review_item_id=item_id,
        reviewer=reviewer,
        decision="hold",
        rationale="Append-only second opinion.",
        confidence=0.4,
        decision_factors=["developability_risk"],
        created_at=FIXED_TIME.replace(hour=4),
    )
    comment = ReviewerComment(
        review_item_id=item_id,
        reviewer=reviewer,
        comment_text="Ask for more literature context.",
        comment_type="evidence_question",
        created_at=FIXED_TIME,
    )
    followup = FollowupRequest(
        review_item_id=item_id,
        requested_by=reviewer,
        request_type="rerun_with_more_literature",
        request_text="Retrieve additional literature evidence.",
        priority="high",
        status="open",
        created_at=FIXED_TIME,
    )

    store.add_decision(workspace.workspace_id, first_decision)
    store.add_decision(workspace.workspace_id, second_decision)
    store.add_comment(workspace.workspace_id, comment)
    store.add_followup_request(workspace.workspace_id, followup)
    store.add_candidate_dossier(
        workspace.workspace_id,
        CandidateDossier(
            review_item_id=item_id,
            disease_name=workspace.disease_name,
            candidate_name="Rasagiline",
            candidate_origin="existing",
            executive_summary="Research triage dossier.",
            evidence_sections=[],
            risk_sections=[],
            reviewer_decisions=[first_decision],
            reviewer_comments=[comment],
            generated_at=FIXED_TIME,
        ),
    )
    store.add_validation_handoff(
        workspace.workspace_id,
        ValidationHandoff(
            review_item_id=item_id,
            candidate_name="Rasagiline",
            candidate_origin="existing",
            disease_name=workspace.disease_name,
            target_symbols=["MAOB"],
            validation_questions=["Is the target rationale strong enough for follow-up?"],
            suggested_assay_classes=["biochemical target engagement assay"],
            required_expert_reviews=["pharmacologist"],
            key_risks_to_check=["developability_risk"],
            evidence_packet_paths={"dossier": "dossier.md"},
            disclaimer="Research validation planning only; no protocols.",
            created_at=FIXED_TIME,
        ),
    )

    loaded = store.get_workspace(workspace.workspace_id)

    assert [decision.decision for decision in loaded.decisions] == [
        "needs_more_data",
        "hold",
    ]
    assert loaded.comments[0].comment_text == "Ask for more literature context."
    assert loaded.followup_requests[0].request_type == "rerun_with_more_literature"
    assert len(loaded.audit_events) >= 6

    with sqlite3.connect(tmp_path / "review.sqlite") as connection:
        decision_rows = connection.execute(
            "select reviewer_id, decision from reviewer_decisions order by created_at"
        ).fetchall()
        dossier_count = connection.execute("select count(*) from candidate_dossiers").fetchone()[0]
        handoff_count = connection.execute("select count(*) from validation_handoffs").fetchone()[0]
    assert decision_rows == [("reviewer-1", "needs_more_data"), ("reviewer-1", "hold")]
    assert dossier_count == 1
    assert handoff_count == 1


def test_status_update_writes_audit_event(tmp_path):
    store = ReviewWorkspaceStore(tmp_path / "review.sqlite")
    workspace = store.create_workspace(_workspace())
    item_id = workspace.review_items[0].review_item_id

    store.update_review_status(
        workspace.workspace_id,
        item_id,
        "needs_more_data",
        actor="reviewer-1",
    )

    loaded = store.get_workspace(workspace.workspace_id)

    assert loaded.review_items[0].review_status == "needs_more_data"
    assert loaded.audit_events[-1].event_type == "review_status_updated"
    assert loaded.audit_events[-1].before == {"review_status": "pending"}
    assert loaded.audit_events[-1].after == {"review_status": "needs_more_data"}


def test_export_import_round_trip(tmp_path):
    source_store = ReviewWorkspaceStore(tmp_path / "source.sqlite")
    workspace = source_store.create_workspace(_workspace())
    source_store.add_decision(
        workspace.workspace_id,
        _decision(workspace.review_items[0].review_item_id),
    )
    export_path = source_store.export_workspace_json(
        workspace.workspace_id,
        tmp_path / "workspace.json",
    )

    imported_store = ReviewWorkspaceStore(tmp_path / "imported.sqlite")
    imported = imported_store.import_workspace_json(export_path)
    reloaded = imported_store.get_workspace(imported.workspace_id)

    assert export_path.exists()
    assert reloaded.workspace_id == workspace.workspace_id
    assert reloaded.review_items[0].candidate_name == "Rasagiline"
    assert reloaded.decisions[0].decision == "needs_more_data"
    assert any(event.event_type == "workspace_imported" for event in reloaded.audit_events)
