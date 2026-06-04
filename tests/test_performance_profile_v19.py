from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.performance.profiler import (
    PerformanceProfile,
    profile_synthetic_workflow,
)
from molecule_ranker.performance.reports import (
    performance_profile_to_json,
    render_performance_markdown,
    write_performance_reports,
)

REQUIRED_MEASUREMENTS = {
    "ranking_pipeline",
    "literature_retrieval",
    "generation",
    "developability",
    "model_training",
    "structure_workflow",
    "graph_build",
    "hypothesis_generation",
    "campaign_planning",
    "evaluation_benchmark",
    "dashboard_response",
    "api_response",
    "job_queue_wait",
    "job_run",
    "artifact_write",
    "artifact_read",
    "codex_task",
}


def test_synthetic_golden_profile_contains_required_measurements() -> None:
    profile = profile_synthetic_workflow("golden")

    assert isinstance(profile, PerformanceProfile)
    assert profile.version == "2.3.0"
    assert profile.workflow == "golden"
    assert profile.live_apis_enabled is False
    assert set(profile.measurements) == REQUIRED_MEASUREMENTS
    assert set(profile.memory_usage_by_step) == REQUIRED_MEASUREMENTS
    for measurement in profile.measurements.values():
        assert measurement["duration_ms"] >= 0
        assert measurement["source"] == "synthetic"
    for memory in profile.memory_usage_by_step.values():
        assert memory["peak_bytes"] >= 0
    assert profile.codex_task_metrics["timeout_rate"] == 0.0


def test_performance_reports_redact_secrets(tmp_path: Path) -> None:
    profile = profile_synthetic_workflow(
        "golden",
        metadata={
            "api_key": "sk-test-secret",
            "operator_note": "authorization: bearer-secret-value",
            "nested": {"token": "service-token-secret"},
        },
    )

    json_text = performance_profile_to_json(profile)
    markdown = render_performance_markdown(profile)
    json_path, markdown_path = write_performance_reports(profile, tmp_path)

    for text in (
        json_text,
        markdown,
        json_path.read_text(),
        markdown_path.read_text(),
    ):
        assert "sk-test-secret" not in text
        assert "bearer-secret-value" not in text
        assert "service-token-secret" not in text
        assert "[REDACTED]" in text


def test_performance_profile_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "performance",
            "profile",
            "--workflow",
            "golden",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    json_path = tmp_path / "performance_report.json"
    markdown_path = tmp_path / "performance_report.md"
    assert json_path.exists()
    assert markdown_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["workflow"] == "golden"
    assert payload["live_apis_enabled"] is False
    assert set(payload["measurements"]) == REQUIRED_MEASUREMENTS


def test_performance_report_cli_renders_from_profile(tmp_path: Path) -> None:
    profile = profile_synthetic_workflow("golden")
    profile_path = tmp_path / "performance_report.json"
    profile_path.write_text(performance_profile_to_json(profile) + "\n")
    output_path = tmp_path / "rendered.md"

    result = CliRunner().invoke(
        app,
        [
            "performance",
            "report",
            "--from-profile",
            str(profile_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    text = output_path.read_text()
    assert "# Performance Profile Report" in text
    assert "ranking_pipeline" in text
    assert "Codex task timeout rate" in text
