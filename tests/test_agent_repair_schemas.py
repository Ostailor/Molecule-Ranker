from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.agent_repair.schemas import (
    AgentSelfEvaluation,
    FailureDiagnosis,
    RegressionCheck,
    RepairAction,
    RepairExecution,
    RepairMemoryRecord,
    RepairPlan,
)


def test_agent_self_evaluation_schema_accepts_allowed_values() -> None:
    evaluation = AgentSelfEvaluation(
        evaluation_id="eval-1",
        session_id="session-1",
        subagent_id="subagent-1",
        task_id="task-1",
        evaluated_object_type="runtime_plan",
        evaluated_object_id="plan-1",
        evaluation_type="pre_execution",
        passed=True,
        score=0.95,
        findings=[],
        required_repairs=[],
        warnings=[],
        created_at=_aware(),
        metadata={"source": "unit-test"},
    )

    assert evaluation.score == 0.95
    assert evaluation.metadata["source"] == "unit-test"


def test_agent_repair_schemas_reject_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        AgentSelfEvaluation(
            evaluation_id="eval-1",
            session_id=None,
            subagent_id=None,
            task_id=None,
            evaluated_object_type="runtime_plan",
            evaluated_object_id="plan-1",
            evaluation_type="pre_execution",
            passed=True,
            score=1.0,
            findings=[],
            required_repairs=[],
            warnings=[],
            created_at=datetime(2026, 6, 4, 12),
            metadata={},
        )


def test_score_confidence_and_success_rate_are_bounded() -> None:
    with pytest.raises(ValidationError):
        AgentSelfEvaluation(
            evaluation_id="eval-1",
            session_id=None,
            subagent_id=None,
            task_id=None,
            evaluated_object_type="runtime_plan",
            evaluated_object_id="plan-1",
            evaluation_type="pre_execution",
            passed=False,
            score=1.1,
            findings=[],
            required_repairs=[],
            warnings=[],
            created_at=_aware(),
            metadata={},
        )

    with pytest.raises(ValidationError):
        _diagnosis(confidence=-0.1)

    with pytest.raises(ValidationError):
        RepairMemoryRecord(
            memory_id="memory-1",
            failure_signature="invalid-schema:tool-1",
            failure_category="invalid_schema",
            successful_repair_plan_id=None,
            repair_success_rate=1.2,
            last_seen_at=_aware(),
            occurrence_count=1,
            recommended_repair_strategy="Rerun schema validation.",
            warnings=[],
            metadata={},
        )


def test_failure_diagnosis_repair_execution_and_regression_schema_contracts() -> None:
    diagnosis = _diagnosis()
    execution = RepairExecution(
        repair_execution_id="repair-execution-1",
        repair_plan_id="repair-plan-1",
        status="succeeded",
        executed_actions=[{"repair_action_id": "repair-action-1", "status": "succeeded"}],
        artifacts_created=["repair-report-1"],
        artifacts_modified=[],
        jobs_created=[],
        approvals_requested=[],
        regression_check_ids=["regression-1"],
        warnings=[],
        started_at=_aware(),
        completed_at=_aware(),
        metadata={},
    )
    regression = RegressionCheck(
        regression_check_id="regression-1",
        repair_execution_id=execution.repair_execution_id,
        check_type="schema_contract",
        passed=True,
        findings=[],
        artifacts_checked=["repair-report-1"],
        created_at=_aware(),
        metadata={},
    )

    assert diagnosis.failure_category == "validation_failed"
    assert execution.status == "succeeded"
    assert regression.check_type == "schema_contract"


def test_schema_literals_reject_unknown_values() -> None:
    with pytest.raises(ValidationError):
        AgentSelfEvaluation.model_validate(
            {
                "evaluation_id": "eval-1",
                "session_id": None,
                "subagent_id": None,
                "task_id": None,
                "evaluated_object_type": "unsupported_object",
                "evaluated_object_id": "plan-1",
                "evaluation_type": "pre_execution",
                "passed": True,
                "score": 1.0,
                "findings": [],
                "required_repairs": [],
                "warnings": [],
                "created_at": _aware(),
                "metadata": {},
            }
        )

    with pytest.raises(ValidationError):
        _action(action_type="invent_evidence")


def test_repair_plan_accepts_operational_repair_without_scientific_content() -> None:
    plan = RepairPlan(
        repair_plan_id="repair-plan-1",
        diagnosis_id="diagnosis-1",
        session_id="session-1",
        plan_summary="Regenerate the report from existing validated artifacts.",
        actions=[
            _action(
                action_type="regenerate_artifact",
                tool_args={"source_artifact_ids": ["artifact-1"]},
                expected_effect="Create a derived report from existing artifacts.",
            )
        ],
        expected_artifacts=["repair-report-1"],
        rollback_plan=[],
        requires_human_approval=False,
        scientific_guardrails=["Do not invent evidence."],
        validated=True,
        validation_errors=[],
        created_by="deterministic",
        created_at=_aware(),
        metadata={"artifact_contract": "runtime_repair_report.v1"},
    )

    assert plan.validated is True
    assert plan.actions[0].tool_args["source_artifact_ids"] == ["artifact-1"]


def test_repair_plan_rejects_fabricated_scientific_content() -> None:
    with pytest.raises(ValidationError, match="fabricated scientific content"):
        RepairPlan(
            repair_plan_id="repair-plan-1",
            diagnosis_id="diagnosis-1",
            session_id="session-1",
            plan_summary="Repair missing validation output.",
            actions=[
                _action(
                    action_type="regenerate_artifact",
                    tool_args={"assay_result": {"candidate": "cand-1", "value": "IC50 = 12 nM"}},
                    expected_effect="Create replacement assay result.",
                )
            ],
            expected_artifacts=[],
            rollback_plan=[],
            requires_human_approval=False,
            scientific_guardrails=[],
            validated=False,
            validation_errors=[],
            created_by="codex",
            created_at=_aware(),
            metadata={},
        )


def _diagnosis(confidence: float = 0.8) -> FailureDiagnosis:
    return FailureDiagnosis(
        diagnosis_id="diagnosis-1",
        failure_object_type="validation",
        failure_object_id="validation-1",
        failure_category="validation_failed",
        root_cause_summary="Assay import artifact failed schema validation.",
        evidence=[{"artifact_id": "assay-upload-1", "error": "missing unit"}],
        recoverable=True,
        repairability="automatic_safe",
        confidence=confidence,
        warnings=[],
        created_at=_aware(),
        metadata={},
    )


def _action(
    *,
    action_type: str = "revalidate_artifact",
    tool_args: dict[str, object] | None = None,
    expected_effect: str = "Run deterministic schema validation.",
) -> RepairAction:
    return RepairAction(
        repair_action_id="repair-action-1",
        action_type=action_type,  # type: ignore[arg-type]
        target_object_type="artifact",
        target_object_id="artifact-1",
        tool_name="validate_artifact",
        tool_args=tool_args or {"artifact_id": "artifact-1"},
        expected_effect=expected_effect,
        side_effect_level="none",
        requires_approval=False,
        approval_reason=None,
        risk_level="low",
        metadata={},
    )


def _aware() -> datetime:
    return datetime(2026, 6, 4, 12, tzinfo=UTC)
