from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.context import redact_sensitive_context
from molecule_ranker.subagents.coordinator import TOOL_CATALOG
from molecule_ranker.subagents.registry import SubagentRegistry
from molecule_ranker.subagents.schemas import SubagentProfile, SubagentRole

REDACTED = "[REDACTED]"
DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": True}
DEFAULT_PROJECT_POLICY = {
    "approved_tools_only": True,
    "artifact_grounding_required": True,
    "schema_validation_required": True,
    "guardrail_check_required": True,
    "audit_trail_required": True,
    "human_approval_required_for": [
        "stage_gate_decisions",
        "external_writes",
        "generated_molecule_assay_advancement",
        "campaign_approval",
        "destructive_actions",
        "policy_overrides",
    ],
}

ROLE_ARTIFACT_CATEGORIES: dict[SubagentRole, tuple[str, ...]] = {
    "program_manager": (
        "project",
        "job",
        "artifact",
        "report",
        "portfolio",
        "campaign",
        "evaluation",
        "summary",
    ),
    "evidence_reviewer": (
        "evidence",
        "literature",
        "ranking",
        "pubmed",
        "openalex",
        "chembl",
        "open_targets",
        "opentargets",
        "graph_query",
    ),
    "molecule_designer": (
        "design",
        "generation",
        "generated_molecule",
        "developability",
        "oracle",
    ),
    "developability_safety": (
        "developability",
        "safety",
        "admet",
        "alert",
        "model_prediction",
    ),
    "experiment_analyst": (
        "experiment",
        "experimental",
        "assay",
        "result_summary",
        "active_learning",
    ),
    "predictive_modeler": ("model", "evaluation", "prediction", "calibration"),
    "structure_reviewer": (
        "structure",
        "docking",
        "pose_qc",
        "pose",
        "interaction",
        "report",
    ),
    "graph_reasoner": ("graph", "mechanism", "contradiction", "stale_decision"),
    "hypothesis_planner": ("hypothesis", "graph", "research_question"),
    "portfolio_strategist": ("portfolio", "scenario", "evaluation_summary"),
    "campaign_planner": ("campaign", "portfolio", "hypothesis", "evaluation"),
    "integration_operator": (
        "integration",
        "connector",
        "connector_health",
        "sync",
        "sync_summary",
        "mapping",
        "tool_marketplace",
    ),
    "evaluation_validator": (
        "evaluation",
        "benchmark",
        "validation",
        "guardrail",
        "reproducibility",
        "release_check",
    ),
    "guardrail_sentinel": (
        "output",
        "critique_target",
        "policy",
        "guardrail",
        "validation",
        "result",
        "consensus",
    ),
    "platform_operator": (
        "ops",
        "operation",
        "support",
        "readiness",
        "job",
        "worker",
        "health",
        "performance",
        "admin",
    ),
}

