from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker.runtime_agents.approvals import approval_type_for_tool
from molecule_ranker.runtime_agents.schemas import RiskLevel, RuntimeActionPlan, RuntimeActionStep
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


class RuntimeSkillValidationError(ValueError):
    """Raised when a runtime skill cannot be deterministically expanded."""


class RuntimeSkillStepTemplate(BaseModel):
    action_type: str
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    approval_requirements: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    optional: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeSkillSpec(BaseModel):
    skill_name: str
    description: str
    input_schema: dict[str, Any]
    default_plan_template: list[RuntimeSkillStepTemplate]
    required_tools: list[str]
    required_permissions: list[str]
    approval_requirements: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_schema")
    @classmethod
    def require_json_object_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("runtime skill input_schema must be a JSON object schema")
        return value

    @model_validator(mode="after")
    def require_declared_template_tools(self) -> RuntimeSkillSpec:
        template_tools = {step.tool_name for step in self.default_plan_template}
        missing = template_tools - set(self.required_tools)
        if missing:
            raise ValueError(
                "runtime skill required_tools must include template tools: "
                + ", ".join(sorted(missing))
            )
        return self


def expand_skill_to_plan(
    skill: RuntimeSkillSpec,
    *,
    session_id: str,
    user_goal: str,
    inputs: dict[str, Any] | None = None,
    registry: RuntimeToolRegistry | None = None,
    user_permissions: set[str] | None = None,
    plan_id: str | None = None,
) -> RuntimeActionPlan:
    active_registry = registry or RuntimeToolRegistry.default()
    _validate_skill_for_expansion(skill, active_registry, user_permissions)
    runtime_plan_id = plan_id or f"runtime-skill-plan-{uuid4().hex[:12]}"
    plan_inputs = inputs or {}
    steps: list[RuntimeActionStep] = []
    tool_specs: dict[str, dict[str, Any]] = {}
    required_approvals: list[str] = list(skill.approval_requirements)

    for index, template in enumerate(skill.default_plan_template):
        spec = active_registry.require(template.tool_name)
        step_approvals = list(
            dict.fromkeys([*skill.approval_requirements, *template.approval_requirements])
        )
        for approval in step_approvals:
            if approval not in required_approvals:
                required_approvals.append(approval)
        requires_approval = bool(step_approvals or spec.requires_approval_by_default)
        approval_reason = None
        if step_approvals:
            approval_reason = f"Skill requires {', '.join(step_approvals)} approval."
        elif spec.requires_approval_by_default:
            approval_reason = "Tool requires approval by default."
        step_args = {**template.tool_args, **plan_inputs}
        steps.append(
            RuntimeActionStep(
                step_id=f"runtime-skill-step-{uuid4().hex[:12]}",
                plan_id=runtime_plan_id,
                step_index=index,
                action_type=template.action_type,
                tool_name=template.tool_name,
                tool_args=step_args,
                requires_approval=requires_approval,
                approval_reason=approval_reason,
                expected_outputs=template.expected_outputs,
                status="pending",
                result_id=None,
                warnings=[],
                metadata={
                    **template.metadata,
                    "runtime_skill": skill.skill_name,
                    "optional": template.optional,
                    "approval_requirements": step_approvals,
                },
            )
        )
        tool_specs[spec.tool_name] = {
            "required_permissions": spec.required_permissions,
            "side_effect_level": spec.side_effect_level,
            "policy_tags": spec.policy_tags,
        }

    return RuntimeActionPlan(
        plan_id=runtime_plan_id,
        session_id=session_id,
        user_goal=user_goal,
        plan_summary=f"Runtime skill plan: {skill.skill_name}.",
        steps=steps,
        required_approvals=required_approvals,
        expected_artifacts=list(skill.expected_artifacts),
        risk_level=_skill_risk_level(skill, active_registry),
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "runtime_skill": {
                "skill_name": skill.skill_name,
                "description": skill.description,
                "guardrails": skill.guardrails,
            },
            "tool_specs": tool_specs,
        },
    )


def _validate_skill_for_expansion(
    skill: RuntimeSkillSpec,
    registry: RuntimeToolRegistry,
    user_permissions: set[str] | None,
) -> None:
    for tool_name in skill.required_tools:
        if registry.get(tool_name) is None:
            raise RuntimeSkillValidationError(f"Runtime skill references unknown tool: {tool_name}")
    missing_permissions = sorted(
        set(skill.required_permissions) - set(user_permissions or skill.required_permissions)
    )
    if missing_permissions:
        raise RuntimeSkillValidationError(
            "Runtime skill user is missing permissions: " + ", ".join(missing_permissions)
        )
    declared_approvals = set(skill.approval_requirements)
    for template in skill.default_plan_template:
        spec = registry.require(template.tool_name)
        step_approvals = declared_approvals.union(template.approval_requirements)
        approval_type = approval_type_for_tool(spec)
        if spec.side_effect_level == "external_write" and not (
            {"external_write", "integration_sync"}.intersection(step_approvals)
        ):
            raise RuntimeSkillValidationError(
                f"Runtime skill {skill.skill_name} uses external write tool "
                f"{spec.tool_name} without approval requirement."
            )
        if approval_type is not None and approval_type not in step_approvals:
            raise RuntimeSkillValidationError(
                f"Runtime skill {skill.skill_name} uses approval-gated tool "
                f"{spec.tool_name} without {approval_type} approval requirement."
            )


def _skill_risk_level(skill: RuntimeSkillSpec, registry: RuntimeToolRegistry) -> RiskLevel:
    step_approvals = {
        approval
        for template in skill.default_plan_template
        for approval in template.approval_requirements
    }
    if skill.approval_requirements or step_approvals:
        return "high"
    side_effects = {
        registry.require(tool_name).side_effect_level for tool_name in skill.required_tools
    }
    if "external_write" in side_effects:
        return "high"
    if "db_write" in side_effects:
        return "medium"
    return "low"


def _object_schema(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": properties or {},
    }


__all__ = [
    "RuntimeSkillSpec",
    "RuntimeSkillStepTemplate",
    "RuntimeSkillValidationError",
    "expand_skill_to_plan",
    "_object_schema",
]
