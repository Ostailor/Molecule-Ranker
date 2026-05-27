from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from molecule_ranker.server import create_app
from molecule_ranker.workspace.schemas import ArtifactRecord
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def test_dashboard_pages_require_auth(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    for path in [
        "/dashboard",
        "/dashboard/projects",
        "/dashboard/projects/workspace-a",
        "/dashboard/projects/workspace-a/runs",
        "/dashboard/projects/workspace-a/runs/run-a",
        "/dashboard/projects/workspace-a/runs/run-a/candidates",
        "/dashboard/projects/workspace-a/runs/run-a/generated",
        "/dashboard/projects/workspace-a/runs/run-a/developability",
        "/dashboard/projects/workspace-a/runs/run-a/experiments",
        "/dashboard/projects/workspace-a/runs/run-a/active-learning",
        "/dashboard/projects/workspace-a/activity",
        "/dashboard/projects/workspace-a/review",
        "/dashboard/projects/workspace-a/candidates/Rasagiline",
        "/dashboard/projects/workspace-a/codex",
        "/dashboard/notifications",
        "/dashboard/audit",
        "/dashboard/admin",
        "/dashboard/admin/users",
        "/dashboard/admin/organizations",
        "/dashboard/admin/teams",
        "/dashboard/admin/memberships",
        "/dashboard/admin/service-accounts",
        "/dashboard/admin/audit",
        "/dashboard/admin/jobs",
        "/dashboard/admin/health",
        "/dashboard/admin/codex-worker",
    ]:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_login_page_renders_research_use_form(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    response = client.get("/login")

    assert response.status_code == 200
    assert "molecule-ranker" in response.text
    assert 'name="email"' in response.text
    assert 'name="password"' in response.text
    assert "Research use only" in response.text


def test_dashboard_core_pages_render_project_run_and_research_views(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard": ["Projects", "Research"],
        "/dashboard/projects": ["Projects", "workspace-a"],
        "/dashboard/projects/workspace-a": ["Research boundaries", "Runs"],
        "/dashboard/projects/workspace-a/runs": ["Runs", "Parkinson disease"],
        "/dashboard/projects/workspace-a/runs/run-a": ["Run views", "Experimental results"],
        "/dashboard/projects/workspace-a/runs/run-a/candidates": [
            "Candidate ranking table",
            "Rasagiline",
        ],
        "/dashboard/projects/workspace-a/runs/run-a/developability": [
            "Developability view",
            "computational screening",
        ],
        "/dashboard/projects/workspace-a/runs/run-a/experiments": [
            "Experimental evidence",
            "Model predictions",
        ],
        "/dashboard/projects/workspace-a/runs/run-a/active-learning": [
            "Active learning view",
            "measure selectivity",
        ],
        "/dashboard/projects/workspace-a/review": ["Review queue", "Rasagiline"],
    }
    for path, snippets in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for snippet in snippets:
            assert snippet in response.text, path


def test_admin_dashboard_pages_render_for_admin(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")

    expectations = {
        "/dashboard/admin": "Admin controls",
        "/dashboard/admin/users": "Users",
        "/dashboard/admin/organizations": "Organizations",
        "/dashboard/admin/teams": "Teams",
        "/dashboard/admin/memberships": "Memberships",
        "/dashboard/admin/service-accounts": "Service accounts",
        "/dashboard/admin/audit": "Audit log",
        "/dashboard/admin/jobs": "Job queue",
        "/dashboard/admin/health": "System health",
        "/dashboard/admin/codex-worker": "Codex worker status",
    }
    for path, snippet in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert snippet in response.text


def test_artifact_download_requires_permission_and_serves_safe_artifact(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    artifact_path = tmp_path / "safe-artifact.txt"
    artifact_path.write_text("registered artifact\n")
    _append_workspace_artifact(tmp_path, artifact_path, artifact_id="safe-artifact")

    unauthenticated = client.get("/projects/workspace-a/artifacts/safe-artifact/download")
    downloaded = client.get(
        "/projects/workspace-a/artifacts/safe-artifact/download",
        headers=admin_headers,
    )

    assert unauthenticated.status_code == 401
    assert downloaded.status_code == 200
    assert downloaded.text == "registered artifact\n"
    assert downloaded.headers["X-Artifact-ID"] == "safe-artifact"


def test_dashboard_rbac_hides_unauthorized_projects(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    created = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    client.cookies.clear()

    _web_login(client, "viewer@example.com", "Viewer-password-1")
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "workspace-a" not in response.text
    assert "No authorized projects" in response.text


def test_dashboard_artifact_path_traversal_is_blocked(tmp_path: Path) -> None:
    outside = tmp_path.parent / "dashboard-outside-secret.txt"
    outside.write_text("secret")
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _append_workspace_artifact(tmp_path, outside, artifact_id="outside-secret")

    outside_response = client.get(
        "/projects/workspace-a/artifacts/outside-secret/download",
        headers=admin_headers,
    )
    traversal_response = client.get(
        "/projects/workspace-a/artifacts/..%2F.env/download",
        headers=admin_headers,
    )

    assert outside_response.status_code == 403
    assert traversal_response.status_code in {403, 404}


def test_dashboard_escapes_user_provided_html(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(
        client,
        tmp_path,
        admin_headers,
        project_id="workspace-a",
        candidate_name="<script>alert(1)</script>",
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/runs/run-a/candidates")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_dashboard_cookie_logout_requires_csrf_token(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    _web_login(client, "admin@example.com", "Admin-password-1")
    csrf_token = client.cookies.get("mr_csrf_token")
    assert csrf_token is not None

    blocked = client.post("/logout", follow_redirects=False)
    allowed = client.post(
        "/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 303
    assert allowed.headers["location"] == "/login"


def test_service_account_token_cannot_access_browser_dashboard(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    app_state = cast(Any, client.app).state
    admin = app_state.platform_database.list_users()[0]
    service_user = app_state.platform_database.create_user(
        email="svc@example.com",
        password="Service-password-1",
    )
    token = "mrs_service_token_for_dashboard_test"
    app_state.platform_database.create_service_account_token(
        name="automation",
        token=token,
        user_id=service_user.user_id,
        created_by_user_id=admin.user_id,
        scopes=["project:read"],
    )

    response = client.get(
        "/dashboard",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_generated_molecules_are_labeled_as_hypotheses(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/runs/run-a/generated")

    assert response.status_code == 200
    assert "Generated molecules are computational hypotheses" in response.text
    assert "Computational hypothesis" in response.text
    assert "Hypothesis-1" in response.text


def test_codex_outputs_are_separate_from_evidence(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _write_codex_output(tmp_path)
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/codex")

    assert response.status_code == 200
    assert "Codex-generated summaries are assistant outputs, not evidence" in response.text
    assert "Codex-generated summaries" in response.text
    assert "Assistant summary grounded in artifacts." in response.text


def test_codex_output_is_safely_escaped(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    _write_codex_output(tmp_path, output_text="<script>alert(1)</script> Codex summary")
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/codex")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; Codex summary" in response.text


def test_uploaded_assay_file_names_are_escaped(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(
        client,
        tmp_path,
        admin_headers,
        project_id="workspace-a",
        assay_file_name="<script>assay</script>.csv",
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/runs/run-a/experiments")

    assert response.status_code == 200
    assert "<script>assay</script>.csv" not in response.text
    assert "\\u003cscript\\u003eassay\\u003c/script\\u003e.csv" in response.text


def test_audit_log_inaccessible_to_non_admin(tmp_path: Path) -> None:
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

    dashboard_audit = client.get("/dashboard/audit")
    admin_audit = client.get("/dashboard/admin/audit")

    assert dashboard_audit.status_code == 403
    assert admin_audit.status_code == 403


def test_candidate_comments_are_separate_from_evidence(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    _create_project_with_run(client, tmp_path, admin_headers, project_id="workspace-a")
    app = cast(Any, client.app)
    admin = app.state.platform_database.list_users()[0]
    app.state.platform_database.add_project_comment(
        project_id="workspace-a",
        author_user_id=admin.user_id,
        body="Team note: review selectivity rationale.",
        object_type="candidate",
        object_id="Rasagiline",
        candidate_id="Rasagiline",
    )
    client.cookies.clear()
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/projects/workspace-a/candidates/Rasagiline")

    assert response.status_code == 200
    assert "Source-backed evidence" in response.text
    assert "Team comments" in response.text
    assert "Comments are collaboration notes, not biomedical evidence" in response.text
    assert "Team note: review selectivity rationale." in response.text


def _app(tmp_path: Path):
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _api_login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _web_login(client: TestClient, email: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/dashboard"


def _create_project_with_run(
    client: TestClient,
    tmp_path: Path,
    headers: dict[str, str],
    *,
    project_id: str,
    candidate_name: str = "Rasagiline",
    assay_file_name: str = "assay-results.csv",
) -> None:
    created = client.post(
        "/projects",
        json={"workspace_id": project_id, "name": "Research"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    _write_run(tmp_path / "run-a", candidate_name=candidate_name, assay_file_name=assay_file_name)
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    store.register_run_dir(tmp_path / "run-a", run_id="run-a", workspace=workspace)


def _write_run(run_dir: Path, *, candidate_name: str, assay_file_name: str) -> None:
    run_dir.mkdir(parents=True)
    payload = {
        "success": True,
        "disease": {"canonical_name": "Parkinson disease"},
        "targets": [{"symbol": "MAOB"}],
        "candidates": [
            {
                "name": candidate_name,
                "origin": "existing",
                "known_targets": ["MAOB"],
                "score": 0.82,
                "score_breakdown": {"confidence": 0.7},
                "developability_summary": {"risk": "low"},
                "evidence_summary": {"literature": ["source-backed"]},
            }
        ],
        "generated_molecule_hypotheses": [
            {
                "name": "Hypothesis-1",
                "origin": "generated",
                "score": 0.41,
                "rationale": "Computationally generated follow-up hypothesis.",
            }
        ],
        "assay_results": [
            {
                "candidate_name": candidate_name,
                "assay_name": "binding screen",
                "result": "inactive",
                "source_file_name": assay_file_name,
            }
        ],
        "active_learning": {"batch_id": "batch-1", "suggestions": ["measure selectivity"]},
        "summary": {"candidate_count": 1, "generated_candidate_count": 1, "target_count": 1},
    }
    (run_dir / "candidates.json").write_text(json.dumps(payload))
    (run_dir / "report.md").write_text("# Report\n")


def _write_codex_output(
    tmp_path: Path,
    *,
    output_text: str = "Assistant summary grounded in artifacts.",
) -> None:
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    output_dir = tmp_path / ".molecule-ranker" / "codex_project_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "summarize_project-20260101T000000Z.json"
    output_path.write_text(
        json.dumps(
            {
                "task_type": "summarize_project",
                "workspace_id": workspace.workspace_id,
                "status": "succeeded",
                "output_text": output_text,
            }
        )
    )
    workspace.codex_outputs.append(
        {
            "task_type": "summarize_project",
            "status": "succeeded",
            "path": str(output_path),
            "artifact_refs": [],
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    store.save(workspace)


def _append_workspace_artifact(tmp_path: Path, path: Path, *, artifact_id: str) -> None:
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    data = path.read_bytes()
    workspace.artifacts.append(
        ArtifactRecord(
            artifact_id=artifact_id,
            workspace_id=workspace.workspace_id,
            path=str(path.resolve()),
            artifact_type="report",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
    )
    store.save(workspace)
