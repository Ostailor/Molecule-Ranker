from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.agent_repair.schemas import RegressionCheck, RepairExecution

DEFAULT_CHECK_TYPES = [
    "schema_contract",
    "artifact_completeness",
    "scientific_integrity",
    "guardrail",
    "permissions",
    "reproducibility",
    "workflow_smoke",
    "performance_smoke",
    "targeted_unit_subset",
    "targeted_integration_subset",
]


class RegressionCheckAgent:
    """Run deterministic regression checks after repair execution."""

    def run_checks(
        self,
        *,
        repair_execution: RepairExecution | Mapping[str, Any],
        changed_artifacts: list[Any] | None = None,
        changed_config: Mapping[str, Any] | None = None,
        affected_workflow: Any | None = None,
        check_types: list[str] | None = None,
    ) -> list[RegressionCheck]:
        execution = _parse_execution(repair_execution)
        artifacts = [_payload(artifact) for artifact in changed_artifacts or []]
        config = dict(changed_config or {})
        workflow = _payload(affected_workflow)

        checks = [
            self._check(
                check_type,
                execution=execution,
                artifacts=artifacts,
                config=config,
                workflow=workflow,
            )
            for check_type in check_types or DEFAULT_CHECK_TYPES
        ]
        return checks

    def required_checks_passed(self, checks: list[RegressionCheck]) -> bool:
        return bool(checks) and all(check.passed for check in checks)

    def execution_status_after_regression(
        self,
        execution: RepairExecution | Mapping[str, Any],
        checks: list[RegressionCheck],
    ) -> str:
        parsed = _parse_execution(execution)
        if self.required_checks_passed(checks):
            return parsed.status
        if parsed.executed_actions:
            return "partially_succeeded"
        return "failed"

    def _check(
        self,
        check_type: str,
        *,
        execution: RepairExecution,
        artifacts: list[dict[str, Any]],
        config: dict[str, Any],
        workflow: dict[str, Any],
    ) -> RegressionCheck:
        findings = _findings_for_check(
            check_type,
            execution=execution,
            artifacts=artifacts,
            config=config,
            workflow=workflow,
        )
        return RegressionCheck(
            regression_check_id=f"regression-check-{uuid4().hex[:12]}",
            repair_execution_id=execution.repair_execution_id,
            check_type=check_type,  # type: ignore[arg-type]
            passed=not findings,
            findings=findings,
            artifacts_checked=_artifact_ids(execution, artifacts),
            created_at=datetime.now(UTC),
            metadata={
                "repair_not_successful_until_required_checks_pass": True,
                "failure_visibility": "regression_failures_are_not_hidden",
            },
        )


def run_regression_checks(
    *,
    repair_execution: RepairExecution | Mapping[str, Any],
    changed_artifacts: list[Any] | None = None,
    changed_config: Mapping[str, Any] | None = None,
    affected_workflow: Any | None = None,
    check_types: list[str] | None = None,
) -> list[RegressionCheck]:
    return RegressionCheckAgent().run_checks(
        repair_execution=repair_execution,
        changed_artifacts=changed_artifacts,
        changed_config=changed_config,
        affected_workflow=affected_workflow,
        check_types=check_types,
    )


def regression_passed(checks: list[RegressionCheck]) -> bool:
    return RegressionCheckAgent().required_checks_passed(checks)


def _findings_for_check(
    check_type: str,
    *,
    execution: RepairExecution,
    artifacts: list[dict[str, Any]],
    config: dict[str, Any],
    workflow: dict[str, Any],
) -> list[str]:
    if check_type == "schema_contract":
        return _schema_contract_findings(artifacts, workflow)
    if check_type == "artifact_completeness":
        return _artifact_completeness_findings(execution, artifacts, workflow)
    if check_type == "scientific_integrity":
        return _scientific_integrity_findings(execution, artifacts, config)
    if check_type == "guardrail":
        return _guardrail_findings(execution, artifacts, config, workflow)
    if check_type == "permissions":
        return _permission_findings(execution, config)
    if check_type == "reproducibility":
        return _boolean_gate_findings(
            workflow,
            config,
            positive_key="reproducible",
            negative_key="reproducibility_failed",
            label="Reproducibility regression failed.",
        )
    if check_type == "workflow_smoke":
        return _workflow_smoke_findings(execution, workflow)
    if check_type == "performance_smoke":
        return _boolean_gate_findings(
            workflow,
            config,
            positive_key="performance_smoke_passed",
            negative_key="performance_regression",
            label="Performance smoke check failed.",
        )
    if check_type in {"targeted_unit_subset", "unit_subset"}:
        return _boolean_gate_findings(
            workflow,
            config,
            positive_key="targeted_unit_subset_passed",
            negative_key="targeted_unit_subset_failed",
            label="Targeted unit subset failed.",
        )
    if check_type in {"targeted_integration_subset", "integration_subset"}:
        return _boolean_gate_findings(
            workflow,
            config,
            positive_key="targeted_integration_subset_passed",
            negative_key="targeted_integration_subset_failed",
            label="Targeted integration subset failed.",
        )
    return [f"Unsupported regression check type: {check_type}."]


