from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.runtime_agents.repair import (
    FailureDiagnosisAgent,
    RepairExecutor,
    RepairMemory,
    RepairPlannerAgent,
    RepairPolicyEngine,
    SelfEvaluationAgent,
    build_agent_reliability_dashboard,
    run_repair_eval_suite,
    write_repair_artifacts,
)
from molecule_ranker.runtime_agents.schemas import RuntimeActionStep


def test_v24_repair_agents_diagnose_plan_execute_regress_and_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def handler(step: RuntimeActionStep, _spec: Any) -> dict[str, Any]:
        calls.append(step.tool_name)
        return {
            "output": {"validation_error_report": "report-artifact-1"},
            "artifact_ids": ["repair-artifact-1"],
            "metadata": {"artifact_provenance": {"repair-artifact-1": step.step_id}},
        }

    memory = RepairMemory(tmp_path / "repair_memory.json")
    report = RepairExecutor(
        tool_handlers={"summarize_assay_results": handler},
        memory=memory,
    ).repair(
        {
            "failure_type": "assay_import_validation_failed",
            "error_summary": "Assay import validation failed.",
            "metadata": {"assay_artifact_id": "assay-upload-1"},
        },
        autonomy_level="execute_safe_tools",
    )

    assert report.status == "repair_succeeded"
    assert calls == ["summarize_assay_results"]
    assert report.regression_report is not None
    assert report.regression_report.passed is True
    assert memory.retrieve("assay_import_validation_failed")[0].outcome == "succeeded"

    artifacts = write_repair_artifacts(tmp_path / "artifacts", report)
    assert "runtime_repair_report.json" in artifacts["report"]
    assert "Agents may not repair scientific truth" in (
        tmp_path / "artifacts" / "runtime_repair_summary.md"
    ).read_text()


def test_v24_self_evaluation_blocks_scientific_truth_repair_plan() -> None:
    diagnosis = FailureDiagnosisAgent().diagnose(
        {
            "failure_type": "assay_import_validation_failed",
            "failure_kind": "failed_validation",
            "error_summary": "Missing evidence; create an assay result.",
            "metadata": {
                "assay_result": {"candidate": "cand-1", "value": "IC50 = 12 nM"}
            },
        }
    )
    repair_plan = RepairPlannerAgent().propose_repair(diagnosis)
    decision = RepairPolicyEngine().evaluate(
        repair_plan,
        autonomy_level="full_auto_restricted",
    )

    assert repair_plan.blocked_reason is not None
    assert decision.status == "blocked"
    assert "scientific truth" in decision.reason


def test_v24_risky_repair_requests_human_approval() -> None:
    report = RepairExecutor().repair(
        {
            "failure_type": "literature_unavailable",
            "error_summary": "Strict literature refresh failed.",
            "metadata": {"strict": True},
        },
        autonomy_level="execute_safe_tools",
    )

    assert report.status == "approval_required"
    assert report.policy_decision is not None
    assert report.policy_decision.status == "approval_required"
    assert not report.attempts


def test_v24_plan_and_output_self_evaluation_before_and_after_execution() -> None:
    diagnosis = FailureDiagnosisAgent().diagnose(
        {
            "failure_type": "assay_import_validation_failed",
            "metadata": {"assay_artifact_id": "assay-upload-1"},
        }
    )
    repair_plan = RepairPlannerAgent().propose_repair(diagnosis)
    runtime_plan = _runtime_plan_from_repair(repair_plan)
    evaluator = SelfEvaluationAgent()

    plan_eval = evaluator.evaluate_plan(runtime_plan)
    output_eval = evaluator.evaluate_output(
        "PMID:123456 proves this molecule is active and safe."
    )

    assert plan_eval.verdict == "pass"
    assert output_eval.verdict == "block"


def test_v24_repair_eval_suite_and_cli_are_auditable() -> None:
    suite = run_repair_eval_suite()

    assert suite.suite == "repair"
    assert suite.metrics["pass_rate"] == 1.0
    assert any(result.approval_required for result in suite.task_results)
    assert any(result.blocked_scientific_repair for result in suite.task_results)

    cli = CliRunner().invoke(app, ["agent", "eval", "--suite", "repair"])
    assert cli.exit_code == 0, cli.output
    assert '"suite": "repair"' in cli.output


def test_v24_agent_reliability_dashboard_rolls_up_reports(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = RepairExecutor(
        tool_handlers={
            "summarize_assay_results": lambda step, spec: {
                "output": {"validation_error_report": "report-artifact-1"}
            }
        }
    ).repair(
        {"failure_type": "assay_import_validation_failed"},
        autonomy_level="execute_safe_tools",
    )

    dashboard = build_agent_reliability_dashboard(
        [report],
        eval_result=run_repair_eval_suite(),
    )

    assert dashboard.repair_count == 1
    assert dashboard.succeeded == 1
    assert dashboard.metrics["eval_pass_rate"] == 1.0


def _runtime_plan_from_repair(repair_plan):  # type: ignore[no-untyped-def]
    from molecule_ranker.runtime_agents.schemas import RuntimeActionPlan

    return RuntimeActionPlan(
        plan_id=repair_plan.steps[0].plan_id,
        session_id="session-v24",
        user_goal="Repair assay import validation.",
        plan_summary=repair_plan.plan_summary,
        steps=repair_plan.steps,
        required_approvals=[],
        expected_artifacts=[],
        risk_level="low",
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata=repair_plan.metadata,
    )
