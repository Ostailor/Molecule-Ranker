from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.agent_repair.schemas import (
    FailureDiagnosis,
    RepairAction,
    RepairPlan,
)

SAFE_DEFAULT_AUTONOMY = {"execute_safe_tools", "execute_with_approval", "full_auto_restricted"}
SCIENTIFIC_MUTATION_KEYS = {
    "assay_result",
    "assay_results",
    "evidence_item",
    "evidence_items",
    "raw_source_artifact",
    "raw_source_artifacts",
    "scientific_score",
    "scientific_scores",
}
DEFAULT_SCIENTIFIC_GUARDRAILS = [
    "Repairs cannot create scientific evidence.",
    "Repairs cannot modify assay results.",
    "Repairs cannot modify raw source artifacts except quarantine or rollback with approval.",
    "External writes and destructive actions require approval.",
    "Guardrail failures must remain visible.",
]


class RepairPlannerAgent:
    """Create deterministic, policy-aware repair plans from failure diagnoses."""

    def __init__(
        self,
        *,
        tool_registry: Any | None = None,
        policy_engine: Any | None = None,
        repair_memory: Any | None = None,
    ) -> None:
        from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

        self.tool_registry = tool_registry or RuntimeToolRegistry.default()
        self.policy_engine = policy_engine
        self.repair_memory = repair_memory

    def plan_repair(
        self,
        diagnosis: FailureDiagnosis | Mapping[str, Any],
        *,
        runtime_session: Any | None = None,
        tool_registry: Any | None = None,
        policy_engine: Any | None = None,
        approvals: Any | None = None,
        repair_memory: Any | None = None,
        user_autonomy_level: str = "suggest_only",
    ) -> RepairPlan:
        diagnosis = _parse_diagnosis(diagnosis)
        active_registry = tool_registry or self.tool_registry
        active_policy_engine = policy_engine if policy_engine is not None else self.policy_engine
        active_repair_memory = repair_memory if repair_memory is not None else self.repair_memory
        active_autonomy = _autonomy_level(runtime_session, user_autonomy_level)
        session_id = _session_id(runtime_session) or _string_or_none(
            diagnosis.metadata.get("session_id")
        )
        actions = self._actions_for_diagnosis(
            diagnosis,
            registry=active_registry,
            policy_engine=active_policy_engine,
            approvals=_approval_set(approvals),
            user_autonomy_level=active_autonomy,
        )
        validation_errors = _validate_actions(actions)
        requires_human_approval = any(action.requires_approval for action in actions)
        return RepairPlan(
            repair_plan_id=f"repair-plan-{uuid4().hex[:12]}",
            diagnosis_id=diagnosis.diagnosis_id,
            session_id=session_id,
            plan_summary=_plan_summary(diagnosis),
            actions=actions,
            expected_artifacts=_expected_artifacts(actions),
            rollback_plan=_rollback_plan(diagnosis, actions),
            requires_human_approval=requires_human_approval,
            scientific_guardrails=DEFAULT_SCIENTIFIC_GUARDRAILS,
            validated=not validation_errors,
            validation_errors=validation_errors,
            created_by="deterministic",
            created_at=datetime.now(UTC),
            metadata={
                "failure_category": diagnosis.failure_category,
                "repairability": diagnosis.repairability,
                "autonomy_level": active_autonomy,
                "memory_strategy": _memory_strategy(active_repair_memory, diagnosis),
            },
        )

    def plan(
        self,
        diagnosis: FailureDiagnosis | Mapping[str, Any],
        **kwargs: Any,
    ) -> RepairPlan:
        return self.plan_repair(diagnosis, **kwargs)

    def _actions_for_diagnosis(
        self,
        diagnosis: FailureDiagnosis,
        *,
        registry: Any,
        policy_engine: Any | None,
        approvals: set[str],
        user_autonomy_level: str,
    ) -> list[RepairAction]:
        category = diagnosis.failure_category
        if category == "missing_input":
            return _missing_input_actions(diagnosis, policy_engine, user_autonomy_level)
        if category in {"invalid_schema", "validation_failed", "parse_error"}:
            return _invalid_schema_actions(diagnosis, registry)
        if category == "missing_artifact":
            return _missing_artifact_actions(diagnosis, registry)
        if category == "external_unavailable":
            return _external_unavailable_actions(diagnosis, policy_engine, user_autonomy_level)
        if category in {"permission_denied", "policy_blocked"}:
            return _permission_actions(diagnosis)
        if category == "guardrail_failed":
            return _guardrail_actions(diagnosis)
        if category in {"timeout", "resource_exhausted"}:
            return _resource_actions(diagnosis)
        if category == "inconsistent_artifacts":
            return _inconsistent_artifact_actions(diagnosis, registry)
        if category == "reproducibility_failure":
            return _reproducibility_actions(diagnosis)
        if category == "tool_error":
            return _generic_recoverable_actions(diagnosis)
        return [
            _action(
                diagnosis,
                action_type="request_missing_input",
                target_object_type=diagnosis.failure_object_type,
                expected_effect="Collect human input for uncertain failure diagnosis.",
                side_effect_level="none",
                requires_approval=False,
                risk_level="low",
                metadata={"reason": "unknown_failure_requires_human_input"},
            )
        ]


