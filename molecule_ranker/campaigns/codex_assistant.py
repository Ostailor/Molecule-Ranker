from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from molecule_ranker.campaigns.schemas import (
    Campaign,
    CampaignPlan,
    contains_procedural_lab_detail,
)
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult

CampaignCodexTaskType = Literal[
    "draft_campaign_memo",
    "summarize_campaign_tradeoffs",
    "explain_replan_trigger",
    "draft_review_questions_for_campaign",
    "summarize_budget_bottlenecks",
    "draft_project_update_from_campaign",
]

CAMPAIGN_CODEX_TASKS = {
    "draft_campaign_memo",
    "summarize_campaign_tradeoffs",
    "explain_replan_trigger",
    "draft_review_questions_for_campaign",
    "summarize_budget_bottlenecks",
    "draft_project_update_from_campaign",
}

FORBIDDEN_CAMPAIGN_CODEX_ACTIONS = [
    "create deterministic campaign plan",
    "approve campaign",
    "approve gates",
    "invent costs",
    "invent assay results",
    "invent evidence",
    "invent hypothesis status",
    "add lab protocols",
    "add synthesis instructions",
    "add dosing",
    "claim selected candidates are active/safe/effective",
]

_COST_RE = re.compile(
    r"(?i)(?:\$|usd\b|eur\b|gbp\b|cost(?:s|ed|ing)?\s+(?:is|are|=)?\s*)\s*\d[\d,]*(?:\.\d+)?"
)
_STATUS_RE = re.compile(
    r"(?i)\bhypothesis(?:\s+status)?\s+(?:is|was|=)\s+"
    r"(?:active|retired|stale|validated|invalidated|approved|rejected)\b"
)
_ASSAY_RESULT_RE = re.compile(
    r"(?i)\b(?:assay result|positive result|negative result|qc passed|qc failed)\b"
)
_OVERCLAIM_RE = re.compile(
    r"(?i)\bselected candidates?\s+(?:are|were|is|was)\s+"
    r"(?:active|safe|effective|efficacious|clinically useful)\b"
)


def build_campaign_codex_task(
    *,
    task_type: CampaignCodexTaskType,
    plan: CampaignPlan,
    campaign: Campaign | None,
    working_directory: str,
    input_artifact_paths: list[str],
) -> CodexTask:
    """Build a guarded Codex task for explanation or memo drafting only."""

    if task_type not in CAMPAIGN_CODEX_TASKS:
        raise ValueError(f"Unsupported campaign Codex task: {task_type}")
    return CodexTask(
        task_id=f"campaign-codex-{task_type}",
        task_type=task_type,
        prompt=_campaign_prompt(task_type, plan, campaign),
        working_directory=working_directory,
        input_artifact_paths=input_artifact_paths,
        allowed_commands=[],
        forbidden_commands=[],
        expected_output_format="json",
        require_json=True,
        metadata={
            "campaign_id": plan.campaign_id,
            "campaign_plan_id": plan.campaign_plan_id,
            "allowed_work_package_ids": sorted(_work_package_ids(plan)),
            "allowed_hypothesis_ids": sorted(_hypothesis_ids(plan, campaign)),
            "allowed_portfolio_selection_ids": sorted(_portfolio_selection_ids(campaign)),
            "codex_campaign_scope": "explanation_and_memo_drafting_only",
            "forbidden_campaign_actions": list(FORBIDDEN_CAMPAIGN_CODEX_ACTIONS),
        },
    )


def validate_campaign_codex_output(
    result: CodexTaskResult,
    *,
    plan: CampaignPlan,
    campaign: Campaign | None = None,
    artifact_ids: Iterable[str] = (),
) -> CodexTaskResult:
    """Validate campaign Codex output against deterministic campaign artifacts."""

    warnings = [
        *result.guardrail_warnings,
        *_campaign_output_warnings(
            _output_text(result),
            plan=plan,
            campaign=campaign,
            artifact_ids=set(artifact_ids) | _artifact_ids(plan, campaign),
        ),
    ]
    if not warnings:
        return result
    return result.model_copy(
        update={
            "status": "guardrail_failed",
            "guardrail_warnings": _dedupe(warnings),
            "metadata": {
                **result.metadata,
                "campaign_codex_guardrails": "failed",
                "codex_campaign_scope": "explanation_and_memo_drafting_only",
            },
        }
    )


