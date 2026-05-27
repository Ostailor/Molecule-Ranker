from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.review.workspace import ReviewWorkspaceStore


def _write_run_artifacts(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "candidates.json").write_text(
        json.dumps(
            {
                "success": True,
                "run_id": "run-parkinson-disease",
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
                        "chemical_metadata": {"canonical_smiles": "CNCCC1=CC=CC=C1"},
                        "score": 0.82,
                        "score_breakdown": {
                            "disease_target_relevance": 0.9,
                            "molecule_target_evidence": 0.8,
                            "mechanism_plausibility": 0.7,
                            "clinical_precedence": 0.5,
                            "safety_prior": 0.5,
                            "data_quality": 0.8,
                            "novelty_or_repurposing_value": 0.4,
                            "final_score": 0.82,
                            "confidence": 0.76,
                            "explanation": "Transparent weighted evidence score.",
                        },
                        "evidence": [{"source": "ChEMBL", "summary": "Activity evidence."}],
                        "literature_evidence": {
                            "claim_counts": {"supports": 2, "contradicts": 0, "mentions": 1}
                        },
                        "developability_summary": {
                            "risk_level": "low",
                            "structure_available": True,
                        },
                        "warnings": ["Research triage only."],
                    }
                ],
                "generated_molecule_hypotheses": [
                    {
                        "name": "Generated-MAOB-001",
                        "canonical_smiles": "CCOC1=CC=CC=C1",
                        "target_symbol": "MAOB",
                        "generation_score": 0.63,
                        "warnings": ["Generated hypothesis; no direct activity evidence."],
                    }
                ],
                "limitations": ["Computational triage only."],
            }
        )
    )


def test_review_sqlite_cli_round_trip_and_append_only_decisions(tmp_path):
    runner = CliRunner()
    run_dir = tmp_path / "results" / "parkinson-disease"
    db_path = tmp_path / "review.sqlite"
    export_json = tmp_path / "workspace.json"
    export_markdown = tmp_path / "workspace.md"
    _write_run_artifacts(run_dir)

    created = runner.invoke(
        app,
        [
            "review",
            "create",
            "--from-run",
            str(run_dir),
            "--db-path",
            str(db_path),
            "--reviewer-id",
            "expert-1",
            "--reviewer-name",
            "Local Reviewer",
            "--reviewer-role",
            "medicinal_chemist",
            "--json",
        ],
    )

    assert created.exit_code == 0, created.stdout
    created_payload = json.loads(created.stdout)
    workspace_id = created_payload["workspace_id"]
    store = ReviewWorkspaceStore(db_path)
    workspace = store.get_workspace(workspace_id)
    item_id = workspace.review_items[0].review_item_id
    assert len(workspace.review_items) == 2

    listed = runner.invoke(
        app,
        ["review", "list", "--db-path", str(db_path), "--json"],
    )
    assert listed.exit_code == 0, listed.stdout
    assert json.loads(listed.stdout)["workspaces"][0]["workspace_id"] == workspace_id

    shown = runner.invoke(
        app,
        ["review", "show", workspace_id, "--db-path", str(db_path), "--json"],
    )
    assert shown.exit_code == 0, shown.stdout
    shown_payload = json.loads(shown.stdout)
    assert shown_payload["disease_name"] == "Parkinson disease"
    assert shown_payload["status_distribution"]["pending"] == 2
    assert shown_payload["top_pending_items"][0]["review_item_id"] == item_id

    item = runner.invoke(
        app,
        ["review", "item", workspace_id, item_id, "--db-path", str(db_path), "--json"],
    )
    assert item.exit_code == 0, item.stdout
    item_payload = json.loads(item.stdout)
    assert item_payload["candidate_name"] == "Rasagiline"
    assert "score_breakdown" in item_payload["evidence_summary"]

    first_decision = runner.invoke(
        app,
        [
            "review",
            "decide",
            workspace_id,
            item_id,
            "--db-path",
            str(db_path),
            "--decision",
            "needs_more_data",
            "--rationale",
            "Needs independent computational follow-up before any validation handoff.",
            "--reviewer-id",
            "expert-1",
            "--reviewer-name",
            "Local Reviewer",
            "--reviewer-role",
            "medicinal_chemist",
            "--confidence",
            "0.72",
            "--factor",
            "weak_literature",
            "--json",
        ],
    )
    assert first_decision.exit_code == 0, first_decision.stdout

    second_decision = runner.invoke(
        app,
        [
            "review",
            "decide",
            workspace_id,
            item_id,
            "--db-path",
            str(db_path),
            "--decision",
            "hold",
            "--rationale",
            "Hold pending a side-by-side expert comparison.",
            "--reviewer-id",
            "expert-1",
            "--confidence",
            "0.5",
        ],
    )
    assert second_decision.exit_code == 0, second_decision.stdout
    workspace = store.get_workspace(workspace_id)
    assert [decision.decision for decision in workspace.decisions] == [
        "needs_more_data",
        "hold",
    ]

    comment = runner.invoke(
        app,
        [
            "review",
            "comment",
            workspace_id,
            item_id,
            "--db-path",
            str(db_path),
            "--comment",
            "Expert triage label only; no clinical conclusion.",
            "--comment-type",
            "general",
            "--reviewer-id",
            "expert-1",
        ],
    )
    assert comment.exit_code == 0, comment.stdout

    followup = runner.invoke(
        app,
        [
            "review",
            "request-followup",
            workspace_id,
            item_id,
            "--db-path",
            str(db_path),
            "--request-type",
            "rerun_with_more_literature",
            "--request-text",
            "Re-check disease-specific literature and evidence limitations.",
            "--priority",
            "high",
            "--json",
        ],
    )
    assert followup.exit_code == 0, followup.stdout
    assert json.loads(followup.stdout)["request_type"] == "rerun_with_more_literature"

    exported_json = runner.invoke(
        app,
        [
            "review",
            "export",
            workspace_id,
            "--db-path",
            str(db_path),
            "--output",
            str(export_json),
            "--format",
            "json",
        ],
    )
    assert exported_json.exit_code == 0, exported_json.stdout
    assert json.loads(export_json.read_text())["workspace_id"] == workspace_id

    exported_markdown = runner.invoke(
        app,
        [
            "review",
            "export",
            workspace_id,
            "--db-path",
            str(db_path),
            "--output",
            str(export_markdown),
            "--format",
            "markdown",
        ],
    )
    assert exported_markdown.exit_code == 0, exported_markdown.stdout
    assert "Human decisions are expert triage labels" in export_markdown.read_text()

    export_zip = tmp_path / "workspace.zip"
    exported_zip = runner.invoke(
        app,
        [
            "review",
            "export",
            workspace_id,
            "--db-path",
            str(db_path),
            "--output",
            str(export_zip),
            "--format",
            "zip",
        ],
    )
    assert exported_zip.exit_code == 0, exported_zip.stdout
    with zipfile.ZipFile(export_zip) as archive:
        names = set(archive.namelist())
    assert "workspace.json" in names
    assert "review_queue.json" in names
    assert "source_artifact_manifest.json" in names
    assert "export_manifest.json" in names

    audit = runner.invoke(
        app,
        ["review", "audit", workspace_id, "--db-path", str(db_path), "--json"],
    )
    assert audit.exit_code == 0, audit.stdout
    event_types = [event["event_type"] for event in json.loads(audit.stdout)["audit_events"]]
    assert "decision_added" in event_types
    assert "comment_added" in event_types
    assert "followup_request_added" in event_types