ROLE_SYSTEM_INSTRUCTIONS: dict[SubagentRole, str] = {
    "program_manager": (
        "Decompose goals, delegate scoped work, track status, coordinate critiques, and "
        "summarize consensus without creating scientific evidence or approving gates."
    ),
    "evidence_reviewer": (
        "Review only artifact-grounded evidence and provenance from approved sources. "
        "Flag gaps and contradictions without inventing evidence, citations, or results."
    ),
    "molecule_designer": (
        "Plan and review approved generation/design loops. Treat generated molecules as "
        "computational hypotheses and do not claim activity or bypass the generation pipeline."
    ),
    "developability_safety": (
        "Review developability, ADMET heuristics, alerts, and safety warnings as triage "
        "signals only, without clinical safety conclusions."
    ),
    "experiment_analyst": (
        "Summarize imported experimental result artifacts, QC status, and contradictions. "
        "Do not fabricate results or treat failed QC as supporting evidence."
    ),
    "predictive_modeler": (
        "Build or evaluate predictive model artifacts, calibration, and applicability domain. "
        "Do not convert predictions or fabricated metrics into evidence."
    ),
    "structure_reviewer": (
        "Review structure selection, docking, pose QC, and interaction profiles. Do not claim "
        "that docking proves binding."
    ),
    "graph_reasoner": (
        "Query validated graph artifacts for mechanisms, contradictions, and stale decisions. "
        "Do not create graph facts outside builder and validator workflows."
    ),
    "hypothesis_planner": (
        "Generate and rank graph-backed hypotheses and research questions without writing "
        "protocols or operational lab instructions."
    ),
    "portfolio_strategist": (
        "Optimize portfolio scenarios and summarize tradeoffs without approving stage gates."
    ),
    "campaign_planner": (
        "Build high-level campaign plans and replan triggers without lab protocols, synthesis "
        "instructions, or campaign approval."
    ),
    "integration_operator": (
        "Inspect connector health, mappings, and dry-run sync summaries. Do not access secrets "
        "or perform external writes without approval."
    ),
    "evaluation_validator": (
        "Run and summarize benchmark, validation, guardrail, reproducibility, and release checks "
        "without inventing metrics."
    ),
    "guardrail_sentinel": (
        "Critique outputs against safety, scientific, policy, schema, and audit guardrails. Do "
        "not mutate scientific outputs directly."
    ),
    "platform_operator": (
        "Review redacted readiness, support, jobs, workers, health, and performance artifacts. "
        "Do not access secrets or bypass RBAC."
    ),
}

SENSITIVE_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "service_token",
    "token",
)
RAW_ASSAY_TYPES = {"raw_assay", "assay_raw", "raw_experiment_file", "raw_assay_file"}
RAW_PAYLOAD_FIELDS = {
    "content",
    "file_bytes",
    "raw",
    "raw_content",
    "raw_file",
    "raw_payload",
    "rows",
}


