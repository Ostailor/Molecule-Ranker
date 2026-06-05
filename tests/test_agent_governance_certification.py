from __future__ import annotations

from datetime import UTC, datetime, timedelta

from molecule_ranker.agent_governance import (
    AgentCertificationAuthorization,
    AgentCertificationManager,
    CertificationEvaluationResult,
)
from molecule_ranker.agent_governance.schemas import AgentIncident, AgentIncidentSeverity

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_certification_created_after_mock_eval_pass() -> None:
    manager = AgentCertificationManager()

    decision = manager.certify_agent(
        agent_id="agent-1",
        certification_type="autonomy_level",
        certified_autonomy_level="execute_with_approval",
        authorization=_auth("admin-1", {"autonomy_level"}),
        evaluation_results=[
            CertificationEvaluationResult(
                suite_name="autonomy-eval",
                passed=True,
                score=0.91,
                artifact_id="eval-artifact-1",
            )
        ],
        certified_at=NOW,
    )

    assert decision.allowed is True
    assert decision.certification is not None
    assert decision.certification.agent_id == "agent-1"
    assert decision.certification.evaluation_artifact_ids == ["eval-artifact-1"]
    assert decision.certification.score == 0.91
    assert decision.certification.expires_at is not None
    assert decision.certification.expires_at > NOW


def test_failed_eval_blocks_certification() -> None:
    manager = AgentCertificationManager()

    decision = manager.certify_agent(
        agent_id="agent-1",
        certification_type="guardrail",
        certified_autonomy_level="execute_safe_tools",
        authorization=_auth("admin-1", {"guardrail"}),
        evaluation_results=[
            CertificationEvaluationResult(
                suite_name="guardrail-eval",
                passed=False,
                score=0.42,
            )
        ],
        certified_at=NOW,
    )

    assert decision.allowed is False
    assert decision.certification is None
    assert "failed eval suite" in decision.reason
    assert manager.certifications == []


def test_expired_certification_caps_autonomy() -> None:
    manager = AgentCertificationManager()
    created = manager.certify_agent(
        agent_id="agent-1",
        certification_type="autonomy_level",
        certified_autonomy_level="supervised_auto",
        authorization=_auth("admin-1", {"autonomy_level"}),
        evaluation_results=[
            CertificationEvaluationResult(
                suite_name="autonomy-eval",
                passed=True,
                score=0.95,
            )
        ],
        certified_at=NOW - timedelta(days=30),
        expires_at=NOW - timedelta(days=1),
    )
    assert created.allowed is True

    decision = manager.check_autonomy_certification(
        agent_id="agent-1",
        requested_autonomy_level="execute_with_approval",
        now=NOW,
    )

    assert decision.allowed is False
    assert decision.effective_autonomy_cap == "suggest_only"
    assert "active certification" in decision.reason


def test_high_incident_requires_recertification() -> None:
    manager = AgentCertificationManager()
    created = manager.certify_agent(
        agent_id="agent-1",
        certification_type="autonomy_level",
        certified_autonomy_level="execute_with_approval",
        authorization=_auth("admin-1", {"autonomy_level"}),
        evaluation_results=[
            CertificationEvaluationResult(
                suite_name="autonomy-eval",
                passed=True,
                score=0.94,
            )
        ],
        certified_at=NOW,
    )
    assert created.certification is not None

    incident = _incident(severity="high", opened_at=NOW + timedelta(hours=1))
    recertification = manager.check_recertification_required(
        agent_id="agent-1",
        certification=created.certification,
        incidents=[incident],
        now=NOW + timedelta(hours=2),
    )
    autonomy = manager.check_autonomy_certification(
        agent_id="agent-1",
        requested_autonomy_level="execute_with_approval",
        incidents=[incident],
        now=NOW + timedelta(hours=2),
    )

    assert recertification.required is True
    assert "high incident" in recertification.reasons[0]
    assert autonomy.allowed is False
    assert autonomy.requires_recertification is True


def test_agents_and_codex_cannot_certify() -> None:
    manager = AgentCertificationManager()

    self_certification = manager.certify_agent(
        agent_id="agent-1",
        certification_type="tool_use",
        certified_autonomy_level="execute_safe_tools",
        authorization=_auth("agent-1", {"tool_use"}, actor_type="agent"),
        evaluation_results=[
            CertificationEvaluationResult(suite_name="tool-eval", passed=True, score=1.0)
        ],
        certified_at=NOW,
    )
    codex_certification = manager.certify_agent(
        agent_id="agent-1",
        certification_type="tool_use",
        certified_autonomy_level="execute_safe_tools",
        authorization=_auth("codex", {"tool_use"}, actor_type="codex"),
        evaluation_results=[
            CertificationEvaluationResult(suite_name="tool-eval", passed=True, score=1.0)
        ],
        certified_at=NOW,
    )

    assert self_certification.allowed is False
    assert "self-certify" in self_certification.reason
    assert codex_certification.allowed is False
    assert "Codex cannot certify" in codex_certification.reason


def _auth(
    actor_id: str,
    permission_scope: set[str],
    *,
    actor_type: str = "admin",
) -> AgentCertificationAuthorization:
    return AgentCertificationAuthorization.model_validate(
        {
            "actor_id": actor_id,
            "actor_type": actor_type,
            "permission_scope": permission_scope,
        }
    )


def _incident(*, severity: AgentIncidentSeverity, opened_at: datetime) -> AgentIncident:
    return AgentIncident(
        incident_id="incident-1",
        org_id="org-1",
        project_id="project-1",
        agent_id="agent-1",
        session_id="session-1",
        severity=severity,
        incident_type="policy_violation",
        summary="Agent incident.",
        artifact_ids=[],
        tool_usage_ids=[],
        session_ids=["session-1"],
        status="open",
        opened_at=opened_at,
        resolved_at=None,
        assigned_to=None,
        metadata={},
    )
