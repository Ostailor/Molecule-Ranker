from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
import requests
import uvicorn
from fastapi import FastAPI

from molecule_ranker.platform.jobs import PlatformJobQueue
from molecule_ranker.workspace.store import ProjectWorkspaceStore
from tests.test_web_dashboard import _app, _write_codex_output, _write_run

pytestmark = pytest.mark.browser

BROWSER_SMOKE_ENV = "MOLECULE_RANKER_RUN_BROWSER_SMOKE"
SECRET_CANARY = "browser-secret-token-12345"


def test_hosted_dashboard_browser_smoke_flow(tmp_path: Path) -> None:
    if os.environ.get(BROWSER_SMOKE_ENV) != "1":
        pytest.skip(f"Set {BROWSER_SMOKE_ENV}=1 to run optional Playwright smoke tests.")
    playwright = pytest.importorskip("playwright.sync_api")

    app = _app(tmp_path)
    with _serve_app(app) as base_url:
        with playwright.sync_playwright() as manager:
            try:
                browser = manager.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is not installed: {exc}")
            try:
                page = browser.new_page()
                _login(page, base_url)
                _create_project(page, base_url)
                _register_mock_run_artifacts(tmp_path)
                _seed_failed_job(app)

                _assert_page(
                    page,
                    f"{base_url}/dashboard",
                    ["Projects", "First-run setup", "Create project"],
                )
                _assert_page(
                    page,
                    f"{base_url}/dashboard/projects/browser-project",
                    [
                        "Research boundaries",
                        "Generated molecule disclaimer",
                        "Codex output",
                        "Model prediction",
                        "Evaluation artifact",
                    ],
                )
                _assert_page(
                    page,
                    f"{base_url}/dashboard/projects/browser-project/runs/browser-run/candidates",
                    ["Candidate ranking table", "Rasagiline", "Scores are model outputs"],
                )
                _assert_page(
                    page,
                    f"{base_url}/dashboard/projects/browser-project/runs/browser-run/generated",
                    [
                        "Generated molecules are computational hypotheses",
                        "not validated actives",
                        "Computational hypothesis",
                        "Hypothesis-1",
                    ],
                )
                _assert_page(
                    page,
                    f"{base_url}/dashboard/projects/browser-project/review",
                    [
                        "Review workflow is optional",
                        "Pending review items",
                        "Reviewer comments are separate from model scores",
                    ],
                )
                _assert_page(
                    page,
                    f"{base_url}/dashboard/projects/browser-project/codex",
                    [
                        "Codex-generated summaries are assistant outputs, not evidence",
                        "Assistant summary grounded in artifacts.",
                    ],
                )
                _assert_page(
                    page,
                    f"{base_url}/dashboard/projects/browser-project/design/readiness",
                    [
                        "Experiment-readiness queue",
                        "Computational hypothesis",
                        "ready_for_expert_review",
                    ],
                )
                _assert_page(
                    page,
                    f"{base_url}/dashboard/admin/jobs",
                    ["Job queue", "failed", "dashboard_build"],
                )
                _assert_page(
                    page,
                    f"{base_url}/dashboard/admin/support",
                    ["Admin support console", "Pilot readiness", "Failed jobs"],
                )

                page.get_by_role("button", name="Run readiness check").click()
                page.wait_for_load_state("networkidle")
                _assert_body_contains(page, ["completed", "report"])
                _assert_body_excludes(page, [SECRET_CANARY, ".env"])

                page.goto(f"{base_url}/dashboard/admin/support", wait_until="networkidle")
                page.get_by_role("button", name="Generate support bundle").click()
                page.wait_for_load_state("networkidle")
                _assert_body_contains(page, ["created", "manifest"])
                _assert_body_excludes(page, [SECRET_CANARY, ".env"])
            finally:
                browser.close()


def _login(page: Any, base_url: str) -> None:
    page.goto(f"{base_url}/login", wait_until="networkidle")
    _assert_body_contains(page, ["Research use only", "molecule-ranker"])
    page.get_by_label("Email").fill("admin@example.com")
    page.get_by_label("Password").fill("Admin-password-1")
    page.get_by_role("button", name="Login").click()
    page.wait_for_url("**/dashboard")
    _assert_body_contains(page, ["First-run setup"])


def _create_project(page: Any, base_url: str) -> None:
    page.goto(f"{base_url}/dashboard/projects", wait_until="networkidle")
    page.get_by_label("Project ID").fill("browser-project")
    page.get_by_label("Project name").fill("Browser smoke project")
    page.get_by_role("button", name="Create project").click()
    page.wait_for_url("**/dashboard/projects/browser-project")
    _assert_body_contains(page, ["Browser smoke project", "Research boundaries"])


def _register_mock_run_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "browser-run"
    _write_run(run_dir, candidate_name="Rasagiline", assay_file_name="assay-results.csv")
    store = ProjectWorkspaceStore(tmp_path)
    workspace = store.load()
    store.register_run_dir(run_dir, run_id="browser-run", workspace=workspace)
    _write_codex_output(tmp_path)
    secret_file = tmp_path / ".env"
    secret_file.write_text(f"API_KEY={SECRET_CANARY}\n", encoding="utf-8")


def _seed_failed_job(app: FastAPI) -> None:
    database = cast(Any, app.state.platform_database)
    admin = database.list_users()[0]
    job = PlatformJobQueue(database).enqueue(
        job_type="dashboard_build",
        requested_by=admin,
        project_id="browser-project",
        config_snapshot={"dashboard_smoke": True},
    )
    PlatformJobQueue(database).fail(job, RuntimeError(f"API_KEY={SECRET_CANARY}"))


def _assert_page(page: Any, url: str, snippets: list[str]) -> None:
    page.goto(url, wait_until="networkidle")
    _assert_body_contains(page, snippets)
    _assert_body_excludes(page, [SECRET_CANARY, "API_KEY=", ".env"])


def _assert_body_contains(page: Any, snippets: list[str]) -> None:
    body = page.locator("body").inner_text(timeout=5_000)
    for snippet in snippets:
        assert snippet in body


def _assert_body_excludes(page: Any, snippets: list[str]) -> None:
    body = page.locator("body").inner_text(timeout=5_000)
    for snippet in snippets:
        assert snippet not in body


@contextmanager
def _serve_app(app: FastAPI) -> Iterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_server(base_url)
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _wait_for_server(base_url: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=0.5)
        except requests.RequestException:
            time.sleep(0.1)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.1)
    raise RuntimeError("Timed out waiting for hosted dashboard smoke server.")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
