from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.biologics.schemas import BiologicCandidate, GeneratedAntibodyHypothesis
from molecule_ranker.campaign import CampaignBudget, CampaignPlanner
from molecule_ranker.hypotheses.schemas import ResearchHypothesis
from molecule_ranker.portfolio.candidate_builder import build_portfolio_candidates
from molecule_ranker.review.dossier import DossierWriterAgent
from molecule_ranker.review.queue_builder import build_review_workspace_from_artifact
from molecule_ranker.review.validation_handoff import build_validation_handoff

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_biologic_review_item_created_with_antibody_sections() -> None:
    workspace = build_review_workspace_from_artifact(
        {
            "disease": {"canonical_name": "Example disease"},
            "biologic_candidates": [_biologic_candidate().model_dump(mode="json")],
            "antibody_sequences": [
                {
                    "sequence_id": "seq-bio-1",
                    "biologic_id": "bio-1",
                    "chain_type": "heavy",
                }
            ],
            "antibody_developability": [_developability()],
            "antibody_novelty": [_novelty()],
        },
        run_id="run-biologics",
    )

    item = workspace.review_items[0]
    handoff = build_validation_handoff(workspace, item.review_item_id)
    dossier = DossierWriterAgent().build_dossier(workspace, item.review_item_id)
    section_titles = [section["title"] for section in dossier.metadata["sections"]]

    assert item.item_type == "biologic"
    assert item.metadata["antibody_sequence"]["sequence_ids"] == ["seq-bio-1"]
    assert "biologics scientist" in handoff.required_expert_reviews
    assert "antibody engineer" in handoff.required_expert_reviews
    assert "developability expert" in handoff.required_expert_reviews
    assert "Antibody sequence" in section_titles
    assert "Antibody developability" in section_titles
    assert "Antibody novelty" in section_titles


def test_generated_antibody_requires_review() -> None:
    workspace = build_review_workspace_from_artifact(
        {
            "disease": {"canonical_name": "Example disease"},
            "generated_antibodies": [_generated_antibody().model_dump(mode="json")],
        },
        run_id="run-generated-antibody",
    )

    item = workspace.review_items[0]

    assert item.item_type == "generated_antibody"
    assert item.review_status == "needs_expert_review"
    assert "generated_antibody_requires_review" in item.risk_flags
    assert "Generated antibody hypothesis" in item.metadata["generated_antibody_warning"]


def test_mixed_portfolio_labels_biologics_modality() -> None:
    candidates = build_portfolio_candidates(
        biologic_candidates=[_biologic_candidate()],
        generated_antibodies=[_generated_antibody()],
        disease_name="Example disease",
        include_biologics_in_mixed_portfolio=True,
    )

    labels = {
        candidate.portfolio_candidate_id: candidate.metadata["portfolio_modality_label"]
        for candidate in candidates
    }

    assert labels["bio-1"] == "biologic:monoclonal_antibody"
    assert labels["gab-1"] == "biologic:generated_antibody"
    generated = next(
        candidate
        for candidate in candidates
        if candidate.portfolio_candidate_id == "gab-1"
    )
    assert generated.review_status == "needs_expert_review"
    assert "sequence_liability_risk" in generated.risk_flags
    assert "review_gate_required" in generated.blocking_risks


def test_campaign_biologics_packages_are_high_level_without_protocols() -> None:
    candidate = build_portfolio_candidates(
        generated_antibodies=[_generated_antibody()],
        disease_name="Example disease",
        include_biologics_in_mixed_portfolio=True,
    )[0]
    hypothesis = ResearchHypothesis(
        hypothesis_id="hyp-bio-1",
        title="Generated antibody review hypothesis",
        statement="Generated antibody hypothesis requires expert biologics review.",
        hypothesis_type="generated_molecule",
        source_artifact_ids=["artifact-biologics"],
        confidence=0.4,
        priority_score=0.6,
        testability_score=0.5,
        status="under_review",
        metadata={
            "disease_name": "Example disease",
            "target_symbols": ["TNF"],
            "candidate_names": ["gab-1"],
            "mechanism_summary": "Source-backed target context only.",
        },
    )

    plan = CampaignPlanner(budget=CampaignBudget(max_work_packages=1)).plan(
        hypotheses=[hypothesis],
        candidates=[candidate],
        campaign_id="campaign-biologics",
    )
    package = plan.work_packages[0]
    text = " ".join(
        [
            package.title,
            *package.high_level_followup_categories,
            *package.dependencies,
            *package.warnings,
        ]
    ).lower()

    assert "biologics_scientist_review" in package.review_gate.required_approvals
    assert "antibody_engineer_review" in package.review_gate.required_approvals
    assert "developability_expert_review" in package.review_gate.required_approvals
    assert "generated_hypothesis_review_gate" in package.review_gate.required_approvals
    for forbidden in ("expression", "purification", "reagent", "incubat", "mg/kg"):
        assert forbidden not in text


def _biologic_candidate() -> BiologicCandidate:
    return BiologicCandidate(
        biologic_id="bio-1",
        name="Existing biologic antibody",
        biologic_type="monoclonal_antibody",
        origin="existing",
        target_symbols=["TNF"],
        antigen_names=["TNF antigen"],
        disease_name="Example disease",
        identifiers={"registry": "REG-BIO-1"},
        sequence_ids=["seq-bio-1"],
        structure_ids=[],
        evidence_item_ids=["ev-bio-1"],
        direct_experimental_evidence=True,
        warnings=[],
        metadata={
            "biologics_score": 0.72,
            "developability": _developability(),
        },
    )


def _generated_antibody() -> GeneratedAntibodyHypothesis:
    return GeneratedAntibodyHypothesis(
        generated_antibody_id="gab-1",
        biologic_id="bio-generated",
        design_objective_id="obj-1",
        generated_sequence_ids=["seq-gab-1"],
        parent_sequence_ids=["seq-bio-1"],
        generation_method="conservative_cdr_mutator",
        antigen_context_id="ag-1",
        target_symbols=["TNF"],
        score=0.41,
        confidence=0.4,
        warnings=["Generated antibody requires expert review."],
        metadata={
            "validation": {"valid": True, "warnings": []},
            "developability": _developability(),
            "novelty": _novelty(),
        },
    )


def _developability() -> dict[str, object]:
    return {
        "assessment_id": "dev-bio-1",
        "biologic_id": "bio-1",
        "sequence_ids": ["seq-bio-1"],
        "aggregation_risk": "medium",
        "polyreactivity_risk": "unknown",
        "immunogenicity_risk": "unknown",
        "viscosity_risk": "unknown",
        "stability_risk": "medium",
        "expression_risk": "unknown",
        "sequence_liability_flags": ["glycosylation motif"],
        "cdr_liability_flags": ["unusual cdr3 length"],
        "overall_developability_score": 0.42,
        "confidence": 0.4,
        "warnings": ["Heuristic triage only."],
        "metadata": {},
    }


def _novelty() -> dict[str, object]:
    return {
        "novelty_id": "nov-bio-1",
        "biologic_id": "bio-1",
        "sequence_ids": ["seq-bio-1"],
        "exact_sequence_match": False,
        "nearest_sequence_identity": 0.91,
        "nearest_known_record": "known-1",
        "cdr3_exact_match": False,
        "cdr3_nearest_identity": 0.82,
        "novelty_class": "close_variant",
        "sources_checked": ["fixture"],
        "warnings": ["Limited to checked sources."],
        "metadata": {},
    }
