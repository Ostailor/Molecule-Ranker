from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker.agent_governance.schemas import (
    AgentGovernanceReport,
    AgentGovernanceSchema,
    AgentPolicyViolation,
)
from molecule_ranker.codex_backbone.guardrails import redact_secrets

GovernanceAuditEventType = Literal[
    "tool_call",
    "guardrail_failure",
    "approval_request",
    "approval_rejection",
    "policy_violation",
    "budget_usage",
    "budget_violation",
    "incident_opened",
    "incident_resolved",
    "autonomy_level",
    "tool_package_risk",
    "human_override",
    "repair_outcome",
    "copilot_action",
    "subagent_disagreement",
    "codex_task",
]
GovernanceAuditPeriod = Literal["daily", "weekly", "monthly"]


class GovernanceAuditEvent(AgentGovernanceSchema):
    event_id: str
    event_type: GovernanceAuditEventType
    occurred_at: datetime
    agent_id: str | None = None
    role: str | None = None
    org_id: str | None = None
    project_id: str | None = None
    campaign_id: str | None = None
    tool_name: str | None = None
    tool_category: str | None = None
    autonomy_level: str | None = None
    outcome: str | None = None
    risk_level: str | None = None
    count: int = Field(default=1, ge=0)
    value: float = Field(default=0.0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GovernanceAuditAnalytics(BaseModel):
    report: AgentGovernanceReport
    period: GovernanceAuditPeriod
    tool_usage_by_agent: dict[str, int] = Field(default_factory=dict)
    guardrail_failures_by_agent: dict[str, int] = Field(default_factory=dict)
    guardrail_failures_by_role: dict[str, int] = Field(default_factory=dict)
    guardrail_failures_by_project: dict[str, int] = Field(default_factory=dict)
    approval_rates: dict[str, float] = Field(default_factory=dict)
    policy_violation_trends: dict[str, int] = Field(default_factory=dict)
    budget_usage_trends: dict[str, float] = Field(default_factory=dict)
    incident_trends: dict[str, int] = Field(default_factory=dict)
    autonomy_level_distribution: dict[str, int] = Field(default_factory=dict)
    tool_package_risk_distribution: dict[str, int] = Field(default_factory=dict)
    human_override_frequency: dict[str, int] = Field(default_factory=dict)
    repair_rates: dict[str, float] = Field(default_factory=dict)
    copilot_action_outcomes: dict[str, int] = Field(default_factory=dict)
    subagent_disagreement_rates: dict[str, float] = Field(default_factory=dict)
    codex_task_failure_rates: dict[str, float] = Field(default_factory=dict)
    redacted: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class GovernanceReportExport(BaseModel):
    report: AgentGovernanceReport
    analytics: GovernanceAuditAnalytics
    json_path: Path
    markdown_path: Path


class AgentGovernanceAuditAnalyticsBuilder:
    """Build redacted V2.6 governance audit analytics reports."""

    def build_report(
        self,
        *,
        events: list[GovernanceAuditEvent],
        org_id: str | None = None,
        project_id: str | None = None,
        period: GovernanceAuditPeriod = "weekly",
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        report_id: str | None = None,
    ) -> GovernanceAuditAnalytics:
        filtered_events = _filter_events(
            events,
            org_id=org_id,
            project_id=project_id,
            period_start=period_start,
            period_end=period_end,
        )
        start, end = _report_period(filtered_events, period_start, period_end)
        tool_usage = _counter_by(filtered_events, "tool_call", "agent_id")
        guardrail_by_agent = _counter_by(filtered_events, "guardrail_failure", "agent_id")
        guardrail_by_role = _counter_by(filtered_events, "guardrail_failure", "role")
        guardrail_by_project = _counter_by(filtered_events, "guardrail_failure", "project_id")
        approval_requests = _event_count(filtered_events, "approval_request")
        approval_rejections = _event_count(filtered_events, "approval_rejection")
        incidents_opened = _event_count(filtered_events, "incident_opened")
        incidents_resolved = _event_count(filtered_events, "incident_resolved")
        policy_violations = _event_count(filtered_events, "policy_violation")
        budget_violations = _event_count(filtered_events, "budget_violation")
        codex_task_count = _event_count(filtered_events, "codex_task")
        agents = {
            event.agent_id
            for event in filtered_events
            if event.agent_id is not None
        }
        disabled_agents = {
            event.agent_id
            for event in filtered_events
            if event.agent_id is not None and event.outcome == "disabled"
        }
        top_risks = _top_keys(_distribution(filtered_events, "tool_package_risk", "risk_level"))
        recommendations = _recommendations(
            guardrail_failures=sum(guardrail_by_agent.values()),
            approval_rejections=approval_rejections,
            approval_requests=approval_requests,
            incidents_opened=incidents_opened,
            budget_violations=budget_violations,
        )
        report = AgentGovernanceReport(
            report_id=report_id or f"agent-governance-report-{uuid4().hex[:12]}",
            org_id=org_id,
            project_id=project_id,
            period_start=start,
            period_end=end,
            agent_count=len(agents),
            active_agent_count=max(len(agents - disabled_agents), 0),
            disabled_agent_count=len(disabled_agents),
            total_tool_calls=sum(tool_usage.values()),
            total_codex_tasks=codex_task_count,
            guardrail_failures=sum(guardrail_by_agent.values()),
            policy_violations=policy_violations,
            approval_requests=approval_requests,
            approval_rejections=approval_rejections,
            incidents_opened=incidents_opened,
            incidents_resolved=incidents_resolved,
            budget_violations=budget_violations,
            top_risks=top_risks,
            recommendations=recommendations,
            metadata=_redact_json(
                {
                    "analytics_period": period,
                    "event_count": len(filtered_events),
                    "source_event_ids": [event.event_id for event in filtered_events],
                }
            ),
        )
        analytics = GovernanceAuditAnalytics(
            report=report,
            period=period,
            tool_usage_by_agent=tool_usage,
            guardrail_failures_by_agent=guardrail_by_agent,
            guardrail_failures_by_role=guardrail_by_role,
            guardrail_failures_by_project=guardrail_by_project,
            approval_rates=_approval_rates(approval_requests, approval_rejections),
            policy_violation_trends=_trend(filtered_events, "policy_violation", period),
            budget_usage_trends=_value_trend(filtered_events, "budget_usage", period),
            incident_trends=_trend(filtered_events, "incident_opened", period),
            autonomy_level_distribution=_distribution(
                filtered_events,
                "autonomy_level",
                "autonomy_level",
            ),
            tool_package_risk_distribution=_distribution(
                filtered_events,
                "tool_package_risk",
                "risk_level",
            ),
            human_override_frequency=_counter_by(filtered_events, "human_override", "agent_id"),
            repair_rates=_outcome_rates(filtered_events, "repair_outcome"),
            copilot_action_outcomes=_outcome_counts(filtered_events, "copilot_action"),
            subagent_disagreement_rates=_agent_rate(
                filtered_events,
                "subagent_disagreement",
                numerator_outcomes={"disagreement"},
            ),
            codex_task_failure_rates=_agent_rate(
                filtered_events,
                "codex_task",
                numerator_outcomes={"failed", "failure", "error"},
            ),
            metadata=_redact_json(
                {
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                }
            ),
        )
        return _redact_analytics(analytics)

    def write_outputs(
        self,
        analytics: GovernanceAuditAnalytics,
        output_dir: Path | str,
    ) -> GovernanceReportExport:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        json_path = target_dir / "governance_report.json"
        markdown_path = target_dir / "governance_report.md"
        json_payload = _redact_json(analytics.model_dump(mode="json"))
        markdown = build_governance_report_markdown(analytics)
        json_path.write_text(
            json.dumps(json_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(markdown, encoding="utf-8")
        return GovernanceReportExport(
            report=analytics.report,
            analytics=analytics,
            json_path=json_path,
            markdown_path=markdown_path,
        )


def build_governance_report_markdown(analytics: GovernanceAuditAnalytics) -> str:
    report = analytics.report
    lines = [
        "# Agent Governance Report",
        "",
        f"- Report ID: `{report.report_id}`",
        f"- Org: `{report.org_id or 'all'}`",
        f"- Project: `{report.project_id or 'all'}`",
        f"- Period: `{report.period_start.isoformat()}` to `{report.period_end.isoformat()}`",
        "",
        "## Summary",
        f"- Agents: {report.agent_count}",
        f"- Active agents: {report.active_agent_count}",
        f"- Disabled agents: {report.disabled_agent_count}",
        f"- Tool calls: {report.total_tool_calls}",
        f"- Codex tasks: {report.total_codex_tasks}",
        f"- Guardrail failures: {report.guardrail_failures}",
        f"- Policy violations: {report.policy_violations}",
        f"- Approval requests: {report.approval_requests}",
        f"- Approval rejections: {report.approval_rejections}",
        f"- Incidents opened: {report.incidents_opened}",
        f"- Incidents resolved: {report.incidents_resolved}",
        f"- Budget violations: {report.budget_violations}",
        "",
        "## Analytics",
        _format_mapping("Tool usage by agent", analytics.tool_usage_by_agent),
        _format_mapping("Guardrail failures by agent", analytics.guardrail_failures_by_agent),
        _format_mapping("Guardrail failures by role", analytics.guardrail_failures_by_role),
        _format_mapping(
            "Guardrail failures by project",
            analytics.guardrail_failures_by_project,
        ),
        _format_mapping("Approval rates", analytics.approval_rates),
        _format_mapping("Policy violation trends", analytics.policy_violation_trends),
        _format_mapping("Budget usage trends", analytics.budget_usage_trends),
        _format_mapping("Incident trends", analytics.incident_trends),
        _format_mapping(
            "Autonomy level distribution",
            analytics.autonomy_level_distribution,
        ),
        _format_mapping(
            "Tool package risk distribution",
            analytics.tool_package_risk_distribution,
        ),
        _format_mapping("Human override frequency", analytics.human_override_frequency),
        _format_mapping("Repair success/failure rates", analytics.repair_rates),
        _format_mapping("Co-pilot action outcomes", analytics.copilot_action_outcomes),
        _format_mapping(
            "Subagent disagreement rates",
            analytics.subagent_disagreement_rates,
        ),
        _format_mapping("Codex task failure rates", analytics.codex_task_failure_rates),
        "",
        "## Recommendations",
        *[f"- {item}" for item in report.recommendations],
    ]
    return redact_secrets("\n".join(lines) + "\n")


def load_governance_audit_events(path: Path | str) -> list[GovernanceAuditEvent]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw_events = raw.get("events", [])
    else:
        raw_events = raw
    if not isinstance(raw_events, list):
        return []
    return [
        GovernanceAuditEvent.model_validate(item)
        for item in raw_events
        if isinstance(item, dict)
    ]


def _filter_events(
    events: list[GovernanceAuditEvent],
    *,
    org_id: str | None,
    project_id: str | None,
    period_start: datetime | None,
    period_end: datetime | None,
) -> list[GovernanceAuditEvent]:
    filtered: list[GovernanceAuditEvent] = []
    for event in events:
        if org_id is not None and event.org_id != org_id:
            continue
        if project_id is not None and event.project_id != project_id:
            continue
        if period_start is not None and event.occurred_at < period_start:
            continue
        if period_end is not None and event.occurred_at > period_end:
            continue
        filtered.append(event)
    return filtered


def _report_period(
    events: list[GovernanceAuditEvent],
    period_start: datetime | None,
    period_end: datetime | None,
) -> tuple[datetime, datetime]:
    if period_start is not None and period_end is not None:
        return period_start, period_end
    if events:
        return (
            period_start or min(event.occurred_at for event in events),
            period_end or max(event.occurred_at for event in events),
        )
    now = datetime.now(UTC)
    return period_start or now, period_end or now


def _event_count(events: list[GovernanceAuditEvent], event_type: str) -> int:
    return sum(event.count for event in events if event.event_type == event_type)


def _counter_by(
    events: list[GovernanceAuditEvent],
    event_type: str,
    field_name: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for event in events:
        if event.event_type != event_type:
            continue
        key = getattr(event, field_name)
        counts[str(key or "unknown")] += event.count
    return dict(sorted(counts.items()))


def _distribution(
    events: list[GovernanceAuditEvent],
    event_type: str,
    field_name: str,
) -> dict[str, int]:
    return _counter_by(events, event_type, field_name)


def _trend(
    events: list[GovernanceAuditEvent],
    event_type: str,
    period: GovernanceAuditPeriod,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for event in events:
        if event.event_type == event_type:
            counts[_period_bucket(event.occurred_at, period)] += event.count
    return dict(sorted(counts.items()))


def _value_trend(
    events: list[GovernanceAuditEvent],
    event_type: str,
    period: GovernanceAuditPeriod,
) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for event in events:
        if event.event_type == event_type:
            totals[_period_bucket(event.occurred_at, period)] += event.value or event.count
    return dict(sorted(totals.items()))


def _period_bucket(timestamp: datetime, period: GovernanceAuditPeriod) -> str:
    if period == "daily":
        return timestamp.date().isoformat()
    if period == "monthly":
        return f"{timestamp.year:04d}-{timestamp.month:02d}"
    iso_year, iso_week, _ = timestamp.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def _approval_rates(requests: int, rejections: int) -> dict[str, float]:
    approvals = max(requests - rejections, 0)
    if requests == 0:
        return {"request_count": 0.0, "approval_rate": 0.0, "rejection_rate": 0.0}
    return {
        "request_count": float(requests),
        "approval_rate": approvals / requests,
        "rejection_rate": rejections / requests,
    }


def _outcome_counts(
    events: list[GovernanceAuditEvent],
    event_type: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for event in events:
        if event.event_type == event_type:
            counts[event.outcome or "unknown"] += event.count
    return dict(sorted(counts.items()))


def _outcome_rates(
    events: list[GovernanceAuditEvent],
    event_type: str,
) -> dict[str, float]:
    counts = _outcome_counts(events, event_type)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {key: value / total for key, value in counts.items()}


def _agent_rate(
    events: list[GovernanceAuditEvent],
    event_type: str,
    *,
    numerator_outcomes: set[str],
) -> dict[str, float]:
    totals: Counter[str] = Counter()
    numerators: Counter[str] = Counter()
    for event in events:
        if event.event_type != event_type:
            continue
        agent = event.agent_id or "unknown"
        totals[agent] += event.count
        if (event.outcome or "").lower() in numerator_outcomes:
            numerators[agent] += event.count
    return {
        agent: numerators[agent] / total
        for agent, total in sorted(totals.items())
        if total > 0
    }


def _top_keys(mapping: dict[str, int], *, limit: int = 5) -> list[str]:
    return [
        key
        for key, _ in sorted(mapping.items(), key=lambda item: item[1], reverse=True)[:limit]
        if key != "unknown"
    ]


def _recommendations(
    *,
    guardrail_failures: int,
    approval_rejections: int,
    approval_requests: int,
    incidents_opened: int,
    budget_violations: int,
) -> list[str]:
    recommendations: list[str] = []
    if guardrail_failures:
        recommendations.append("Review agents with guardrail failures before increasing autonomy.")
    if approval_requests and approval_rejections / approval_requests >= 0.25:
        recommendations.append("Investigate high approval rejection rate.")
    if incidents_opened:
        recommendations.append("Triage open incidents and confirm mitigation ownership.")
    if budget_violations:
        recommendations.append("Review autonomy budgets and pause exhausted agents if needed.")
    if not recommendations:
        recommendations.append("Continue standard monitoring.")
    return recommendations


def _format_mapping(title: str, mapping: dict[str, Any]) -> str:
    if not mapping:
        return f"### {title}\n- None\n"
    lines = [f"### {title}", *[f"- `{key}`: {value}" for key, value in mapping.items()], ""]
    return "\n".join(lines)


def _redact_analytics(analytics: GovernanceAuditAnalytics) -> GovernanceAuditAnalytics:
    return GovernanceAuditAnalytics.model_validate(_redact_json(analytics.model_dump(mode="json")))


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {redact_secrets(str(key)): _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


__all__ = [
    "AgentGovernanceAuditAnalyticsBuilder",
    "AgentGovernanceReport",
    "AgentPolicyViolation",
    "GovernanceAuditAnalytics",
    "GovernanceAuditEvent",
    "GovernanceReportExport",
    "build_governance_report_markdown",
    "load_governance_audit_events",
]