def _campaign_output_warnings(
    text: str,
    *,
    plan: CampaignPlan,
    campaign: Campaign | None,
    artifact_ids: set[str],
) -> list[str]:
    warnings: list[str] = []
    lowered = text.lower()
    if contains_procedural_lab_detail(text):
        warnings.append("Campaign Codex output contains procedural lab detail.")
    if "synthesis instruction" in lowered or "synthesis route" in lowered:
        warnings.append("Campaign Codex output contains synthesis instructions.")
    if "dosing" in lowered or "dose " in lowered:
        warnings.append("Campaign Codex output contains dosing guidance.")
    if _OVERCLAIM_RE.search(text):
        warnings.append(
            "Campaign Codex output claims selected candidates are active/safe/effective."
        )
    if _ASSAY_RESULT_RE.search(text):
        warnings.append("Campaign Codex output appears to invent assay results.")
    if _STATUS_RE.search(text):
        warnings.append("Campaign Codex output appears to invent hypothesis status.")
    warnings.extend(_cost_warnings(text, plan))
    warnings.extend(_unknown_id_warnings(text, plan, campaign, artifact_ids))
    warnings.extend(_missing_required_citation_warnings(text, plan, campaign, artifact_ids))
    return warnings


def _cost_warnings(text: str, plan: CampaignPlan) -> list[str]:
    if not _COST_RE.search(text):
        return []
    allowed_values = {
        str(value).lower()
        for value in _flatten_values(
            [
                plan.budget.model_dump(mode="json"),
                plan.budget_summary,
                [package.model_dump(mode="json") for package in plan.work_packages],
            ]
        )
    }
    cost_basis = str(
        plan.budget_summary.get("cost_basis") or plan.budget.metadata.get("cost_basis")
    )
    if cost_basis.lower() == "unknown":
        return ["Campaign Codex output contains invented cost; campaign cost basis is unknown."]
    for match in _COST_RE.findall(text):
        normalized = str(match).lower().replace(",", "")
        if normalized and normalized not in allowed_values:
            return ["Campaign Codex output contains invented cost not present in artifacts."]
    return []


def _unknown_id_warnings(
    text: str,
    plan: CampaignPlan,
    campaign: Campaign | None,
    artifact_ids: set[str],
) -> list[str]:
    warnings: list[str] = []
    allowed_work_packages = _lower(_work_package_ids(plan))
    allowed_hypotheses = _lower(_hypothesis_ids(plan, campaign))
    allowed_selections = _lower(_portfolio_selection_ids(campaign))
    allowed_artifacts = _lower(artifact_ids)
    for item in _id_mentions(text, ("wp-", "work-package-", "campaign-work-package")):
        if item.lower() not in allowed_work_packages:
            warnings.append(f"Campaign Codex output cites unknown work package ID: {item}.")
    for item in _id_mentions(text, ("hypothesis-", "hypothesis:")):
        if item.lower() not in allowed_hypotheses:
            warnings.append(f"Campaign Codex output cites unknown hypothesis ID: {item}.")
    for item in _id_mentions(text, ("selection-", "portfolio-selection", "selection:")):
        if item.lower() not in allowed_selections:
            warnings.append(f"Campaign Codex output cites unknown portfolio selection ID: {item}.")
    for item in _id_mentions(text, ("artifact:", "campaign-artifact")):
        if item.lower() not in allowed_artifacts:
            warnings.append(f"Campaign Codex output cites unknown artifact ID: {item}.")
    return warnings


def _missing_required_citation_warnings(
    text: str,
    plan: CampaignPlan,
    campaign: Campaign | None,
    artifact_ids: set[str],
) -> list[str]:
    lowered = text.lower()
    required = {
        "campaign_id": {plan.campaign_id},
        "campaign_plan_id": {plan.campaign_plan_id},
        "work_package_ids": _work_package_ids(plan),
        "hypothesis_ids": _hypothesis_ids(plan, campaign),
        "portfolio_selection_ids": _portfolio_selection_ids(campaign),
        "artifact_ids": artifact_ids,
    }
    warnings: list[str] = []
    for label, values in required.items():
        if values and not any(value.lower() in lowered for value in values):
            warnings.append(f"Campaign Codex output is missing required citation: {label}.")
    return warnings


