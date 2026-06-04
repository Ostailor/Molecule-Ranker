from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.agent_repair.reports import (
    REPAIR_ARTIFACT_FILENAMES,
    REPAIR_REPORT_SECTIONS,
    write_repair_artifacts,
)
from molecule_ranker.agent_repair.schemas import (
    AgentSelfEvaluation,
    FailureDiagnosis,
    RegressionCheck,
    RepairAction,
    RepairExecution,
    RepairPlan,
)


def test_repair_report_artifacts_generated(tmp_path: Path) -> None:
    paths = write_repair_artifacts(
        tmp_path,
        self_evaluation=_self_evaluation(),
        failure_diagnosis=_diagnosis(),
        repair_plan=_plan(),
        repair_execution=_execution(),
        regression_checks=[_regression()],
    )

    assert set(paths) == set(REPAIR_ARTIFACT_FILENAMES)
    for path in paths.values():
        assert path.exists(), path
    assert json.loads((tmp_path / "repair_execution.json").read_text())["status"] == "succeeded"
    assert json.loads((tmp_path / "regression_checks.json").read_text())[0]["check_type"] == (
        "schema_contract"
    )


def test_repair_report_includes_root_cause_evidence_and_sections(tmp_path: Path) -> None:
    write_repair_artifacts(
        tmp_path,
        self_evaluation=_self_evaluation(),
        failure_diagnosis=_diagnosis(),
        repair_plan=_plan(),
        repair_execution=_execution(),
        regression_checks=[_regression()],
    )

    report = (tmp_path / "repair_report.md").read_text()

    for section in REPAIR_REPORT_SECTIONS:
        assert f"## {section}" in report
    assert "schema validation failed" in report
    assert "artifact-1" in report
    assert "missing required field" in report


def test_repair_report_includes_regression_check(tmp_path: Path) -> None:
    write_repair_artifacts(
        tmp_path,
        self_evaluation=_self_evaluation(),
        failure_diagnosis=_diagnosis(),
        repair_plan=_plan(),
        repair_execution=_execution(),
        regression_checks=[_regression()],
    )

    report = (tmp_path / "repair_report.md").read_text()

    assert "regression-1" in report
    assert "schema_contract" in report
    assert "passed=`True`" in report


def test_repair_report_redacts_forbidden_input_text(tmp_path: Path) -> None:
    diagnosis = _diagnosis(
        evidence=[
            {
                "artifact_id": "artifact-1",
                "error": "medical advice and synthesis route with IC50 = 12 nM",
                "secret": "OPENAI_API_KEY=sk-secret-value-123456789",
            }
        ]
    )

    write_repair_artifacts(
        tmp_path,
        self_evaluation=_self_evaluation(),
        failure_diagnosis=diagnosis,
        repair_plan=_plan(),
        repair_execution=_execution(),
        regression_checks=[_regression()],
    )

    report = (tmp_path / "repair_report.md").read_text()

    assert "Repairs are operational workflow repairs." in report
    assert "Repairs do not fabricate scientific evidence." in report
    assert "Repairs do not validate molecules." in report
    assert "no medical/lab/synthesis/dosing guidance" in report
    assert "IC50 = 12 nM" not in report
    assert "synthesis route" not in report
    assert "medical advice" not in report
    assert "sk-secret-value" not in report
    assert "[REDACTED" in report


def _self_evaluation() -> AgentSelfEvaluation:
    return AgentSelfEvaluation(
        evaluation_id="evaluation-1",
        session_id="session-1",
        subagent_id=None,
        task_id=None,
        evaluated_object_type="workflow",
        evaluated_object_id="workflow-1",
        evaluation_type="post_execution",
        passed=True,
        score=0.9,
        findings=["Operational repair report is grounded in artifacts."],
        required_repairs=[],
        warnings=[],
        created_at=_aware(),
        metadata={},
    )


def _diagnosis(evidence: list[dict[str, object]] | None = None) -> FailureDiagnosis:
    return FailureDiagnosis(
        diagnosis_id="diagnosis-1",
        failure_object_type="validation",
        failure_object_id="validation-1",
        failure_category="validation_failed",
        root_cause_summary="schema validation failed for derived report",
        evidence=evidence
        or [{"artifact_id": "artifact-1", "error": "missing required field"}],
        recoverable=True,
        repairability="automatic_safe",
        confidence=0.82,
        warnings=[],
        created_at=_aware(),
        metadata={},
    )


def _plan() -> RepairPlan:
    return RepairPlan(
        repair_plan_id="repair-plan-1",
        diagnosis_id="diagnosis-1",
        session_id="session-1",
        plan_summary="Regenerate derived report from existing artifacts.",
        actions=[
            RepairAction(
                repair_action_id="repair-action-1",
                action_type="revalidate_artifact",
                target_object_type="artifact",
                target_object_id="artifact-1",
                tool_name=None,
                tool_args={"artifact_id": "artifact-1"},
                expected_effect="Validate repaired artifact contract.",
                side_effect_level="none",
                requires_approval=False,
                approval_reason=None,
                risk_level="low",
                metadata={},
            )
        ],
        expected_artifacts=["artifact-1"],
        rollback_plan=[],
        requires_human_approval=False,
        scientific_guardrails=["Do not create scientific evidence."],
        validated=True,
        validation_errors=[],
        created_by="deterministic",
        created_at=_aware(),
        metadata={},
    )


def _execution() -> RepairExecution:
    return RepairExecution(
        repair_execution_id="repair-execution-1",
        repair_plan_id="repair-plan-1",
        status="succeeded",
        executed_actions=[{"repair_action_id": "repair-action-1", "status": "succeeded"}],
        artifacts_created=["artifact-1"],
        artifacts_modified=[],
        jobs_created=[],
        approvals_requested=[],
        regression_check_ids=["regression-1"],
        warnings=[],
        started_at=_aware(),
        completed_at=_aware(),
        metadata={},
    )


def _regression() -> RegressionCheck:
    return RegressionCheck(
        regression_check_id="regression-1",
        repair_execution_id="repair-execution-1",
        check_type="schema_contract",
        passed=True,
        findings=["schema contract passed"],
        artifacts_checked=["artifact-1"],
        created_at=_aware(),
        metadata={},
    )


def _aware() -> datetime:
    return datetime(2026, 6, 4, 12, tzinfo=UTC)
