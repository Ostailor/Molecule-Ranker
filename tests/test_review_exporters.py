from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.review.decision_engine import ReviewDecisionEngine
from molecule_ranker.review.exporters import export_review_package
from molecule_ranker.review.schemas import Reviewer, ReviewerComment, ReviewItem, ReviewWorkspace

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _workspace(tmp_path: Path) -> ReviewWorkspace:
    report_path = tmp_path / "results" / "report.md"
    cache_path = tmp_path / ".cache" / "raw-response.json"
    env_path = tmp_path / ".env"
    report_path.parent.mkdir(parents=True)
    cache_path.parent.mkdir(parents=True)
    report_path.write_text("# Generated report\n")
    cache_path.write_text('{"api_key": "sk-test-secret"}\n')
    env_path.write_text("OPENAI_API_KEY=sk-test-secret\n")
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
        evidence_summary={"molecule_evidence_count": 3},
        literature_summary={
            "citations": [{"pmid": "123", "title": "Citation metadata"}],
            "full_text": "This full article text must not be exported.",
        },
        developability_summary={"risk_level": "medium"},
        generation_summary=None,
        risk_flags=["developability_risk"],
        warnings=["Computational triage only."],
        priority_bucket="high_priority",
        review_status="pending",
    )
    generated = ReviewItem(
        run_id="run-1",
        disease_name="Parkinson disease",
        candidate_id="gen-1",
        candidate_name="Generated-MAOB-001",
        candidate_origin="generated",
        target_symbols=["MAOB"],
        canonical_smiles="CCOC1=CC=CC=C1",
        score=0.55,
        confidence=0.4,
        evidence_summary={"generated_score": 0.55},
        literature_summary={},
        developability_summary={"risk_level": "low"},
        generation_summary={"method": "target_conditioned"},
        risk_flags=[],
        warnings=["Generated hypothesis; no direct evidence."],
        priority_bucket="needs_review",
        review_status="pending",
    )
    return ReviewWorkspace(
        run_id="run-1",
        disease_name="Parkinson disease",
        created_at=FIXED_TIME,
        review_items=[item, generated],
        metadata={
            "artifact_paths": {
                "report_md": str(report_path),
                "cache": str(cache_path),
                "env": str(env_path),
            },
            "api_key": "sk-test-secret",
            "environment": {"OPENAI_API_KEY": "sk-test-secret"},
        },
    )


def _reviewed_workspace(tmp_path: Path) -> ReviewWorkspace:
    workspace = _workspace(tmp_path)
    reviewer = Reviewer(reviewer_id="expert-1", role="medicinal_chemist")
    ReviewDecisionEngine().record_decision(
        workspace,
        review_item_id=workspace.review_items[0].review_item_id,
        reviewer=reviewer,
        decision="needs_more_data",
        rationale="Expert triage label only.",
        confidence=0.7,
    )
    workspace.comments.append(
        ReviewerComment(
            review_item_id=workspace.review_items[0].review_item_id,
            reviewer=reviewer,
            comment_text="Keep reviewer comments separate from evidence.",
            comment_type="general",
            created_at=FIXED_TIME,
        )
    )
    return workspace


def _package_text(root: Path) -> str:
    return "\n".join(
        path.read_text(errors="ignore")
        for path in root.rglob("*")
        if path.is_file()
    )


def test_json_export_package_writes_expected_files_and_sanitizes_secrets(tmp_path):
    output = tmp_path / "json-package"

    result = export_review_package(_reviewed_workspace(tmp_path), output, output_format="json")

    expected = {
        "workspace.json",
        "review_queue.json",
        "decisions.json",
        "comments.json",
        "audit_log.json",
        "source_artifact_manifest.json",
        "limitations.md",
        "README.md",
        "export_manifest.json",
    }
    assert result.output_path == output
    assert expected <= {path.relative_to(output).as_posix() for path in output.rglob("*")}
    assert any(path.startswith("dossiers/") and path.endswith(".json") for path in result.files)
    assert any(
        path.startswith("validation_handoffs/") and path.endswith(".json")
        for path in result.files
    )
    export_manifest = json.loads((output / "export_manifest.json").read_text())
    assert expected <= set(export_manifest["files"])
    artifact_manifest = json.loads((output / "source_artifact_manifest.json").read_text())
    assert artifact_manifest["artifacts"] == {"report_md": str(tmp_path / "results" / "report.md")}
    package_text = _package_text(output)
    assert "sk-test-secret" not in package_text
    assert "OPENAI_API_KEY" not in package_text
    assert "This full article text must not be exported" not in package_text


def test_markdown_export_package_writes_markdown_workspace_and_dossiers(tmp_path):
    output = tmp_path / "markdown-package"

    result = export_review_package(_reviewed_workspace(tmp_path), output, output_format="markdown")

    assert (output / "workspace.md").exists()
    assert any(path.startswith("dossiers/") and path.endswith(".md") for path in result.files)
    assert any(
        path.startswith("validation_handoffs/") and path.endswith(".md")
        for path in result.files
    )
    workspace_markdown = (output / "workspace.md").read_text()
    assert "Human decisions are expert triage labels" in workspace_markdown


def test_zip_export_package_contains_expected_files_without_cache_or_secrets(tmp_path):
    output = tmp_path / "review-export.zip"

    result = export_review_package(_reviewed_workspace(tmp_path), output, output_format="zip")

    assert result.output_path == output
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        combined = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in names
            if not name.endswith("/")
        )
    assert "workspace.json" in names
    assert "review_queue.json" in names
    assert "export_manifest.json" in names
    assert any(name.startswith("dossiers/") for name in names)
    assert not any(".cache" in name or name.endswith(".env") for name in names)
    assert "sk-test-secret" not in combined
    assert "OPENAI_API_KEY" not in combined