class SubagentContextPolicy(BaseModel):
    visible_artifact_ids: list[str] | None = None
    allowed_tool_names: list[str] | None = None
    permit_raw_assay_files: bool = False
    project_policy: dict[str, Any] = Field(default_factory=dict)
    autonomy_level: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubagentContext(BaseModel):
    subagent_id: str
    role: SubagentRole
    system_instructions: str
    allowed_tools: list[dict[str, Any]]
    allowed_artifacts: list[dict[str, Any]]
    relevant_summaries: list[dict[str, Any]]
    denied_actions: list[str]
    output_schema: dict[str, Any]
    guardrail_constraints: list[str]
    project_policy: dict[str, Any]
    autonomy_level: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubagentContextBuilder:
    def __init__(
        self,
        *,
        registry: SubagentRegistry | None = None,
        tool_catalog: dict[str, list[str]] | None = None,
    ) -> None:
        self.registry = registry or SubagentRegistry()
        self.tool_catalog = tool_catalog or TOOL_CATALOG

    def build(
        self,
        *,
        subagent_id: str,
        artifacts: list[dict[str, Any]],
        policy: SubagentContextPolicy | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> SubagentContext:
        profile = self.registry.require(subagent_id)
        context_policy = policy or SubagentContextPolicy()
        allowed_tools = self._allowed_tools(profile, context_policy)
        allowed_artifacts = self._allowed_artifacts(profile, artifacts, context_policy)
        project_policy = _merge_project_policy(context_policy.project_policy)

        return SubagentContext(
            subagent_id=profile.subagent_id,
            role=profile.role,
            system_instructions=ROLE_SYSTEM_INSTRUCTIONS[profile.role],
            allowed_tools=allowed_tools,
            allowed_artifacts=allowed_artifacts,
            relevant_summaries=_relevant_summaries(allowed_artifacts),
            denied_actions=_denied_actions(profile),
            output_schema=_redact_json_like(output_schema or DEFAULT_OUTPUT_SCHEMA),
            guardrail_constraints=_guardrail_constraints(profile),
            project_policy=project_policy,
            autonomy_level=context_policy.autonomy_level or profile.default_autonomy_level,
            metadata={
                "guardrail_profile": profile.guardrail_profile,
                "required_permissions": profile.required_permissions,
                "context_policy": _context_policy_summary(context_policy),
            },
        )

    def _allowed_tools(
        self,
        profile: SubagentProfile,
        policy: SubagentContextPolicy,
    ) -> list[dict[str, Any]]:
        allowed_by_profile: list[dict[str, Any]] = []
        for category in profile.allowed_tool_categories:
            for tool_name in self.tool_catalog.get(category, []):
                allowed_by_profile.append({"tool_name": tool_name, "category": category})

        allowed_names = set(policy.allowed_tool_names or [])
        if allowed_names:
            allowed_by_profile = [
                tool for tool in allowed_by_profile if tool["tool_name"] in allowed_names
            ]

        denied_categories = set(profile.denied_tool_categories)
        unique: dict[str, dict[str, Any]] = {}
        for tool in allowed_by_profile:
            if tool["category"] in denied_categories:
                continue
            unique.setdefault(str(tool["tool_name"]), tool)
        return list(unique.values())

    def _allowed_artifacts(
        self,
        profile: SubagentProfile,
        artifacts: list[dict[str, Any]],
        policy: SubagentContextPolicy,
    ) -> list[dict[str, Any]]:
        visible_ids = set(policy.visible_artifact_ids or [])
        allowed: list[dict[str, Any]] = []
        for artifact in artifacts:
            artifact_id = _artifact_id(artifact)
            if visible_ids and artifact_id not in visible_ids:
                continue
            if not _role_can_see_artifact(profile.role, artifact):
                continue
            if _is_raw_assay_artifact(artifact) and not policy.permit_raw_assay_files:
                continue
            allowed.append(_safe_artifact_for_role(profile.role, artifact, policy))
        return allowed


def build_subagent_context(
    *,
    subagent_id: str,
    artifacts: list[dict[str, Any]],
    policy: SubagentContextPolicy | None = None,
    output_schema: dict[str, Any] | None = None,
    registry: SubagentRegistry | None = None,
    tool_catalog: dict[str, list[str]] | None = None,
) -> SubagentContext:
    return SubagentContextBuilder(registry=registry, tool_catalog=tool_catalog).build(
        subagent_id=subagent_id,
        artifacts=artifacts,
        policy=policy,
        output_schema=output_schema,
    )


def _role_can_see_artifact(role: SubagentRole, artifact: dict[str, Any]) -> bool:
    categories = _artifact_categories(artifact)
    allowed_tokens = ROLE_ARTIFACT_CATEGORIES[role]
    return any(token in category for token in allowed_tokens for category in categories)


def _safe_artifact_for_role(
    role: SubagentRole,
    artifact: dict[str, Any],
    policy: SubagentContextPolicy,
) -> dict[str, Any]:
    safe = _redact_json_like(artifact)
    if role == "experiment_analyst" and not policy.permit_raw_assay_files:
        safe = {
            key: value
            for key, value in safe.items()
            if key.lower().replace("-", "_") not in RAW_PAYLOAD_FIELDS
        }
    if role in {"integration_operator", "platform_operator"}:
        safe = _redact_json_like(safe)
    return safe


def _relevant_summaries(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_id = _artifact_id(artifact)
        summary = artifact.get("summary")
        if summary is None and artifact.get("description") is not None:
            summary = artifact["description"]
        if summary is None:
            continue
        summaries.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact.get("artifact_type", artifact.get("type")),
                "summary": _redact_json_like(summary),
            }
        )
    return summaries


def _denied_actions(profile: SubagentProfile) -> list[str]:
    actions = [
        *[str(item) for item in profile.metadata.get("cannot", [])],
        *[f"use denied tool category: {category}" for category in profile.denied_tool_categories],
    ]
    return list(dict.fromkeys(actions))


