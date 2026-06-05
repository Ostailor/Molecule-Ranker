from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from molecule_ranker.agent_governance.audits import GovernanceAuditAnalytics
from molecule_ranker.agent_governance.schemas import (
    AgentAutonomyBudget,
    AgentCapabilityGrant,
    AgentCertification,
    AgentGovernancePolicy,
    AgentGovernanceReport,
    AgentIncident,
    AgentPolicyViolation,
    AgentRiskProfile,
)
from molecule_ranker.codex_backbone.guardrails import redact_secrets

ARTIFACT_FILENAMES = {
    "governance_policy": "governance_policy.json",
    "capability_grants": "capability_grants.json",
    "autonomy_budgets": "autonomy_budgets.json",
    "agent_risk_profiles": "agent_risk_profiles.json",
    "agent_certifications": "agent_certifications.json",
    "agent_incidents": "agent_incidents.json",
    "governance_report_json": "governance_report.json",
    "governance_report_md": "governance_report.md",
}
REPORT_DISCLAIMERS = [
    "This governance report is operational oversight, not scientific evidence.",
    "No clinical claims are made or implied.",
    "No lab protocols are provided.",
    "No synthesis instructions are provided.",
    "No dosing guidance is provided.",
]
UNSAFE_RECOMMENDATION_TERMS = (
    "clinical claim",
    "lab protocol",
    "protocol step",
    "synthesis instruction",
    "synthesis route",
    "dosing",
    "dose",
)


class GovernanceArtifactBundle(BaseModel):
    governance_policy_path: Path
    capability_grants_path: Path
    autonomy_budgets_path: Path
    agent_risk_profiles_path: Path
    agent_certifications_path: Path
    agent_incidents_path: Path
    governance_report_json_path: Path
    governance_report_markdown_path: Path


def write_governance_artifacts(
    *,
    output_dir: Path | str,
    analytics: GovernanceAuditAnalytics,
    policies: list[AgentGovernancePolicy] | None = None,
    capability_grants: list[AgentCapabilityGrant] | None = None,
    autonomy_budgets: list[AgentAutonomyBudget] | None = None,
    risk_profiles: list[AgentRiskProfile] | None = None,
    certifications: list[AgentCertification] | None = None,
    incidents: list[AgentIncident] | None = None,
    policy_violations: list[AgentPolicyViolation] | None = None,
) -> GovernanceArtifactBundle:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    policies = list(policies or [])
    capability_grants = list(capability_grants or [])
    autonomy_budgets = list(autonomy_budgets or [])
    risk_profiles = list(risk_profiles or [])
    certifications = list(certifications or [])
    incidents = list(incidents or [])
    policy_violations = list(policy_violations or [])

    paths = GovernanceArtifactBundle(
        governance_policy_path=target_dir / ARTIFACT_FILENAMES["governance_policy"],
        capability_grants_path=target_dir / ARTIFACT_FILENAMES["capability_grants"],
        autonomy_budgets_path=target_dir / ARTIFACT_FILENAMES["autonomy_budgets"],
        agent_risk_profiles_path=target_dir / ARTIFACT_FILENAMES["agent_risk_profiles"],
        agent_certifications_path=target_dir / ARTIFACT_FILENAMES["agent_certifications"],
        agent_incidents_path=target_dir / ARTIFACT_FILENAMES["agent_incidents"],
        governance_report_json_path=target_dir / ARTIFACT_FILENAMES["governance_report_json"],
        governance_report_markdown_path=target_dir / ARTIFACT_FILENAMES["governance_report_md"],
    )
    _write_json(
        paths.governance_policy_path,
        {"policies": [policy.model_dump(mode="json") for policy in policies]},
    )
    _write_json(
        paths.capability_grants_path,
        {"grants": [grant.model_dump(mode="json") for grant in capability_grants]},
    )
    _write_json(
        paths.autonomy_budgets_path,
        {"budgets": [budget.model_dump(mode="json") for budget in autonomy_budgets]},
    )
    _write_json(
        paths.agent_risk_profiles_path,
        {"risk_profiles": [profile.model_dump(mode="json") for profile in risk_profiles]},
    )
    _write_json(
        paths.agent_certifications_path,
        {"certifications": [cert.model_dump(mode="json") for cert in certifications]},
    )
    _write_json(
        paths.agent_incidents_path,
        {"incidents": [incident.model_dump(mode="json") for incident in incidents]},
    )
    report_payload = {
        "report": analytics.report.model_dump(mode="json"),
        "analytics": analytics.model_dump(mode="json"),
        "artifacts": {
            "governance_policy": paths.governance_policy_path.name,
            "capability_grants": paths.capability_grants_path.name,
            "autonomy_budgets": paths.autonomy_budgets_path.name,
            "agent_risk_profiles": paths.agent_risk_profiles_path.name,
            "agent_certifications": paths.agent_certifications_path.name,
            "agent_incidents": paths.agent_incidents_path.name,
        },
        "policy_violations": [
            violation.model_dump(mode="json") for violation in policy_violations
        ],
        "disclaimers": REPORT_DISCLAIMERS,
        "redacted": True,
    }
    _write_json(paths.governance_report_json_path, report_payload)
    paths.governance_report_markdown_path.write_text(
        build_governance_artifact_markdown(
            analytics=analytics,
            policies=policies,
            capability_grants=capability_grants,
            autonomy_budgets=autonomy_budgets,
            risk_profiles=risk_profiles,
            certifications=certifications,
            incidents=incidents,
            policy_violations=policy_violations,
        ),
        encoding="utf-8",
    )
    return paths


