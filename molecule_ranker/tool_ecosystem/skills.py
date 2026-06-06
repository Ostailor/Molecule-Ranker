from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker.runtime_agents.approvals import approval_type_for_tool
from molecule_ranker.runtime_agents.schemas import RiskLevel, RuntimeActionPlan, RuntimeActionStep
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2
from molecule_ranker.tool_ecosystem.schemas import SkillPack


class SkillPackExpansionError(ValueError):
    """Raised when a governed skill cannot be expanded into a runtime plan."""


class SkillStepTemplate(BaseModel):
    action_type: str
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    approval_gates: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolEcosystemSkill(BaseModel):
    skill_id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_artifacts: list[str]
    required_tools: list[str]
    required_permissions: list[str]
    approval_gates: list[str] = Field(default_factory=list)
    guardrails: list[str]
    steps: list[SkillStepTemplate]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_schema")
    @classmethod
    def require_object_input_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("skill input_schema must be a JSON object schema")
        return value

    @model_validator(mode="after")
    def require_steps_and_declared_tools(self) -> ToolEcosystemSkill:
        if not self.steps:
            raise ValueError("skill requires at least one deterministic step")
        template_tools = {step.tool_name for step in self.steps}
        missing = template_tools - set(self.required_tools)
        if missing:
            raise ValueError(
                "skill required_tools must include template tools: "
                + ", ".join(sorted(missing))
            )
        return self

    def as_manifest_entry(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def list_builtin_skill_packs() -> list[SkillPack]:
    return [
        _pack(
            "discovery_basics",
            [
                _skill(
                    "rank_disease",
                    "rank disease",
                    "Rank disease-linked molecules and summarize the source-backed result.",
                    tools=[
                        "builtins.ranking.run_ranking",
                        "builtins.ranking.summarize_ranking",
                    ],
                    permissions=["run:create", "run:read"],
                    artifacts=["ranking_artifact", "ranking_summary"],
                    guardrails=[
                        "Ranking outputs must remain source-backed.",
                        "Codex summaries cannot create biomedical evidence.",
                    ],
                    steps=[
                        _step(
                            "rank_disease",
                            "builtins.ranking.run_ranking",
                            ["ranking_artifact"],
                        ),
                        _step(
                            "summarize_ranking",
                            "builtins.ranking.summarize_ranking",
                            ["ranking_summary"],
                        ),
                    ],
                    inputs={"disease": {"type": "string"}, "project_id": {"type": "string"}},
                ),
                _skill(
                    "literature_update",
                    "literature update",
                    "Refresh literature evidence context and summarize the update.",
                    tools=[
                        "builtins.literature.run_literature_update",
                        "builtins.literature.summarize_literature",
                    ],
                    permissions=["literature:update", "literature:read"],
                    artifacts=["literature_update", "literature_summary"],
                    guardrails=[
                        "Literature summaries must preserve citations and provenance.",
                        "Codex cannot fabricate citations or evidence strength.",
                    ],
                    steps=[
                        _step(
                            "literature_update",
                            "builtins.literature.run_literature_update",
                            ["literature_update"],
                        ),
                        _step(
                            "summarize_literature",
                            "builtins.literature.summarize_literature",
                            ["literature_summary"],
                        ),
                    ],
                    inputs={"query": {"type": "string"}, "project_id": {"type": "string"}},
                ),
                _skill(
                    "create_report",
                    "create report",
                    "Create a reviewed dossier from approved project artifacts.",
                    tools=[
                        "builtins.codex.summarize_artifacts",
                        "builtins.review.create_dossier",
                    ],
                    permissions=["codex:run", "review:write"],
                    artifacts=["artifact_summary", "review_dossier"],
                    guardrails=[
                        "Report content must be derived from approved artifacts.",
                        "Dossiers do not approve stage gates or campaigns.",
                    ],
                    steps=[
                        _step(
                            "summarize_artifacts",
                            "builtins.codex.summarize_artifacts",
                            ["artifact_summary"],
                        ),
                        _step(
                            "create_dossier",
                            "builtins.review.create_dossier",
                            ["review_dossier"],
                        ),
                    ],
                ),
            ],
        ),
        _pack(
            "generation_triage",
            [
                _skill(
                    "design_plan",
                    "design plan",
                    "Build a constrained design-loop plan for generation work.",
                    tools=["builtins.generation.run_design_loop"],
                    permissions=["generation:run"],
                    artifacts=["design_plan"],
                    guardrails=[
                        "Design plans are computational proposals, not approvals.",
                        "Generated molecule follow-up requires review gates.",
                    ],
                    steps=[
                        _step(
                            "design_plan",
                            "builtins.generation.run_design_loop",
                            ["design_plan"],
                        )
                    ],
                    inputs={
                        "project_id": {"type": "string"},
                        "generation_objective_id": {"type": "string"},
                    },
                ),
                _skill(
                    "generation",
                    "generation",
                    "Run molecule generation under governed hypothesis constraints.",
                    tools=["builtins.generation.run_generation"],
                    permissions=["generation:run"],
                    artifacts=["generated_molecule_hypotheses"],
                    guardrails=[
                        "Generated molecules are hypotheses only.",
                        "Do not claim generated molecules are active, safe, or validated.",
                    ],
                    steps=[
                        _step(
                            "generation",
                            "builtins.generation.run_generation",
                            ["generated_molecule_hypotheses"],
                        )
                    ],
                ),
                _skill(
                    "developability",
                    "developability",
                    "Run developability triage and summarize generated molecule risks.",
                    tools=[
                        "builtins.developability.run_developability",
                        "builtins.developability.assess_developability_artifact",
                    ],
                    permissions=["developability:run", "developability:read"],
                    artifacts=["developability_artifact", "developability_summary"],
                    guardrails=[
                        "Developability triage is computational screening.",
                        "Do not present triage scores as assay results.",
                    ],
                    steps=[
                        _step(
                            "run_developability",
                            "builtins.developability.run_developability",
                            ["developability_artifact"],
                        ),
                        _step(
                            "assess_developability",
                            "builtins.developability.assess_developability_artifact",
                            ["developability_summary"],
                        ),
                    ],
                ),
                _skill(
                    "experiment_readiness",
                    "experiment-readiness",
                    "Prepare a validation handoff for human experimental review.",
                    tools=["builtins.review.create_validation_handoff"],
                    permissions=["review:write"],
                    artifacts=["validation_handoff"],
                    guardrails=[
                        "Validation handoff is not experimental approval.",
                        "Codex cannot create assay results or stage-gate approvals.",
                    ],
                    steps=[
                        _step(
                            "experiment_readiness",
                            "builtins.review.create_validation_handoff",
                            ["validation_handoff"],
                        )
                    ],
                ),
            ],
        ),
        _pack(
            "review_and_handoff",
            [
                _skill(
                    "review_workspace",
                    "review workspace",
                    "Create an expert review workspace for project artifacts.",
                    tools=["builtins.review.create_review_workspace"],
                    permissions=["review:write"],
                    artifacts=["review_workspace"],
                    guardrails=[
                        "Review workspaces support expert assessment only.",
                        "Codex cannot approve review outcomes.",
                    ],
                    steps=[
                        _step(
                            "review_workspace",
                            "builtins.review.create_review_workspace",
                            ["review_workspace"],
                        )
                    ],
                ),
                _skill(
                    "dossier",
                    "dossier",
                    "Create a reviewed dossier from project artifacts.",
                    tools=["builtins.review.create_dossier"],
                    permissions=["review:write"],
                    artifacts=["review_dossier"],
                    guardrails=["Dossiers must preserve artifact provenance."],
                    steps=[
                        _step(
                            "dossier",
                            "builtins.review.create_dossier",
                            ["review_dossier"],
                        )
                    ],
                ),
                _skill(
                    "validation_handoff",
                    "high-level validation handoff",
                    "Create a high-level validation handoff for human review.",
                    tools=["builtins.review.create_validation_handoff"],
                    permissions=["review:write"],
                    artifacts=["validation_handoff"],
                    guardrails=[
                        "Validation handoff cannot approve campaigns or stage gates.",
                        "Human review remains required for validation decisions.",
                    ],
                    steps=[
                        _step(
                            "validation_handoff",
                            "builtins.review.create_validation_handoff",
                            ["validation_handoff"],
                        )
                    ],
                ),
            ],
        ),
        _pack(
            "experiment_feedback",
            [
                _skill(
                    "import_results",
                    "import results",
                    "Import validated experimental results through the governed importer.",
                    tools=["builtins.experiments.import_assay_results"],
                    permissions=["experiment:write"],
                    artifacts=["assay_result_import"],
                    approvals=["execute_plan"],
                    guardrails=[
                        "Assay results must come from validated import schemas.",
                        "Codex cannot create assay results directly.",
                    ],
                    steps=[
                        _step(
                            "import_results",
                            "builtins.experiments.import_assay_results",
                            ["assay_result_import"],
                            approvals=["execute_plan"],
                        )
                    ],
                    inputs={
                        "project_id": {"type": "string"},
                        "validated_import_artifact_id": {"type": "string"},
                    },
                ),
                _skill(
                    "link_results",
                    "link results",
                    "Link imported results to ranked molecules and hypotheses.",
                    tools=["builtins.experiments.link_assay_results"],
                    permissions=["experiment:write"],
                    artifacts=["assay_result_links"],
                    approvals=["execute_plan"],
                    guardrails=[
                        "Result links must preserve source result identifiers.",
                        "Codex cannot reinterpret assay outcomes as new evidence.",
                    ],
                    steps=[
                        _step(
                            "link_results",
                            "builtins.experiments.link_assay_results",
                            ["assay_result_links"],
                            approvals=["execute_plan"],
                        )
                    ],
                ),
                _skill(
                    "update_scores",
                    "update scores",
                    "Update ranking scores after validated experimental feedback.",
                    tools=[
                        "builtins.experiments.summarize_assay_results",
                        "builtins.ranking.rerun_ranking",
                    ],
                    permissions=["experiment:read", "run:create"],
                    artifacts=["assay_result_summary", "ranking_artifact"],
                    guardrails=[
                        "Score updates must be traceable to imported results.",
                        "Scores remain model outputs, not evidence records.",
                    ],
                    steps=[
                        _step(
                            "summarize_assay_results",
                            "builtins.experiments.summarize_assay_results",
                            ["assay_result_summary"],
                        ),
                        _step(
                            "rerun_ranking",
                            "builtins.ranking.rerun_ranking",
                            ["ranking_artifact"],
                        ),
                    ],
                ),
                _skill(
                    "active_learning",
                    "active learning",
                    "Run active-learning design update from validated feedback artifacts.",
                    tools=["builtins.generation.run_design_loop"],
                    permissions=["generation:run"],
                    artifacts=["active_learning_design_plan"],
                    guardrails=[
                        "Active learning suggestions are computational hypotheses.",
                        "Do not create generated molecules outside the generation pipeline.",
                    ],
                    steps=[
                        _step(
                            "active_learning",
                            "builtins.generation.run_design_loop",
                            ["active_learning_design_plan"],
                        )
                    ],
                ),
            ],
        ),
        _pack(
            "graph_hypothesis_campaign",
            [
                _skill(
                    "graph_build",
                    "graph build",
                    "Build and check the project knowledge graph.",
                    tools=[
                        "builtins.graph.build_graph",
                        "builtins.graph.detect_contradictions",
                    ],
                    permissions=["graph:build", "graph:read"],
                    artifacts=["knowledge_graph", "contradiction_report"],
                    guardrails=[
                        "Graph edges must preserve provenance.",
                        "Contradictions must be surfaced, not hidden.",
                    ],
                    steps=[
                        _step("graph_build", "builtins.graph.build_graph", ["knowledge_graph"]),
                        _step(
                            "detect_contradictions",
                            "builtins.graph.detect_contradictions",
                            ["contradiction_report"],
                        ),
                    ],
                ),
                _skill(
                    "hypotheses",
                    "hypotheses",
                    "Generate and rank source-grounded hypotheses.",
                    tools=[
                        "builtins.hypotheses.generate_hypotheses",
                        "builtins.hypotheses.rank_hypotheses",
                    ],
                    permissions=["hypotheses:generate", "hypotheses:rank"],
                    artifacts=["hypotheses", "ranked_hypotheses"],
                    guardrails=[
                        "Hypotheses are not EvidenceItem records.",
                        "Ranked hypotheses require source provenance.",
                    ],
                    steps=[
                        _step(
                            "generate_hypotheses",
                            "builtins.hypotheses.generate_hypotheses",
                            ["hypotheses"],
                        ),
                        _step(
                            "rank_hypotheses",
                            "builtins.hypotheses.rank_hypotheses",
                            ["ranked_hypotheses"],
                        ),
                    ],
                ),
                _skill(
                    "portfolio",
                    "portfolio",
                    "Build and optimize a candidate portfolio.",
                    tools=[
                        "builtins.portfolio.build_portfolio_candidates",
                        "builtins.portfolio.optimize_portfolio",
                        "builtins.portfolio.run_scenarios",
                    ],
                    permissions=["portfolio:run"],
                    artifacts=[
                        "portfolio_candidates",
                        "portfolio_optimization",
                        "portfolio_scenarios",
                    ],
                    guardrails=[
                        "Portfolio outputs cannot approve campaigns.",
                        "Scenario outputs must preserve constraints and assumptions.",
                    ],
                    steps=[
                        _step(
                            "build_portfolio_candidates",
                            "builtins.portfolio.build_portfolio_candidates",
                            ["portfolio_candidates"],
                        ),
                        _step(
                            "optimize_portfolio",
                            "builtins.portfolio.optimize_portfolio",
                            ["portfolio_optimization"],
                        ),
                        _step(
                            "run_scenarios",
                            "builtins.portfolio.run_scenarios",
                            ["portfolio_scenarios"],
                        ),
                    ],
                ),
                _skill(
                    "campaign",
                    "campaign",
                    "Prepare a campaign plan without approving campaign advancement.",
                    tools=["builtins.campaign.plan_campaign"],
                    permissions=["campaign:plan"],
                    artifacts=["campaign_plan"],
                    approvals=["campaign_advance", "stage_gate"],
                    guardrails=[
                        "Campaign advancement requires human approval.",
                        "Codex cannot perform stage-gate approvals.",
                    ],
                    steps=[
                        _step(
                            "campaign",
                            "builtins.campaign.plan_campaign",
                            ["campaign_plan"],
                            approvals=["campaign_advance", "stage_gate"],
                        )
                    ],
                ),
            ],
        ),
        _pack(
            "eval_and_readiness",
            [
                _skill(
                    "benchmark",
                    "benchmark",
                    "Run governed benchmark evaluation.",
                    tools=["builtins.evaluation.run_benchmark"],
                    permissions=["evaluation:run"],
                    artifacts=["benchmark_report"],
                    guardrails=["Benchmarks must preserve dataset and split metadata."],
                    steps=[
                        _step(
                            "benchmark",
                            "builtins.evaluation.run_benchmark",
                            ["benchmark_report"],
                        )
                    ],
                ),
                _skill(
                    "guardrail_audit",
                    "guardrail audit",
                    "Run scientific guardrail benchmark audit.",
                    tools=["builtins.evaluation.run_guardrail_benchmark"],
                    permissions=["evaluation:run"],
                    artifacts=["guardrail_audit"],
                    guardrails=["Guardrail audit findings cannot be suppressed by Codex."],
                    steps=[
                        _step(
                            "guardrail_audit",
                            "builtins.evaluation.run_guardrail_benchmark",
                            ["guardrail_audit"],
                        )
                    ],
                ),
                _skill(
                    "reproducibility",
                    "reproducibility",
                    "Run reproducibility checks for governed outputs.",
                    tools=["builtins.evaluation.run_reproducibility_check"],
                    permissions=["evaluation:run"],
                    artifacts=["reproducibility_report"],
                    guardrails=[
                        "Reproducibility failures must be reported as validation failures."
                    ],
                    steps=[
                        _step(
                            "reproducibility",
                            "builtins.evaluation.run_reproducibility_check",
                            ["reproducibility_report"],
                        )
                    ],
                ),
                _skill(
                    "readiness",
                    "readiness",
                    "Run platform readiness checks.",
                    tools=["builtins.admin.run_readiness"],
                    permissions=["admin:readiness"],
                    artifacts=["readiness_report"],
                    guardrails=["Readiness reports cannot waive release gates."],
                    steps=[
                        _step(
                            "readiness",
                            "builtins.admin.run_readiness",
                            ["readiness_report"],
                        )
                    ],
                ),
            ],
        ),
    ]


def validate_skill_pack(
    pack: SkillPack,
    *,
    registry: ToolRegistryV2 | None = None,
) -> None:
    skills = [_coerce_skill(skill) for skill in pack.skills]
    declared_tools = set(pack.required_tools)
    declared_guardrails = set(pack.guardrails)
    for skill in skills:
        missing_tools = set(skill.required_tools) - declared_tools
        if missing_tools:
            raise SkillPackExpansionError(
                f"skill pack {pack.name} is missing declared tools: "
                + ", ".join(sorted(missing_tools))
            )
        missing_guardrails = set(skill.guardrails) - declared_guardrails
        if missing_guardrails:
            raise SkillPackExpansionError(
                f"skill pack {pack.name} is missing declared guardrails: "
                + ", ".join(sorted(missing_guardrails))
            )
        if registry is not None:
            _validate_skill_tools_available(skill, registry)


def get_builtin_skill_pack(name: str) -> SkillPack:
    for pack in list_builtin_skill_packs():
        if pack.name == name or pack.skill_pack_id == f"builtins.skill_pack.{name}":
            return pack
    raise KeyError(f"unknown built-in skill pack: {name}")


def list_builtin_skills() -> list[ToolEcosystemSkill]:
    return [
        _coerce_skill(raw_skill)
        for pack in list_builtin_skill_packs()
        for raw_skill in pack.skills
    ]


def get_builtin_skill(skill_id: str) -> ToolEcosystemSkill:
    for skill in list_builtin_skills():
        if skill.skill_id == skill_id or skill.name == skill_id:
            return skill
    raise KeyError(f"unknown built-in skill: {skill_id}")


def expand_skill_to_plan(
    skill: ToolEcosystemSkill | dict[str, Any] | str,
    *,
    session_id: str,
    user_goal: str,
    inputs: dict[str, Any] | None = None,
    registry: ToolRegistryV2 | None = None,
    user_permissions: set[str] | list[str] | None = None,
    plan_id: str | None = None,
) -> RuntimeActionPlan:
    active_skill = get_builtin_skill(skill) if isinstance(skill, str) else _coerce_skill(skill)
    active_registry = registry or ToolRegistryV2.default()
    _validate_skill_inputs(active_skill, inputs or {})
    _validate_skill_tools_available(active_skill, active_registry)
    _validate_user_permissions(active_skill, user_permissions)

    runtime_plan_id = plan_id or f"tool-skill-plan-{uuid4().hex[:12]}"
    plan_inputs = inputs or {}
    steps: list[RuntimeActionStep] = []
    required_approvals = list(active_skill.approval_gates)
    tool_specs: dict[str, dict[str, Any]] = {}

    for index, template in enumerate(active_skill.steps):
        spec = active_registry.resolve_tool(template.tool_name)
        step_approvals = _dedupe([*active_skill.approval_gates, *template.approval_gates])
        approval_type = approval_type_for_tool(spec)
        if approval_type is not None and approval_type not in step_approvals:
            step_approvals.append(approval_type)
        for approval in step_approvals:
            if approval not in required_approvals:
                required_approvals.append(approval)
        requires_approval = bool(step_approvals or spec.requires_approval_by_default)
        steps.append(
            RuntimeActionStep(
                step_id=f"tool-skill-step-{uuid4().hex[:12]}",
                plan_id=runtime_plan_id,
                step_index=index,
                action_type=template.action_type,
                tool_name=spec.tool_name,
                tool_args={**template.tool_args, **plan_inputs},
                requires_approval=requires_approval,
                approval_reason=_approval_reason(step_approvals, spec.requires_approval_by_default),
                expected_outputs=template.expected_outputs,
                status="pending",
                result_id=None,
                warnings=[],
                metadata={
                    **template.metadata,
                    "skill_id": active_skill.skill_id,
                    "skill_name": active_skill.name,
                    "approval_gates": step_approvals,
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
        plan_summary=f"Skill expansion: {active_skill.name}.",
        steps=steps,
        required_approvals=required_approvals,
        expected_artifacts=active_skill.output_artifacts,
        risk_level=_risk_level(active_skill, active_registry),
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "skill": active_skill.model_dump(mode="json"),
            "codex_selected_skill": True,
            "deterministic_expansion": True,
            "tool_specs": tool_specs,
        },
    )


def _pack(name: str, skills: list[ToolEcosystemSkill]) -> SkillPack:
    required_tools = sorted({tool for skill in skills for tool in skill.required_tools})
    guardrails = sorted({guardrail for skill in skills for guardrail in skill.guardrails})
    return SkillPack(
        skill_pack_id=f"builtins.skill_pack.{name}",
        package_id="builtins.skill_packs",
        name=name,
        version="2.8.0",
        skills=[skill.as_manifest_entry() for skill in skills],
        required_tools=required_tools,
        guardrails=guardrails,
        metadata={
            "source": "built_in",
            "deterministic_expansion": True,
            "codex_selectable": True,
        },
    )


def _skill(
    skill_id: str,
    name: str,
    description: str,
    *,
    tools: list[str],
    permissions: list[str],
    artifacts: list[str],
    guardrails: list[str],
    steps: list[SkillStepTemplate],
    approvals: list[str] | None = None,
    inputs: dict[str, Any] | None = None,
) -> ToolEcosystemSkill:
    return ToolEcosystemSkill(
        skill_id=skill_id,
        name=name,
        description=description,
        input_schema=_object_schema(inputs),
        output_artifacts=artifacts,
        required_tools=tools,
        required_permissions=permissions,
        approval_gates=approvals or [],
        guardrails=guardrails,
        steps=steps,
    )


def _step(
    action_type: str,
    tool_name: str,
    outputs: list[str],
    *,
    approvals: list[str] | None = None,
    args: dict[str, Any] | None = None,
) -> SkillStepTemplate:
    return SkillStepTemplate(
        action_type=action_type,
        tool_name=tool_name,
        tool_args=args or {},
        approval_gates=approvals or [],
        expected_outputs=outputs,
    )


def _coerce_skill(skill: ToolEcosystemSkill | dict[str, Any]) -> ToolEcosystemSkill:
    if isinstance(skill, ToolEcosystemSkill):
        return skill
    return ToolEcosystemSkill.model_validate(skill)


def _validate_skill_tools_available(skill: ToolEcosystemSkill, registry: ToolRegistryV2) -> None:
    for tool_name in skill.required_tools:
        try:
            registry.resolve_tool(tool_name)
        except (KeyError, ValueError) as exc:
            raise SkillPackExpansionError(
                f"skill {skill.skill_id} requires unavailable tool: {tool_name}"
            ) from exc


def _validate_user_permissions(
    skill: ToolEcosystemSkill,
    user_permissions: set[str] | list[str] | None,
) -> None:
    if user_permissions is None:
        return
    missing = sorted(set(skill.required_permissions) - set(user_permissions))
    if missing:
        raise SkillPackExpansionError(
            "skill user is missing permissions: " + ", ".join(missing)
        )


def _validate_skill_inputs(skill: ToolEcosystemSkill, inputs: dict[str, Any]) -> None:
    required = skill.input_schema.get("required", [])
    if isinstance(required, list):
        missing = [field for field in required if field not in inputs]
        if missing:
            raise SkillPackExpansionError(
                f"skill {skill.skill_id} is missing required inputs: "
                + ", ".join(sorted(missing))
            )


def _approval_reason(approvals: list[str], default_required: bool) -> str | None:
    if approvals:
        return "Skill requires approval gates: " + ", ".join(approvals) + "."
    if default_required:
        return "Tool requires approval by default."
    return None


def _risk_level(skill: ToolEcosystemSkill, registry: ToolRegistryV2) -> RiskLevel:
    if skill.approval_gates or any(step.approval_gates for step in skill.steps):
        return "high"
    side_effects = {
        registry.resolve_tool(tool_name).side_effect_level for tool_name in skill.required_tools
    }
    if "external_write" in side_effects:
        return "high"
    if "db_write" in side_effects or "codex_subprocess" in side_effects:
        return "medium"
    return "low"


def _object_schema(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": properties or {},
    }


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "SkillPack",
    "SkillPackExpansionError",
    "SkillStepTemplate",
    "ToolEcosystemSkill",
    "expand_skill_to_plan",
    "get_builtin_skill",
    "get_builtin_skill_pack",
    "list_builtin_skill_packs",
    "list_builtin_skills",
    "validate_skill_pack",
]
