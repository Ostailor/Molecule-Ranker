from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.agent_repair.regression import (
    DEFAULT_CHECK_TYPES,
    regression_passed,
)
from molecule_ranker.agent_repair.regression import (
    RegressionCheckAgent as CoreRegressionCheckAgent,
)
from molecule_ranker.agent_repair.schemas import RepairExecution
from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.regression_check import RegressionCheckAgent


def test_valid_repair_passes_required_regression_checks() -> None:
    execution = _execution()

    checks = CoreRegressionCheckAgent().run_checks(
        repair_execution=execution,
        changed_artifacts=[_artifact()],
        changed_config={},
        affected_workflow=_workflow(),
    )

    assert len(checks) == len(DEFAULT_CHECK_TYPES)
    assert regression_passed(checks) is True
    assert all(check.passed for check in checks)


def test_artifact_contract_failure_fails_schema_regression() -> None:
    execution = _execution()

    checks = CoreRegressionCheckAgent().run_checks(
        repair_execution=execution,
        changed_artifacts=[
            _artifact(schema_valid=False, schema_errors=["missing provenance"])
        ],
        changed_config={},
        affected_workflow=_workflow(),
    )

    schema_check = _check(checks, "schema_contract")
    assert regression_passed(checks) is False
    assert schema_check.passed is False
    assert "schema contract" in " ".join(schema_check.findings).lower()


def test_guardrail_failure_fails_regression_and_is_visible() -> None:
    execution = _execution(status="succeeded")

    checks = CoreRegressionCheckAgent().run_checks(
        repair_execution=execution,
        changed_artifacts=[_artifact(guardrail_passed=False, guardrail_failures=["unsafe"])],
        changed_config={},
        affected_workflow=_workflow(),
    )

    guardrail_check = _check(checks, "guardrail")
    status = CoreRegressionCheckAgent().execution_status_after_regression(execution, checks)

    assert regression_passed(checks) is False
    assert guardrail_check.passed is False
    assert guardrail_check.findings
    assert status == "partially_succeeded"


def test_pipeline_regression_check_agent_records_checks_in_context() -> None:
    execution = _execution()
    context = PipelineContext(
        disease_input="test",
        config={
            "repair_execution_result": execution.model_dump(mode="json"),
            "changed_artifacts": [_artifact()],
            "changed_config": {},
            "affected_workflow": _workflow(),
        },
    )

    updated = RegressionCheckAgent().run(context)

    assert updated.config["regression_passed"] is True
    assert len(updated.config["regression_checks"]) == len(DEFAULT_CHECK_TYPES)
    assert updated.config["repair_execution_regression_status"] == "succeeded"


def _execution(status: str = "succeeded") -> RepairExecution:
    return RepairExecution(
        repair_execution_id="repair-execution-1",
        repair_plan_id="repair-plan-1",
        status=status,  # type: ignore[arg-type]
        executed_actions=[
            {
                "repair_action_id": "repair-action-1",
                "status": "succeeded",
                "side_effect_level": "artifact_write",
                "approved": True,
            }
        ],
        artifacts_created=["repair-artifact-1"],
        artifacts_modified=[],
        jobs_created=[],
        approvals_requested=[],
        regression_check_ids=[],
        warnings=[],
        started_at=_aware(),
        completed_at=_aware(),
        metadata={},
    )


def _artifact(**overrides: object) -> dict[str, object]:
    artifact = {
        "artifact_id": "repair-artifact-1",
        "exists": True,
        "schema_valid": True,
        "contract_valid": True,
        "schema_version": "runtime_repair.v1",
        "guardrail_passed": True,
    }
    artifact.update(overrides)
    return artifact


def _workflow(**overrides: object) -> dict[str, object]:
    workflow = {
        "required_artifact_ids": ["repair-artifact-1"],
        "schema_contract_passed": True,
        "artifact_completeness_passed": True,
        "guardrail_passed": True,
        "reproducible": True,
        "workflow_smoke_passed": True,
        "expected_next_step_available": True,
        "performance_smoke_passed": True,
        "targeted_unit_subset_passed": True,
        "targeted_integration_subset_passed": True,
    }
    workflow.update(overrides)
    return workflow


def _check(checks, check_type: str):  # type: ignore[no-untyped-def]
    return next(check for check in checks if check.check_type == check_type)


def _aware() -> datetime:
    return datetime(2026, 6, 4, 12, tzinfo=UTC)
