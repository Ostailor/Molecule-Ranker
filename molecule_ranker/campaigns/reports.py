from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.campaigns.schemas import Campaign, CampaignMemo, CampaignPlan


def build_campaign_memo(plan: CampaignPlan, *, title: str | None = None) -> CampaignMemo:
    """Build a deterministic campaign memo from an already-computed plan."""

    budget_usage = plan.budget_summary.get("usage") or plan.budget_summary.get("totals") or {}
    exceeded = plan.budget_summary.get("exceeded_dimensions") or []
    approvals = sorted(
        {
            approval
            for package in plan.work_packages
            for approval in package.required_approvals
        }
    )
    if plan.human_approval_required and "campaign_approval" not in approvals:
        approvals.append("campaign_approval")
    limitations = [
        "This memo is a research-management artifact.",
        "It does not include procedural experimental instructions.",
        "It does not include chemical-making instructions or dosing guidance.",
        (
            "Generated molecules remain computational hypotheses unless exact imported "
            "evidence exists."
        ),
        "Codex may summarize deterministic artifacts but does not approve campaign advancement.",
    ]
    return CampaignMemo(
        memo_id=_stable_id("campaign-memo", plan.campaign_plan_id),
        campaign_id=plan.campaign_id,
        title=title or f"Campaign Memo: {plan.campaign_id}",
        executive_summary=(
            f"Deterministic campaign plan {plan.campaign_plan_id} selected "
            f"{len(plan.work_packages)} high-level work package(s) for review."
        ),
        objectives_summary=(
            f"{len(plan.objectives)} objective(s) are linked to source-backed campaign inputs."
        ),
        selected_work_packages=[package.work_package_id for package in plan.work_packages],
        budget_summary=(
            "Planning estimate usage: "
            + (
                ", ".join(f"{key}={value}" for key, value in sorted(budget_usage.items()))
                if budget_usage
                else "no configured usage values"
            )
            + (
                f"; exceeded dimensions: {', '.join(str(item) for item in exceeded)}"
                if exceeded
                else "; no selected-work budget exceedance"
            )
        ),
        key_tradeoffs=[
            "Higher learning-value work was balanced against configured resource limits.",
            "Blocked or excluded packages remain advisory planning candidates.",
        ],
        risks=list(plan.risk_summary.get("critical_safety_or_contradiction_packages", [])),
        uncertainty_notes=[
            f"Expected learning value: {plan.expected_learning_value:.3f}",
            f"Uncertainty summary: {plan.uncertainty_summary}",
        ],
        replan_triggers=list(plan.replan_triggers),
        approvals_required=approvals,
        limitations=limitations,
        created_at=datetime.now(UTC),
        metadata={
            "source_campaign_plan_id": plan.campaign_plan_id,
            "deterministic_campaign_artifact_summary": True,
            "codex_generated_metrics": False,
        },
    )


def render_campaign_memo_markdown(memo: CampaignMemo, plan: CampaignPlan) -> str:
    lines = [
        f"# {memo.title}",
        "",
        "## Executive Summary",
        "",
        memo.executive_summary,
        "",
        "## Objectives",
        "",
        memo.objectives_summary,
        "",
        "## Selected Work Packages",
        "",
        *[
            f"- {package.work_package_id}: {package.title} ({package.package_type})"
            for package in plan.work_packages
        ],
        "",
        "## Budget",
        "",
        memo.budget_summary,
        "",
        "## Tradeoffs",
        "",
        *[f"- {item}" for item in memo.key_tradeoffs],
        "",
        "## Risks",
        "",
        *([f"- {item}" for item in memo.risks] or ["- None flagged in selected packages."]),
        "",
        "## Uncertainty",
        "",
        *[f"- {item}" for item in memo.uncertainty_notes],
        "",
        "## Replan Triggers",
        "",
        *([f"- {item}" for item in memo.replan_triggers] or ["- None currently configured."]),
        "",
        "## Approvals",
        "",
        *(
            [f"- {item}" for item in memo.approvals_required]
            or ["- No additional approval labels recorded."]
        ),
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in memo.limitations],
        "",
    ]
    return "\n".join(lines)


