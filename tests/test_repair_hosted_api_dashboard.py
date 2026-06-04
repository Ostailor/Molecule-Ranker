from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import update

from molecule_ranker.agent_repair.hosted import RepairHostedStore
from molecule_ranker.agent_repair.schemas import (
    FailureDiagnosis,
    RegressionCheck,
    RepairAction,
    RepairExecution,
    RepairPlan,
)
from molecule_ranker.platform.database import users
from molecule_ranker.server import create_app


def test_repair_api_permissions_enforced(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    admin_headers = _api_login(client, "admin@example.com", "Admin-password-1")
    viewer = client.post(
        "/admin/users",
        json={"email": "viewer@example.com", "password": "Viewer-password-1"},
        headers=admin_headers,
    )
    repairer = client.post(
        "/admin/users",
        json={"email": "repairer@example.com", "password": "Repairer-password-1"},
        headers=admin_headers,
    )
    assert viewer.status_code == 200, viewer.text
    assert repairer.status_code == 200, repairer.text
    _grant_metadata_permissions(client, repairer.json()["user"]["user_id"], ["repair:diagnose"])

    viewer_headers = _api_login(client, "viewer@example.com", "Viewer-password-1")
    repairer_headers = _api_login(client, "repairer@example.com", "Repairer-password-1")
    payload = {"failure_category": "tool_error", "error_summary": "pytest failed"}

    blocked = client.post("/api/v2/repair/diagnose", json=payload, headers=viewer_headers)
    allowed = client.post("/api/v2/repair/diagnose", json=payload, headers=repairer_headers)
    read_blocked = client.get("/api/v2/repair/executions", headers=repairer_headers)

    assert blocked.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert read_blocked.status_code == 403


def test_repair_approval_queue_renders(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    _seed_repair_store(tmp_path)
    _web_login(client, "admin@example.com", "Admin-password-1")

    response = client.get("/dashboard/repair/approvals")

    assert response.status_code == 200, response.text
    assert "Repair approval queue" in response.text
    assert "approval-1" in response.text
    assert "approval required for external write" in response.text


def test_repair_regression_result_visible(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    _seed_repair_store(tmp_path)
    _web_login(client, "admin@example.com", "Admin-password-1")

    checks = client.get("/dashboard/repair/regression-checks")
    timeline = client.get("/dashboard/repair/executions/execution-1")

    assert checks.status_code == 200, checks.text
    assert timeline.status_code == 200, timeline.text
    assert "regression-1" in checks.text
    assert "schema_contract" in checks.text
    assert "regression-1" in timeline.text
    assert "Repair execution timeline" in timeline.text


def test_repair_dashboard_redacts_secrets(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    _seed_repair_store(tmp_path)
    _web_login(client, "admin@example.com", "Admin-password-1")

    approval = client.get("/dashboard/repair/approvals")
    diagnosis = client.get("/dashboard/repair/diagnoses/diagnosis-1")

    assert approval.status_code == 200, approval.text
    assert diagnosis.status_code == 200, diagnosis.text
    assert "sk-secret-value-123456789" not in approval.text
    assert "hidden-token-value" not in diagnosis.text
    assert "[REDACTED" in approval.text or "[REDACTED" in diagnosis.text


def _app(tmp_path: Path) -> Any:
    return create_app(
        root_dir=tmp_path,
        hosted_mode=True,
        auth_secret="test-hosted-secret-value-with-at-least-32-chars",
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="Admin-password-1",
    )


def _seed_repair_store(root: Path) -> None:
    store = RepairHostedStore(root)
    diagnosis = FailureDiagnosis(
        diagnosis_id="diagnosis-1",
        failure_object_type="guardrail",
        failure_object_id="guardrail-1",
        failure_category="guardrail_failed",
        root_cause_summary="Guardrail failed with token=hidden-token-value",
        evidence=[{"log": "OPENAI_API_KEY=sk-secret-value-123456789"}],
        recoverable=True,
        repairability="approval_required",
        confidence=0.8,
        warnings=["token hidden-token-value was present"],
        created_at=datetime.now(UTC),
        metadata={},
    )
    action = RepairAction(
        repair_action_id="action-1",
        action_type="retry_external_read",
        target_object_type="tool_call",
        target_object_id="tool-1",
        tool_name=None,
        tool_args={},
        expected_effect="Retry read.",
        side_effect_level="external_read",
        requires_approval=True,
        approval_reason="approval required for external write sk-secret-value-123456789",
        risk_level="medium",
        metadata={},
    )
    plan = RepairPlan(
        repair_plan_id="plan-1",
        diagnosis_id=diagnosis.diagnosis_id,
        session_id=None,
        plan_summary="Repair plan with secret sk-secret-value-123456789",
        actions=[action],
        expected_artifacts=[],
        rollback_plan=[],
        requires_human_approval=True,
        scientific_guardrails=["Do not invent scientific truth."],
        validated=True,
        validation_errors=[],
        created_by="deterministic",
        created_at=datetime.now(UTC),
        metadata={},
    )
    execution = RepairExecution(
        repair_execution_id="execution-1",
        repair_plan_id=plan.repair_plan_id,
        status="succeeded",
        executed_actions=[{"action_id": "action-1", "status": "succeeded"}],
        artifacts_created=[],
        artifacts_modified=[],
        jobs_created=[],
        approvals_requested=["approval-1"],
        regression_check_ids=["regression-1"],
        warnings=[],
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        metadata={},
    )
    regression = RegressionCheck(
        regression_check_id="regression-1",
        repair_execution_id=execution.repair_execution_id,
        check_type="schema_contract",
        passed=True,
        findings=["schema contract passed"],
        artifacts_checked=["artifact-1"],
        created_at=datetime.now(UTC),
        metadata={},
    )
    store.save_diagnosis(diagnosis)
    store.save_plan(plan)
    store.save_execution(execution)
    store.save_regression_check(regression)
    store.save_approval(
        {
            "approval_id": "approval-1",
            "repair_plan_id": plan.repair_plan_id,
            "repair_action_id": action.repair_action_id,
            "status": "pending",
            "reason": action.approval_reason,
            "created_at": datetime.now(UTC).isoformat(),
        }
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


def _grant_metadata_permissions(
    client: TestClient,
    user_id: str,
    permissions: list[str],
) -> None:
    app = cast(Any, client.app)
    database = app.state.platform_database
    with database.engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.user_id == user_id)
            .values(metadata_json={"permissions": permissions})
        )
