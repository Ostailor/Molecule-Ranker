from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation.enterprise_golden import (
    ENTERPRISE_GOLDEN_STEP_COUNT,
    run_enterprise_golden_workflow,
)


def test_enterprise_golden_workflow_passes_in_mocked_mode(tmp_path: Path) -> None:
    output_dir = tmp_path / "enterprise_golden"

    report = run_enterprise_golden_workflow(output_dir=output_dir, root_dir=tmp_path)

    assert report["status"] == "pass"
    assert report["mocked_mode"] is True
    assert report["step_count"] == ENTERPRISE_GOLDEN_STEP_COUNT
    assert all(step["status"] == "pass" for step in report["steps"])
    assert all(assertion["status"] == "pass" for assertion in report["assertions"])
    assert (output_dir / "enterprise_golden_report.json").exists()
    assert (output_dir / "validation_package" / "validation_package_manifest.json").exists()


def test_enterprise_golden_outputs_are_labeled_and_separated(tmp_path: Path) -> None:
    output_dir = tmp_path / "enterprise_golden"

    run_enterprise_golden_workflow(output_dir=output_dir, root_dir=tmp_path)

    generated = json.loads((output_dir / "06_generated_molecule.json").read_text())
    codex = json.loads((output_dir / "17_codex_summary.json").read_text())
    review = json.loads((output_dir / "18_review_workspace.json").read_text())
    evaluation = json.loads((output_dir / "16_benchmark_evaluation.json").read_text())
    campaign = json.loads((output_dir / "12_campaign.json").read_text())

    assert generated["hypothesis_only"] is True
    assert generated["evidence_boundary"] == "computational_hypothesis_requires_review"
    assert codex["artifact_type"] == "codex_task_result"
    assert codex["creates_evidence"] is False
    assert review["review_items"][0]["source_artifact_type"] != "evidence_item"
    assert evaluation["artifact_type"] == "evaluation"
    assert "protocol" not in json.dumps(campaign).lower()


def test_enterprise_golden_cli_outputs_json(tmp_path: Path) -> None:
    output_dir = tmp_path / "enterprise_golden"

    result = CliRunner().invoke(
        app,
        [
            "validate",
            "enterprise-golden",
            "--root",
            str(tmp_path),
            "--output",
            str(output_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["output_dir"] == str(output_dir.resolve())
    assert payload["assertion_summary"]["failed"] == 0
