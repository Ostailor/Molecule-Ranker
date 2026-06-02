from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.pilot.feedback import PilotFeedbackStore, submit_feedback
from tests.test_web_dashboard import _app, _web_login


def test_pilot_feedback_redacts_secrets_and_marks_not_evidence(tmp_path: Path) -> None:
    feedback = submit_feedback(
        root_dir=tmp_path,
        user_id="user-1",
        project_id="project-1",
        page_or_command="/dashboard/projects/project-1?api_key=secret-token-value",
        feedback_type="bug_report",
        severity="high",
        text="The page failed with service_token=secret-token-value",
        artifact_refs=["artifact-1"],
        metadata={"password": "secret-token-value", "browser": "test"},
    )

    assert "secret-token-value" not in feedback.model_dump_json()
    assert "[REDACTED]" in feedback.text
    assert feedback.metadata["not_scientific_evidence"] is True
    assert feedback.metadata["not_biomedical_evidence"] is True
    listed = PilotFeedbackStore(tmp_path).list()
    assert listed[0].feedback_id == feedback.feedback_id


def test_feedback_export_excludes_cache_and_artifact_payloads(tmp_path: Path) -> None:
    cache_file = tmp_path / ".cache" / "payload.json"
    cache_file.parent.mkdir()
    cache_file.write_text('{"api_key":"secret-token-value"}', encoding="utf-8")
    artifact_file = tmp_path / "artifacts" / "artifact-1.json"
    artifact_file.parent.mkdir()
    artifact_file.write_text('{"payload":"do not include"}', encoding="utf-8")
    submit_feedback(
        root_dir=tmp_path,
        user_id="user-1",
        page_or_command="feedback submit",
        feedback_type="feature_request",
        text="Please improve the empty state.",
        artifact_refs=["artifact-1"],
    )
    output = tmp_path / "feedback_export.json"

    PilotFeedbackStore(tmp_path).export(output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    exported = output.read_text(encoding="utf-8")
    assert payload["excludes_cache_payloads"] is True
    assert payload["excludes_artifact_payloads"] is True
    assert payload["feedback"][0]["artifact_refs"] == ["artifact-1"]
    assert "secret-token-value" not in exported
    assert "do not include" not in exported


def test_feedback_cli_submit_list_export(tmp_path: Path) -> None:
    runner = CliRunner()
    submit = runner.invoke(
        app,
        [
            "feedback",
            "submit",
            "Bug on queue page api_key=secret-token-value",
            "--root",
            str(tmp_path),
            "--type",
            "bug_report",
            "--user-id",
            "user-1",
            "--page-or-command",
            "molecule-ranker ops metrics",
            "--artifact-ref",
            "artifact-1",
        ],
    )
    listed = runner.invoke(app, ["feedback", "list", "--root", str(tmp_path)])
    export_path = tmp_path / "feedback.json"
    exported = runner.invoke(
        app,
        ["feedback", "export", "--root", str(tmp_path), "--output", str(export_path)],
    )

    assert submit.exit_code == 0, submit.output
    assert listed.exit_code == 0, listed.output
    assert exported.exit_code == 0, exported.output
    assert "secret-token-value" not in submit.output
    assert "secret-token-value" not in listed.output
    assert export_path.exists()
    assert "not_scientific_evidence" in export_path.read_text(encoding="utf-8")


def test_feedback_dashboard_pages_and_admin_permissions(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    page = client.get("/dashboard/feedback")
    submitted = client.post(
        "/dashboard/feedback/submit",
        data={
            "text": "Feedback with token=secret-token-value",
            "feedback_type": "usability_issue",
            "severity": "medium",
            "page_or_command": "/dashboard",
        },
    )
    admin_page = client.get("/dashboard/admin/feedback")
    exported = client.get("/dashboard/admin/feedback/export")

    assert page.status_code == 200
    assert "Submit feedback" in page.text
    assert submitted.status_code == 200, submitted.text
    assert "Feedback submitted" in submitted.text
    assert "secret-token-value" not in submitted.text
    assert admin_page.status_code == 200
    assert "Pilot feedback admin" in admin_page.text
    assert "secret-token-value" not in admin_page.text
    assert exported.status_code == 200
    assert exported.json()["excludes_cache_payloads"] is True


def test_feedback_admin_page_requires_admin(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    client.cookies.clear()
    _web_login(client, "viewer@example.com", "Viewer-password-1")

    response = client.get("/dashboard/admin/feedback")

    assert response.status_code == 403


def _api_login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