def build_governance_artifact_markdown(
    *,
    analytics: GovernanceAuditAnalytics,
    policies: list[AgentGovernancePolicy] | None = None,
    capability_grants: list[AgentCapabilityGrant] | None = None,
    autonomy_budgets: list[AgentAutonomyBudget] | None = None,
    risk_profiles: list[AgentRiskProfile] | None = None,
    certifications: list[AgentCertification] | None = None,
    incidents: list[AgentIncident] | None = None,
    policy_violations: list[AgentPolicyViolation] | None = None,
) -> str:
    report = analytics.report
    policies = list(policies or [])
    capability_grants = list(capability_grants or [])
    autonomy_budgets = list(autonomy_budgets or [])
    risk_profiles = list(risk_profiles or [])
    certifications = list(certifications or [])
    incidents = list(incidents or [])
    policy_violations = list(policy_violations or [])
    lines = [
        "# Agent Governance Report",
        "",
        *[f"- {disclaimer}" for disclaimer in REPORT_DISCLAIMERS],
        "",
        "## Executive Summary",
        f"- Report ID: `{report.report_id}`",
        f"- Org: `{report.org_id or 'all'}`",
        f"- Project: `{report.project_id or 'all'}`",
        f"- Period: `{report.period_start.isoformat()}` to `{report.period_end.isoformat()}`",
        f"- Active agents: {report.active_agent_count}",
        f"- Disabled agents: {report.disabled_agent_count}",
        f"- Guardrail failures: {report.guardrail_failures}",
        f"- Policy violations: {report.policy_violations}",
        f"- Opened incidents: {report.incidents_opened}",
        "",
        "## Active Agent Inventory",
        _agent_inventory(report, risk_profiles, certifications, capability_grants),
        "",
        "## Autonomy Levels",
        _format_mapping("Autonomy level distribution", analytics.autonomy_level_distribution),
        "",
        "## Capability Grants",
        _grant_summary(capability_grants),
        "",
        "## Budget Usage",
        _budget_summary(autonomy_budgets, analytics),
        "",
        "## Risk Profiles",
        _risk_summary(risk_profiles, report),
        "",
        "## Certifications",
        _certification_summary(certifications),
        "",
        "## Policy Violations",
        _policy_violation_summary(policy_violations, analytics, report),
        "",
        "## Incidents",
        _incident_summary(incidents, report),
        "",
        "## Human Approval Metrics",
        _approval_summary(analytics, report),
        "",
        "## Tool Package Risk",
        _format_mapping(
            "Tool package risk distribution",
            analytics.tool_package_risk_distribution,
        ),
        "",
        "## Recommendations",
        *[f"- {item}" for item in _safe_recommendations(report.recommendations)],
        "",
        "## Limitations",
        "- This report summarizes governance telemetry and administrative controls only.",
        "- Counts depend on available audit events and configured governance state artifacts.",
        "- Absence of a finding is not evidence of scientific validity or clinical readiness.",
        (
            "- Scientific claims, assay results, citations, molecules, metrics, and "
            "approvals must be verified in their source systems."
        ),
    ]
    return redact_secrets("\n".join(lines) + "\n")


def _agent_inventory(
    report: AgentGovernanceReport,
    risk_profiles: list[AgentRiskProfile],
    certifications: list[AgentCertification],
    grants: list[AgentCapabilityGrant],
) -> str:
    agent_ids = {
        *[profile.agent_id for profile in risk_profiles],
        *[cert.agent_id for cert in certifications],
        *[grant.agent_id for grant in grants],
    }
    if not agent_ids:
        return (
            f"- Agent count: {report.agent_count}\n"
            f"- Active agents: {report.active_agent_count}\n"
            f"- Disabled agents: {report.disabled_agent_count}"
        )
    lines = []
    for agent_id in sorted(agent_ids):
        risk = next(
            (
                profile.risk_level
                for profile in risk_profiles
                if profile.agent_id == agent_id
            ),
            "unknown",
        )
        cert_count = sum(1 for cert in certifications if cert.agent_id == agent_id and cert.passed)
        grant_count = sum(
            1
            for grant in grants
            if grant.agent_id == agent_id and grant.status == "active"
        )
        lines.append(
            f"- `{agent_id}`: risk={risk}, active_grants={grant_count}, "
            f"passed_certifications={cert_count}"
        )
    return "\n".join(lines)


