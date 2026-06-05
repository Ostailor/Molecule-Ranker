from __future__ import annotations

from datetime import UTC, datetime, timedelta

from molecule_ranker.agent_governance import (
    AgentRiskInputs,
    AgentRiskScorer,
    AgentRunControlManager,
    RiskScoreAuthorization,
)
from molecule_ranker.agent_governance.schemas import (
    AgentIncident,
    AgentIncidentSeverity,
    AgentIncidentStatus,
    AgentRiskLevel,
    AgentRiskProfile,
)

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)


def test_guardrail_failures_raise_risk_and_cap_autonomy() -> None:
    scorer = AgentRiskScorer()

    baseline = scorer.score_agent(AgentRiskInputs(agent_id="agent-1"), computed_at=NOW)
    risky = scorer.score_agent(
        AgentRiskInputs(
            agent_id="agent-1",
            guardrail_failures=2,
            autonomy_level="execute_safe_tools",
        ),
        computed_at=NOW,
    )

    assert baseline.profile.risk_level == "low"
    assert risky.profile.risk_level == "medium"
    assert risky.risk_score > baseline.risk_score
    assert risky.profile.metadata["risk_score_visible"] is True
    assert risky.allowed_autonomy_cap == "execute_with_approval"


def test_unresolved_incident_raises_risk() -> None:
    scorer = AgentRiskScorer()

    decision = scorer.score_agent(
        AgentRiskInputs(
            agent_id="agent-1",
            incidents=[_incident(severity="critical", status="open")],
        ),
        computed_at=NOW,
    )

    assert decision.profile.risk_level == "high"
    assert "incident_severity" in decision.profile.risk_factors
    assert decision.allowed_autonomy_cap == "suggest_only"


def test_clean_eval_lowers_risk_slowly_after_human_review() -> None:
    scorer = AgentRiskScorer()
    previous = _risk_profile(risk_level="critical", risk_score=0.90, computed_at=NOW)

    lowered = scorer.score_agent(
        AgentRiskInputs(
            agent_id="agent-1",
            clean_eval_passes=4,
            human_review_completed=True,
            resolved_incidents=2,
        ),
        previous_profile=previous,
        authorization=RiskScoreAuthorization(
            actor_id="admin-1",
            actor_type="admin",
        ),
        computed_at=NOW + timedelta(days=3),
    )

    assert lowered.risk_score < 0.90
    assert lowered.risk_score > lowered.raw_risk_score
    assert lowered.risk_score == 0.71
    assert lowered.profile.risk_level == "high"
    assert "Risk lowered gradually" in lowered.profile.metadata["lowering_reasons"][0]


def test_codex_cannot_lower_risk_score() -> None:
    scorer = AgentRiskScorer()
    previous = _risk_profile(risk_level="high", risk_score=0.62, computed_at=NOW)

    decision = scorer.score_agent(
        AgentRiskInputs(
            agent_id="agent-1",
            clean_eval_passes=10,
            human_review_completed=True,
            resolved_incidents=5,
        ),
        previous_profile=previous,
        authorization=RiskScoreAuthorization(
            actor_id="codex",
            actor_type="codex",
        ),
        computed_at=NOW + timedelta(days=10),
    )

    assert decision.risk_score == 0.62
    assert decision.profile.risk_level == "high"
    assert "Codex cannot lower" in decision.profile.metadata["lowering_reasons"][0]


def test_critical_risk_triggers_run_control() -> None:
    scorer = AgentRiskScorer()
    manager = AgentRunControlManager()
    decision = scorer.score_agent(
        AgentRiskInputs(
            agent_id="agent-1",
            secret_exposure_attempts=3,
            external_write_attempts=2,
            autonomy_level="supervised_auto",
        ),
        computed_at=NOW,
    )

    control = scorer.apply_recommended_run_control(
        decision,
        manager,
        applied_by="risk-engine",
        org_id="org-1",
        project_id="project-1",
        now=NOW,
    )

    assert decision.profile.risk_level == "critical"
    assert decision.requires_run_control is True
    assert control is not None
    assert control.control_type == "pause"
    assert control.metadata["requires_admin_review"] is True


def _incident(
    *,
    severity: AgentIncidentSeverity,
    status: AgentIncidentStatus,
) -> AgentIncident:
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
        status=status,
        opened_at=NOW,
        resolved_at=None,
        assigned_to=None,
        metadata={},
    )


def _risk_profile(
    *,
    risk_level: AgentRiskLevel,
    risk_score: float,
    computed_at: datetime,
) -> AgentRiskProfile:
    return AgentRiskProfile(
        risk_profile_id="risk-previous",
        agent_id="agent-1",
        risk_level=risk_level,
        risk_factors=["previous incident"],
        recent_guardrail_failures=2,
        recent_policy_violations=1,
        recent_failed_repairs=1,
        recent_human_overrides=1,
        unsafe_action_attempts=1,
        external_write_attempts=1,
        approval_rejection_rate=0.5,
        confidence=0.9,
        computed_at=computed_at,
        metadata={"risk_score": risk_score},
    )