def render_campaign_report_markdown(plan: CampaignPlan, campaign: Campaign | None = None) -> str:
    budget_usage = plan.budget_summary.get("usage") or plan.budget_summary.get("totals") or {}
    budget_limits = plan.budget_summary.get("limits") or {}
    dependency_edges = plan.dependency_graph.get("edges", [])
    dependency_nodes = plan.dependency_graph.get("nodes", [])
    campaign_name = campaign.name if campaign is not None else plan.campaign_id
    status = campaign.status if campaign is not None else "draft"
    lines = [
        f"# Campaign Report: {campaign_name}",
        "",
        "## Campaign Summary",
        "",
        f"- Campaign ID: {plan.campaign_id}",
        f"- Plan ID: {plan.campaign_plan_id}",
        f"- Status: {status}",
        f"- Work packages selected: {len(plan.work_packages)}",
        "- Campaign plan is research-management guidance.",
        "",
        "## Objectives",
        "",
        *_objective_lines(plan),
        "",
        "## Work Packages",
        "",
        *_work_package_lines(plan),
        "",
        "## Budget and Resource Use",
        "",
        *_key_value_lines("Usage", budget_usage),
        *_key_value_lines("Limits", budget_limits),
        "",
        "## Dependency Graph",
        "",
        (
            "- Nodes: "
            f"{', '.join(str(item) for item in dependency_nodes) if dependency_nodes else 'None'}"
        ),
        *[
            (
                f"- {edge.get('from')} -> {edge.get('to')} "
                f"({edge.get('dependency_type')})"
            )
            for edge in dependency_edges
            if isinstance(edge, dict)
        ],
        "",
        "## Recommended Sequence",
        "",
        *([f"- {item}" for item in plan.recommended_sequence] or ["- No sequence recorded."]),
        "",
        "## Stage Gates and Approvals",
        "",
        *_gate_lines(plan),
        "",
        "## Replan Triggers",
        "",
        *([f"- {item}" for item in plan.replan_triggers] or ["- None currently configured."]),
        "",
        "## Risks and Uncertainty",
        "",
        *_key_value_lines("Risk", plan.risk_summary),
        *_key_value_lines("Uncertainty", plan.uncertainty_summary),
        "",
        "## Expected Learning Value",
        "",
        f"- Expected learning value: {plan.expected_learning_value:.3f}",
        "",
        "## Limitations",
        "",
        "- Campaign plan is research-management guidance.",
        "- Not a lab protocol.",
        "- No synthesis instructions.",
        "- No dosing.",
        "- No clinical guidance.",
        "- Selected candidates are not proven active/safe/effective.",
        (
            "- Generated molecules remain computational hypotheses unless exact imported "
            "evidence exists."
        ),
        "",
    ]
    return "\n".join(lines)


def _objective_lines(plan: CampaignPlan) -> list[str]:
    if not plan.objectives:
        return ["- No objectives recorded."]
    return [
        (
            f"- {objective.objective_id}: {objective.name} "
            f"({objective.objective_type}, weight={objective.priority_weight:.3f})"
        )
        for objective in plan.objectives
    ]


def _work_package_lines(plan: CampaignPlan) -> list[str]:
    if not plan.work_packages:
        return ["- No work packages selected."]
    return [
        (
            f"- {package.work_package_id}: {package.title} "
            f"({package.package_type}, status={package.status})"
        )
        for package in plan.work_packages
    ]


def _gate_lines(plan: CampaignPlan) -> list[str]:
    if not plan.stage_gates:
        return ["- No stage gates recorded."]
    return [
        (
            f"- {gate.get('gate_id')}: {gate.get('gate_type')} "
            f"status={gate.get('approval_status', gate.get('decision', 'pending'))}"
        )
        for gate in plan.stage_gates
    ]


def _key_value_lines(label: str, payload: dict[str, Any]) -> list[str]:
    if not payload:
        return [f"- {label}: none recorded"]
    return [f"- {label} {key}: {value}" for key, value in sorted(payload.items())]


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts if part is not None) or prefix
    return f"{prefix}:{uuid5(NAMESPACE_URL, raw).hex[:12]}"


__all__ = [
    "build_campaign_memo",
    "render_campaign_memo_markdown",
    "render_campaign_report_markdown",
]
