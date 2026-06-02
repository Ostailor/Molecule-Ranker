from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.pilot.usability import run_usability_checks
from tests.test_web_dashboard import _app, _web_login


def test_usability_checks_cover_enterprise_pilot_workflow_polish() -> None:
    report = run_usability_checks(Path(__file__).resolve().parents[1])

    assert len(report["checks"]) == 11
    assert report["failed_count"] == 0
    assert {check["check_id"] for check in report["checks"]} == {
        "first_run_setup_clarity",
        "dashboard_empty_states",
        "job_failure_explanations",
        "artifact_missing_explanations",
        "generated_molecule_warnings_visibility",
        "codex_output_labeling",
        "model_prediction_labeling",
        "benchmark_evaluation_labeling",
        "review_workflow_discoverability",
        "campaign_workflow_discoverability",
        "integration_dry_run_write_mode_clarity",
    }


def test_dashboard_empty_state_pages_render_pilot_guidance(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    home = client.get("/dashboard")
    project = _create_empty_project(client, tmp_path)

    assert home.status_code == 200
    assert "First-run checklist" in home.text
    assert "Project setup wizard" in home.text
    assert "What can I do next?" in home.text
    assert "Pilot feedback" in home.text
    assert project.status_code == 200
    assert "Job failure remediation" in project.text
    assert "Artifact missing remediation" in project.text
    assert "Generated molecule hypotheses are computational hypotheses" in project.text
    assert "Codex output" in project.text
    assert "Model prediction" in project.text
    assert "Evaluation artifact" in project.text


def test_next_steps_command_works(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "run-a"
    run_dir.mkdir(parents=True)
    (run_dir / "candidates.json").write_text("{}\n")

    result = CliRunner().invoke(app, ["next-steps", str(run_dir)])

    assert result.exit_code == 0, result.output
    assert "Next steps for" in result.output
    assert "Review candidate ranking outputs" in result.output
    assert "generated molecule hypotheses" in result.output


def test_error_explanations_contain_safe_remediation() -> None:
    result = CliRunner().invoke(app, ["explain-error", "artifact-not-found"])

    assert result.exit_code == 0, result.output
    assert "Check that the artifact ID belongs to the selected project" in result.output
    lowered = result.output.lower()
    for prohibited in ("medical", "lab", "synthesis", "dosing"):
        assert prohibited not in lowered


def test_doctor_command_runs_enterprise_pilot_checks(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "doctor",
            "--root",
            str(Path(__file__).resolve().parents[1]),
            "--db-path",
            str(tmp_path / "doctor.sqlite"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"pilot_readiness"' in result.output
    assert '"usability"' in result.output


def _create_empty_project(client: TestClient, tmp_path: Path):
    csrf_token = client.cookies.get("mr_csrf_token")
    assert csrf_token is not None
    created = client.post(
        "/dashboard/projects/create",
        data={
            "csrf_token": csrf_token,
            "workspace_id": "empty-project",
            "name": "Empty project",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    return client.get("/dashboard/projects/empty-project")
