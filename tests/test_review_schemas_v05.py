from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from molecule_ranker.review.schemas import (
    CandidateDossier,
    FollowupRequest,
    ReviewAuditEvent,
    Reviewer,
    ReviewerComment,
    ReviewerDecision,
    ReviewItem,
    ReviewWorkspace,
    ValidationHandoff,
)

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _reviewer() -> Reviewer:
    return Reviewer(
        reviewer_id="reviewer-1",
        name="Local Reviewer",
        role="medicinal_chemist",
        organization="MolCreate",
    )


def _item(**overrides: object) -> ReviewItem:
    payload: dict[str, Any] = {
        "run_id": "run-1",
        "disease_name": "Parkinson disease",
        "candidate_id": "CHEMBL887",
        "candidate_name": "Rasagiline",
        "candidate_origin": "existing",
        "target_symbols": ["MAOB"],
        "canonical_smiles": "C#CCN1CCC2=CC=CC=C21",
        "score": 0.65,
        "confidence": 0.75,
        "evidence_summary": {"database_records": 3},
        "literature_summary": {"papers": 2},
        "developability_summary": {"risk": "medium"},
        "generation_summary": None,
        "risk_flags": ["requires_expert_review"],
        "warnings": ["Computational triage only."],
        "priority_bucket": "high_priority",
        "review_status": "pending",
    }
    payload.update(overrides)
    return ReviewItem(**payload)


def test_review_schemas_validate_allowed_values_and_timezone_defaults():
    reviewer = _reviewer()
    item = _item()
    decision = ReviewerDecision(
        review_item_id=item.review_item_id,
        reviewer=reviewer,
        decision="accept_for_followup",
        rationale="Strong target rationale, but still requires independent validation.",
        confidence=0.8,
        decision_factors=["strong_target_rationale", "developability_risk"],
        created_at=FIXED_TIME,
    )
    comment = ReviewerComment(
        review_item_id=item.review_item_id,
        reviewer=reviewer,
        comment_text="Check whether literature evidence is disease-specific.",
        comment_type="literature_note",
        created_at=FIXED_TIME,
    )
    followup = FollowupRequest(
        review_item_id=item.review_item_id,
        requested_by=reviewer,
        request_type="rerun_with_more_literature",
        request_text="Repeat literature retrieval with broader aliases.",
        priority="high",
        status="open",
        created_at=FIXED_TIME,
    )
    audit = ReviewAuditEvent(
        event_type="decision_recorded",
        actor=reviewer.reviewer_id,
        timestamp=FIXED_TIME,
        object_type="ReviewerDecision",
        object_id=decision.decision_id,
        summary="Reviewer accepted candidate for computational follow-up.",
        before=None,
        after=decision.model_dump(mode="json"),
    )
    workspace = ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=FIXED_TIME,
        review_items=[item],
        decisions=[decision],
        comments=[comment],
        followup_requests=[followup],
        audit_events=[audit],
    )

    assert reviewer.metadata == {}
    assert item.review_item_id == "review-item-run-1-chembl887"
    assert decision.decision_id.startswith("decision-")
    assert decision.created_at.tzinfo is not None
    assert followup.request_id.startswith("followup-")
    assert workspace.workspace_id == "workspace-run-1-parkinson-disease"
    assert workspace.audit_events[0].timestamp.tzinfo is not None


def test_review_schema_rejects_invalid_literals_and_naive_timestamps():
    with pytest.raises(ValidationError) as bad_origin:
        _item(candidate_origin="invented")
    assert "candidate_origin" in str(bad_origin.value)

    with pytest.raises(ValidationError) as bad_status:
        _item(review_status="clinically_approved")
    assert "review_status" in str(bad_status.value)

    with pytest.raises(ValidationError) as bad_time:
        ReviewerComment(
            review_item_id="item-1",
            reviewer=_reviewer(),
            comment_text="Naive timestamp should fail.",
            comment_type="general",
            created_at=datetime(2026, 1, 2, 3, 4, 5),
        )
    assert "timezone-aware" in str(bad_time.value)


def test_validation_handoff_allows_assay_classes_but_rejects_protocol_content():
    handoff = ValidationHandoff(
        review_item_id="item-1",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        disease_name="Parkinson disease",
        target_symbols=["MAOB"],
        validation_questions=["Does the molecule engage the nominated target in relevant models?"],
        suggested_assay_classes=[
            "biochemical target engagement assay",
            "cellular phenotype assay",
        ],
        required_expert_reviews=["medicinal_chemist", "pharmacologist"],
        key_risks_to_check=["safety_risk", "developability_risk"],
        evidence_packet_paths={"dossier": "review_dossier.md"},
        disclaimer="Research validation planning only; no protocols or treatment advice.",
        created_at=FIXED_TIME,
    )

    assert handoff.handoff_id.startswith("handoff-")
    assert "biochemical target engagement assay" in handoff.suggested_assay_classes

    with pytest.raises(ValidationError) as error:
        ValidationHandoff(
            review_item_id="item-1",
            candidate_name="Rasagiline",
            candidate_origin="existing",
            disease_name="Parkinson disease",
            target_symbols=["MAOB"],
            validation_questions=["Use reagent X at 37 C in step 1."],
            suggested_assay_classes=["biochemical target engagement assay"],
            required_expert_reviews=[],
            key_risks_to_check=[],
            evidence_packet_paths={},
            disclaimer="Research only.",
        )
    assert "must not include lab protocols" in str(error.value)


def test_candidate_dossier_uses_nested_review_records():
    reviewer = _reviewer()
    item = _item()
    decision = ReviewerDecision(
        review_item_id=item.review_item_id,
        reviewer=reviewer,
        decision="needs_more_data",
        rationale="Literature signal is weak.",
        confidence=0.5,
        decision_factors=["weak_literature"],
        created_at=FIXED_TIME,
    )
    comment = ReviewerComment(
        review_item_id=item.review_item_id,
        reviewer=reviewer,
        comment_text="Ask for more literature before handoff.",
        comment_type="evidence_question",
        created_at=FIXED_TIME,
    )

    dossier = CandidateDossier(
        review_item_id=item.review_item_id,
        disease_name=item.disease_name,
        candidate_name=item.candidate_name,
        candidate_origin=item.candidate_origin,
        executive_summary="Expert triage dossier, not a clinical conclusion.",
        evidence_sections=[{"title": "Evidence", "items": []}],
        risk_sections=[{"title": "Risks", "items": item.risk_flags}],
        reviewer_decisions=[decision],
        reviewer_comments=[comment],
        limitations=["No medical advice."],
        generated_at=FIXED_TIME,
    )

    assert dossier.dossier_id.startswith("dossier-")
    assert dossier.reviewer_decisions[0].reviewer.reviewer_id == "reviewer-1"