def create_repair_plan(
    diagnosis: FailureDiagnosis | Mapping[str, Any],
    *,
    runtime_session: Any | None = None,
    tool_registry: Any | None = None,
    policy_engine: Any | None = None,
    approvals: set[str] | list[str] | None = None,
    repair_memory: Any | None = None,
    user_autonomy_level: str = "suggest_only",
) -> RepairPlan:
    return RepairPlannerAgent(
        tool_registry=tool_registry,
        policy_engine=policy_engine,
        repair_memory=repair_memory,
    ).plan_repair(
        diagnosis,
        runtime_session=runtime_session,
        tool_registry=tool_registry,
        policy_engine=policy_engine,
        approvals=approvals,
        repair_memory=repair_memory,
        user_autonomy_level=user_autonomy_level,
    )


def plan_repair(
    diagnosis: FailureDiagnosis | Mapping[str, Any],
    **kwargs: Any,
) -> RepairPlan:
    return RepairPlannerAgent().plan_repair(diagnosis, **kwargs)


def _missing_input_actions(
    diagnosis: FailureDiagnosis,
    policy_engine: Any | None,
    autonomy_level: str,
) -> list[RepairAction]:
    actions = [
        _action(
            diagnosis,
            action_type="request_missing_input",
            target_object_type=diagnosis.failure_object_type,
            expected_effect="Request the missing user or workflow input.",
            side_effect_level="none",
            requires_approval=False,
            risk_level="low",
        )
    ]
    if (
        autonomy_level in SAFE_DEFAULT_AUTONOMY
        and (
            _metadata_flag(diagnosis, "safe_defaults_allowed")
            or _policy_flag(policy_engine, "allow_safe_defaults")
        )
    ):
        actions.append(
            _action(
                diagnosis,
                action_type="adjust_safe_config",
                target_object_type="workflow",
                expected_effect="Apply approved safe default configuration.",
                side_effect_level="none",
                requires_approval=False,
                risk_level="low",
                metadata={
                    "before_config": _metadata_value(diagnosis, "before_config", {}),
                    "after_config": _metadata_value(diagnosis, "safe_defaults", {}),
                    "config_change_recorded": True,
                },
            )
        )
    return actions


def _invalid_schema_actions(
    diagnosis: FailureDiagnosis,
    registry: Any,
) -> list[RepairAction]:
    return [
        _action(
            diagnosis,
            action_type="retry_codex_with_schema",
            target_object_type=diagnosis.failure_object_type,
            tool_name=_available_tool(registry, "plan_followup"),
            expected_effect="Retry Codex output with a stricter JSON schema.",
            side_effect_level="none",
            requires_approval=False,
            risk_level="low",
            metadata={"schema_retry": True},
        ),
        _action(
            diagnosis,
            action_type="regenerate_artifact",
            target_object_type="artifact",
            tool_name=_available_tool(registry, "register_artifacts"),
            expected_effect="Regenerate derived artifact from existing source artifacts.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="low",
            metadata={"source_artifact_ids": _source_artifacts(diagnosis)},
        ),
        _action(
            diagnosis,
            action_type="revalidate_artifact",
            target_object_type="validation",
            expected_effect="Revalidate artifact after schema repair.",
            side_effect_level="none",
            requires_approval=False,
            risk_level="low",
        ),
    ]


