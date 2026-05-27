from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from molecule_ranker.review import DossierWriterAgent, Reviewer
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.dossier import render_dossier_json, render_dossier_markdown
from molecule_ranker.review.schemas import ReviewItem, ReviewWorkspace


def _workspace() -> ReviewWorkspace:
    existing = ReviewItem(
        run_id="run-1",
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
            "items": [
                {
                    "source": "ChEMBL",
                    "source_record_id": "CHEMBL_ACT_1",
                    "summary": "Curated target interaction record.",
                }
            ],
        },
        literature_summary={
            "items": [
                {
                    "title": "Target biology paper",
                    "doi": "10.1000/example",
                    "pmid": "12345",
                    "journal": "Example Journal",
                    "publication_year": 2025,
                    "abstract": "This long article text should not appear in a dossier.",
                    "full_text": "Full article text should not be copied.",
                }
            ],
            "claim_counts": {"supports": 2, "contradicts": 0, "mentions": 1},
            "quality_score": 0.71,
        },
        developability_summary={
            "risk_level": "medium",
            "structure_available": True,
            "triage_recommendation": "review_flags",
        },
        generation_summary=None,
        risk_flags=["developability_risk"],
        warnings=["Public-source warning metadata requires expert review."],
        priority_bucket="medium_priority",
        review_status="pending",
        metadata={"artifact_paths": {"candidate_json": "results/pd/candidates.json"}},
    )
    generated = ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="generated-1",
        candidate_name="Generated-MAOB-001",
        candidate_origin="generated",
        target_symbols=["MAOB"],
        canonical_smiles="CCOC1=CC=CC=C1",
        score=0.61,
        confidence=None,
        evidence_summary={
            "score_breakdown": None,
            "target_evidence_count": 4,
            "molecule_evidence_count": 0,
            "literature_claim_counts": {"supports": 0, "contradicts": 0, "mentions": 0},
            "safety_warning_count": 0,
            "developability_risk_level": "unknown",
            "generated_score": 0.61,
        },
        literature_summary={"items": []},
        developability_summary={"risk_level": "unknown", "structure_available": True},
        generation_summary={
            "generation_score": 0.61,
            "target_symbol": "MAOB",
            "seed_molecule_names": ["Seed-1"],
            "source": "target_conditioned_generation",
            "trace": {"model": "local"},
        },
        risk_flags=["generated_no_direct_evidence"],
        warnings=["Generated hypothesis; no direct activity evidence."],
        priority_bucket="needs_review",
        review_status="pending",
        metadata={"artifact_paths": {"generated_json": "results/pd/generated_candidates.json"}},
    )
    return ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        review_items=[existing, generated],
        metadata={"artifact_paths": {"report": "results/pd/report.md"}},
    )


def _non_disclaimer_text(markdown: str) -> str:
    return markdown.split("## Limitations and disclaimers", maxsplit=1)[0].lower()


def _assert_no_forbidden_claim_words(text: str) -> None:
    without_disclaimers = _non_disclaimer_text(text)
    for word in ("cure", "safe", "effective", "active"):
        assert re.search(rf"\b{word}\b", without_disclaimers) is None


def test_generated_dossier_has_required_sections_and_direct_evidence_warning():
    workspace = _workspace()
    generated_id = workspace.review_items[1].review_item_id
    dossier = DossierWriterAgent().build_dossier(workspace, generated_id)

    markdown = render_dossier_markdown(dossier)
    section_titles = [section["title"] for section in dossier.metadata["sections"]]

    assert "Generated molecules have no direct experimental evidence." in markdown
    assert section_titles == [
        "Executive summary",
        "Candidate identity",
        "Origin",
        "Disease and target rationale",
        "Molecule-target evidence",
        "Experimental evidence",
        "Literature evidence",
        "Safety and warning evidence",
        "Developability assessment",
        "Generated molecule provenance",
        "Key uncertainties",
        "Reviewer decisions and comments",
        "Codex review assistance",
        "Recommended follow-up questions",
        "Limitations and disclaimers",
        "Source provenance and artifact paths",
    ]
    assert "## Generated molecule provenance" in markdown
    _assert_no_forbidden_claim_words(markdown)


def test_existing_dossier_filters_article_text_and_avoids_approval_safety_implication():
    workspace = _workspace()
    existing_id = workspace.review_items[0].review_item_id
    reviewer = Reviewer(reviewer_id="expert-1", role="medicinal_chemist")
    ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=existing_id,
        reviewer=reviewer,
        decision="needs_more_data",
        rationale="Ask for disease-specific target rationale review.",
        confidence=0.6,
        decision_factors=["weak_literature"],
    )
    ReviewDecisionEngine().add_comment(
        workspace,
        review_item_id=existing_id,
        reviewer=reviewer,
        comment_text="Check citation metadata only.",
        comment_type="literature_note",
    )

    dossier = DossierWriterAgent().build_dossier(workspace, existing_id)
    markdown = render_dossier_markdown(dossier)
    json_payload = json.loads(render_dossier_json(dossier))

    assert "existing evidence-backed molecule" in markdown
    assert "approved means safe" not in markdown.lower()
    assert "This existing-molecule dossier does not infer suitability" in markdown
    assert "abstract" not in json.dumps(json_payload).lower()
    assert "full article text" not in markdown.lower()
    literature_section = next(
        section
        for section in json_payload["metadata"]["sections"]
        if section["title"] == "Literature evidence"
    )
    assert literature_section["content"]["citations"][0] == {
        "doi": "10.1000/example",
        "journal": "Example Journal",
        "pmid": "12345",
        "publication_year": 2025,
        "title": "Target biology paper",
    }
    assert "Reviewer decisions and comments" in markdown
    assert "Recommended follow-up questions" in markdown
    _assert_no_forbidden_claim_words(markdown)