def _campaign_prompt(
    task_type: CampaignCodexTaskType,
    plan: CampaignPlan,
    campaign: Campaign | None,
) -> str:
    return (
        "Use Codex only for campaign explanation and memo drafting. "
        f"Task: {task_type}. "
        f"Cite campaign_id={plan.campaign_id}; campaign_plan_id={plan.campaign_plan_id}; "
        f"work_package_ids={sorted(_work_package_ids(plan))}; "
        f"hypothesis_ids={sorted(_hypothesis_ids(plan, campaign))}; "
        f"portfolio_selection_ids={sorted(_portfolio_selection_ids(campaign))}; "
        f"artifact_ids={sorted(_artifact_ids(plan, campaign))}. "
        "Do not create deterministic campaign plans, approve campaigns or gates, invent costs, "
        "invent assay results, invent evidence, invent hypothesis status, add lab protocols, "
        "add synthesis instructions, add dosing, or claim selected candidates are "
        "active/safe/effective."
    )


def _output_text(result: CodexTaskResult) -> str:
    pieces = [result.output_text, result.stdout, result.stderr]
    if result.output_json is not None:
        pieces.append(str(result.output_json))
    return "\n".join(piece for piece in pieces if piece)


def _work_package_ids(plan: CampaignPlan) -> set[str]:
    return {package.work_package_id for package in plan.work_packages}


def _hypothesis_ids(plan: CampaignPlan, campaign: Campaign | None) -> set[str]:
    ids = set(campaign.hypothesis_ids if campaign is not None else [])
    for objective in plan.objectives:
        ids.update(objective.linked_hypothesis_ids)
    for package in plan.work_packages:
        ids.update(package.linked_hypothesis_ids)
    return ids


def _portfolio_selection_ids(campaign: Campaign | None) -> set[str]:
    return set(campaign.portfolio_selection_ids if campaign is not None else [])


def _artifact_ids(plan: CampaignPlan, campaign: Campaign | None) -> set[str]:
    ids = set(_strings_from_value(plan.metadata.get("artifact_ids")))
    for package in plan.work_packages:
        ids.update(_strings_from_value(package.metadata.get("artifact_ids")))
    if campaign is not None:
        ids.update(_strings_from_value(campaign.metadata.get("artifact_ids")))
        ids.update(_strings_from_value(campaign.metadata.get("source_artifacts")))
    return ids


def _id_mentions(text: str, prefixes: tuple[str, ...]) -> set[str]:
    mentions: set[str] = set()
    for prefix in prefixes:
        pattern = re.compile(rf"\b{re.escape(prefix)}[A-Za-z0-9_.:-]+\b", re.I)
        mentions.update(match.group(0).rstrip(".,;)") for match in pattern.finditer(text))
    return mentions


def _strings_from_value(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {
            item
            for key, val in value.items()
            for item in {*_strings_from_value(key), *_strings_from_value(val)}
        }
    if isinstance(value, Iterable):
        return {item for sub in value for item in _strings_from_value(sub)}
    return {str(value)}


def _flatten_values(values: Iterable[Any]) -> set[Any]:
    flattened: set[Any] = set()
    for value in values:
        if isinstance(value, dict):
            flattened.update(_flatten_values(value.values()))
        elif isinstance(value, list | tuple | set):
            flattened.update(_flatten_values(value))
        else:
            flattened.add(value)
    return flattened


def _lower(values: Iterable[str]) -> set[str]:
    return {value.lower() for value in values}


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value not in output:
            output.append(value)
    return output


__all__ = [
    "CAMPAIGN_CODEX_TASKS",
    "FORBIDDEN_CAMPAIGN_CODEX_ACTIONS",
    "CampaignCodexTaskType",
    "build_campaign_codex_task",
    "validate_campaign_codex_output",
]