def _grant_summary(grants: list[AgentCapabilityGrant]) -> str:
    if not grants:
        return "- No capability grants supplied."
    return "\n".join(
        f"- `{grant.grant_id}`: agent={grant.agent_id}, "
        f"capability={grant.granted_capability}, status={grant.status}, "
        f"scope={grant.scope_type}:{grant.scope_id or 'all'}"
        for grant in grants
    )


def _budget_summary(
    budgets: list[AgentAutonomyBudget],
    analytics: GovernanceAuditAnalytics,
) -> str:
    lines = []
    if budgets:
        for budget in budgets:
            lines.append(
                f"- `{budget.budget_id}`: period={budget.period}, "
                f"enabled={budget.enabled}, usage={budget.current_usage}"
            )
    else:
        lines.append("- No autonomy budgets supplied.")
    lines.append(_format_mapping("Budget usage trends", analytics.budget_usage_trends))
    lines.append(f"- Budget violations: {analytics.report.budget_violations}")
    return "\n".join(lines)


def _risk_summary(
    risk_profiles: list[AgentRiskProfile],
    report: AgentGovernanceReport,
) -> str:
    if not risk_profiles:
        risks = ", ".join(report.top_risks) if report.top_risks else "none"
        return f"- Top risks: {risks}"
    return "\n".join(
        f"- `{profile.agent_id}`: risk={profile.risk_level}, "
        f"guardrail_failures={profile.recent_guardrail_failures}, "
        f"policy_violations={profile.recent_policy_violations}, "
        f"confidence={profile.confidence:.2f}"
        for profile in risk_profiles
    )


def _certification_summary(certifications: list[AgentCertification]) -> str:
    if not certifications:
        return "- No certifications supplied."
    return "\n".join(
        f"- `{cert.certification_id}`: agent={cert.agent_id}, "
        f"type={cert.certification_type}, passed={cert.passed}, "
        f"autonomy={cert.certified_autonomy_level}, "
        f"expires={cert.expires_at.isoformat() if cert.expires_at else 'never'}"
        for cert in certifications
    )


def _policy_violation_summary(
    violations: list[AgentPolicyViolation],
    analytics: GovernanceAuditAnalytics,
    report: AgentGovernanceReport,
) -> str:
    lines = [f"- Total policy violations: {report.policy_violations}"]
    lines.append(_format_mapping("Policy violation trends", analytics.policy_violation_trends))
    if violations:
        lines.extend(
            f"- `{violation.violation_id}`: policy={violation.policy_id}, "
            f"agent={violation.agent_id or 'unknown'}, blocked={violation.blocked}, "
            f"summary={violation.summary}"
            for violation in violations
        )
    return "\n".join(lines)


def _incident_summary(incidents: list[AgentIncident], report: AgentGovernanceReport) -> str:
    lines = [
        f"- Incidents opened: {report.incidents_opened}",
        f"- Incidents resolved: {report.incidents_resolved}",
    ]
    if incidents:
        lines.extend(
            f"- `{incident.incident_id}`: severity={incident.severity}, "
            f"type={incident.incident_type}, status={incident.status}, "
            f"summary={incident.summary}"
            for incident in incidents
        )
    return "\n".join(lines)


def _approval_summary(
    analytics: GovernanceAuditAnalytics,
    report: AgentGovernanceReport,
) -> str:
    lines = [
        f"- Approval requests: {report.approval_requests}",
        f"- Approval rejections: {report.approval_rejections}",
        _format_mapping("Approval rates", analytics.approval_rates),
        _format_mapping("Human override frequency", analytics.human_override_frequency),
    ]
    return "\n".join(lines)


def _safe_recommendations(recommendations: list[str]) -> list[str]:
    safe: list[str] = []
    for recommendation in recommendations or ["Continue standard monitoring."]:
        lowered = recommendation.lower()
        if any(term in lowered for term in UNSAFE_RECOMMENDATION_TERMS):
            safe.append("Escalate this recommendation to a governance administrator for review.")
        else:
            safe.append(recommendation)
    return safe


def _format_mapping(title: str, mapping: dict[str, Any]) -> str:
    if not mapping:
        return f"- {title}: none"
    values = ", ".join(f"{key}={value}" for key, value in sorted(mapping.items()))
    return f"- {title}: {values}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(_redact_json(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {redact_secrets(str(key)): _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


__all__ = [
    "ARTIFACT_FILENAMES",
    "GovernanceArtifactBundle",
    "REPORT_DISCLAIMERS",
    "build_governance_artifact_markdown",
    "write_governance_artifacts",
]
