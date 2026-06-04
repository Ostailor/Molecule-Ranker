from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.agent_repair.repair_planner import (
    RepairPlannerAgent as CoreRepairPlannerAgent,
)
from molecule_ranker.agent_repair.repair_planner import (
    create_repair_plan,
    plan_repair,
)
from molecule_ranker.agent_repair.schemas import FailureDiagnosis
from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.repair_planner import RepairPlannerAgent


def test_missing_input_requests_input_and_only_uses_safe_defaults_when_allowed() -> None:
    diagnosis = _diagnosis("missing_input")

    default_plan = CoreRepairPlannerAgent().plan_repair(diagnosis)
    allowed_plan = CoreRepairPlannerAgent().plan_repair(
        diagnosis,
        policy_engine={"allow_safe_defaults": True},
        user_autonomy_level="execute_safe_tools",
    )

    assert _action_types(default_plan) == ["request_missing_input"]
    assert "adjust_safe_config" in _action_types(allowed_plan)
    config_action = _first_action(allowed_plan, "adjust_safe_config")
    assert "before_config" in config_action.metadata
    assert "after_config" in config_action.metadata


def test_invalid_schema_retries_codex_regenerates_derived_artifact_and_revalidates() -> None:
    plan = plan_repair(_diagnosis("invalid_schema"))

    assert _action_types(plan) == [
        "retry_codex_with_schema",
        "regenerate_artifact",
        "revalidate_artifact",
    ]
    assert plan.validated is True
    assert _first_action(plan, "retry_codex_with_schema").tool_name == "plan_followup"
    assert _first_action(plan, "regenerate_artifact").tool_name == "register_artifacts"


def test_missing_artifact_reruns_deterministic_producer_or_marks_optional_skipped() -> None:
    required = create_repair_plan(
        _diagnosis("missing_artifact", metadata={"producer_tool_name": "run_ranking"})
    )
    optional = create_repair_plan(
        _diagnosis("missing_artifact", metadata={"optional": True})
    )

    assert _action_types(required) == ["rerun_tool", "rebuild_index"]
    assert _first_action(required, "rerun_tool").tool_name == "run_ranking"
    assert _action_types(optional) == ["mark_skipped"]


def test_missing_artifact_does_not_auto_modify_source_measurements() -> None:
    plan = create_repair_plan(
        _diagnosis("missing_artifact", metadata={"producer_tool_name": "import_assay_results"})
    )

    assert _action_types(plan) == ["request_human_approval"]
    assert plan.requires_human_approval is True
    assert plan.validated is True


def test_external_unavailable_uses_bounded_retry_policy_cache_and_non_strict_warning() -> None:
    plan = CoreRepairPlannerAgent().plan_repair(
        _diagnosis("external_unavailable", metadata={"strict": False}),
        policy_engine={"allow_cached_real_response": True},
    )

    assert _action_types(plan) == [
        "retry_external_read",
        "adjust_safe_config",
        "mark_skipped",
    ]
    assert _first_action(plan, "retry_external_read").side_effect_level == "external_read"
    assert "before_config" in _first_action(plan, "adjust_safe_config").metadata


def test_permission_denied_requests_approval_without_bypass() -> None:
    plan = CoreRepairPlannerAgent().plan_repair(_diagnosis("permission_denied"))

    assert _action_types(plan) == ["request_human_approval"]
    assert plan.requires_human_approval is True
    assert plan.actions[0].requires_approval is True


def test_guardrail_failed_quarantines_retries_with_schema_and_requires_sentinel_review() -> None:
    plan = CoreRepairPlannerAgent().plan_repair(
        _diagnosis("guardrail_failed", metadata={"high_risk": True})
    )

    assert _action_types(plan) == [
        "quarantine_artifact",
        "retry_codex_with_schema",
        "request_human_approval",
    ]
    assert plan.requires_human_approval is True
    assert all(action.requires_approval for action in plan.actions if action.risk_level == "high")
    assert _first_action(plan, "request_human_approval").metadata["reviewer"] == (
        "GuardrailSentinel"
    )


def test_timeout_and_resource_exhausted_reduce_limits_and_retry_bounded_work() -> None:
    plan = CoreRepairPlannerAgent().plan_repair(
        _diagnosis("timeout", metadata={"checkpoint_available": True})
    )

    assert _action_types(plan) == ["adjust_safe_config", "rerun_job", "rerun_job"]
    config_action = _first_action(plan, "adjust_safe_config")
    assert "before_config" in config_action.metadata
    assert "after_config" in config_action.metadata


def test_inconsistent_artifacts_validate_regenerate_and_report_source_conflicts() -> None:
    plan = CoreRepairPlannerAgent().plan_repair(
        _diagnosis(
            "inconsistent_artifacts",
            root_cause_summary="Source artifacts conflict during contract validation.",
            metadata={"source_artifact_conflict": True},
        )
    )

    assert _action_types(plan) == [
        "revalidate_artifact",
        "regenerate_artifact",
        "create_issue_report",
    ]
    assert plan.validated is True


def test_pipeline_repair_planner_wrapper_records_plan_in_context() -> None:
    diagnosis = _diagnosis("invalid_schema")
    context = PipelineContext(
        disease_input="test",
        config={
            "failure_diagnosis_result": diagnosis.model_dump(mode="json"),
            "runtime_session": {"session_id": "session-1", "autonomy_level": "suggest_only"},
        },
    )

    updated = RepairPlannerAgent().run(context)

    assert updated.config["repair_plan"]["diagnosis_id"] == diagnosis.diagnosis_id
    assert updated.config["repair_plan"]["actions"][0]["action_type"] == (
        "retry_codex_with_schema"
    )


def _diagnosis(
    category: str,
    *,
    metadata: dict[str, object] | None = None,
    root_cause_summary: str = "Runtime repair category identified.",
) -> FailureDiagnosis:
    return FailureDiagnosis(
        diagnosis_id=f"diagnosis-{category}",
        failure_object_type="workflow",
        failure_object_id=f"object-{category}",
        failure_category=category,  # type: ignore[arg-type]
        root_cause_summary=root_cause_summary,
        evidence=[
            {
                "artifact_id": "artifact-source-1",
                "payload": {"source_artifact_ids": ["artifact-source-1"]},
            }
        ],
        recoverable=category
        in {
            "invalid_schema",
            "missing_artifact",
            "external_unavailable",
            "timeout",
            "resource_exhausted",
            "inconsistent_artifacts",
        },
        repairability="automatic_with_limits",
        confidence=0.8,
        warnings=[],
        created_at=datetime(2026, 6, 4, 12, tzinfo=UTC),
        metadata=metadata or {},
    )


def _action_types(plan) -> list[str]:  # type: ignore[no-untyped-def]
    return [action.action_type for action in plan.actions]


def _first_action(plan, action_type: str):  # type: ignore[no-untyped-def]
    return next(action for action in plan.actions if action.action_type == action_type)
