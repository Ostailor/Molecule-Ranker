from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.autonomy_validation.reliability import (
    RELIABILITY_AGENT_TYPES,
    AgentReliabilityObservation,
    build_clean_reliability_observations,
    compute_agent_reliability_scorecard,
    compute_agent_reliability_scorecards,
)

NOW = datetime(2026, 6, 6, tzinfo=UTC)


def _observation(**updates: object) -> AgentReliabilityObservation:
    payload = {
        "session_id": "session-runtime-1",
        "agent_type": "RuntimeAgent",
        "started_at": NOW,
        "completed_at": NOW,
        "successful": True,
        "tool_calls": 10,
        "tool_failures": 1,
        "grounded_artifacts": 4,
        "total_artifacts": 5,
        "unsupported_claims": 1,
        "total_claims": 10,
        "repair_attempts": 2,
        "repair_successes": 1,
        "human_escalations_required": 1,
        "human_escalations_performed": 1,
        "safe_outcome_seconds": 8.0,
    }
    payload.update(updates)
    return AgentReliabilityObservation.model_validate(payload)


def test_scorecard_computes() -> None:
    scorecard = compute_agent_reliability_scorecard(
        agent_type="RuntimeAgent",
        observations=[_observation()],
    )

    assert scorecard.agent_type == "RuntimeAgent"
    assert scorecard.total_sessions == 1
    assert scorecard.tool_success_rate == 0.9
    assert scorecard.artifact_grounding_rate == 0.8
    assert scorecard.unsupported_claim_rate == 0.1
    assert scorecard.metadata["metrics"]["session_success_rate"] == 1


def test_unsafe_escape_is_critical_risk() -> None:
    scorecard = compute_agent_reliability_scorecard(
        agent_type="RuntimeAgent",
        observations=[_observation(unsafe_action_escapes=1)],
    )

    assert scorecard.risk_level == "critical"
    assert scorecard.metadata["v3_readiness"]["ready"] is False
    assert any("Block V3 readiness" in item for item in scorecard.recommendations)


def test_clean_agent_is_low_risk() -> None:
    scorecard = compute_agent_reliability_scorecard(
        agent_type="GuardrailSentinel",
        observations=[
            observation
            for observation in build_clean_reliability_observations()
            if observation.agent_type == "GuardrailSentinel"
        ],
    )

    assert scorecard.risk_level == "low"
    assert scorecard.reliability_score >= 0.95
    assert scorecard.metadata["metrics"]["human_escalation_recall"] == 1


def test_missing_data_is_unknown_risk() -> None:
    scorecard = compute_agent_reliability_scorecard(
        agent_type="RepairExecutor",
        observations=[],
        period_start=NOW,
        period_end=NOW,
    )

    assert scorecard.risk_level == "unknown"
    assert scorecard.total_sessions == 0
    assert scorecard.metadata["v3_readiness"]["ready"] is False


def test_all_required_agent_scorecards_compute() -> None:
    scorecards = compute_agent_reliability_scorecards(
        build_clean_reliability_observations(period_start=NOW, period_end=NOW)
    )

    assert [scorecard.agent_type for scorecard in scorecards] == list(
        RELIABILITY_AGENT_TYPES
    )
    assert all(scorecard.risk_level == "low" for scorecard in scorecards)
