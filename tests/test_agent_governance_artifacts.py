from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from molecule_ranker.agent_governance.audits import (
    AgentGovernanceAuditAnalyticsBuilder,
    GovernanceAuditEvent,
)
from molecule_ranker.agent_governance.capability_grants import (
    CapabilityGrantAuthorization,
    CapabilityGrantManager,
)
from molecule_ranker.agent_governance.certification import (
    AgentCertificationAuthorization,
    AgentCertificationManager,
    CertificationEvaluationResult,
)
from molecule_ranker.agent_governance.incidents import AgentIncidentManager, IncidentTriggerEvent
from molecule_ranker.agent_governance.policies import default_platform_policy
from molecule_ranker.agent_governance.reports import (
    ARTIFACT_FILENAMES,
    write_governance_artifacts,
)
from molecule_ranker.agent_governance.risk import AgentRiskInputs, AgentRiskScorer
from molecule_ranker.agent_governance.schemas import AgentAutonomyBudget, AgentGovernancePolicy

NOW = datetime(2026, 6, 5, 12, tzinfo=UTC)
REPORT_SECTIONS = [
    "## Executive Summary",
    "## Active Agent Inventory",
    "## Autonomy Levels",
    "## Capability Grants",
    "## Budget Usage",
    "## Risk Profiles",
    "## Certifications",
    "## Policy Violations",
    "## Incidents",
    "## Human Approval Metrics",
    "## Tool Package Risk",
    "## Recommendations",
    "## Limitations",
]
UNSAFE_RECOMMENDATION_TERMS = {"clinical", "lab protocol", "synthesis", "dosing", "dose"}


def test_governance_artifacts_generated_with_required_sections(tmp_path: Path) -> None:
    analytics = AgentGovernanceAuditAnalyticsBuilder().build_report(
        events=_events(),
        org_id="org-1",
        project_id="project-1",
        period="weekly",
    )
    incident = _incident()

    paths = write_governance_artifacts(
        output_dir=tmp_path,
        analytics=analytics,
        policies=[_policy()],
        capability_grants=[_grant()],
        autonomy_budgets=[_budget()],
        risk_profiles=[_risk_profile()],
        certifications=[_certification()],
        incidents=[incident],
    )

    for filename in ARTIFACT_FILENAMES.values():
        assert (tmp_path / filename).exists(), filename
    report = paths.governance_report_markdown_path.read_text(encoding="utf-8")
    for section in REPORT_SECTIONS:
        assert section in report
    assert "operational oversight, not scientific evidence" in report
    assert "No clinical claims" in report
    assert "No lab protocols" in report
    assert "No synthesis instructions" in report
    assert "No dosing guidance" in report


def test_governance_artifacts_redact_secrets_and_include_incidents(tmp_path: Path) -> None:
    analytics = AgentGovernanceAuditAnalyticsBuilder().build_report(
        events=_events(secret="api_key=supersecretvalue"),
        org_id="org-1",
        project_id="project-1",
        period="weekly",
    )
    incident = _incident(summary="Unauthorized tool attempt with token=secretsecret.")

    paths = write_governance_artifacts(
        output_dir=tmp_path,
        analytics=analytics,
        incidents=[incident],
    )

    report_json = paths.governance_report_json_path.read_text(encoding="utf-8")
    report_md = paths.governance_report_markdown_path.read_text(encoding="utf-8")
    incidents_json = paths.agent_incidents_path.read_text(encoding="utf-8")
    combined = report_json + report_md + incidents_json
    assert incident.incident_id in combined
    assert "secretsecret" not in combined
    assert "supersecretvalue" not in combined
    assert "[REDACTED" in combined


def test_governance_report_recommendations_are_safe(tmp_path: Path) -> None:
    analytics = AgentGovernanceAuditAnalyticsBuilder().build_report(
        events=_events(),
        org_id="org-1",
        project_id="project-1",
        period="weekly",
    )

    paths = write_governance_artifacts(output_dir=tmp_path, analytics=analytics)

    payload = json.loads(paths.governance_report_json_path.read_text(encoding="utf-8"))
    recommendations = payload["report"]["recommendations"]
    markdown = paths.governance_report_markdown_path.read_text(encoding="utf-8")
    assert recommendations
    for recommendation in recommendations:
        lowered = recommendation.lower()
        assert not any(term in lowered for term in UNSAFE_RECOMMENDATION_TERMS)
    recommendations_section = markdown.split("## Recommendations", maxsplit=1)[1]
    recommendations_section = recommendations_section.split("## Limitations", maxsplit=1)[0]
    lowered_section = recommendations_section.lower()
    assert not any(term in lowered_section for term in UNSAFE_RECOMMENDATION_TERMS)


