from __future__ import annotations

from pathlib import Path

from conftest import (
    assert_common_release_invariants,
    has_forbidden_key,
    load_json,
    run_validation_workflow,
)


def test_review_workflow_release_validation(tmp_path: Path) -> None:
    result = run_validation_workflow(tmp_path, "review_workflow")

    assert_common_release_invariants(result)

    review_queue = load_json(result.artifact_dir / "review_queue.json")
    decisions = load_json(result.artifact_dir / "review_decisions.json")
    dossier = (result.artifact_dir / "candidate_dossier.md").read_text().lower()

    assert review_queue["review_items"][0]["source_record_id"] == "synthetic-candidate-1"
    assert decisions["decisions"][0]["decision"] == "needs_review"
    assert "separate from evidence" in dossier
    assert not has_forbidden_key(review_queue, "EvidenceItem")
    assert not has_forbidden_key(decisions, "EvidenceItem")
