from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.dossier import DossierWriterAgent, render_dossier_json
from molecule_ranker.review.feedback import FeedbackStore
from molecule_ranker.review.schemas import Reviewer, ReviewItem, ReviewWorkspace
from molecule_ranker.review.validation_handoff import build_validation_handoff
from molecule_ranker.schemas import EvidenceItem

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _workspace() -> ReviewWorkspace:
    existing = ReviewItem(
        run_id="run-guardrail",
        disease_name="Parkinson disease",
        candidate_id="CHEMBL887",
        candidate_name="Rasagiline",
        candidate_origin="existing",
        target_symbols=["MAOB"],
        canonical_smiles="C#CCN1CCC2=CC=CC=C21",
        score=0.72,
        confidence=0.68,
        evidence_summary={
            "score_breakdown": {"final_score": 0.72, "confidence": 0.68},
            "target_evidence_count": 4,
            "molecule_evidence_count": 3,
            "literature_claim_counts": {"supports": 2, "contradicts": 0, "mentions": 1},
            "safety_warning_count": 1,
            "developability_risk_level": "medium",
            "generated_score": None,
            "reagent_note": "Use reagent X in a reaction condition.",
        },
        literature_summary={
            "items": [
                {
                    "title": "Target biology paper",
                    "doi": "10.1000/example",
                    "pmid": "12345",
                    "journal": "Example Journal",
                    "publication_year": 2025,
                    "abstract": "Full abstract text should not be exported.",
                }
            ],
            "claim_counts": {"supports": 2, "contradicts": 0, "mentions": 1},
        },
        developability_summary={
            "risk_level": "medium",
            "reaction_conditions": "Hold at 37 C for 30 minutes.",
        },
        generation_summary=None,
        risk_flags=["developability_risk"],
        warnings=["Computational triage only."],
        priority_bucket="medium_priority",
        review_status="pending",
        metadata={
            "artifact_paths": {
                "candidate_json": "results/pd/candidates.json",
                "synthesis_route": "private/synthesis_route.md",
                "protocol": "private/protocol.md",
            }
        },
    )
    generated = ReviewItem(
        run_id="run-guardrail",
        disease_name="Parkinson disease",
        candidate_id="generated-1",
        candidate_name="Generated-MAOB-001",
        candidate_origin="generated",
        target_symbols=["MAOB"],
        canonical_smiles="CCOC1=CC=CC=C1",
        score=0.61,
        confidence=None,
        evidence_summary={
            "target_evidence_count": 4,
            "molecule_evidence_count": 0,
            "literature_claim_counts": {"supports": 0, "contradicts": 0, "mentions": 0},
            "safety_warning_count": 0,
            "developability_risk_level": "unknown",
            "generated_score": 0.61,
            "dosage_note": "Give 5 mg/kg to a patient.",
        },
        literature_summary={"items": []},
        developability_summary={"risk_level": "unknown", "temperature": "37 C"},
        generation_summary={
            "generation_score": 0.61,
            "target_symbol": "MAOB",
            "synthesis_route": "Step-by-step synthesis instructions.",
        },
        risk_flags=["generated_no_direct_evidence"],
        warnings=["Generated hypothesis; no direct activity evidence."],
        priority_bucket="needs_review",
        review_status="pending",
        metadata={
            "artifact_paths": {
                "generated_json": "results/pd/generated_candidates.json",
                "reagents": "private/reagents.txt",
            }
        },
    )
    return ReviewWorkspace(
        run_id="run-guardrail",
        disease_name="Parkinson disease",
        created_at=FIXED_TIME,
        review_items=[existing, generated],
        metadata={
            "artifact_paths": {
                "review_queue": "results/pd/review_queue.json",
                "reaction_conditions": "private/reaction_conditions.md",
            }
        },
    )


def _assert_no_operational_detail(serialized: str) -> None:
    lowered = serialized.lower()
    forbidden_patterns = [
        r"\breagent\b",
        r"\breagents\b",
        r"\breaction condition",
        r"\bsynthesis route",
        r"\bprivate/synthesis",
        r"\bprivate/protocol",
        r"\bprivate/reagents",
        r"\b37\s*c\b",
        r"\b30\s*minutes\b",
        r"\b5\s*mg/kg\b",
        r"\bgive\s+5\b",
        r"\bstep-by-step\b",
    ]
    assert not any(re.search(pattern, lowered) for pattern in forbidden_patterns)


def test_guardrails_generated_dossier_keeps_generated_label_and_no_direct_evidence_boundary():
    workspace = _workspace()
    generated = workspace.review_items[1]

    dossier = DossierWriterAgent().build_dossier(workspace, generated.review_item_id)
    payload = dossier.model_dump(mode="json")
    serialized = json.dumps(payload)

    assert payload["candidate_origin"] == "generated"
    assert "Generated molecules have no direct experimental evidence." in serialized
    assert "direct experimental evidence is available" not in serialized.lower()
    assert "direct experimental evidence supports" not in serialized.lower()
    _assert_no_operational_detail(serialized)


def test_guardrails_validation_handoff_has_no_protocol_like_fields_or_actions():
    workspace = _workspace()
    generated = workspace.review_items[1]

    handoff = build_validation_handoff(workspace, generated.review_item_id)
    payload = handoff.model_dump(mode="json")
    serialized = json.dumps(payload)

    assert payload["candidate_origin"] == "generated"
    assert "Generated molecules have no direct experimental evidence." in serialized
    assert "biochemical target engagement assay" in payload["suggested_assay_classes"]
    _assert_no_operational_detail(serialized)


def test_guardrails_reviewer_decisions_and_feedback_do_not_become_biomedical_evidence():
    workspace = _workspace()
    existing = workspace.review_items[0]
    reviewer = Reviewer(reviewer_id="expert-1", role="medicinal_chemist")

    ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=existing.review_item_id,
        reviewer=reviewer,
        decision="needs_more_data",
        rationale="Expert triage label only.",
        confidence=0.6,
        decision_factors=["weak_literature"],
    )
    feedback = FeedbackStore.in_memory_from_workspace(workspace)

    assert not isinstance(workspace.decisions[0], EvidenceItem)
    assert not isinstance(feedback[0], EvidenceItem)
    assert feedback[0].metadata["source_label"] == "expert review feedback"
    assert feedback[0].metadata["score_boundary"].endswith(
        "biomedical EvidenceItem evidence."
    )


def test_guardrails_source_citations_are_preserved_without_article_text():
    workspace = _workspace()
    existing = workspace.review_items[0]

    dossier = DossierWriterAgent().build_dossier(workspace, existing.review_item_id)
    payload = json.loads(render_dossier_json(dossier))
    literature_section = next(
        section
        for section in payload["metadata"]["sections"]
        if section["title"] == "Literature evidence"
    )

    assert literature_section["content"]["citations"] == [
        {
            "doi": "10.1000/example",
            "journal": "Example Journal",
            "pmid": "12345",
            "publication_year": 2025,
            "title": "Target biology paper",
        }
    ]
    serialized = json.dumps(payload)
    assert "Full abstract text should not be exported" not in serialized