def test_review_cli_create_can_exclude_generated_and_limit_items(tmp_path):
    runner = CliRunner()
    run_dir = tmp_path / "results" / "parkinson-disease"
    db_path = tmp_path / "review.sqlite"
    _write_run_artifacts(run_dir)

    created = runner.invoke(
        app,
        [
            "review",
            "create",
            "--from-run",
            str(run_dir),
            "--db-path",
            str(db_path),
            "--exclude-generated",
            "--max-review-items",
            "1",
            "--json",
        ],
    )

    assert created.exit_code == 0, created.stdout
    workspace_id = json.loads(created.stdout)["workspace_id"]
    workspace = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
    assert len(workspace.review_items) == 1
    assert workspace.review_items[0].candidate_origin == "existing"


def test_review_cli_codex_questions_command_stores_review_assistance(tmp_path):
    runner = CliRunner()
    run_dir = tmp_path / "results" / "parkinson-disease"
    db_path = tmp_path / "review.sqlite"
    _write_run_artifacts(run_dir)

    created = runner.invoke(
        app,
        [
            "review",
            "create",
            "--from-run",
            str(run_dir),
            "--db-path",
            str(db_path),
            "--json",
        ],
    )
    assert created.exit_code == 0, created.stdout
    workspace_id = json.loads(created.stdout)["workspace_id"]
    workspace = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
    item_id = workspace.review_items[0].review_item_id

    result = runner.invoke(
        app,
        [
            "review",
            "codex-questions",
            workspace_id,
            item_id,
            "--db-path",
            str(db_path),
            "--codex-mode",
            "dry_run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["task_type"] == "codex_review_questions"
    reloaded = ReviewWorkspaceStore(db_path).get_workspace(workspace_id)
    assert len(reloaded.codex_review_artifacts) == 1
    assert reloaded.decisions == []