def _missing_artifact_actions(
    diagnosis: FailureDiagnosis,
    registry: Any,
) -> list[RepairAction]:
    if _metadata_flag(diagnosis, "optional_artifact") or _metadata_flag(diagnosis, "optional"):
        return [
            _action(
                diagnosis,
                action_type="mark_skipped",
                target_object_type="artifact",
                expected_effect="Mark optional missing artifact as skipped with a warning.",
                side_effect_level="artifact_write",
                requires_approval=False,
                risk_level="low",
                metadata={"optional": True},
            )
        ]
    producer_tool = (
        _string_or_none(diagnosis.metadata.get("producer_tool_name"))
        or _string_or_none(diagnosis.metadata.get("producer_tool"))
        or "run_ranking"
    )
    if producer_tool in {"import_assay_results", "link_assay_results"}:
        return [
            _action(
                diagnosis,
                action_type="request_human_approval",
                target_object_type=diagnosis.failure_object_type,
                expected_effect=(
                    "Request operator action because source measurement tools cannot "
                    "be auto-repaired."
                ),
                side_effect_level="none",
                requires_approval=True,
                approval_reason="Repair planner cannot modify source measurements.",
                risk_level="high",
            )
        ]
    return [
        _action(
            diagnosis,
            action_type="rerun_tool",
            target_object_type="tool_call",
            tool_name=_available_tool(registry, producer_tool),
            expected_effect="Rerun deterministic tool that produces the missing artifact.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="medium",
        ),
        _action(
            diagnosis,
            action_type="rebuild_index",
            target_object_type="artifact",
            expected_effect="Rebuild artifact index after artifact regeneration.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="low",
        ),
    ]


def _external_unavailable_actions(
    diagnosis: FailureDiagnosis,
    policy_engine: Any | None,
    autonomy_level: str,
) -> list[RepairAction]:
    actions = [
        _action(
            diagnosis,
            action_type="retry_external_read",
            target_object_type=diagnosis.failure_object_type,
            expected_effect="Retry approved external read with bounded backoff.",
            side_effect_level="external_read",
            requires_approval=False,
            risk_level="low",
            metadata={"backoff": {"max_attempts": 2, "strategy": "exponential"}},
        )
    ]
    if _metadata_flag(diagnosis, "cached_real_response_allowed") or _policy_flag(
        policy_engine, "allow_cached_real_response"
    ):
        actions.append(
            _action(
                diagnosis,
                action_type="adjust_safe_config",
                target_object_type="workflow",
                expected_effect="Switch to cached real response allowed by policy.",
                side_effect_level="none",
                requires_approval=autonomy_level == "suggest_only",
                approval_reason="Using cached provider responses requires policy confirmation."
                if autonomy_level == "suggest_only"
                else None,
                risk_level="medium",
                metadata={
                    "before_config": {"external_read_mode": "live"},
                    "after_config": {"external_read_mode": "cached_real_response"},
                    "config_change_recorded": True,
                },
            )
        )
    if _metadata_flag(diagnosis, "non_strict") or diagnosis.metadata.get("strict") is False:
        actions.append(
            _action(
                diagnosis,
                action_type="mark_skipped",
                target_object_type="workflow",
                expected_effect="Continue non-strict path with user-visible warning.",
                side_effect_level="none",
                requires_approval=False,
                risk_level="low",
                metadata={"warning": "external_unavailable_non_strict"},
            )
        )
    return actions


def _permission_actions(diagnosis: FailureDiagnosis) -> list[RepairAction]:
    return [
        _action(
            diagnosis,
            action_type="request_human_approval",
            target_object_type=diagnosis.failure_object_type,
            expected_effect="Request approval or admin action without bypassing permission.",
            side_effect_level="none",
            requires_approval=True,
            approval_reason="Permission or policy failure requires authorized human action.",
            risk_level="medium",
            metadata={"do_not_bypass_permission": True},
        )
    ]


def _guardrail_actions(diagnosis: FailureDiagnosis) -> list[RepairAction]:
    high_risk = _metadata_flag(diagnosis, "high_risk")
    return [
        _action(
            diagnosis,
            action_type="quarantine_artifact",
            target_object_type=diagnosis.failure_object_type,
            expected_effect="Quarantine unsafe output while preserving guardrail evidence.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="medium",
            metadata={"preserve_guardrail_failure": True},
        ),
        _action(
            diagnosis,
            action_type="retry_codex_with_schema",
            target_object_type="codex_output",
            tool_name="plan_followup",
            expected_effect="Request revised Codex output with stricter prompt and schema.",
            side_effect_level="none",
            requires_approval=high_risk,
            approval_reason="High-risk guardrail repair requires human approval."
            if high_risk
            else None,
            risk_level="high" if high_risk else "medium",
            metadata={"requires_guardrail_sentinel_review": True},
        ),
        _action(
            diagnosis,
            action_type="request_human_approval",
            target_object_type="guardrail",
            expected_effect="Require GuardrailSentinel review before accepting repaired output.",
            side_effect_level="none",
            requires_approval=True,
            approval_reason="GuardrailSentinel review is required.",
            risk_level="high",
            metadata={"reviewer": "GuardrailSentinel", "do_not_auto_accept": high_risk},
        ),
    ]


