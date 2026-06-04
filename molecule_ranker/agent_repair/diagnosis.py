from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.agent_repair.schemas import (
    FailureCategory,
    FailureDiagnosis,
    FailureObjectType,
    Repairability,
)

CATEGORY_REPAIRABILITY: dict[str, Repairability] = {
    "missing_input": "human_input_required",
    "invalid_schema": "automatic_safe",
    "missing_artifact": "automatic_with_limits",
    "external_unavailable": "automatic_with_limits",
    "permission_denied": "approval_required",
    "policy_blocked": "approval_required",
    "guardrail_failed": "approval_required",
    "timeout": "automatic_with_limits",
    "resource_exhausted": "approval_required",
    "tool_error": "automatic_with_limits",
    "validation_failed": "automatic_safe",
    "parse_error": "automatic_safe",
    "unsafe_output": "approval_required",
    "inconsistent_artifacts": "human_input_required",
    "reproducibility_failure": "automatic_with_limits",
    "unknown": "human_input_required",
}
RECOVERABLE_CATEGORIES = {
    "invalid_schema",
    "missing_artifact",
    "external_unavailable",
    "timeout",
    "tool_error",
    "validation_failed",
    "parse_error",
    "reproducibility_failure",
}
GUARDRAIL_TERMS = {"guardrail_failed", "guardrail_failure", "blocked_guardrail_failed"}


class FailureDiagnosisAgent:
    """Deterministically diagnose repair-loop failures from runtime evidence."""

    def diagnose(
        self,
        *,
        failed_tool_result: Any | None = None,
        failed_job: Any | None = None,
        failed_validation_report: Any | None = None,
        failed_guardrail_report: Any | None = None,
        failed_codex_output: Any | None = None,
        missing_artifact: Any | None = None,
        exception_trace: Any | None = None,
        recent_audit_events: list[Any] | None = None,
        tool_usage_record: Any | None = None,
        related_artifacts: list[Any] | None = None,
    ) -> FailureDiagnosis:
        evidence = _collect_evidence(
            failed_tool_result=failed_tool_result,
            failed_job=failed_job,
            failed_validation_report=failed_validation_report,
            failed_guardrail_report=failed_guardrail_report,
            failed_codex_output=failed_codex_output,
            missing_artifact=missing_artifact,
            exception_trace=exception_trace,
            recent_audit_events=recent_audit_events or [],
            tool_usage_record=tool_usage_record,
            related_artifacts=related_artifacts or [],
        )
        category = _classify_category(evidence)
        object_type = _failure_object_type(evidence, category)
        object_id = _failure_object_id(evidence, object_type)
        confidence = _confidence(category, evidence)
        warnings = _warnings(category, evidence)
        return FailureDiagnosis(
            diagnosis_id=f"failure-diagnosis-{uuid4().hex[:12]}",
            failure_object_type=object_type,
            failure_object_id=object_id,
            failure_category=category,
            root_cause_summary=_root_cause_summary(category, evidence),
            evidence=evidence,
            recoverable=category in RECOVERABLE_CATEGORIES,
            repairability=CATEGORY_REPAIRABILITY[category],
            confidence=confidence,
            warnings=warnings,
            created_at=datetime.now(UTC),
            metadata={
                "deterministic_category": category,
                "evidence_count": len(evidence),
                "codex_summary_allowed": True,
                "deterministic_evidence_decides_category": True,
            },
        )


def diagnose_failure(
    *,
    failed_tool_result: Any | None = None,
    failed_job: Any | None = None,
    failed_validation_report: Any | None = None,
    failed_guardrail_report: Any | None = None,
    failed_codex_output: Any | None = None,
    missing_artifact: Any | None = None,
    exception_trace: Any | None = None,
    recent_audit_events: list[Any] | None = None,
    tool_usage_record: Any | None = None,
    related_artifacts: list[Any] | None = None,
) -> FailureDiagnosis:
    return FailureDiagnosisAgent().diagnose(
        failed_tool_result=failed_tool_result,
        failed_job=failed_job,
        failed_validation_report=failed_validation_report,
        failed_guardrail_report=failed_guardrail_report,
        failed_codex_output=failed_codex_output,
        missing_artifact=missing_artifact,
        exception_trace=exception_trace,
        recent_audit_events=recent_audit_events,
        tool_usage_record=tool_usage_record,
        related_artifacts=related_artifacts,
    )