def _events(secret: str | None = None) -> list[GovernanceAuditEvent]:
    return [
        _event("tool-1", "tool_call", agent_id="agent-1", count=3, metadata={"raw": secret}),
        _event("approval-1", "approval_request", agent_id="agent-1", count=4),
        _event("approval-reject-1", "approval_rejection", agent_id="agent-1", count=1),
        _event("policy-1", "policy_violation", agent_id="agent-1", count=2),
        _event("budget-violation-1", "budget_violation", agent_id="agent-1"),
        _event("incident-1", "incident_opened", agent_id="agent-1"),
        _event("incident-2", "incident_resolved", agent_id="agent-1"),
        _event(
            "autonomy-1",
            "autonomy_level",
            agent_id="agent-1",
            autonomy_level="execute_safe_tools",
        ),
        _event("tool-risk-1", "tool_package_risk", risk_level="high"),
    ]


def _event(
    event_id: str,
    event_type: str,
    *,
    agent_id: str | None = None,
    count: int = 1,
    autonomy_level: str | None = None,
    risk_level: str | None = None,
    metadata: dict[str, str | None] | None = None,
) -> GovernanceAuditEvent:
    return GovernanceAuditEvent.model_validate(
        {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": NOW + timedelta(hours=1),
            "agent_id": agent_id,
            "org_id": "org-1",
            "project_id": "project-1",
            "count": count,
            "autonomy_level": autonomy_level,
            "risk_level": risk_level,
            "metadata": {key: value for key, value in (metadata or {}).items() if value},
        }
    )


def _policy() -> AgentGovernancePolicy:
    base = default_platform_policy().model_dump()
    base.update(
        {
            "policy_id": "policy-1",
            "org_id": "org-1",
            "project_id": "project-1",
            "policy_name": "Project governance policy",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    return AgentGovernancePolicy.model_validate(base)


def _grant():
    decision = CapabilityGrantManager().create_grant(
        agent_id="agent-1",
        agent_type="runtime_agent",
        granted_capability="run_ranking",
        scope_type="project",
        scope_id="project-1",
        authorization=CapabilityGrantAuthorization(
            actor_id="admin-1",
            actor_type="admin",
            permission_scope={"*"},
        ),
        granted_at=NOW,
    )
    assert decision.grant is not None
    return decision.grant


def _budget() -> AgentAutonomyBudget:
    return AgentAutonomyBudget(
        budget_id="budget-1",
        org_id="org-1",
        project_id="project-1",
        campaign_id=None,
        agent_id="agent-1",
        period="daily",
        max_tool_calls=10,
        max_codex_tasks=None,
        max_runtime_minutes=None,
        max_artifact_writes=None,
        max_db_writes=None,
        max_external_reads=None,
        max_external_writes=0,
        max_generation_jobs=None,
        max_docking_jobs=None,
        max_model_training_jobs=None,
        max_campaign_replans=None,
        max_cost_units=None,
        current_usage={"tool_calls": 3},
        reset_at=None,
        enabled=True,
        metadata={},
    )


def _risk_profile():
    return AgentRiskScorer().score_agent(
        AgentRiskInputs(
            agent_id="agent-1",
            guardrail_failures=1,
            policy_violations=1,
            autonomy_level="execute_safe_tools",
        ),
        computed_at=NOW,
    ).profile


def _certification():
    decision = AgentCertificationManager().certify_agent(
        agent_id="agent-1",
        certification_type="tool_use",
        certified_autonomy_level="execute_safe_tools",
        authorization=AgentCertificationAuthorization(
            actor_id="admin-1",
            actor_type="admin",
            permission_scope={"*"},
        ),
        evaluation_results=[
            CertificationEvaluationResult(
                suite_name="governance-artifact-test",
                passed=True,
                score=1.0,
                artifact_id="eval-artifact-1",
            )
        ],
        certified_at=NOW,
    )
    assert decision.certification is not None
    return decision.certification


def _incident(summary: str = "Unauthorized tool attempt.") :
    return AgentIncidentManager().create_incident_from_trigger(
        IncidentTriggerEvent(
            trigger_type="unauthorized_tool_attempt",
            agent_id="agent-1",
            org_id="org-1",
            project_id="project-1",
            summary=summary,
        ),
        opened_at=NOW,
        incident_id="incident-1",
    )
