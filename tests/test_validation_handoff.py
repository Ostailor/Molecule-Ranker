from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from molecule_ranker.review.schemas import ReviewItem, ReviewWorkspace
from molecule_ranker.review.validation_handoff import build_validation_handoff
from molecule_ranker.review.workspace import create_validation_handoff


def _workspace() -> ReviewWorkspace:
    generated = ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="generated-1",
        candidate_name="Generated-MAOB-001",
        candidate_origin="generated",
        target_symbols=["MAOB"],
        canonical_smiles="CCOC1=CC=CC=C1",
        score=0.62,
        confidence=None,
        evidence_summary={
            "target_evidence_count": 3,
            "molecule_evidence_count": 0,
            "literature_claim_counts": {"supports": 0, "contradicts": 0, "mentions": 0},
            "safety_warning_count": 1,
            "developability_risk_level": "high",
            "generated_score": 0.62,
            "operational_note": "Use reagent X at 37 C for 30 minutes.",
        },
        literature_summary={"items": []},
        developability_summary={
            "risk_level": "high",
            "triage_recommendation": "review_flags",
            "procedural_detail": "Incubate for 24 h before measurement.",
        },
        generation_summary={
            "generation_score": 0.62,
            "target_symbol": "MAOB",
            "trace": {"method": "target-conditioned generation"},
        },
        risk_flags=["safety_risk", "developability_risk"],
        warnings=["Do not use 10 uM concentration or a step-by-step protocol."],
        priority_bucket="needs_review",
        review_status="pending",
        metadata={"artifact_paths": {"generated_json": "results/pd/generated_candidates.json"}},
    )
    return ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        review_items=[generated],
        metadata={"artifact_paths": {"review_queue": "results/pd/review_queue.json"}},
    )


def test_validation_handoff_packet_includes_high_level_questions_and_artifacts():
    workspace = _workspace()
    item = workspace.review_items[0]

    handoff = build_validation_handoff(
        workspace,
        item.review_item_id,
        evidence_packet_paths={"dossier": "results/pd/dossier.md"},
    )

    assert handoff.candidate_name == "Generated-MAOB-001"
    assert handoff.disease_name == "Parkinson disease"
    assert handoff.target_symbols == ["MAOB"]
    assert "biochemical target engagement assay" in handoff.suggested_assay_classes
    assert "orthogonal binding assay" in handoff.suggested_assay_classes
    assert "expert medicinal chemistry review" in handoff.suggested_assay_classes
    assert "expert toxicology review" in handoff.suggested_assay_classes
    assert "medicinal_chemist" in handoff.required_expert_reviews
    assert "toxicologist" in handoff.required_expert_reviews
    assert handoff.evidence_packet_paths["dossier"] == "results/pd/dossier.md"
    assert handoff.evidence_packet_paths["review_queue"] == "results/pd/review_queue.json"
    assert handoff.metadata["candidate_identity"]["candidate_id"] == "generated-1"
    assert handoff.metadata["key_hypothesis"].startswith("Review whether")
    assert handoff.metadata["evidence_summary"]["target_evidence_count"] == 3


def test_validation_handoff_omits_procedural_lab_details():
    workspace = _workspace()
    item = workspace.review_items[0]

    handoff = build_validation_handoff(workspace, item.review_item_id)
    serialized = json.dumps(handoff.model_dump(mode="json")).lower()

    forbidden_patterns = [
        r"\breagent\b",
        r"\b37\s*c\b",
        r"\b30\s*minutes\b",
        r"\b10\s*um\b",
        r"\bincubat",
        r"\bstep-by-step\b",
        r"\bmg/kg\b",
    ]
    assert not any(re.search(pattern, serialized) for pattern in forbidden_patterns)


def test_validation_handoff_generated_warning_and_risk_questions():
    workspace = _workspace()
    item = workspace.review_items[0]

    handoff = create_validation_handoff(workspace, review_item_id=item.review_item_id)
    questions = "\n".join(handoff.validation_questions)

    assert "Generated molecules have no direct experimental evidence." in questions
    assert "safety_risk" in questions
    assert "developability_risk" in questions
    assert "What safety warning evidence should an expert review?" in questions
    assert (
        "What developability limitations should a medicinal chemistry expert review?"
        in questions
    )
