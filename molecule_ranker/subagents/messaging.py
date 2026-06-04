from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.context import redact_sensitive_context
from molecule_ranker.subagents.schemas import SubagentMessage, SubagentMessageType

MessageFindingSeverity = Literal["warning", "block"]

SECRET_REDACTION_CODE = "secret_redacted"
EVIDENCE_CREATION_CODE = "evidence_creation_attempt"
SCORE_MUTATION_CODE = "score_mutation_attempt"
MISSING_ARTIFACT_REFERENCE_CODE = "missing_artifact_reference"

EVIDENCE_CREATION_RE = re.compile(
    r"\b(?:invent|fabricate|create|add|write|generate)\b.{0,40}"
    r"\b(?:evidence|citation|citations|assay result|assay results|graph fact|graph facts)\b",
    re.I,
)
SCIENCE_CLAIM_RE = re.compile(
    r"\b(?:PMID:?\s*\d{4,9}|10\.\d{4,9}/[-._;()/:A-Z0-9]+|"
    r"IC50|EC50|Ki|Kd|active|binds|binding|safe|effective|validated|confirmed)\b",
    re.I,
)
SCORE_MUTATION_RE = re.compile(
    r"\b(?:score|scores|new_score|updated_score|override_score|score_updates)\b"
    r"\s*(?:=|:|to|->)",
    re.I,
)


class MessagePolicyError(ValueError):
    """Raised when an inter-agent message violates deterministic messaging policy."""


class MessageSafetyFinding(BaseModel):
    code: str
    message: str
    severity: MessageFindingSeverity = "block"
    sentinel_subagent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageSafetyReport(BaseModel):
    allowed: bool
    sanitized_content: str
    findings: list[MessageSafetyFinding] = Field(default_factory=list)


