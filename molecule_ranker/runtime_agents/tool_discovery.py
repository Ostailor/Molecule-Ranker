from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2
from molecule_ranker.tool_ecosystem.schemas import SkillPack, WorkflowTemplate
from molecule_ranker.tool_ecosystem.skills import list_builtin_skill_packs

DEFAULT_TOOL_CONTEXT_BUDGET = 8
MIN_SELECTED_TOOLS = 1

KEYWORD_RULES: tuple[tuple[re.Pattern[str], set[str], set[str]], ...] = (
    (
        re.compile(r"\b(?:rank|ranking|prioriti[sz]e|disease|candidate)\b", re.I),
        {"ranking"},
        {"run_ranking", "summarize_ranking", "rerun_ranking"},
    ),
    (
        re.compile(r"\b(?:literature|citation|pubmed|paper|publication|evidence update)\b", re.I),
        {"literature"},
        {"run_literature_update", "summarize_literature"},
    ),
    (
        re.compile(r"\b(?:generate|generation|design|molecule|candidate design)\b", re.I),
        {"generation"},
        {"run_generation", "run_design_loop", "benchmark_generation"},
    ),
    (
        re.compile(r"\b(?:developability|admet|triage|drug[- ]?like)\b", re.I),
        {"developability"},
        {"run_developability", "assess_developability_artifact"},
    ),
    (
        re.compile(r"\b(?:experiment|assay|results?|active learning|feedback)\b", re.I),
        {"experiments"},
        {"import_assay_results", "link_assay_results", "summarize_assay_results"},
    ),
    (
        re.compile(r"\b(?:graph|knowledge graph|contradiction|staleness|query graph)\b", re.I),
        {"graph"},
        {"build_graph", "detect_contradictions", "detect_staleness", "query_graph"},
    ),
    (
        re.compile(r"\b(?:hypothes|research question)\b", re.I),
        {"hypotheses"},
        {"generate_hypotheses", "rank_hypotheses", "create_research_questions"},
    ),
    (
        re.compile(r"\b(?:portfolio|scenario|optimi[sz]e)\b", re.I),
        {"portfolio"},
        {"build_portfolio_candidates", "optimize_portfolio", "run_scenarios"},
    ),
    (
        re.compile(r"\b(?:campaign|stage gate|stage-gate)\b", re.I),
        {"campaign"},
        {"plan_campaign", "create_campaign", "replan_campaign"},
    ),
    (
        re.compile(r"\b(?:review|workspace|dossier|handoff|report)\b", re.I),
        {"review", "codex"},
        {"create_review_workspace", "create_dossier", "create_validation_handoff"},
    ),
    (
        re.compile(r"\b(?:benchmark|eval|evaluation|readiness|reproducib|guardrail audit)\b", re.I),
        {"evaluation", "admin"},
        {"run_benchmark", "run_guardrail_benchmark", "run_reproducibility_check", "run_readiness"},
    ),
)


class ToolDiscoveryError(ValueError):
    """Raised when deterministic tool discovery rejects a Codex tool request."""


class ExcludedTool(BaseModel):
    tool_name: str
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillSuggestion(BaseModel):
    skill_pack_id: str
    skill_id: str
    name: str
    reason: str
    required_tools: list[str] = Field(default_factory=list)


class WorkflowSuggestion(BaseModel):
    workflow_template_id: str
    name: str
    reason: str
    required_tools: list[str] = Field(default_factory=list)


class ToolDiscoveryResult(BaseModel):
    selected_tools: list[RuntimeToolSpec]
    selected_skill_suggestions: list[SkillSuggestion] = Field(default_factory=list)
    selected_workflow_suggestions: list[WorkflowSuggestion] = Field(default_factory=list)
    excluded_tools: list[ExcludedTool] = Field(default_factory=list)
    context_budget: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def selected_tool_names(self) -> list[str]:
        return [tool.tool_name for tool in self.selected_tools]


EmbeddingSearch = Callable[[str, list[RuntimeToolSpec], int], list[RuntimeToolSpec]]


