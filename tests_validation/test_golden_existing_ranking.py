from __future__ import annotations

from pathlib import Path

from conftest import assert_common_release_invariants, load_json, run_validation_workflow


def test_existing_molecule_ranking_release_validation(tmp_path: Path) -> None:
    result = run_validation_workflow(tmp_path, "existing_molecule_ranking")

    assert_common_release_invariants(result)

    candidates = load_json(result.artifact_dir / "candidates.json")
    literature = load_json(result.artifact_dir / "literature.json")

    assert candidates["candidates"][0]["source_record_id"] == "synthetic-candidate-1"
    assert literature["literature"][0]["source_record_id"] == "synthetic-literature-1"
    assert candidates["candidates"][0]["score"] == 0.42