def _guardrail_constraints(profile: SubagentProfile) -> list[str]:
    constraints = [
        f"Use guardrail profile: {profile.guardrail_profile}.",
        "Use only scoped artifacts and approved tools in this context.",
        (
            "Ground outputs in visible artifacts; do not invent evidence, assay results, "
            "citations, molecules, graph facts, model metrics, docking scores, campaign "
            "outcomes, or benchmark results."
        ),
        (
            "Do not provide medical advice, lab protocols, synthesis instructions, dosing, "
            "or patient treatment guidance."
        ),
        (
            "Do not approve stage gates, campaign advancement, external writes, "
            "generated-molecule assay advancement, destructive actions, or policy overrides."
        ),
        (
            "Do not bypass deterministic validators, RBAC, policy, approvals, sandbox "
            "profiles, or audit logging."
        ),
    ]
    for cannot in profile.metadata.get("cannot", []):
        constraints.append(f"Cannot: {cannot}.")
    return list(dict.fromkeys(constraints))


def _context_policy_summary(policy: SubagentContextPolicy) -> dict[str, Any]:
    return _redact_json_like(
        {
            "has_artifact_scope": policy.visible_artifact_ids is not None,
            "has_tool_scope": policy.allowed_tool_names is not None,
            "permit_raw_assay_files": policy.permit_raw_assay_files,
            "autonomy_level": policy.autonomy_level,
            "project_policy_keys": sorted(policy.project_policy),
            "metadata_keys": sorted(policy.metadata),
        }
    )


def _merge_project_policy(policy: dict[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_PROJECT_POLICY)
    merged.update(policy)
    return _redact_json_like(merged)


def _artifact_id(artifact: dict[str, Any]) -> str:
    for key in ("artifact_id", "id", "artifactId"):
        value = artifact.get(key)
        if isinstance(value, str):
            return value
    return ""


def _artifact_categories(artifact: dict[str, Any]) -> set[str]:
    categories: set[str] = set()
    for key in ("artifact_type", "category", "kind", "type"):
        value = artifact.get(key)
        if isinstance(value, str):
            categories.add(_normalize_token(value))
    for key in ("artifact_categories", "categories", "tags"):
        value = artifact.get(key)
        if isinstance(value, list):
            categories.update(_normalize_token(str(item)) for item in value)
    metadata = artifact.get("metadata")
    if isinstance(metadata, dict):
        for key in ("artifact_type", "category", "kind", "type"):
            value = metadata.get(key)
            if isinstance(value, str):
                categories.add(_normalize_token(value))
    return categories or {"artifact"}


def _is_raw_assay_artifact(artifact: dict[str, Any]) -> bool:
    categories = _artifact_categories(artifact)
    if categories.intersection(RAW_ASSAY_TYPES):
        return True
    if artifact.get("raw") is True:
        return True
    return any("raw_assay" in category for category in categories)


def _redact_json_like(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_context(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = _normalize_token(str(key))
            if _sensitive_key(normalized):
                redacted[str(key)] = REDACTED
            else:
                redacted[str(key)] = _redact_json_like(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json_like(item) for item in value]
    return value


def _sensitive_key(normalized_key: str) -> bool:
    return any(marker in normalized_key for marker in SENSITIVE_FIELD_MARKERS)


def _normalize_token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def context_size_bytes(context: SubagentContext) -> int:
    return len(
        json.dumps(context.model_dump(mode="json"), sort_keys=True, default=str).encode("utf-8")
    )


__all__ = [
    "DEFAULT_PROJECT_POLICY",
    "ROLE_ARTIFACT_CATEGORIES",
    "ROLE_SYSTEM_INSTRUCTIONS",
    "SubagentContext",
    "SubagentContextBuilder",
    "SubagentContextPolicy",
    "build_subagent_context",
    "context_size_bytes",
]