class MessageAuditEvent(BaseModel):
    event_id: str
    event_type: str
    message_id: str | None = None
    actor_subagent_id: str
    created_at: datetime
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class InterAgentMessageBus:
    def __init__(self, *, initial_messages: list[SubagentMessage] | None = None) -> None:
        self._messages = list(initial_messages or [])
        self._audit_events: list[MessageAuditEvent] = []
        self._flagged_messages: dict[str, list[MessageSafetyFinding]] = {}

    @property
    def audit_events(self) -> list[MessageAuditEvent]:
        return list(self._audit_events)

    @property
    def messages(self) -> list[SubagentMessage]:
        return list(self._messages)

    def send_message(
        self,
        *,
        parent_session_id: str,
        from_subagent_id: str,
        to_subagent_id: str | None = None,
        message_type: SubagentMessageType = "status_update",
        content: str,
        referenced_artifact_ids: list[str] | None = None,
        referenced_entity_ids: list[str] | None = None,
        referenced_tool_names: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentMessage:
        artifact_refs = _unique_strings(referenced_artifact_ids)
        entity_refs = _unique_strings(referenced_entity_ids)
        tool_refs = _unique_strings(referenced_tool_names)
        report = check_message_safety(
            content,
            referenced_artifact_ids=artifact_refs,
            sentinel_subagent_id=(
                "guardrail-sentinel" if from_subagent_id == "guardrail-sentinel" else None
            ),
        )
        blocking_findings = [
            finding for finding in report.findings if finding.severity == "block"
        ]
        if blocking_findings:
            self._audit(
                event_type="message_blocked",
                actor_subagent_id=from_subagent_id,
                summary="Blocked unsafe inter-agent message.",
                metadata={
                    "finding_codes": [finding.code for finding in blocking_findings],
                    "message_type": message_type,
                },
            )
            raise MessagePolicyError(
                "message violates policy: "
                + ", ".join(finding.code for finding in blocking_findings)
            )

        message = SubagentMessage(
            message_id=f"subagent-message-{uuid4().hex[:12]}",
            parent_session_id=parent_session_id,
            from_subagent_id=from_subagent_id,
            to_subagent_id=to_subagent_id,
            message_type=message_type,
            content=report.sanitized_content,
            referenced_artifact_ids=artifact_refs,
            referenced_entity_ids=entity_refs,
            referenced_tool_names=tool_refs,
            created_at=_now(),
            metadata={
                **(metadata or {}),
                "safety_findings": [
                    finding.model_dump(mode="json") for finding in report.findings
                ],
            },
        )
        self._messages.append(message)
        self._audit(
            event_type="message_sent",
            message_id=message.message_id,
            actor_subagent_id=from_subagent_id,
            summary=f"Sent {message_type} message.",
            metadata={
                "to_subagent_id": to_subagent_id,
                "referenced_artifact_ids": artifact_refs,
                "referenced_entity_ids": entity_refs,
                "referenced_tool_names": tool_refs,
                "redacted": any(
                    finding.code == SECRET_REDACTION_CODE for finding in report.findings
                ),
            },
        )
        return message

    def receive_messages(
        self,
        *,
        parent_session_id: str,
        subagent_id: str,
        message_type: SubagentMessageType | None = None,
    ) -> list[SubagentMessage]:
        messages = [
            message
            for message in self._messages
            if message.parent_session_id == parent_session_id
            and (message.to_subagent_id in {None, subagent_id})
            and (message_type is None or message.message_type == message_type)
        ]
        self._audit(
            event_type="messages_received",
            actor_subagent_id=subagent_id,
            summary=f"Received {len(messages)} inter-agent messages.",
            metadata={
                "parent_session_id": parent_session_id,
                "message_type": message_type,
                "message_ids": [message.message_id for message in messages],
            },
        )
        return messages

    def receive_message(self, message_id: str) -> SubagentMessage | None:
        for message in self._messages:
            if message.message_id == message_id:
                self._audit(
                    event_type="message_received",
                    message_id=message_id,
                    actor_subagent_id=message.to_subagent_id or "broadcast",
                    summary="Received inter-agent message.",
                )
                return message
        return None

    def request_clarification(
        self,
        *,
        parent_session_id: str,
        from_subagent_id: str,
        to_subagent_id: str,
        content: str,
        referenced_artifact_ids: list[str] | None = None,
        referenced_entity_ids: list[str] | None = None,
        referenced_tool_names: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentMessage:
        return self.send_message(
            parent_session_id=parent_session_id,
            from_subagent_id=from_subagent_id,
            to_subagent_id=to_subagent_id,
            message_type="clarification",
            content=content,
            referenced_artifact_ids=referenced_artifact_ids,
            referenced_entity_ids=referenced_entity_ids,
            referenced_tool_names=referenced_tool_names,
            metadata=metadata,
        )

    def send_critique(
        self,
        *,
        parent_session_id: str,
        from_subagent_id: str,
        to_subagent_id: str,
        content: str,
        referenced_artifact_ids: list[str] | None = None,
        referenced_entity_ids: list[str] | None = None,
        referenced_tool_names: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentMessage:
        return self.send_message(
            parent_session_id=parent_session_id,
            from_subagent_id=from_subagent_id,
            to_subagent_id=to_subagent_id,
            message_type="critique",
            content=content,
            referenced_artifact_ids=referenced_artifact_ids,
            referenced_entity_ids=referenced_entity_ids,
            referenced_tool_names=referenced_tool_names,
            metadata=metadata,
        )

    def escalate(
        self,
        *,
        parent_session_id: str,
        from_subagent_id: str,
        content: str,
        to_subagent_id: str | None = None,
        referenced_artifact_ids: list[str] | None = None,
        referenced_entity_ids: list[str] | None = None,
        referenced_tool_names: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentMessage:
        return self.send_message(
            parent_session_id=parent_session_id,
            from_subagent_id=from_subagent_id,
            to_subagent_id=to_subagent_id,
            message_type="escalation",
            content=content,
            referenced_artifact_ids=referenced_artifact_ids,
            referenced_entity_ids=referenced_entity_ids,
            referenced_tool_names=referenced_tool_names,
            metadata=metadata,
        )

    def status_update(
        self,
        *,
        parent_session_id: str,
        from_subagent_id: str,
        content: str,
        to_subagent_id: str | None = None,
        referenced_artifact_ids: list[str] | None = None,
        referenced_entity_ids: list[str] | None = None,
        referenced_tool_names: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentMessage:
        return self.send_message(
            parent_session_id=parent_session_id,
            from_subagent_id=from_subagent_id,
            to_subagent_id=to_subagent_id,
            message_type="status_update",
            content=content,
            referenced_artifact_ids=referenced_artifact_ids,
            referenced_entity_ids=referenced_entity_ids,
            referenced_tool_names=referenced_tool_names,
            metadata=metadata,
        )

    def flag_unsafe_message(
        self,
        message: SubagentMessage,
        *,
        sentinel_subagent_id: str = "guardrail-sentinel",
    ) -> list[MessageSafetyFinding]:
        report = check_message_safety(
            message.content,
            referenced_artifact_ids=message.referenced_artifact_ids,
            sentinel_subagent_id=sentinel_subagent_id,
        )
        self._flagged_messages[message.message_id] = report.findings
        self._audit(
            event_type="message_guardrail_reviewed",
            message_id=message.message_id,
            actor_subagent_id=sentinel_subagent_id,
            summary="GuardrailSentinel reviewed inter-agent message.",
            metadata={"finding_codes": [finding.code for finding in report.findings]},
        )
        return report.findings

    def _audit(
        self,
        *,
        event_type: str,
        actor_subagent_id: str,
        summary: str,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._audit_events.append(
            MessageAuditEvent(
                event_id=f"message-audit-{uuid4().hex[:12]}",
                event_type=event_type,
                message_id=message_id,
                actor_subagent_id=actor_subagent_id,
                created_at=_now(),
                summary=summary,
                metadata=metadata or {},
            )
        )


def check_message_safety(
    content: str,
    *,
    referenced_artifact_ids: list[str] | None = None,
    sentinel_subagent_id: str | None = None,
) -> MessageSafetyReport:
    sanitized = redact_sensitive_context(content)
    artifact_refs = _unique_strings(referenced_artifact_ids)
    findings: list[MessageSafetyFinding] = []
    if sanitized != content:
        findings.append(
            MessageSafetyFinding(
                code=SECRET_REDACTION_CODE,
                message="Secret-like content was redacted before message persistence.",
                severity="warning",
                sentinel_subagent_id=sentinel_subagent_id,
            )
        )
    if EVIDENCE_CREATION_RE.search(sanitized):
        findings.append(
            MessageSafetyFinding(
                code=EVIDENCE_CREATION_CODE,
                message=(
                    "Messages cannot create evidence, citations, assay results, or "
                    "graph facts."
                ),
                sentinel_subagent_id=sentinel_subagent_id,
            )
        )
    if SCORE_MUTATION_RE.search(sanitized):
        findings.append(
            MessageSafetyFinding(
                code=SCORE_MUTATION_CODE,
                message="Messages cannot directly mutate scores.",
                sentinel_subagent_id=sentinel_subagent_id,
            )
        )
    if SCIENCE_CLAIM_RE.search(sanitized) and not artifact_refs:
        findings.append(
            MessageSafetyFinding(
                code=MISSING_ARTIFACT_REFERENCE_CODE,
                message="Scientific claims in messages must preserve artifact references.",
                sentinel_subagent_id=sentinel_subagent_id,
            )
        )
    return MessageSafetyReport(
        allowed=not any(finding.severity == "block" for finding in findings),
        sanitized_content=sanitized,
        findings=findings,
    )


def _unique_strings(values: list[str] | None) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values or [] if str(value).strip()))


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "EVIDENCE_CREATION_CODE",
    "InterAgentMessageBus",
    "MISSING_ARTIFACT_REFERENCE_CODE",
    "MessageAuditEvent",
    "MessagePolicyError",
    "MessageSafetyFinding",
    "MessageSafetyReport",
    "SCORE_MUTATION_CODE",
    "SECRET_REDACTION_CODE",
    "check_message_safety",
]
