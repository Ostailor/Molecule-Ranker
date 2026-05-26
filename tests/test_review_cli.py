from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.schemas import Reviewer, ReviewWorkspace


def _candidate_artifact() -> dict[str, object]:
    return {
        "success": True,
        "disease": {
            "input_name": "PD",
            "canonical_name": "Parkinson disease",
            "identifiers": {"mondo": "MONDO:0005180"},
        },
        "candidates": [
            {
                "name": "Rasagiline",
                "molecule_type": "small_molecule",
                "origin": "existing",
                "identifiers": {"chembl": "CHEMBL887"},
                "known_targets": ["MAOB"],
                "score": 0.65,
                "score_breakdown": {
                    "disease_target_relevance": 0.8,
                    "molecule_target_evidence": 0.7,
                    "mechanism_plausibility": 0.6,
                    "clinical_precedence": 0.5,
                    "safety_prior": 0.4,
                    "data_quality": 0.9,
                    "novelty_or_repurposing_value": 0.3,
                    "final_score": 0.65,
                    "confidence": 0.75,
                    "explanation": "Transparent weighted evidence score.",
                },
                "evidence": [
                    {
                        "source": "ChEMBL",
                        "source_record_id": "CHEMBL123",
                        "title": "Target activity record",
                        "evidence_type": "activity",
                        "summary": "Retrieved public-source activity evidence.",
                        "confidence": 0.7,
                    }
                ],
                "warnings": ["Requires experimental validation."],
            }
        ],
        "generated_molecule_hypotheses": [
            {
                "name": "Generated-MAOB-001",
                "canonical_smiles": "CCOC1=CC=CC=C1",
                "target_symbol": "MAOB",
                "generation_score": 0.42,
                "min_seed_similarity": 0.2,
                "max_seed_similarity": 0.6,
                "mean_seed_similarity": 0.4,
                "warnings": ["Generated hypothesis; no direct activity evidence."],
            }
        ],
        "limitations": ["Computational triage only."],
    }


def test_review_cli_creates_workspace_decisions_dossiers_and_dashboard(tmp_path):
    runner = CliRunner()
    input_path = tmp_path / "candidates.json"
    workspace_path = tmp_path / "review_workspace.json"
    dossier_path = tmp_path / "dossier.md"
    dashboard_path = tmp_path / "review.html"
    feedback_path = tmp_path / "feedback.json"
    handoff_path = tmp_path / "handoff.json"
    input_path.write_text(json.dumps(_candidate_artifact()))

    init = runner.invoke(
        app,
        [
            "review",
            "init",
            "--input",
            str(input_path),
            "--output",
            str(workspace_path),
            "--reviewer-id",
            "expert-1",
            "--reviewer-name",
            "Local Reviewer",
            "--dashboard",
            str(dashboard_path),
        ],
    )

    assert init.exit_code == 0
    assert workspace_path.exists()
    assert dashboard_path.exists()
    workspace = json.loads(workspace_path.read_text())
    item_id = workspace["review_items"][0]["review_item_id"]
    assert workspace["review_items"][1]["candidate_origin"] == "generated"

    followup = runner.invoke(
        app,
        [
            "review",
            "follow-up",
            "--workspace",
            str(workspace_path),
            "--item-id",
            item_id,
            "--reviewer-id",
            "expert-1",
            "--check-type",
            "literature_review",
            "--question",
            "Re-check disease specificity.",
        ],
    )
    assert followup.exit_code == 0

    workspace_model = ReviewWorkspace.model_validate_json(workspace_path.read_text())
    reviewer = Reviewer(reviewer_id="expert-1")
    ReviewDecisionEngine().record_decision(
        workspace_model,
        review_item_id=item_id,
        reviewer=reviewer,
        decision="needs_more_data",
        rationale="Needs independent computational follow-up before any validation handoff.",
        confidence=0.5,
    )
    ReviewDecisionEngine().add_comment(
        workspace_model,
        review_item_id=item_id,
        reviewer=reviewer,
        comment_text="Expert triage label only.",
    )
    workspace_path.write_text(
        json.dumps(workspace_model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )

    dossier = runner.invoke(
        app,
        [
            "review",
            "dossier",
            "--workspace",
            str(workspace_path),
            "--item-id",
            item_id,
            "--output",
            str(dossier_path),
        ],
    )
    assert dossier.exit_code == 0
    assert "Reviewer Decisions" in dossier_path.read_text()

    handoff = runner.invoke(
        app,
        [
            "review",
            "handoff",
            "--workspace",
            str(workspace_path),
            "--item-id",
            item_id,
            "--reviewer-id",
            "expert-1",
            "--output",
            str(handoff_path),
        ],
    )
    assert handoff.exit_code == 0
    handoff_payload = json.loads(handoff_path.read_text())
    assert handoff_payload["candidate_name"] == "Rasagiline"
    assert "biochemical target engagement assay" in handoff_payload["suggested_assay_classes"]

    feedback = runner.invoke(
        app,
        [
            "review",
            "ingest-feedback",
            "--workspace",
            str(workspace_path),
            "--output",
            str(feedback_path),
        ],
    )
    assert feedback.exit_code == 0
    feedback_payload = json.loads(feedback_path.read_text())
    assert feedback_payload["feedback"][0]["ranking_signal"] == "needs_more_evidence"
    assert feedback_payload["feedback"][0]["model_score_override"] is None


def test_review_cli_help_is_registered():
    runner = CliRunner()

    result = runner.invoke(app, ["review", "--help"])

    assert result.exit_code == 0
    assert "init" in result.stdout
    assert "decide" in result.stdout
    assert "compare" in result.stdout