def _schema_contract_findings(
    artifacts: list[dict[str, Any]],
    workflow: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    for artifact in artifacts:
        artifact_id = _object_id(artifact)
        if artifact.get("schema_valid") is False or artifact.get("contract_valid") is False:
            findings.append(f"Artifact {artifact_id} failed schema contract validation.")
        errors = artifact.get("schema_errors") or artifact.get("contract_errors")
        if isinstance(errors, list) and errors:
            findings.append(f"Artifact {artifact_id} has schema contract errors.")
        if not artifact.get("schema_version") and artifact.get("schema_version_required") is True:
            findings.append(f"Artifact {artifact_id} is missing required schema version.")
    if workflow.get("schema_contract_passed") is False:
        findings.append("Affected workflow schema contract check failed.")
    return findings


def _artifact_completeness_findings(
    execution: RepairExecution,
    artifacts: list[dict[str, Any]],
    workflow: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    artifact_by_id = {_object_id(artifact): artifact for artifact in artifacts}
    for artifact_id in [*execution.artifacts_created, *execution.artifacts_modified]:
        artifact = artifact_by_id.get(artifact_id)
        if artifact and artifact.get("exists") is False:
            findings.append(f"Changed artifact {artifact_id} is missing.")
    required_ids = workflow.get("required_artifact_ids")
    if isinstance(required_ids, list):
        available = set(artifact_by_id) | set(execution.artifacts_created) | set(
            execution.artifacts_modified
        )
        missing = sorted(
            item for item in required_ids if isinstance(item, str) and item not in available
        )
        if missing:
            findings.append("Required artifacts are missing: " + ", ".join(missing))
    if workflow.get("artifact_completeness_passed") is False:
        findings.append("Affected workflow artifact completeness check failed.")
    return findings


def _scientific_integrity_findings(
    execution: RepairExecution,
    artifacts: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    if execution.metadata.get("direct_score_edit") is True:
        findings.append("Repair execution attempted a direct score edit.")
    if config.get("direct_score_edit") is True or config.get("raw_evidence_edit") is True:
        findings.append("Changed config attempts direct scientific-content editing.")
    for artifact in artifacts:
        artifact_id = _object_id(artifact)
        for key in (
            "fabricated_scientific_content",
            "promotes_prediction_to_evidence",
            "promotes_docking_to_binding_evidence",
            "promotes_graph_inference_to_evidence",
            "direct_score_edit",
            "raw_evidence_edit",
        ):
            if artifact.get(key) is True:
                findings.append(f"Artifact {artifact_id} violates scientific integrity: {key}.")
    return findings


def _guardrail_findings(
    execution: RepairExecution,
    artifacts: list[dict[str, Any]],
    config: dict[str, Any],
    workflow: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    if execution.status == "guardrail_blocked":
        findings.append("Repair execution is guardrail blocked.")
    if config.get("guardrail_failed") is True or workflow.get("guardrail_passed") is False:
        findings.append("Guardrail check failed after repair.")
    for artifact in artifacts:
        artifact_id = _object_id(artifact)
        if artifact.get("guardrail_passed") is False:
            findings.append(f"Artifact {artifact_id} failed guardrail checks.")
        failures = artifact.get("guardrail_failures")
        if isinstance(failures, list) and failures:
            findings.append(f"Artifact {artifact_id} has guardrail failures.")
    return findings


def _permission_findings(
    execution: RepairExecution,
    config: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    if config.get("permission_denied") is True or config.get("policy_bypassed") is True:
        findings.append("Permission or policy failure was detected after repair.")
    for action in execution.executed_actions:
        side_effect = action.get("side_effect_level")
        if side_effect in {"external_write", "destructive"} and not action.get("approved"):
            findings.append(
                f"Executed action {action.get('repair_action_id')} required approval."
            )
    return findings


def _workflow_smoke_findings(
    execution: RepairExecution,
    workflow: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    if execution.status in {"failed", "cancelled", "guardrail_blocked"}:
        findings.append(f"Repair execution status is {execution.status}.")
    if workflow.get("workflow_smoke_passed") is False:
        findings.append("Affected workflow smoke check failed.")
    expected_next_step = workflow.get("expected_next_step_available")
    if expected_next_step is False:
        findings.append("Expected next workflow step is not available.")
    return findings


def _boolean_gate_findings(
    workflow: dict[str, Any],
    config: dict[str, Any],
    *,
    positive_key: str,
    negative_key: str,
    label: str,
) -> list[str]:
    if workflow.get(positive_key) is False:
        return [label]
    if workflow.get(negative_key) is True or config.get(negative_key) is True:
        return [label]
    return []


def _parse_execution(value: RepairExecution | Mapping[str, Any]) -> RepairExecution:
    if isinstance(value, RepairExecution):
        return value
    return RepairExecution.model_validate(value)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return {}


def _artifact_ids(execution: RepairExecution, artifacts: list[dict[str, Any]]) -> list[str]:
    ids = [_object_id(artifact) for artifact in artifacts]
    ids.extend(execution.artifacts_created)
    ids.extend(execution.artifacts_modified)
    return sorted(set(item for item in ids if item))


def _object_id(payload: dict[str, Any]) -> str:
    for key in ("artifact_id", "id", "object_id"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return "unknown"


__all__ = [
    "DEFAULT_CHECK_TYPES",
    "RegressionCheck",
    "RegressionCheckAgent",
    "regression_passed",
    "run_regression_checks",
]