def _resource_actions(diagnosis: FailureDiagnosis) -> list[RepairAction]:
    actions = [
        _action(
            diagnosis,
            action_type="adjust_safe_config",
            target_object_type="job",
            expected_effect="Reduce safe job limits before retry.",
            side_effect_level="none",
            requires_approval=False,
            risk_level="low",
            metadata={
                "before_config": _metadata_value(diagnosis, "before_config", {}),
                "after_config": _metadata_value(
                    diagnosis,
                    "after_config",
                    {"batch_size": "reduced", "timeout_seconds": "bounded"},
                ),
                "config_change_recorded": True,
            },
        ),
        _action(
            diagnosis,
            action_type="rerun_job",
            target_object_type="job",
            expected_effect="Split or rerun job with reduced limits.",
            side_effect_level="artifact_write",
            requires_approval=diagnosis.failure_category == "resource_exhausted",
            approval_reason="Resource-exhausted retry may consume additional quota."
            if diagnosis.failure_category == "resource_exhausted"
            else None,
            risk_level="medium",
            metadata={"split_job": True},
        ),
    ]
    if _metadata_flag(diagnosis, "checkpoint_available"):
        actions.append(
            _action(
                diagnosis,
                action_type="rerun_job",
                target_object_type="job",
                expected_effect="Resume from checkpoint if available.",
                side_effect_level="artifact_write",
                requires_approval=False,
                risk_level="low",
                metadata={"resume_from_checkpoint": True},
            )
        )
    return actions


def _inconsistent_artifact_actions(
    diagnosis: FailureDiagnosis,
    registry: Any,
) -> list[RepairAction]:
    actions = [
        _action(
            diagnosis,
            action_type="revalidate_artifact",
            target_object_type="artifact",
            tool_name=_available_tool(registry, "run_release_check"),
            expected_effect="Run contract validation across conflicting artifacts.",
            side_effect_level="none",
            requires_approval=False,
            risk_level="low",
        ),
        _action(
            diagnosis,
            action_type="regenerate_artifact",
            target_object_type="artifact",
            tool_name=_available_tool(registry, "register_artifacts"),
            expected_effect="Regenerate derived artifacts from validated sources only.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="medium",
            metadata={"source_artifact_ids": _source_artifacts(diagnosis)},
        ),
    ]
    if _source_artifacts_conflict(diagnosis):
        actions.append(
            _action(
                diagnosis,
                action_type="create_issue_report",
                target_object_type="artifact",
                expected_effect="Create issue report because source artifacts conflict.",
                side_effect_level="artifact_write",
                requires_approval=False,
                risk_level="low",
                metadata={"source_conflict_report": True},
            )
        )
    return actions


def _reproducibility_actions(diagnosis: FailureDiagnosis) -> list[RepairAction]:
    return [
        _action(
            diagnosis,
            action_type="run_regression_check",
            target_object_type="workflow",
            tool_name="run_reproducibility_check",
            expected_effect="Run reproducibility checks before retrying recorded inputs.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="medium",
        ),
        _action(
            diagnosis,
            action_type="rerun_job",
            target_object_type="job",
            expected_effect="Rerun using the existing recorded inputs.",
            side_effect_level="artifact_write",
            requires_approval=False,
            risk_level="medium",
            metadata={"retry_mode": "recorded_inputs"},
        ),
    ]


def _generic_recoverable_actions(diagnosis: FailureDiagnosis) -> list[RepairAction]:
    return [
        _action(
            diagnosis,
            action_type="retry_codex_with_schema",
            target_object_type=diagnosis.failure_object_type,
            tool_name="plan_followup",
            expected_effect="Retry schema-bound assistant output or deterministic transformation.",
            side_effect_level="none",
            requires_approval=False,
            risk_level="low",
        ),
        _action(
            diagnosis,
            action_type="run_regression_check",
            target_object_type="workflow",
            expected_effect="Run regression checks after repair.",
            side_effect_level="none",
            requires_approval=False,
            risk_level="low",
        ),
    ]


