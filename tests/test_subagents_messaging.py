from __future__ import annotations

import pytest

from molecule_ranker.subagents.messaging import (
    EVIDENCE_CREATION_CODE,
    MISSING_ARTIFACT_REFERENCE_CODE,
    SCORE_MUTATION_CODE,
    SECRET_REDACTION_CODE,
    InterAgentMessageBus,
    MessagePolicyError,
    check_message_safety,
)


def test_message_creation_and_receive_preserves_audit() -> None:
    bus = InterAgentMessageBus()

    message = bus.send_message(
        parent_session_id="session-1",
        from_subagent_id="program-manager",
        to_subagent_id="evidence-reviewer",
        message_type="task_request",
        content="Please review the evidence gap summary.",
        referenced_artifact_ids=["artifact-evidence-1"],
        referenced_entity_ids=["target-1"],
        referenced_tool_names=["summarize_literature"],
    )
    received = bus.receive_messages(
        parent_session_id="session-1",
        subagent_id="evidence-reviewer",
    )

    assert message.message_type == "task_request"
    assert received == [message]
    assert [event.event_type for event in bus.audit_events] == [
        "message_sent",
        "messages_received",
    ]


def test_message_preserves_artifact_entity_and_tool_references() -> None:
    bus = InterAgentMessageBus()

    message = bus.send_critique(
        parent_session_id="session-1",
        from_subagent_id="guardrail-sentinel",
        to_subagent_id="molecule-designer",
        content="The binding claim needs provenance from artifact-evidence-1.",
        referenced_artifact_ids=["artifact-evidence-1", "artifact-evidence-1"],
        referenced_entity_ids=["molecule-1"],
        referenced_tool_names=["run_guardrail_benchmark"],
        metadata={"target_result_id": "result-1"},
    )

    assert message.message_type == "critique"
    assert message.referenced_artifact_ids == ["artifact-evidence-1"]
    assert message.referenced_entity_ids == ["molecule-1"]
    assert message.referenced_tool_names == ["run_guardrail_benchmark"]
    assert message.metadata["target_result_id"] == "result-1"


def test_message_redacts_secret_content_before_persistence() -> None:
    bus = InterAgentMessageBus()

    message = bus.status_update(
        parent_session_id="session-1",
        from_subagent_id="integration-operator",
        content="Connector check used API_KEY=super-secret-token.",
        referenced_artifact_ids=["connector-summary-1"],
    )
    dumped = str(message.model_dump())

    assert "super-secret-token" not in dumped
    assert "[REDACTED]" in message.content
    assert message.metadata["safety_findings"][0]["code"] == SECRET_REDACTION_CODE
    assert bus.audit_events[-1].metadata["redacted"] is True


def test_unsafe_content_detection_and_send_blocking() -> None:
    report = check_message_safety(
        "Create evidence and set score = 0.99 because the molecule is active."
    )

    assert report.allowed is False
    assert {finding.code for finding in report.findings} == {
        EVIDENCE_CREATION_CODE,
        SCORE_MUTATION_CODE,
        MISSING_ARTIFACT_REFERENCE_CODE,
    }

    bus = InterAgentMessageBus()
    with pytest.raises(MessagePolicyError, match=EVIDENCE_CREATION_CODE):
        bus.send_message(
            parent_session_id="session-1",
            from_subagent_id="evidence-reviewer",
            to_subagent_id="program-manager",
            content="Create evidence and set score = 0.99.",
        )

    assert bus.audit_events[-1].event_type == "message_blocked"


def test_guardrail_sentinel_can_flag_unsafe_received_message() -> None:
    bus = InterAgentMessageBus()
    message = bus.status_update(
        parent_session_id="session-1",
        from_subagent_id="structure-reviewer",
        content="Docking concern noted for artifact-structure-1.",
        referenced_artifact_ids=["artifact-structure-1"],
    )

    findings = bus.flag_unsafe_message(message, sentinel_subagent_id="guardrail-sentinel")

    assert findings == []
    assert bus.audit_events[-1].event_type == "message_guardrail_reviewed"
    assert bus.audit_events[-1].actor_subagent_id == "guardrail-sentinel"


def test_claim_without_artifact_reference_is_blocked() -> None:
    bus = InterAgentMessageBus()

    with pytest.raises(MessagePolicyError, match=MISSING_ARTIFACT_REFERENCE_CODE):
        bus.escalate(
            parent_session_id="session-1",
            from_subagent_id="experiment-analyst",
            content="IC50 = 10 nM was confirmed.",
        )
