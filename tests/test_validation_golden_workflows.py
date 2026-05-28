from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.validation import GoldenWorkflow, list_golden_workflows, run_golden_workflows
from molecule_ranker.validation.runner import check_forbidden_outputs


def test_golden_workflow_registry_defines_v1_workflows() -> None:
    workflows = list_golden_workflows()

    assert [workflow.workflow_id for workflow in workflows] == [
        "existing_molecule_ranking",
        "generation_workflow",
        "review_workflow",
        "experimental_feedback_workflow",
        "codex_backbone_workflow",
        "hosted_platform_workflow",
        "integration_sync_workflow",
        "v1_1_design_optimization_workflow",
        "v1_1_agentic_generation_workflow",
        "v1_1_generator_benchmark_workflow",
    ]
    assert all(isinstance(workflow, GoldenWorkflow) for workflow in workflows)
    assert all(workflow.expected_artifacts for workflow in workflows)
    assert all(workflow.required_checks for workflow in workflows)
    assert all(workflow.forbidden_outputs for workflow in workflows)


def test_all_golden_workflows_run_in_deterministic_test_mode(tmp_path: Path) -> None:
    report = run_golden_workflows(workflow="all", output_dir=tmp_path)

    assert report.status == "pass"
    assert report.live_validation is False
    assert len(report.results) == 10
    assert {result.status for result in report.results} == {"pass"}
    assert {result.mode for result in report.results} == {"test"}

    for result in report.results:
        assert result.artifact_dir.is_relative_to(tmp_path)
        assert result.missing_artifacts == []
        assert result.forbidden_findings == []
        assert result.metadata["external_services"] == "mocked"
        for artifact in result.artifacts:
            assert artifact.exists()
            assert artifact.is_file()


def test_single_golden_workflow_selection(tmp_path: Path) -> None:
    report = run_golden_workflows(workflow="generation_workflow", output_dir=tmp_path)

    assert report.status == "pass"
    assert [result.workflow_id for result in report.results] == ["generation_workflow"]
    generated_report = tmp_path / "generation_workflow" / "generated_report.md"
    assert generated_report.exists()
    assert "computational hypotheses" in generated_report.read_text()


def test_forbidden_output_check_flags_overclaims(tmp_path: Path) -> None:
    artifact = tmp_path / "report.md"
    artifact.write_text("SyntheticCandidate cures ExampleDisease.\n")

    findings = check_forbidden_outputs([artifact], ["cures"])

    assert len(findings) == 1
    assert findings[0].artifact_path == artifact
    assert findings[0].phrase == "cures"


def test_validate_golden_cli_runs_all_workflows(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "validate",
            "golden",
            "--workflow",
            "all",
            "--root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["workflow_count"] == 10
    assert payload["live_validation"] is False
