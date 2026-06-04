from __future__ import annotations

from molecule_ranker.agent_repair.evaluators import (
    ArtifactEvaluator,
    OutputEvaluator,
    PlanEvaluator,
    ScientificIntegrityEvaluator,
)
from molecule_ranker.runtime_agents.schemas import RuntimeActionPlan, RuntimeActionStep
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


def test_valid_plan_passes_self_evaluation() -> None:
    plan = _plan(
        [
            _step(
                tool_name="run_ranking",
                tool_args={"goal": "Rank source-backed candidates."},
            )
        ],
        expected_artifacts=["ranking-artifact-1"],
    )

    evaluation = PlanEvaluator().evaluate(plan)

    assert evaluation.passed is True
    assert evaluation.findings == []
    assert evaluation.evaluated_object_type == "runtime_plan"
    assert evaluation.evaluation_type == "pre_execution"


def test_plan_missing_approval_fails_self_evaluation() -> None:
    plan = _plan(
        [
            _step(
                tool_name="run_sync_write_enabled",
                tool_args={"goal": "Write sync approved records."},
                requires_approval=True,
            )
        ]
    )

    evaluation = PlanEvaluator().evaluate(plan)

    assert evaluation.passed is False
    assert any("required approval is missing" in finding for finding in evaluation.findings)
    assert any("external write without approval" in finding for finding in evaluation.findings)


def test_output_fake_citation_fails_self_evaluation() -> None:
    output = {
        "result_id": "result-1",
        "output": {
            "summary": "PMID:123456 proves this candidate is active.",
            "limitations": ["Source-backed validation required."],
            "referenced_artifact_ids": ["literature-artifact-1"],
        },
    }

    evaluation = OutputEvaluator().evaluate(
        output,
        known_artifacts={"literature-artifact-1"},
    )

    assert evaluation.passed is False
    assert any("fabricate citations" in finding for finding in evaluation.findings)


def test_artifact_missing_provenance_fails_self_evaluation() -> None:
    artifact = {
        "artifact_id": "artifact-1",
        "artifact_type": "ranking_report",
        "schema_version": "1.0",
    }

    evaluation = ArtifactEvaluator().evaluate(artifact)

    assert evaluation.passed is False
    assert "Artifact provenance is missing." in evaluation.findings


def test_generated_overclaim_fails_scientific_integrity_evaluation() -> None:
    output = {
        "output_id": "codex-output-1",
        "summary": "This generated molecule is safe and active.",
        "grounded_artifact_ids": ["generated-artifact-1"],
    }

    evaluation = ScientificIntegrityEvaluator().evaluate(
        output,
        object_id="codex-output-1",
    )

    assert evaluation.passed is False
    assert any("overclaim" in finding.lower() for finding in evaluation.findings)


def _plan(
    steps: list[RuntimeActionStep],
    *,
    expected_artifacts: list[str] | None = None,
) -> RuntimeActionPlan:
    registry = RuntimeToolRegistry.default()
    return RuntimeActionPlan(
        plan_id="plan-1",
        session_id="session-1",
        user_goal="Run agent repair evaluator test.",
        plan_summary="Evaluate runtime plan.",
        steps=steps,
        required_approvals=[],
        expected_artifacts=expected_artifacts or [],
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


def _step(
    *,
    tool_name: str,
    tool_args: dict[str, object],
    requires_approval: bool = False,
) -> RuntimeActionStep:
    return RuntimeActionStep(
        step_id=f"step-{tool_name}",
        plan_id="plan-1",
        step_index=0,
        action_type=tool_name,
        tool_name=tool_name,
        tool_args=tool_args,
        requires_approval=requires_approval,
        approval_reason=None,
        expected_outputs=[],
        status="pending",
        result_id=None,
        warnings=[],
        metadata={},
    )
