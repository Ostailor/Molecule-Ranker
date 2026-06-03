from __future__ import annotations

import json
from typing import Any

import pytest

from molecule_ranker.runtime_agents.planner import (
    CodexPlannerUnavailable,
    CodexRuntimePlanner,
    RuntimePlanValidationError,
)


def test_codex_mocked_valid_plan_is_accepted() -> None:
    codex = FakeCodexPlannerClient(
        _plan_payload(
            steps=[
                {
                    "step_id": "step-1",
                    "plan_id": "plan-1",
                    "step_index": 0,
                    "action_type": "run_ranking",
                    "tool_name": "run_ranking",
                    "tool_args": {"disease": "Alzheimer disease"},
                    "requires_approval": False,
                    "approval_reason": None,
                    "expected_outputs": ["ranking_run"],
                    "status": "pending",
                    "result_id": None,
                    "warnings": [],
                    "metadata": {},
                }
            ]
        )
    )
    planner = CodexRuntimePlanner(codex_client=codex)

    plan = planner.plan(
        user_goal="Rank disease candidates for Alzheimer disease.",
        session_id="session-1",
        project_id="project-1",
        org_id="org-1",
        user_id="user-1",
        user_permissions={"run:create"},
        allowed_tools=["run_ranking"],
        current_artifacts=[{"artifact_id": "source-1", "artifact_type": "source_manifest"}],
        policy_constraints=["source-backed ranking only"],
        autonomy_level="suggest_only",
    )

    assert plan.validated is True
    assert plan.created_by == "codex"
    assert plan.steps[0].tool_name == "run_ranking"
    assert "run_ranking" in plan.metadata["tool_specs"]
    assert "run_ranking" in codex.prompts[0]
    assert "No medical advice" in codex.prompts[0]
    assert codex.sandbox_modes == ["read-only"]


def test_unknown_tool_is_rejected() -> None:
    planner = CodexRuntimePlanner(
        codex_client=FakeCodexPlannerClient(
            _plan_payload(
                steps=[
                    {
                        "step_id": "step-1",
                        "plan_id": "plan-1",
                        "step_index": 0,
                        "action_type": "invent_scores",
                        "tool_name": "invent_scores",
                        "tool_args": {},
                        "requires_approval": False,
                        "approval_reason": None,
                        "expected_outputs": [],
                        "status": "pending",
                        "result_id": None,
                        "warnings": [],
                        "metadata": {},
                    }
                ]
            )
        )
    )

    with pytest.raises(RuntimePlanValidationError, match="not allowed"):
        planner.plan(
            user_goal="Invent better scores.",
            session_id="session-1",
            user_permissions={"run:create"},
            allowed_tools=["run_ranking"],
        )


def test_unauthorized_tool_is_rejected() -> None:
    planner = CodexRuntimePlanner(
        codex_client=FakeCodexPlannerClient(
            _plan_payload(
                steps=[
                    {
                        "step_id": "step-1",
                        "plan_id": "plan-1",
                        "step_index": 0,
                        "action_type": "run_generation",
                        "tool_name": "run_generation",
                        "tool_args": {"project_id": "project-1"},
                        "requires_approval": False,
                        "approval_reason": None,
                        "expected_outputs": ["generated_candidates"],
                        "status": "pending",
                        "result_id": None,
                        "warnings": [],
                        "metadata": {},
                    }
                ]
            )
        )
    )

    with pytest.raises(RuntimePlanValidationError, match="Missing permission"):
        planner.plan(
            user_goal="Generate candidates.",
            session_id="session-1",
            user_permissions={"project:read"},
            allowed_tools=["run_generation"],
        )


def test_external_write_requires_approval() -> None:
    planner = CodexRuntimePlanner(
        codex_client=FakeCodexPlannerClient(
            _plan_payload(
                steps=[
                    {
                        "step_id": "step-1",
                        "plan_id": "plan-1",
                        "step_index": 0,
                        "action_type": "run_sync_write_enabled",
                        "tool_name": "run_sync_write_enabled",
                        "tool_args": {"connector_id": "eln-1"},
                        "requires_approval": False,
                        "approval_reason": None,
                        "expected_outputs": ["sync_report"],
                        "status": "pending",
                        "result_id": None,
                        "warnings": [],
                        "metadata": {},
                    }
                ]
            )
        )
    )

    plan = planner.plan(
        user_goal="Sync approved review output to ELN.",
        session_id="session-1",
        user_permissions={"integration:write"},
        allowed_tools=["run_sync_write_enabled"],
        autonomy_level="execute_with_approval",
    )

    assert plan.steps[0].requires_approval is True
    assert plan.steps[0].approval_reason == "Tool requires approval by default."
    assert plan.required_approvals == ["external_write"]
    assert plan.risk_level == "high"


def test_unsafe_prompt_output_is_rejected() -> None:
    planner = CodexRuntimePlanner(
        codex_client=FakeCodexPlannerClient(
            _plan_payload(
                steps=[
                    {
                        "step_id": "step-1",
                        "plan_id": "plan-1",
                        "step_index": 0,
                        "action_type": "summarize_artifacts",
                        "tool_name": "summarize_artifacts",
                        "tool_args": {"summary": "Compound X is safe and give a synthesis route."},
                        "requires_approval": False,
                        "approval_reason": None,
                        "expected_outputs": ["memo"],
                        "status": "pending",
                        "result_id": None,
                        "warnings": [],
                        "metadata": {},
                    }
                ]
            )
        )
    )

    with pytest.raises(RuntimePlanValidationError, match="Unsafe planner output"):
        planner.plan(
            user_goal="Summarize artifacts.",
            session_id="session-1",
            user_permissions={"codex:run"},
            allowed_tools=["summarize_artifacts"],
        )


def test_deterministic_fallback_works_when_codex_unavailable() -> None:
    planner = CodexRuntimePlanner(codex_client=UnavailableCodexPlannerClient())

    plan = planner.plan(
        user_goal="Rank disease Parkinson disease.",
        session_id="session-1",
        user_permissions={"run:create"},
        allowed_tools=["run_ranking"],
    )

    assert plan.created_by == "deterministic_template"
    assert plan.validated is True
    assert [step.tool_name for step in plan.steps] == ["run_ranking"]
    assert plan.steps[0].tool_args["goal"] == "Rank disease Parkinson disease."


class FakeCodexPlannerClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []
        self.sandbox_modes: list[str] = []

    def plan(self, *, prompt: str, sandbox_mode: str, jsonl_output_path: str | None) -> str:
        self.prompts.append(prompt)
        self.sandbox_modes.append(sandbox_mode)
        return json.dumps(self.payload)


class UnavailableCodexPlannerClient:
    def plan(self, *, prompt: str, sandbox_mode: str, jsonl_output_path: str | None) -> str:
        raise CodexPlannerUnavailable("codex unavailable")


def _plan_payload(*, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "plan_id": "plan-1",
        "session_id": "session-1",
        "user_goal": "Test goal",
        "plan_summary": "Test plan",
        "steps": steps,
        "required_approvals": [],
        "expected_artifacts": [],
        "risk_level": "low",
        "guardrail_warnings": [],
        "created_by": "codex",
        "validated": False,
        "validation_errors": [],
        "metadata": {},
    }