class DynamicToolDiscovery:
    """Select task-relevant visible tools for Codex runtime planning."""

    def __init__(
        self,
        *,
        registry: RuntimeToolRegistry | ToolRegistryV2 | None = None,
        embedding_search: EmbeddingSearch | None = None,
        default_context_budget: int = DEFAULT_TOOL_CONTEXT_BUDGET,
    ) -> None:
        self.registry = registry or RuntimeToolRegistry.default()
        self.embedding_search = embedding_search
        self.default_context_budget = default_context_budget

    def discover(
        self,
        *,
        user_goal: str,
        project_context: dict[str, Any] | None = None,
        available_tools: list[RuntimeToolSpec] | None = None,
        user_permissions: set[str] | list[str] | None = None,
        policy_constraints: list[str] | None = None,
        skill_packs: list[SkillPack | dict[str, Any]] | None = None,
        workflow_templates: list[WorkflowTemplate | dict[str, Any]] | None = None,
        project_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        max_tools: int | None = None,
    ) -> ToolDiscoveryResult:
        budget = max_tools or self.default_context_budget
        permissions = set(user_permissions or [])
        raw_tools = available_tools or self._visible_tools(
            user_permissions=permissions,
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
        )
        visible_tools, excluded = self._filter_visible(
            raw_tools,
            user_permissions=permissions,
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
        )
        query_text = _query_text(user_goal, project_context or {}, policy_constraints or [])
        scored = [
            (_score_tool(tool, query_text), index, tool)
            for index, tool in enumerate(visible_tools)
        ]
        scored.sort(key=lambda item: (-item[0], item[1], item[2].tool_name))
        selected = [tool for score, _index, tool in scored if score > 0][:budget]

        if self.embedding_search is not None and len(selected) < budget:
            embedding_candidates = self.embedding_search(
                query_text,
                [tool for _score, _index, tool in scored],
                budget,
            )
            for tool in embedding_candidates:
                if tool.tool_name not in {selected_tool.tool_name for selected_tool in selected}:
                    selected.append(tool)
                if len(selected) >= budget:
                    break

        if not selected and visible_tools:
            selected = [tool for _score, _index, tool in scored[:MIN_SELECTED_TOOLS]]

        selected_names = {tool.tool_name for tool in selected}
        for score, _index, tool in scored:
            if tool.tool_name in selected_names:
                continue
            reason = "not relevant to the current goal" if score == 0 else "tool context budget"
            excluded.append(ExcludedTool(tool_name=tool.tool_name, reason=reason))

        skill_suggestions = _suggest_skills(
            skill_packs or list_builtin_skill_packs(),
            query_text=query_text,
            selected_tool_names=selected_names,
            visible_tool_names={tool.tool_name for tool in visible_tools},
        )
        workflow_suggestions = _suggest_workflows(
            workflow_templates or [],
            query_text=query_text,
            selected_tool_names=selected_names,
            visible_tool_names={tool.tool_name for tool in visible_tools},
        )
        return ToolDiscoveryResult(
            selected_tools=selected,
            selected_skill_suggestions=skill_suggestions,
            selected_workflow_suggestions=workflow_suggestions,
            excluded_tools=_dedupe_exclusions(excluded),
            context_budget=budget,
            metadata={
                "discovery_strategy": "keyword_rule_based",
                "embedding_search_enabled": self.embedding_search is not None,
                "visible_tool_count": len(visible_tools),
                "policy_constraints": policy_constraints or [],
            },
        )

    def validate_codex_tool_request(
        self,
        requested_tool_names: list[str],
        *,
        discovery_result: ToolDiscoveryResult | None = None,
        user_goal: str | None = None,
        project_context: dict[str, Any] | None = None,
        user_permissions: set[str] | list[str] | None = None,
        policy_constraints: list[str] | None = None,
        project_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> list[RuntimeToolSpec]:
        result = discovery_result or self.discover(
            user_goal=user_goal or "",
            project_context=project_context,
            user_permissions=user_permissions,
            policy_constraints=policy_constraints,
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
            max_tools=max(self.default_context_budget, len(requested_tool_names)),
        )
        visible_by_name = {
            tool.tool_name: tool
            for tool in self._visible_tools(
                user_permissions=set(user_permissions or []),
                project_id=project_id,
                org_id=org_id,
                user_id=user_id,
            )
        }
        selected: list[RuntimeToolSpec] = []
        for name in requested_tool_names:
            if name in {tool.tool_name for tool in result.selected_tools}:
                selected.append(
                    next(tool for tool in result.selected_tools if tool.tool_name == name)
                )
                continue
            tool = visible_by_name.get(name)
            if tool is None:
                raise ToolDiscoveryError(f"Codex requested unknown or unauthorized tool: {name}")
            selected.append(tool)
        return selected

    def _visible_tools(
        self,
        *,
        user_permissions: set[str],
        project_id: str | None,
        org_id: str | None,
        user_id: str | None,
    ) -> list[RuntimeToolSpec]:
        if isinstance(self.registry, ToolRegistryV2):
            return self.registry.list_tools_visible_to_user(
                user_permissions=user_permissions,
                project_id=project_id,
                org_id=org_id,
            )
        return self.registry.discover_approved_tools(
            org_id=org_id,
            project_id=project_id,
            user_id=user_id,
            user_permissions=user_permissions,
        )

    def _filter_visible(
        self,
        tools: list[RuntimeToolSpec],
        *,
        user_permissions: set[str],
        project_id: str | None,
        org_id: str | None,
        user_id: str | None,
    ) -> tuple[list[RuntimeToolSpec], list[ExcludedTool]]:
        visible: list[RuntimeToolSpec] = []
        excluded: list[ExcludedTool] = []
        registry_names = {tool.tool_name for tool in self._visible_tools(
            user_permissions=user_permissions,
            project_id=project_id,
            org_id=org_id,
            user_id=user_id,
        )}
        for tool in tools:
            if tool.tool_name not in registry_names:
                excluded.append(ExcludedTool(tool_name=tool.tool_name, reason="not visible"))
                continue
            if not set(tool.required_permissions).issubset(user_permissions):
                excluded.append(
                    ExcludedTool(tool_name=tool.tool_name, reason="missing permissions")
                )
                continue
            if not _tool_package_visible(tool):
                excluded.append(
                    ExcludedTool(tool_name=tool.tool_name, reason="unapproved or disabled package")
                )
                continue
            visible.append(tool)
        return visible, excluded


def discover_relevant_tools(
    *,
    user_goal: str,
    project_context: dict[str, Any] | None = None,
    available_tools: list[RuntimeToolSpec] | None = None,
    user_permissions: set[str] | list[str] | None = None,
    policy_constraints: list[str] | None = None,
    skill_packs: list[SkillPack | dict[str, Any]] | None = None,
    workflow_templates: list[WorkflowTemplate | dict[str, Any]] | None = None,
    registry: RuntimeToolRegistry | ToolRegistryV2 | None = None,
    project_id: str | None = None,
    org_id: str | None = None,
    user_id: str | None = None,
    max_tools: int | None = None,
) -> ToolDiscoveryResult:
    return DynamicToolDiscovery(registry=registry).discover(
        user_goal=user_goal,
        project_context=project_context,
        available_tools=available_tools,
        user_permissions=user_permissions,
        policy_constraints=policy_constraints,
        skill_packs=skill_packs,
        workflow_templates=workflow_templates,
        project_id=project_id,
        org_id=org_id,
        user_id=user_id,
        max_tools=max_tools,
    )


def _score_tool(tool: RuntimeToolSpec, query_text: str) -> int:
    score = 0
    legacy_name = str(tool.metadata.get("legacy_tool_name") or tool.tool_name)
    normalized_names = {tool.tool_name.lower(), legacy_name.lower()}
    category = tool.category.lower()
    for pattern, categories, tool_names in KEYWORD_RULES:
        if not pattern.search(query_text):
            continue
        if category in categories:
            score += 6
        if _short_tool_name(tool.tool_name) in tool_names or legacy_name in tool_names:
            score += 8
    haystack = " ".join(
        [
            tool.tool_name,
            legacy_name,
            tool.category,
            tool.description,
            " ".join(tool.required_permissions),
            " ".join(tool.policy_tags),
        ]
    ).lower()
    for token in _tokens(query_text):
        if token in normalized_names:
            score += 5
        elif token in haystack:
            score += 1
    return score


def _suggest_skills(
    skill_packs: Sequence[SkillPack | dict[str, Any]],
    *,
    query_text: str,
    selected_tool_names: set[str],
    visible_tool_names: set[str],
) -> list[SkillSuggestion]:
    suggestions: list[SkillSuggestion] = []
    selected_short_names = {_short_tool_name(name) for name in selected_tool_names}
    visible_short_names = {_short_tool_name(name) for name in visible_tool_names}
    for raw_pack in skill_packs:
        pack = raw_pack if isinstance(raw_pack, SkillPack) else SkillPack.model_validate(raw_pack)
        for raw_skill in pack.skills:
            if not isinstance(raw_skill, dict):
                continue
            required_tools = [str(tool) for tool in raw_skill.get("required_tools", [])]
            required_short = {_short_tool_name(tool) for tool in required_tools}
            if not required_short.issubset(visible_short_names):
                continue
            skill_text = json.dumps(raw_skill, sort_keys=True, default=str).lower()
            overlap = required_short.intersection(selected_short_names)
            keyword_match = any(token in skill_text for token in _tokens(query_text))
            if overlap or keyword_match:
                suggestions.append(
                    SkillSuggestion(
                        skill_pack_id=pack.skill_pack_id,
                        skill_id=str(raw_skill.get("skill_id") or raw_skill.get("name")),
                        name=str(raw_skill.get("name") or raw_skill.get("skill_id")),
                        reason="matches selected tools" if overlap else "matches goal keywords",
                        required_tools=required_tools,
                    )
                )
    return suggestions[:5]


def _suggest_workflows(
    workflow_templates: Sequence[WorkflowTemplate | dict[str, Any]],
    *,
    query_text: str,
    selected_tool_names: set[str],
    visible_tool_names: set[str],
) -> list[WorkflowSuggestion]:
    suggestions: list[WorkflowSuggestion] = []
    selected_short_names = {_short_tool_name(name) for name in selected_tool_names}
    visible_short_names = {_short_tool_name(name) for name in visible_tool_names}
    for raw in workflow_templates:
        workflow = (
            raw if isinstance(raw, WorkflowTemplate) else WorkflowTemplate.model_validate(raw)
        )
        required_short = {_short_tool_name(tool) for tool in workflow.required_tools}
        if required_short and not required_short.issubset(visible_short_names):
            continue
        text = json.dumps(workflow.model_dump(mode="json"), sort_keys=True).lower()
        overlap = required_short.intersection(selected_short_names)
        keyword_match = any(token in text for token in _tokens(query_text))
        if overlap or keyword_match:
            suggestions.append(
                WorkflowSuggestion(
                    workflow_template_id=workflow.workflow_template_id,
                    name=workflow.name,
                    reason="matches selected tools" if overlap else "matches goal keywords",
                    required_tools=workflow.required_tools,
                )
            )
    return suggestions[:5]


def _tool_package_visible(tool: RuntimeToolSpec) -> bool:
    package = tool.metadata.get("tool_package")
    if not isinstance(package, dict):
        return True
    return (
        package.get("approval_status") == "approved"
        and package.get("security_scan_status") == "passed"
        and package.get("status", "approved") == "approved"
    )


def _query_text(
    user_goal: str,
    project_context: dict[str, Any],
    policy_constraints: list[str],
) -> str:
    return " ".join(
        [
            user_goal,
            json.dumps(project_context, sort_keys=True, default=str),
            " ".join(policy_constraints),
        ]
    ).lower()


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]{3,}", text.lower())}


def _short_tool_name(tool_name: str) -> str:
    return tool_name.rsplit(".", maxsplit=1)[-1]


def _dedupe_exclusions(exclusions: list[ExcludedTool]) -> list[ExcludedTool]:
    observed: set[tuple[str, str]] = set()
    deduped: list[ExcludedTool] = []
    for exclusion in exclusions:
        key = (exclusion.tool_name, exclusion.reason)
        if key in observed:
            continue
        observed.add(key)
        deduped.append(exclusion)
    return deduped


__all__ = [
    "DynamicToolDiscovery",
    "ExcludedTool",
    "SkillSuggestion",
    "ToolDiscoveryError",
    "ToolDiscoveryResult",
    "WorkflowSuggestion",
    "discover_relevant_tools",
]
