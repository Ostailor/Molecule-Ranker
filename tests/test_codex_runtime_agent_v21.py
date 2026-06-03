from __future__ import annotations

from pathlib import Path

from molecule_ranker.codex_runtime import (
    ActionPlanner,
    CodexRuntimeAgent,
    RecoverableToolError,
    RuntimeContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


def test_runtime_agent_executes_registered_tools_through_policy_and_audit() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            action_type="run_ranking",
            permission="ranking:run",
            description="Run deterministic molecule ranking.",
            executor=lambda params, context: _recorded_result(
                calls,
                "run_ranking",
                params,
                artifact_type="ranking_run",
            ),
        )
    )
    registry.register(
        ToolSpec(
            action_type="export_reports",
            permission="reports:export",
            description="Export reviewer-ready reports.",
            executor=lambda params, context: _recorded_result(
                calls,
                "export_reports",
                params,
                artifact_type="report_export",
            ),
        )
    )
    agent = CodexRuntimeAgent(
        planner=ActionPlanner(),
        registry=registry,
        audit_log_path=None,
    )

    result = agent.run(
        "Rank the project candidates and export a reviewer report.",
        RuntimeContext(
            actor_id="user-1",
            org_id="org-1",
            project_id="project-1",
            permissions={"ranking:run", "reports:export"},
        ),
    )

    assert result.status == "succeeded"
    assert [call[0] for call in calls] == ["run_ranking", "export_reports"]
    assert [step.action_type for step in result.steps] == ["run_ranking", "export_reports"]
    assert result.review_outputs == [
        "run_ranking completed; artifact IDs: run_ranking-artifact",
        "export_reports completed; artifact IDs: export_reports-artifact",
    ]
    assert result.audit_events
    assert {event.component for event in result.audit_events} >= {
        "ActionPlanner",
        "ToolRegistry",
        "PolicyEngine",
        "ApprovalGate",
        "ActionExecutor",
        "ArtifactValidator",
        "GuardrailChecker",
        "AuditLogger",
    }
    assert all(event.actor_id == "user-1" for event in result.audit_events)


def test_runtime_agent_blocks_unregistered_and_forbidden_actions() -> None:
    agent = CodexRuntimeAgent(registry=ToolRegistry())

    result = agent.run(
        "Change scores directly so the campaign can advance.",
        RuntimeContext(
            actor_id="user-1",
            org_id="org-1",
            project_id="project-1",
            permissions={"scores:write"},
        ),
        requested_actions=["change_scores_directly"],
    )

    assert result.status == "guardrail_failed"
    assert "Codex runtime cannot change scores directly." in result.guardrail_warnings
    assert result.steps == []


def test_runtime_agent_requires_approval_for_high_risk_actions() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            action_type="optimize_portfolio",
            permission="portfolio:optimize",
            description="Run deterministic portfolio optimizer.",
            requires_approval=True,
            executor=lambda params, context: _recorded_result(
                calls,
                "optimize_portfolio",
                params,
                artifact_type="portfolio_optimization",
            ),
        )
    )
    agent = CodexRuntimeAgent(registry=registry)
    context = RuntimeContext(
        actor_id="user-1",
        org_id="org-1",
        project_id="project-1",
        permissions={"portfolio:optimize"},
    )

    pending = agent.run(
        "Optimize the discovery portfolio.",
        context,
        requested_actions=["optimize_portfolio"],
    )

    assert pending.status == "approval_required"
    assert pending.pending_approvals == ["optimize_portfolio"]
    assert calls == []

    approved = agent.run(
        "Optimize the discovery portfolio.",
        context.model_copy(update={"approved_action_types": {"optimize_portfolio"}}),
        requested_actions=["optimize_portfolio"],
    )

    assert approved.status == "succeeded"
    assert [call[0] for call in calls] == ["optimize_portfolio"]


def test_runtime_agent_rejects_overclaiming_tool_output() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            action_type="run_generation",
            permission="generation:run",
            description="Run deterministic generation pipeline.",
            executor=lambda params, context: ToolResult(
                status="succeeded",
                summary="Generated molecule GEN-1 is safe, active, and binding.",
                artifacts=[
                    {
                        "artifact_id": "gen-1",
                        "artifact_type": "generated_candidates",
                        "sha256": "abc123",
                    }
                ],
            ),
        )
    )
    agent = CodexRuntimeAgent(registry=registry)

    result = agent.run(
        "Generate molecule hypotheses.",
        RuntimeContext(
            actor_id="user-1",
            org_id="org-1",
            project_id="project-1",
            permissions={"generation:run"},
        ),
        requested_actions=["run_generation"],
    )

    assert result.status == "guardrail_failed"
    assert any("claims molecules are safe" in warning for warning in result.guardrail_warnings)
    assert result.steps[0].status == "guardrail_failed"


def test_runtime_agent_retries_recoverable_tool_failures(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def flaky_tool(params: dict[str, object], context: RuntimeContext) -> ToolResult:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RecoverableToolError("temporary artifact store timeout")
        return ToolResult(
            status="succeeded",
            summary="Support bundle generated from audited logs.",
            artifacts=[
                {
                    "artifact_id": "support-bundle-1",
                    "artifact_type": "support_bundle",
                    "sha256": "abc123",
                }
            ],
        )

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            action_type="generate_support_bundle",
            permission="support:bundle",
            description="Generate a support bundle.",
            retry_count=1,
            executor=flaky_tool,
        )
    )
    audit_path = tmp_path / "runtime-audit.jsonl"
    agent = CodexRuntimeAgent(registry=registry, audit_log_path=audit_path)

    result = agent.run(
        "Generate a support bundle for the failed job.",
        RuntimeContext(
            actor_id="support-1",
            org_id="org-1",
            project_id="project-1",
            permissions={"support:bundle"},
        ),
        requested_actions=["generate_support_bundle"],
    )

    assert result.status == "succeeded"
    assert attempts["count"] == 2
    assert result.steps[0].recovery_attempts == 1
    audit_text = audit_path.read_text()
    assert "temporary artifact store timeout" in audit_text
    assert "ActionExecutor" in audit_text


def _recorded_result(
    calls: list[tuple[str, dict[str, object]]],
    action_type: str,
    params: dict[str, object],
    *,
    artifact_type: str,
) -> ToolResult:
    calls.append((action_type, params))
    return ToolResult(
        status="succeeded",
        summary=f"{action_type} completed deterministically.",
        artifacts=[
            {
                "artifact_id": f"{action_type}-artifact",
                "artifact_type": artifact_type,
                "sha256": "abc123",
            }
        ],
    )
