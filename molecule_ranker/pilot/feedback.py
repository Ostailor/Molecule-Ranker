from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker.codex_backbone.guardrails import redact_secrets

FeedbackType = Literal["usability_issue", "feature_request", "bug_report", "workflow_friction"]
FeedbackSeverity = Literal["low", "medium", "high", "critical"]
FeedbackStatus = Literal["open", "triaged", "in_progress", "resolved", "closed"]

SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|secret|service[_-]?token|token)"
    r"\s*[:=]\s*[^\s,;]+"
)


class PilotFeedback(BaseModel):
    feedback_id: str = Field(default_factory=lambda: f"pilot-feedback-{uuid.uuid4().hex[:12]}")
    user_id: str
    project_id: str | None = None
    page_or_command: str
    feedback_type: FeedbackType
    severity: FeedbackSeverity = "medium"
    text: str
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: FeedbackStatus = "open"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text", "page_or_command", mode="before")
    @classmethod
    def redact_text_fields(cls, value: Any) -> str:
        return redact_feedback_text(str(value or ""))

    @field_validator("metadata", mode="before")
    @classmethod
    def redact_metadata(cls, value: Any) -> dict[str, Any]:
        return _redact_json(value if isinstance(value, dict) else {})

    @model_validator(mode="after")
    def enforce_feedback_boundary(self) -> PilotFeedback:
        self.metadata = {
            **self.metadata,
            "not_scientific_evidence": True,
            "not_biomedical_evidence": True,
            "export_excludes_artifact_payloads": True,
        }
        return self


class UsabilityIssue(PilotFeedback):
    feedback_type: FeedbackType = "usability_issue"


class FeatureRequest(PilotFeedback):
    feedback_type: FeedbackType = "feature_request"


class BugReport(PilotFeedback):
    feedback_type: FeedbackType = "bug_report"


class WorkflowFrictionReport(PilotFeedback):
    feedback_type: FeedbackType = "workflow_friction"


class PilotFeedbackStore:
    def __init__(self, root_dir: str | Path = ".") -> None:
        self.root_dir = Path(root_dir).resolve()
        self.feedback_path = self.root_dir / ".molecule-ranker" / "pilot_feedback.jsonl"

    def submit(self, feedback: PilotFeedback) -> PilotFeedback:
        self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with self.feedback_path.open("a", encoding="utf-8") as handle:
            handle.write(feedback.model_dump_json() + "\n")
        return feedback

    def list(
        self,
        *,
        project_id: str | None = None,
        status: FeedbackStatus | None = None,
        limit: int = 100,
    ) -> list[PilotFeedback]:
        items = self._read_all()
        if project_id is not None:
            items = [item for item in items if item.project_id == project_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[: max(1, limit)]

    def export(self, output_path: str | Path, *, include_artifact_payloads: bool = False) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "feedback": [item.model_dump(mode="json") for item in self.list(limit=10_000)],
            "not_scientific_evidence": True,
            "not_biomedical_evidence": True,
            "excludes_cache_payloads": True,
            "excludes_artifact_payloads": True,
            "include_artifact_payloads_requested": bool(include_artifact_payloads),
            "artifact_export_policy": (
                "Artifact references are exported as IDs only unless explicitly referenced; raw "
                "artifact payloads are not included by pilot feedback export."
            ),
        }
        path.write_text(json.dumps(_redact_json(payload), indent=2, sort_keys=True) + "\n")
        return path

    def _read_all(self) -> list[PilotFeedback]:
        if not self.feedback_path.exists():
            return []
        items: list[PilotFeedback] = []
        for line in self.feedback_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            items.append(PilotFeedback.model_validate_json(line))
        return items


def submit_feedback(
    *,
    root_dir: str | Path = ".",
    user_id: str,
    page_or_command: str,
    feedback_type: FeedbackType,
    text: str,
    project_id: str | None = None,
    severity: FeedbackSeverity = "medium",
    artifact_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PilotFeedback:
    feedback = PilotFeedback(
        user_id=user_id,
        project_id=project_id,
        page_or_command=page_or_command,
        feedback_type=feedback_type,
        severity=severity,
        text=text,
        artifact_refs=artifact_refs or [],
        metadata=metadata or {},
    )
    return PilotFeedbackStore(root_dir).submit(feedback)


def export_feedback(
    root_dir: str | Path,
    output_path: str | Path,
    *,
    include_artifact_payloads: bool = False,
) -> Path:
    return PilotFeedbackStore(root_dir).export(
        output_path,
        include_artifact_payloads=include_artifact_payloads,
    )


def pilot_feedback_record(
    *,
    workflow: str,
    summary: str,
    severity: str = "medium",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_severity: FeedbackSeverity = "medium"
    if severity in {"low", "medium", "high", "critical"}:
        clean_severity = cast(FeedbackSeverity, severity)
    feedback = PilotFeedback(
        user_id="system",
        page_or_command=workflow,
        feedback_type="workflow_friction",
        severity=clean_severity,
        text=summary,
        metadata=metadata or {},
    )
    return feedback.model_dump(mode="json")


def redact_feedback_text(value: str) -> str:
    return SENSITIVE_ASSIGNMENT_RE.sub("[REDACTED]", redact_secrets(value))


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            sensitive_parts = ("api_key", "password", "secret", "token", "credential")
            if any(part in lowered for part in sensitive_parts):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_feedback_text(value)
    return value


__all__ = [
    "BugReport",
    "FeatureRequest",
    "PilotFeedback",
    "PilotFeedbackStore",
    "UsabilityIssue",
    "WorkflowFrictionReport",
    "export_feedback",
    "pilot_feedback_record",
    "redact_feedback_text",
    "submit_feedback",
]