def _action(
    diagnosis: FailureDiagnosis,
    *,
    action_type: str,
    target_object_type: str,
    expected_effect: str,
    side_effect_level: str,
    requires_approval: bool,
    risk_level: str,
    tool_name: str | None = None,
    approval_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RepairAction:
    side_effect = side_effect_level
    approval = (
        requires_approval
        or side_effect in {"external_write", "destructive"}
        or risk_level in {"high", "critical"}
    )
    reason = approval_reason
    if approval and reason is None:
        if side_effect in {"external_write", "destructive"}:
            reason = f"{side_effect} repair action requires human approval."
        else:
            reason = "High-risk repair action requires human approval."
    return RepairAction(
        repair_action_id=f"repair-action-{uuid4().hex[:12]}",
        action_type=action_type,  # type: ignore[arg-type]
        target_object_type=target_object_type,
        target_object_id=diagnosis.failure_object_id,
        tool_name=tool_name,
        tool_args=_safe_tool_args(diagnosis, metadata or {}),
        expected_effect=expected_effect,
        side_effect_level=side_effect,  # type: ignore[arg-type]
        requires_approval=approval,
        approval_reason=reason,
        risk_level=risk_level,  # type: ignore[arg-type]
        metadata={
            **(metadata or {}),
            "diagnosis_id": diagnosis.diagnosis_id,
            "failure_category": diagnosis.failure_category,
        },
    )


def _safe_tool_args(diagnosis: FailureDiagnosis, metadata: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {
        "diagnosis_id": diagnosis.diagnosis_id,
        "failure_object_id": diagnosis.failure_object_id,
        "failure_category": diagnosis.failure_category,
    }
    for key in ("source_artifact_ids", "before_config", "after_config", "backoff"):
        if key in metadata:
            args[key] = metadata[key]
    return args


def _validate_actions(actions: list[RepairAction]) -> list[str]:
    errors: list[str] = []
    for action in actions:
        payload = action.model_dump(mode="python")
        if _contains_forbidden_scientific_mutation(payload):
            errors.append(f"{action.repair_action_id} attempts scientific evidence mutation.")
        if action.action_type == "adjust_safe_config":
            if "before_config" not in action.metadata or "after_config" not in action.metadata:
                errors.append(f"{action.repair_action_id} config change lacks before/after.")
        if (
            action.side_effect_level in {"external_write", "destructive"}
            and not action.requires_approval
        ):
            errors.append(f"{action.repair_action_id} risky action lacks approval.")
        if action.risk_level in {"high", "critical"} and not action.requires_approval:
            errors.append(f"{action.repair_action_id} high-risk action lacks approval.")
        if (
            action.action_type in {"rollback_artifact", "rollback_job"}
            and not action.requires_approval
        ):
            errors.append(f"{action.repair_action_id} rollback requires approval.")
        if action.tool_name in {"import_assay_results", "link_assay_results"}:
            errors.append(f"{action.repair_action_id} would modify source measurements.")
    return errors


def _rollback_plan(diagnosis: FailureDiagnosis, actions: list[RepairAction]) -> list[RepairAction]:
    if not any(action.side_effect_level in {"artifact_write", "db_write"} for action in actions):
        return []
    return [
        _action(
            diagnosis,
            action_type="rollback_artifact",
            target_object_type=diagnosis.failure_object_type,
            expected_effect="Rollback derived repair artifacts if regression checks fail.",
            side_effect_level="destructive",
            requires_approval=True,
            approval_reason="Rollback actions are destructive and require approval.",
            risk_level="high",
            metadata={"rollback_for_repair_plan": True},
        )
    ]


def _expected_artifacts(actions: list[RepairAction]) -> list[str]:
    artifact_actions = {
        "regenerate_artifact",
        "revalidate_artifact",
        "quarantine_artifact",
        "rebuild_index",
        "create_issue_report",
        "run_regression_check",
    }
    return [
        f"repair-{action.action_type}-{index}"
        for index, action in enumerate(actions, start=1)
        if action.action_type in artifact_actions or action.side_effect_level == "artifact_write"
    ]


def _source_artifacts(diagnosis: FailureDiagnosis) -> list[str]:
    artifact_ids: list[str] = []
    for item in diagnosis.evidence:
        value = item.get("artifact_id")
        if isinstance(value, str):
            artifact_ids.append(value)
        payload = item.get("payload")
        if isinstance(payload, Mapping):
            for key in ("artifact_id", "source_artifact_id"):
                raw = payload.get(key)
                if isinstance(raw, str):
                    artifact_ids.append(raw)
            raw_list = payload.get("source_artifact_ids")
            if isinstance(raw_list, list):
                artifact_ids.extend(item for item in raw_list if isinstance(item, str))
    return list(dict.fromkeys(artifact_ids))


def _parse_diagnosis(diagnosis: FailureDiagnosis | Mapping[str, Any]) -> FailureDiagnosis:
    if isinstance(diagnosis, FailureDiagnosis):
        return diagnosis
    return FailureDiagnosis.model_validate(diagnosis)


def _autonomy_level(runtime_session: Any | None, explicit: str) -> str:
    session_payload = _payload(runtime_session)
    value = session_payload.get("autonomy_level") or explicit
    return str(value)


def _payload(value: Any | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return {}


def _approval_set(approvals: Any | None) -> set[str]:
    if approvals is None:
        return set()
    if isinstance(approvals, Mapping):
        values = approvals.get("approved") or approvals.get("approval_ids") or []
        if isinstance(values, list):
            return {str(value) for value in values}
        return set()
    if isinstance(approvals, (set, list, tuple)):
        return {str(value) for value in approvals}
    return set()


def _policy_flag(policy_engine: Any | None, flag: str) -> bool:
    if policy_engine is None:
        return False
    if isinstance(policy_engine, Mapping):
        return bool(policy_engine.get(flag))
    attr = getattr(policy_engine, flag, None)
    if isinstance(attr, bool):
        return attr
    if callable(attr):
        try:
            return bool(attr())
        except TypeError:
            return False
    return False


def _available_tool(registry: Any, tool_name: str) -> str | None:
    try:
        return tool_name if registry.get(tool_name) is not None else None
    except AttributeError:
        return tool_name


def _source_artifacts_conflict(diagnosis: FailureDiagnosis) -> bool:
    if _metadata_flag(diagnosis, "source_artifact_conflict"):
        return True
    text = " ".join(
        [
            diagnosis.root_cause_summary,
            " ".join(diagnosis.warnings),
            repr(diagnosis.evidence),
        ]
    ).lower()
    return "source" in text and "conflict" in text


def _plan_summary(diagnosis: FailureDiagnosis) -> str:
    return (
        f"Repair {diagnosis.failure_category} for "
        f"{diagnosis.failure_object_type} `{diagnosis.failure_object_id}`."
    )


def _memory_strategy(memory: Any | None, diagnosis: FailureDiagnosis) -> str | None:
    if memory is None:
        return None
    retrieve = getattr(memory, "retrieve", None)
    if not callable(retrieve):
        return None
    try:
        records = retrieve(diagnosis.failure_category)
    except TypeError:
        records = retrieve(failure_category=diagnosis.failure_category)
    except Exception:
        return None
    if not isinstance(records, (list, tuple)) or not records:
        return None
    record = records[0]
    if hasattr(record, "recommended_repair_strategy"):
        return str(record.recommended_repair_strategy)
    if isinstance(record, Mapping):
        strategy = record.get("recommended_repair_strategy")
        return strategy if isinstance(strategy, str) else None
    return None


def _contains_forbidden_scientific_mutation(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in SCIENTIFIC_MUTATION_KEYS:
                return True
            if _contains_forbidden_scientific_mutation(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_scientific_mutation(item) for item in value)
    return False


def _metadata_flag(diagnosis: FailureDiagnosis, key: str) -> bool:
    return bool(diagnosis.metadata.get(key))


def _metadata_value(diagnosis: FailureDiagnosis, key: str, default: Any) -> Any:
    return diagnosis.metadata.get(key, default)


def _session_id(runtime_session: Any | None) -> str | None:
    if runtime_session is None:
        return None
    if hasattr(runtime_session, "session_id"):
        value = runtime_session.session_id
        return value if isinstance(value, str) else None
    if isinstance(runtime_session, Mapping):
        return _string_or_none(runtime_session.get("session_id"))
    return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


__all__ = [
    "RepairAction",
    "RepairPlan",
    "RepairPlannerAgent",
    "create_repair_plan",
    "plan_repair",
]