def _collect_evidence(**sources: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for source_name, value in sources.items():
        if value is None or value == []:
            continue
        if source_name in {"recent_audit_events", "related_artifacts"} and isinstance(value, list):
            evidence.extend(
                _evidence(source_name[:-1], item, index=index)
                for index, item in enumerate(value)
            )
        else:
            evidence.append(_evidence(source_name, value))
    return evidence


def _evidence(source: str, value: Any, *, index: int | None = None) -> dict[str, Any]:
    payload = _payload(value)
    text = _text(payload or value)
    item = {
        "source": source,
        "summary": text[:500],
        "payload": payload,
    }
    if index is not None:
        item["index"] = index
    for key in (
        "result_id",
        "job_id",
        "artifact_id",
        "validation_id",
        "guardrail_id",
        "output_id",
        "sync_job_id",
        "training_run_id",
        "campaign_id",
        "status",
        "error_summary",
        "event_type",
        "tool_name",
    ):
        if payload.get(key) is not None:
            item[key] = payload[key]
    return item


def _classify_category(evidence: list[dict[str, Any]]) -> FailureCategory:
    if _has_source(evidence, "failed_guardrail_report") or _has_guardrail_failure(evidence):
        return "guardrail_failed"
    if _has_source(evidence, "missing_artifact"):
        return "missing_artifact"
    if _has_source(evidence, "failed_validation_report"):
        return _validation_category(evidence)
    if _has_source(evidence, "failed_codex_output"):
        return _codex_category(evidence)

    text = _evidence_text(evidence)
    statuses = {_normalize(str(item.get("status", ""))) for item in evidence}
    event_types = {_normalize(str(item.get("event_type", ""))) for item in evidence}
    if "permission_denied" in text or "403" in text or "unauthorized" in text:
        return "permission_denied"
    if "policy_blocked" in statuses or "policy_blocked" in text or "policy" in event_types:
        return "policy_blocked"
    if "external_unavailable" in text or "rate limit" in text or "503" in text:
        return "external_unavailable"
    if "timeout" in text or "timed out" in text or "job_timed_out" in text:
        return "timeout"
    if "resource exhausted" in text or "quota" in text or "out of memory" in text:
        return "resource_exhausted"
    if "missing input" in text or "required input" in text:
        return "missing_input"
    if "invalid schema" in text or "schema" in text and "invalid" in text:
        return "invalid_schema"
    if "validation_failed" in statuses or "validation failed" in text:
        return "validation_failed"
    if "parse" in text and "failed" in text:
        return "parse_error"
    if "unsafe" in text or "forbidden" in text:
        return "unsafe_output"
    if "inconsistent" in text or "conflict" in text:
        return "inconsistent_artifacts"
    if "reproducibility" in text or "non-reproducible" in text:
        return "reproducibility_failure"
    if _has_source(evidence, "failed_tool_result") or "tool_error" in text:
        return "tool_error"
    return "unknown"


def _validation_category(evidence: list[dict[str, Any]]) -> FailureCategory:
    text = _evidence_text(evidence)
    if "missing input" in text or "required input" in text:
        return "missing_input"
    if "schema" in text or "contract" in text:
        return "invalid_schema"
    if "inconsistent" in text:
        return "inconsistent_artifacts"
    if "reproducibility" in text:
        return "reproducibility_failure"
    return "validation_failed"


def _codex_category(evidence: list[dict[str, Any]]) -> FailureCategory:
    text = _evidence_text(evidence)
    if "parse" in text or "json" in text or "schema" in text:
        return "parse_error"
    if "unsafe" in text or "medical advice" in text or "synthesis" in text:
        return "unsafe_output"
    return "parse_error"


def _failure_object_type(
    evidence: list[dict[str, Any]],
    category: FailureCategory,
) -> FailureObjectType:
    if category == "guardrail_failed":
        return "guardrail"
    source_to_type: tuple[tuple[str, FailureObjectType], ...] = (
        ("failed_tool_result", "tool_call"),
        ("failed_job", "job"),
        ("failed_validation_report", "validation"),
        ("failed_codex_output", "codex_output"),
        ("missing_artifact", "artifact"),
        ("tool_usage_record", "tool_call"),
    )
    for source, object_type in source_to_type:
        if _has_source(evidence, source):
            return object_type
    text = _evidence_text(evidence)
    if "integration" in text or "sync" in text:
        return "integration_sync"
    if "model" in text or "training" in text:
        return "model_training"
    if "structure" in text or "docking" in text:
        return "structure_workflow"
    if "campaign" in text:
        return "campaign"
    return "workflow"


def _failure_object_id(evidence: list[dict[str, Any]], object_type: FailureObjectType) -> str:
    candidates_by_type = {
        "tool_call": ("result_id", "tool_name"),
        "job": ("job_id",),
        "artifact": ("artifact_id",),
        "validation": ("validation_id", "artifact_id"),
        "guardrail": ("guardrail_id", "event_type"),
        "codex_output": ("output_id", "result_id"),
        "integration_sync": ("sync_job_id", "job_id"),
        "model_training": ("training_run_id", "job_id"),
        "campaign": ("campaign_id",),
    }
    for key in candidates_by_type.get(object_type, ("result_id", "job_id", "artifact_id")):
        for item in evidence:
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return f"{object_type}-unknown"


def _root_cause_summary(category: FailureCategory, evidence: list[dict[str, Any]]) -> str:
    deterministic = {
        "missing_input": "Required user or workflow input is missing.",
        "invalid_schema": "A payload or artifact does not satisfy its schema contract.",
        "missing_artifact": "A required artifact is missing and was not invented.",
        "external_unavailable": "An approved external provider is unavailable or rate-limited.",
        "permission_denied": "The actor lacks required RBAC permission.",
        "policy_blocked": "Policy blocked the requested action.",
        "guardrail_failed": "Guardrail findings blocked the workflow and must remain visible.",
        "timeout": "The job or tool call exceeded its runtime deadline.",
        "resource_exhausted": "The workflow exhausted quota, memory, or another bounded resource.",
        "tool_error": "A registered tool failed during execution.",
        "validation_failed": "Deterministic validation failed.",
        "parse_error": "Codex or tool output could not be parsed into the required schema.",
        "unsafe_output": "Output contains unsafe or prohibited content.",
        "inconsistent_artifacts": "Related artifacts are inconsistent.",
        "reproducibility_failure": "The workflow failed reproducibility checks.",
        "unknown": "Available evidence is insufficient for deterministic classification.",
    }[category]
    summary = _first_error_summary(evidence)
    return f"{deterministic} Evidence: {summary}" if summary else deterministic


def _confidence(category: FailureCategory, evidence: list[dict[str, Any]]) -> float:
    if category == "unknown":
        return 0.25
    if _has_source(evidence, "failed_guardrail_report") and category == "guardrail_failed":
        return 1.0
    if any(item.get("status") or item.get("error_summary") for item in evidence):
        return 0.85
    return 0.65


def _warnings(category: FailureCategory, evidence: list[dict[str, Any]]) -> list[str]:
    warnings = [
        "Deterministic evidence decides diagnosis category; Codex summaries are advisory only.",
        "Do not diagnose by inventing missing artifacts.",
    ]
    if category == "guardrail_failed":
        warnings.append("Guardrail failures must not be hidden or downgraded.")
    if category == "unknown":
        warnings.append("Uncertain diagnosis requires human input.")
    if _has_source(evidence, "missing_artifact"):
        warnings.append("Missing artifacts are not evidence and must not be fabricated.")
    return warnings


def _has_guardrail_failure(evidence: list[dict[str, Any]]) -> bool:
    for item in evidence:
        status = _normalize(str(item.get("status", "")))
        event_type = _normalize(str(item.get("event_type", "")))
        if status in GUARDRAIL_TERMS or "guardrail" in event_type and "failed" in event_type:
            return True
        payload = item.get("payload")
        if isinstance(payload, Mapping):
            if payload.get("allowed") is False and payload.get("violations"):
                return True
    return "guardrail failed" in _evidence_text(evidence)


def _has_source(evidence: list[dict[str, Any]], source: str) -> bool:
    return any(item.get("source") == source for item in evidence)


def _first_error_summary(evidence: list[dict[str, Any]]) -> str | None:
    for item in evidence:
        for key in ("error_summary", "summary"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value[:240]
    return None


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, BaseException):
        return {"exception_type": type(value).__name__, "error_summary": str(value)}
    if isinstance(value, str):
        return {"error_summary": value}
    return {"repr": repr(value)}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, Mapping):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, list | tuple | set):
        return " ".join(_text(item) for item in value)
    return "" if value is None else str(value).lower()


def _evidence_text(evidence: list[dict[str, Any]]) -> str:
    return _text(evidence)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


__all__ = ["FailureDiagnosis", "FailureDiagnosisAgent", "diagnose_failure"]
