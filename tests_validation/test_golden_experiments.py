from __future__ import annotations

from pathlib import Path

from conftest import assert_common_release_invariants, load_json, run_validation_workflow


def test_experimental_feedback_release_validation(tmp_path: Path) -> None:
    result = run_validation_workflow(tmp_path, "experimental_feedback_workflow")

    assert_common_release_invariants(result)

    import_report = load_json(result.artifact_dir / "import_report.json")
    experimental_results = load_json(result.artifact_dir / "experimental_results.json")
    scores = load_json(result.artifact_dir / "recalibrated_scores.json")

    assert import_report["source_type"] == "file"
    assert import_report["source_path"] == "assay_results.csv"
    assert import_report["live_external_import"] is False
    assert {row["qc_status"] for row in experimental_results["results"]} == {"passed", "failed"}

    score = scores["scores"][0]
    assert score["updated"] <= score["previous"]
    assert score["failed_qc_result_ids"] == ["SYN-R2"]
    assert score["failed_qc_improved_score"] is False
