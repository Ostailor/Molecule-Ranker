from __future__ import annotations

import html
from collections.abc import Sequence

from .schemas import CampaignPlan, CampaignWorkPackage, campaign_protocol_violations


def render_campaign_memo_markdown(plan: CampaignPlan) -> str:
    lines = [
        f"# Campaign Memo: {plan.title}",
        "",
        "## Scope",
        "",
        "This is a closed-loop research-management artifact, not a lab protocol.",
        "It summarizes deterministic campaign priority, budget fit, slot allocation, "
        "review gates, replan triggers, expected learning value, and opportunity cost.",
        "",
        "## Budget Fit",
        "",
        f"- Budget: `{plan.budget_fit.budget_id}`",
        f"- Within budget: `{plan.budget_fit.within_budget}`",
        f"- Work packages selected: {plan.budget_fit.work_packages_selected}",
        f"- Work packages deferred: {plan.budget_fit.work_packages_deferred}",
        f"- Assay slots allocated: {plan.budget_fit.assay_slots_used}",
        f"- Review slots allocated: {plan.budget_fit.review_slots_used}",
        f"- Computation slots allocated: {plan.budget_fit.computation_slots_used}",
        f"- Known total cost: {_format_cost(plan)}",
        "",
        "## Work Packages",
        "",
        *_package_lines(plan.work_packages),
        "",
        "## Deferred Work",
        "",
        *_deferred_lines(plan),
        "",
        "## Replan Triggers",
        "",
        *_trigger_lines(plan),
        "",
        "## Guardrails",
        "",
        "- Campaign priorities, budget fit, dependencies, and triggers are computed by "
        "deterministic modules.",
        "- Codex text, when present, is allowed only as a draft from deterministic campaign "
        "artifacts.",
        "- Generated molecules remain computational hypotheses unless exact imported "
        "experimental evidence exists.",
        "- No synthesis routes, procedural experimental instructions, dosing, or clinical "
        "claims are provided.",
        "",
    ]
    markdown = "\n".join(lines)
    violations = validate_campaign_guardrails(markdown)
    if violations:
        raise ValueError(f"Campaign memo failed guardrails: {', '.join(violations)}")
    return markdown


def render_campaign_dashboard_html(plan: CampaignPlan) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(package.work_package_id)}</td>"
        f"<td>{html.escape(package.work_type)}</td>"
        f"<td>{package.priority_score:.3f}</td>"
        f"<td>{package.expected_learning_value:.3f}</td>"
        f"<td>{html.escape(package.status)}</td>"
        "</tr>"
        for package in plan.work_packages
    )
    triggers = "".join(
        f"<li>{html.escape(trigger.trigger_type)} from "
        f"{html.escape(', '.join(trigger.source_event_ids))}</li>"
        for trigger in plan.replan_triggers
    ) or "<li>None recorded</li>"
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Campaign dashboard</title>
</head>
<body>
  <h1>Campaign dashboard</h1>
  <p>This dashboard is not a lab protocol and contains no procedural instructions.</p>
  <section>
    <h2>Budget fit</h2>
    <ul>
      <li>Within budget: {str(plan.budget_fit.within_budget).lower()}</li>
      <li>Assay slots: {plan.budget_fit.assay_slots_used}</li>
      <li>Review slots: {plan.budget_fit.review_slots_used}</li>
      <li>Computation slots: {plan.budget_fit.computation_slots_used}</li>
      <li>Deferred work packages: {plan.budget_fit.work_packages_deferred}</li>
    </ul>
  </section>
  <section>
    <h2>Work packages</h2>
    <table>
      <thead>
        <tr><th>ID</th><th>Type</th><th>Priority</th><th>Learning</th><th>Status</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </section>
  <section>
    <h2>Replan triggers</h2>
    <ul>{triggers}</ul>
  </section>
</body>
</html>
"""
    violations = validate_campaign_guardrails(html_text)
    if violations:
        raise ValueError(f"Campaign dashboard failed guardrails: {', '.join(violations)}")
    return html_text


def validate_campaign_guardrails(text: str) -> list[str]:
    return campaign_protocol_violations(text)


def _format_cost(plan: CampaignPlan) -> str:
    if plan.budget_fit.total_cost is None:
        return "unknown"
    units = plan.budget.cost_units or ""
    return f"{plan.budget_fit.total_cost:g} {units}".strip()


def _package_lines(packages: Sequence[CampaignWorkPackage]) -> list[str]:
    if not packages:
        return ["- None selected."]
    lines: list[str] = []
    for package in packages:
        lines.append(
            f"- `{package.work_package_id}` `{package.work_type}` priority "
            f"{package.priority_score:.3f}; Expected learning value "
            f"{package.expected_learning_value:.3f}; status `{package.status}`; "
            f"candidates {_join(package.candidate_ids)}; "
            f"hypotheses {_join(package.hypothesis_ids)}."
        )
    return lines


def _deferred_lines(plan: CampaignPlan) -> list[str]:
    if not plan.deferred_work_packages:
        return ["- None. Opportunity cost 0.000."]
    return [
        f"- `{item.work_package_id}` deferred for `{item.defer_reason}`. "
        f"Opportunity cost {item.opportunity_cost_score:.3f}."
        for item in plan.deferred_work_packages
    ]


def _trigger_lines(plan: CampaignPlan) -> list[str]:
    if not plan.replan_triggers:
        return ["- None."]
    return [
        f"- `{trigger.trigger_type}` from {_join(trigger.source_event_ids)}: "
        f"{trigger.rationale}"
        for trigger in plan.replan_triggers
    ]


def _join(values: Sequence[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "`none`"
