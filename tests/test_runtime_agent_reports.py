from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.runtime_agents.executor import RuntimeExecutionResult
from molecule_ranker.runtime_agents.guardrails import (
    RuntimeGuardrailResult,
    RuntimeGuardrailViolation,
)
from molecule_ranker.runtime_agents.reports import write_runtime_artifacts
from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeAgentAuditEvent,
    RuntimeAgentSession,
    RuntimeApprovalRequest,
    RuntimeToolResult,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


def test_runtime_artifacts_written(tmp_path: Path) -> None:
    bundle = write_runtime_artifacts(
        tmp_path,
        session=_session(),
        execution_result=_execution_result(),
        approvals=[],
        guardrail_report=RuntimeGuardrailResult(allowed=True),
    )

    expected = {
        "runtime_session.json",
        "runtime_action_plan.json",
        "runtime_tool_results.json",
        "runtime_audit_log.json",
        "runtime_guardrail_report.json",
        "runtime_summary.md",
    }

    assert set(bundle.artifact_paths) == expected
    assert all((tmp_path / name).exists() for name in expected)


def test_report_includes_approvals(tmp_path: Path) -> None:
    approval = RuntimeApprovalRequest(
        approval_id="approval-1",
        session_id="session-1",
        plan_id="plan-1",
        step_id="step-1",
        requested_by="codex",
        approval_type="external_write",
        reason="Write integration sync.",
        risk_summary="External write.",
        requested_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
        status="pending",
        decided_by=None,
        decided_at=None,
        decision_rationale=None,
        metadata={},
    )

    write_runtime_artifacts(
        tmp_path,
        session=_session(),
        execution_result=_execution_result(),
        approvals=[approval],
        guardrail_report=RuntimeGuardrailResult(allowed=True),
    )

    summary = (tmp_path / "runtime_summary.md").read_text(encoding="utf-8")

    assert "## 5. Approvals requested" in summary
    assert "approval-1" in summary
    assert "external_write" in summary


def test_guardrail_failures_shown(tmp_path: Path) -> None:
    guardrail_report = RuntimeGuardrailResult(
        allowed=False,
        violations=[
            RuntimeGuardrailViolation(
                scope="output",
                code="fake_citation",
                message="Blocked fake citation: PMID:12345678.",
            )
        ],
    )

    write_runtime_artifacts(
        tmp_path,
        session=_session(),
        execution_result=_execution_result(),
        approvals=[],
        guardrail_report=guardrail_report,
    )

    summary = (tmp_path / "runtime_summary.md").read_text(encoding="utf-8")
    report = (tmp_path / "runtime_guardrail_report.json").read_text(encoding="utf-8")

    assert "fake_citation" in summary
    assert "fake_citation" in report
    assert "blocked" in summary.lower()


def test_runtime_report_does_not_repeat_forbidden_text(tmp_path: Path) -> None:
    execution = _execution_result(
        result_output={
            "summary": (
                "Lab protocol: incubate cells. Synthesis route uses reagent X. "
                "Dose patients at 5 mg/kg."
            )
        },
        warnings=["Lab protocol: incubate cells."],
    )

    write_runtime_artifacts(
        tmp_path,
        session=_session(),
        execution_result=execution,
        approvals=[],
        guardrail_report=RuntimeGuardrailResult(
            allowed=False,
            violations=[
                RuntimeGuardrailViolation(
                    scope="output",
                    code="lab_protocol",
                    message="Blocked lab protocol text.",
                )
            ],
        ),
    )

    summary = (tmp_path / "runtime_summary.md").read_text(encoding="utf-8")
    tool_results = (tmp_path / "runtime_tool_results.json").read_text(encoding="utf-8")

    assert "incubate cells" not in summary
    assert "Synthesis route" not in summary
    assert "5 mg/kg" not in summary
    assert "incubate cells" not in tool_results
    assert "Synthesis route" not in tool_results
    assert "5 mg/kg" not in tool_results


def _session() -> RuntimeAgentSession:
    return RuntimeAgentSession(
        session_id="session-1",
        project_id="project-1",
        org_id="org-1",
        user_id="user-1",
        user_goal="Rank candidates and prepare review output.",
        autonomy_level="execute_with_approval",
        status="succeeded",
        started_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
        completed_at=datetime(2026, 6, 3, 12, 5, tzinfo=UTC),
        metadata={},
    )


def _execution_result(
    *,
    result_output: dict[str, object] | None = None,
    warnings: list[str] | None = None,
) -> RuntimeExecutionResult:
    plan = _plan()
    result = RuntimeToolResult(
        result_id="result-1",
        step_id="step-1",
        tool_name="run_ranking",
        status="succeeded",
        output=result_output or {"summary": "Ranking completed."},
        artifact_ids=["ranking-1"],
        job_ids=["job-1"],
        error_summary=None,
        warnings=warnings or [],
        started_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
        completed_at=datetime(2026, 6, 3, 12, 1, tzinfo=UTC),
        metadata={"artifact_provenance": {"ranking-1": "source-1"}},
    )
    return RuntimeExecutionResult(
        execution_id="exec-1",
        plan=plan,
        mode="execute_with_approval",
        status="succeeded",
        results=[result],
        artifact_ids=["ranking-1"],
        job_ids=["job-1"],
        warnings=warnings or [],
        audit_events=[
            RuntimeAgentAuditEvent(
                event_id="audit-1",
                session_id="session-1",
                event_type="runtime_step_succeeded",
                actor="user-1",
                timestamp=datetime(2026, 6, 3, 12, 1, tzinfo=UTC),
                summary="run_ranking succeeded.",
                object_type="RuntimeToolResult",
                object_id="result-1",
                before=None,
                after=None,
                metadata={},
            )
        ],
        started_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
        completed_at=datetime(2026, 6, 3, 12, 2, tzinfo=UTC),
        metadata={},
    )


def _plan() -> RuntimeActionPlan:
    registry = RuntimeToolRegistry.default()
    return RuntimeActionPlan(
        plan_id="plan-1",
        session_id="session-1",
        user_goal="Rank candidates and prepare review output.",
        plan_summary="Run ranking and collect artifacts.",
        steps=[
            RuntimeActionStep(
                step_id="step-1",
                plan_id="plan-1",
                step_index=0,
                action_type="run_ranking",
                tool_name="run_ranking",
                tool_args={"artifact_id": "source-1"},
                requires_approval=False,
                approval_reason=None,
                expected_outputs=["ranking-1"],
                status="succeeded",
                result_id="result-1",
                warnings=[],
                metadata={},
            )
        ],
        required_approvals=["external_write"],
        expected_artifacts=["ranking-1"],
        risk_level="medium",
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                "run_ranking": {
                    "required_permissions": registry.require("run_ranking").required_permissions,
                    "side_effect_level": registry.require("run_ranking").side_effect_level,
                }
            }
        },
    )
