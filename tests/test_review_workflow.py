from __future__ import annotations

from molecule_ranker.review import DossierWriterAgent, FeedbackIngestionAgent, Reviewer
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.dossier import render_dossier_markdown
from molecule_ranker.review.queue_builder import build_review_workspace_from_artifact
from molecule_ranker.review.workspace import create_validation_handoff


def test_review_workspace_tracks_decisions_followups_handoffs_and_feedback():
    payload = {
        "run_id": "run-1",
        "disease": {"canonical_name": "Parkinson disease"},
        "candidates": [
            {
                "name": "Rasagiline",
                "identifiers": {"chembl": "CHEMBL887"},
                "known_targets": ["MAOB"],
                "score": 0.65,
                "score_breakdown": {"confidence": 0.75},
                "evidence": [{"summary": "Retrieved public-source activity evidence."}],
                "warnings": ["Requires experimental validation."],
            }
        ],
        "generated_molecule_hypotheses": [
            {
                "name": "Generated-MAOB-001",
                "canonical_smiles": "CCOC1=CC=CC=C1",
                "target_symbol": "MAOB",
                "generation_score": 0.42,
                "warnings": ["Generated hypothesis; no direct activity evidence."],
            }
        ],
    }
    reviewer = Reviewer(
        reviewer_id="expert-1",
        name="Local Reviewer",
        role="medicinal_chemist",
    )

    workspace = build_review_workspace_from_artifact(payload, reviewer=reviewer)

    assert [item.candidate_name for item in workspace.review_items] == [
        "Rasagiline",
        "Generated-MAOB-001",
    ]
    assert workspace.review_items[0].score == 0.65
    assert workspace.review_items[1].candidate_origin == "generated"
    assert workspace.review_items[1].direct_evidence_available is False

    engine = ReviewDecisionEngine()
    followup = engine.request_followup(
        workspace,
        review_item_id=workspace.review_items[0].review_item_id,
        reviewer=reviewer,
        request_type="rerun_with_more_literature",
        request_text="Check whether the target rationale is disease-specific.",
        priority="high",
    )
    decision = engine.record_decision(
        workspace,
        review_item_id=workspace.review_items[0].review_item_id,
        reviewer=reviewer,
        decision="needs_more_data",
        rationale="Evidence is plausible but needs an independent literature check.",
        confidence=0.7,
        decision_factors=["weak_literature"],
    )

    assert decision.decision == "needs_more_data"
    assert followup.status == "open"
    assert workspace.decisions[0].rationale.startswith("Evidence is plausible")
    assert [event.event_type for event in workspace.audit_events] == [
        "workspace_created",
        "followup_requested",
        "decision_recorded",
    ]

    handoff = create_validation_handoff(
        workspace,
        review_item_id=workspace.review_items[0].review_item_id,
        evidence_packet_paths={"dossier": "dossier.md"},
    )
    assert handoff.candidate_name == "Rasagiline"
    assert "biochemical target engagement assay" in handoff.suggested_assay_classes
    assert "dossier" in handoff.evidence_packet_paths

    dossier = DossierWriterAgent().build_dossier(
        workspace,
        workspace.review_items[0].review_item_id,
    )
    markdown = render_dossier_markdown(dossier)
    assert "Reviewer Decisions" in markdown
    assert "Evidence summary" in markdown
    assert "not a clinical conclusion" in dossier.executive_summary

    ingestion = FeedbackIngestionAgent().build_feedback(workspace)
    assert ingestion.workspace_id == workspace.workspace_id
    assert ingestion.feedback[0].decision == "needs_more_data"
    assert ingestion.feedback[0].ranking_signal == "needs_more_evidence"
    assert ingestion.feedback[0].model_score_override is None
