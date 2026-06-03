from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from molecule_ranker.runtime_agents.guardrails import RuntimeGuardrailChecker
from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeToolResult,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


def test_fake_citation_blocked() -> None:
    checker = RuntimeGuardrailChecker()

    result = checker.check_output(
        {
            "summary": "This is supported by PMID:12345678 and DOI 10.1234/fake.paper.",
            "citations": ["PMID:12345678"],
        },
        known_citations=set(),
    )

    assert result.allowed is False
    assert any("fake citation" in violation.message for violation in result.violations)


def test_generated_molecule_advancement_blocked() -> None:
    checker = RuntimeGuardrailChecker()
    plan = _plan(
        [
            _step(
                "plan_campaign",
                tool_args={"generated_molecule_id": "gm-1", "advance_to_assay": True},
            )
        ]
    )

    result = checker.check_plan(plan)

    assert result.allowed is False
    assert any(
        "generated molecule advancement" in violation.message
        for violation in result.violations
    )


def test_external_write_blocked() -> None:
    checker = RuntimeGuardrailChecker()
    plan = _plan([_step("run_sync_write_enabled")])

    result = checker.check_plan(plan, approvals=set())

    assert result.allowed is False
    assert any("external write" in violation.message for violation in result.violations)


def test_lab_protocol_text_blocked() -> None:
    checker = RuntimeGuardrailChecker()

    result = checker.check_output(
        "Lab protocol: incubate cells for 24 hours, then dose patients at 5 mg/kg.",
    )

    assert result.allowed is False
    assert any("lab protocol" in violation.message for violation in result.violations)
    assert any("dosing" in violation.message for violation in result.violations)


def test_unsupported_score_mutation_blocked() -> None:
    checker = RuntimeGuardrailChecker()
    result = RuntimeToolResult(
        result_id="result-1",
        step_id="step-1",
        tool_name="summarize_ranking",
        status="succeeded",
        output={"score_updates": [{"candidate_id": "c1", "score": 0.99}]},
        artifact_ids=["ranking-1"],
        job_ids=[],
        error_summary=None,
        warnings=[],
        started_at=datetime(2026, 6, 3, 12, tzinfo=UTC),
        completed_at=datetime(2026, 6, 3, 12, 1, tzinfo=UTC),
        metadata={"artifact_provenance": {"ranking-1": "run-1"}},
    )

    guardrail = checker.check_state(
        result,
        known_artifacts={"ranking-1"},
        expected_output_schema={"type": "object", "additionalProperties": True},
    )

    assert guardrail.allowed is False
    assert any("score" in violation.message for violation in guardrail.violations)


def test_safe_plan_passes() -> None:
    checker = RuntimeGuardrailChecker()
    plan = _plan([_step("run_ranking", tool_args={"artifact_id": "source-1"})])

    result = checker.check_plan(
        plan,
        user_permissions={"run:create"},
        known_artifacts={"source-1"},
    )

    assert result.allowed is True
    assert result.violations == []


def _plan(steps: list[RuntimeActionStep]) -> RuntimeActionPlan:
    registry = RuntimeToolRegistry.default()
    return RuntimeActionPlan(
        plan_id="plan-1",
        session_id="session-1",
        user_goal="Run runtime guardrail test.",
        plan_summary="Guardrail test plan.",
        steps=steps,
        required_approvals=[],
        expected_artifacts=[],
        risk_level="low",
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                step.tool_name: {
                    "required_permissions": registry.require(step.tool_name).required_permissions,
                    "side_effect_level": registry.require(step.tool_name).side_effect_level,
                    "policy_tags": registry.require(step.tool_name).policy_tags,
                }
                for step in steps
            }
        },
    )


def _step(tool_name: str, *, tool_args: dict[str, Any] | None = None) -> RuntimeActionStep:
    return RuntimeActionStep(
        step_id=f"step-{tool_name}",
        plan_id="plan-1",
        step_index=0,
        action_type=tool_name,
        tool_name=tool_name,
        tool_args=tool_args or {},
        requires_approval=False,
        approval_reason=None,
        expected_outputs=[],
        status="pending",
        result_id=None,
        warnings=[],
        metadata={},
    )
