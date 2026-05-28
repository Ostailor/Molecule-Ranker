from __future__ import annotations

from pathlib import Path

from conftest import assert_common_release_invariants, load_json, run_validation_workflow


def test_generation_workflow_release_validation(tmp_path: Path) -> None:
    result = run_validation_workflow(tmp_path, "generation_workflow")

    assert_common_release_invariants(result)

    generated = load_json(result.artifact_dir / "generated_candidates.json")
    molecule = generated["generated"][0]

    assert molecule["origin"] == "generated"
    assert molecule["is_generated"] is True
    assert molecule["label"] == "computational_hypothesis"
    assert molecule["evidence"] == []
    assert molecule["fake_evidence"] is False
    assert molecule["validated_active"] is False
    assert "not validated actives" in (result.artifact_dir / "generated_report.md").read_text()
