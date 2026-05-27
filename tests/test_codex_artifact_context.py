from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.codex_backbone.artifact_context import (
    build_artifact_context,
    extract_allowed_candidate_ids,
    extract_allowed_citation_ids,
    select_relevant_artifacts,
    summarize_large_artifact,
    validate_output_references,
)
from molecule_ranker.codex_backbone.schemas import CodexTaskResult


def test_artifact_selection_excludes_cache(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path)
    cache_dir = run_dir / ".cache"
    cache_dir.mkdir()
    (cache_dir / "candidates.json").write_text("{}")

    selected = select_relevant_artifacts("summarize_run", run_dir)

    assert run_dir / "candidates.json" in selected
    assert all(".cache" not in str(path) for path in selected)


def test_artifact_selection_excludes_secrets(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path)
    (run_dir / ".env").write_text("OPENAI_API_KEY=sk-test-secret-value")
    (run_dir / "secrets.json").write_text('{"token": "secret"}')

    selected = select_relevant_artifacts("inspect_artifacts", run_dir)
    context = build_artifact_context(run_dir, max_bytes=500)

    assert all(path.name not in {".env", "secrets.json"} for path in selected)
    assert ".env" not in json.dumps(context.model_dump(mode="json"))
    assert "sk-test-secret-value" not in json.dumps(context.model_dump(mode="json"))


def test_allowed_citation_and_candidate_ids_extracted(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path)
    artifacts = select_relevant_artifacts("summarize_run", run_dir)

    citations = extract_allowed_citation_ids(artifacts)
    candidates = extract_allowed_candidate_ids(artifacts)

    assert "PMID:12345" in citations
    assert "10.1000/example" in citations
    assert "lit-0001" in citations
    assert "Rasagiline" in candidates
    assert "GEN-MAOB-001" in candidates


def test_fake_citation_in_output_flagged(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path)
    context = build_artifact_context(run_dir, max_bytes=1_000)
    result = _result("Summary cites PMID:99999999 and candidate: FakeMol.")

    warnings = validate_output_references(result, context)

    assert "Unbacked citation reference: PMID:99999999." in warnings
    assert "Unbacked candidate reference: FakeMol." in warnings


def test_large_artifact_truncated_safely(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_text(json.dumps({"api_key": "secretvalue123", "payload": "A" * 2_000}))

    summary = summarize_large_artifact(path, max_bytes=200)

    assert summary.startswith("[TRUNCATED:")
    assert "original_size_bytes" in summary
    assert "secretvalue123" not in summary
    assert "[REDACTED]" in summary


def _write_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    payload = {
        "success": True,
        "disease": {"canonical_name": "Parkinson disease"},
        "literature_queries": [{"query_id": "lit-0001"}],
        "literature_papers": [
            {
                "pmid": "12345",
                "doi": "10.1000/example",
                "citation": {"pmid": "12345", "doi": "10.1000/example"},
            }
        ],
        "extracted_claims": [{"citation_id": "claim-1", "pmid": "12345"}],
        "candidates": [
            {
                "name": "Rasagiline",
                "score": 0.82,
                "known_targets": ["MAOB"],
                "score_breakdown": {"confidence": 0.7},
            }
        ],
        "generated_molecule_hypotheses": [
            {"generated_id": "GEN-MAOB-001", "molecule_name": "Generated MAOB 1"}
        ],
        "summary": {"candidate_count": 1, "generated_candidate_count": 1},
    }
    (run_dir / "candidates.json").write_text(json.dumps(payload))
    (run_dir / "report.md").write_text("# Report\nCitation PMID:12345\n")
    (run_dir / "trace.json").write_text("{}")
    return run_dir


def _result(text: str) -> CodexTaskResult:
    now = datetime.now(UTC)
    return CodexTaskResult(
        task_id="artifact-context-test",
        task_type="summarize_run",
        status="succeeded",
        output_text=text,
        output_json=None,
        stdout=text,
        stderr="",
        return_code=0,
        artifacts_read=[],
        artifacts_written=[],
        commands_observed=[],
        guardrail_warnings=[],
        usage_summary={},
        started_at=now,
        completed_at=now,
        metadata={},
    )
