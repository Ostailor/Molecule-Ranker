from __future__ import annotations

from pathlib import Path

from conftest import (
    assert_common_release_invariants,
    has_forbidden_key,
    load_json,
    run_validation_workflow,
)

from molecule_ranker.codex_backbone import CodexTask, NullCodexProvider


def test_codex_backbone_release_validation_uses_null_provider(tmp_path: Path) -> None:
    result = run_validation_workflow(tmp_path, "codex_backbone_workflow")

    assert_common_release_invariants(result)

    codex_backbone = load_json(result.artifact_dir / "codex_backbone.json")
    summary = load_json(result.artifact_dir / "codex_summary.json")
    explanation = load_json(result.artifact_dir / "candidate_explanation.json")

    assert codex_backbone["results"][0]["provider"] == "NullCodexProvider"
    assert summary["creates_evidence_items"] is False
    assert explanation["creates_evidence_items"] is False
    assert not has_forbidden_key(codex_backbone, "EvidenceItem")
    assert not has_forbidden_key(summary, "EvidenceItem")

    provider = NullCodexProvider()
    task = CodexTask(
        task_id="validation-null-codex",
        task_type="summarize_run",
        prompt="Summarize synthetic artifacts only.",
        working_directory=str(tmp_path),
        input_artifact_paths=[],
        allowed_commands=[],
        forbidden_commands=[],
        expected_output_format="json",
        timeout_seconds=30,
        require_json=True,
        metadata={},
    )
    provider_result = provider.run_task(task)
    assert provider_result.status == "succeeded"
    assert provider_result.metadata["live_validation"] is False
    assert provider_result.output_json is not None
    assert provider_result.output_json["creates_evidence_items"] is False
