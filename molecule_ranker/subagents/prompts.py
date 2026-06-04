from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.subagents.registry import SubagentRegistry, builtin_subagent_profiles
from molecule_ranker.subagents.schemas import SubagentProfile, SubagentRole

REQUIRED_GUARDRAIL_REMINDERS = [
    "Use only provided artifacts and approved tools.",
    "Do not invent biomedical evidence.",
    "Do not invent assay results.",
    "Do not invent citations.",
    "Do not invent molecules.",
    "Do not claim generated molecules are active.",
    "Do not treat predictions/docking/graph inference as evidence.",
    "Do not provide medical advice.",
    "Do not provide lab protocols.",
    "Do not provide synthesis instructions.",
    "Do not provide dosing or patient guidance.",
]
OUTPUT_SCHEMA_REQUIREMENT = (
    "Output must be valid JSON matching the expected output schema."
)
TASK_PROMPT_TEMPLATE = """Task objective:
{objective}

Use only provided artifacts and approved tools.
Approved tools for this task:
{allowed_tool_names}

Provided artifact ids:
{artifact_ids}

Relevant artifact summaries:
{artifact_summaries}

Expected output schema:
{output_schema}

Output must be valid JSON matching the expected output schema.
Ground every scientific claim in the provided artifact ids. If evidence is missing, say so.
Do not invent biomedical evidence.
Do not invent assay results.
Do not invent citations.
Do not invent molecules.
Do not claim generated molecules are active.
Do not treat predictions/docking/graph inference as evidence.
Do not provide medical advice.
Do not provide lab protocols.
Do not provide synthesis instructions.
Do not provide dosing or patient guidance.
"""


class SubagentPromptTemplate(BaseModel):
    subagent_id: str
    name: str
    role: SubagentRole
    system_prompt: str
    task_prompt_template: str
    output_json_schema_instructions: str
    allowed_actions: list[str]
    forbidden_actions: list[str]
    guardrail_reminders: list[str]
    artifact_grounding_instructions: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def prompt_text(self) -> str:
        return "\n".join(
            [
                self.system_prompt,
                self.task_prompt_template,
                self.output_json_schema_instructions,
                "\n".join(self.allowed_actions),
                "\n".join(self.forbidden_actions),
                "\n".join(self.guardrail_reminders),
                self.artifact_grounding_instructions,
            ]
        )

    def render_task_prompt(
        self,
        *,
        objective: str,
        artifact_ids: list[str],
        allowed_tool_names: list[str],
        output_schema: dict[str, Any],
        artifact_summaries: list[dict[str, Any]] | None = None,
    ) -> str:
        return self.task_prompt_template.format(
            objective=objective,
            allowed_tool_names=_json_list(allowed_tool_names),
            artifact_ids=_json_list(artifact_ids),
            artifact_summaries=json.dumps(artifact_summaries or [], sort_keys=True),
            output_schema=json.dumps(output_schema, sort_keys=True),
        )


def build_prompt_template(profile: SubagentProfile) -> SubagentPromptTemplate:
    allowed_actions = _allowed_actions(profile)
    forbidden_actions = _forbidden_actions(profile)
    return SubagentPromptTemplate(
        subagent_id=profile.subagent_id,
        name=profile.name,
        role=profile.role,
        system_prompt=_system_prompt(profile, allowed_actions, forbidden_actions),
        task_prompt_template=TASK_PROMPT_TEMPLATE,
        output_json_schema_instructions=_output_schema_instructions(),
        allowed_actions=allowed_actions,
        forbidden_actions=forbidden_actions,
        guardrail_reminders=list(REQUIRED_GUARDRAIL_REMINDERS),
        artifact_grounding_instructions=_artifact_grounding_instructions(),
        metadata={
            "guardrail_profile": profile.guardrail_profile,
            "allowed_tool_categories": profile.allowed_tool_categories,
            "denied_tool_categories": profile.denied_tool_categories,
            "required_permissions": profile.required_permissions,
        },
    )


def builtin_prompt_templates() -> dict[str, SubagentPromptTemplate]:
    return {
        profile.subagent_id: build_prompt_template(profile)
        for profile in builtin_subagent_profiles()
    }


def get_prompt_template(
    subagent_id: str,
    *,
    registry: SubagentRegistry | None = None,
) -> SubagentPromptTemplate:
    profile = (registry or SubagentRegistry()).require(subagent_id)
    return build_prompt_template(profile)


def _system_prompt(
    profile: SubagentProfile,
    allowed_actions: list[str],
    forbidden_actions: list[str],
) -> str:
    responsibilities = [str(item) for item in profile.metadata.get("responsibilities", [])]
    return "\n".join(
        [
            f"You are {profile.name}, a specialized Codex subagent.",
            f"Role: {profile.role}.",
            f"Description: {profile.description}",
            f"Guardrail profile: {profile.guardrail_profile}.",
            "Responsibilities:",
            *_bullet_lines(responsibilities),
            "Allowed actions:",
            *_bullet_lines(allowed_actions),
            "Forbidden actions:",
            *_bullet_lines(forbidden_actions),
            "Guardrail reminders:",
            *_bullet_lines(REQUIRED_GUARDRAIL_REMINDERS),
            _artifact_grounding_instructions(),
            _output_schema_instructions(),
        ]
    )


def _allowed_actions(profile: SubagentProfile) -> list[str]:
    responsibilities = [str(item) for item in profile.metadata.get("responsibilities", [])]
    actions = [
        *responsibilities,
        *[f"use approved {category} tools" for category in profile.allowed_tool_categories],
        (
            "request human approval when required"
            if profile.can_request_approval
            else "operate without approval requests"
        ),
    ]
    if profile.can_delegate:
        actions.append("delegate scoped subtasks to authorized subagents")
    if profile.can_write_artifacts:
        actions.append("write schema-valid artifacts when authorized")
    return list(dict.fromkeys(actions))


def _forbidden_actions(profile: SubagentProfile) -> list[str]:
    cannot = [str(item) for item in profile.metadata.get("cannot", [])]
    actions = [
        *cannot,
        *[f"use denied tool category: {category}" for category in profile.denied_tool_categories],
        (
            "approve stage gates, campaign advancement, external writes, destructive "
            "actions, or policy overrides"
        ),
        "bypass RBAC, policy, approvals, deterministic validators, or sandbox boundaries",
    ]
    if not profile.can_execute_tools:
        actions.append("execute tools")
    if not profile.can_write_artifacts:
        actions.append("write artifacts directly")
    return list(dict.fromkeys(actions))


def _output_schema_instructions() -> str:
    return "\n".join(
        [
            OUTPUT_SCHEMA_REQUIREMENT,
            "Return one JSON object only.",
            "Include every required field from the expected output schema.",
            "Use null or empty arrays only when the provided artifacts do not support a value.",
            "Do not add unsupported facts to satisfy the schema.",
        ]
    )


def _artifact_grounding_instructions() -> str:
    return (
        "Artifact grounding: cite artifact ids for all evidence-linked claims; separate "
        "artifact-backed facts from hypotheses, predictions, docking outputs, graph inference, "
        "and operational recommendations."
    )


def _bullet_lines(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items]


def _json_list(values: list[str]) -> str:
    return json.dumps(list(dict.fromkeys(values)), sort_keys=True)


__all__ = [
    "OUTPUT_SCHEMA_REQUIREMENT",
    "REQUIRED_GUARDRAIL_REMINDERS",
    "SubagentPromptTemplate",
    "build_prompt_template",
    "builtin_prompt_templates",
    "get_prompt_template",
]
