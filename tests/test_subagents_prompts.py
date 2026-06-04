from __future__ import annotations

from molecule_ranker.subagents.prompts import (
    OUTPUT_SCHEMA_REQUIREMENT,
    REQUIRED_GUARDRAIL_REMINDERS,
    builtin_prompt_templates,
    get_prompt_template,
)
from molecule_ranker.subagents.registry import SubagentRegistry, builtin_subagent_profiles


def test_all_prompts_contain_required_guardrails() -> None:
    templates = builtin_prompt_templates()

    assert set(templates) == {
        profile.subagent_id for profile in builtin_subagent_profiles()
    }
    for template in templates.values():
        prompt_text = template.prompt_text()
        for guardrail in REQUIRED_GUARDRAIL_REMINDERS:
            assert guardrail in prompt_text, template.subagent_id
        rendered = template.render_task_prompt(
            objective="Review scoped artifacts.",
            artifact_ids=["artifact-1"],
            allowed_tool_names=["summarize_literature"],
            output_schema={"type": "object", "required": ["summary"]},
        )
        for guardrail in REQUIRED_GUARDRAIL_REMINDERS:
            assert guardrail in rendered, template.subagent_id


def test_all_prompts_include_output_schema_requirement() -> None:
    for template in builtin_prompt_templates().values():
        assert OUTPUT_SCHEMA_REQUIREMENT in template.system_prompt
        assert OUTPUT_SCHEMA_REQUIREMENT in template.task_prompt_template
        assert OUTPUT_SCHEMA_REQUIREMENT in template.output_json_schema_instructions


def test_role_specific_forbidden_actions_present() -> None:
    registry = SubagentRegistry()

    for profile in registry.list_profiles():
        template = get_prompt_template(profile.subagent_id, registry=registry)
        prompt_text = template.prompt_text()
        cannot = [str(item) for item in profile.metadata.get("cannot", [])]

        for forbidden_action in cannot:
            assert forbidden_action in template.forbidden_actions
            assert forbidden_action in prompt_text

    molecule_designer = get_prompt_template("molecule-designer")
    assert "claim activity" in molecule_designer.prompt_text()
    assert "create molecules outside generation pipeline" in molecule_designer.prompt_text()
    campaign_planner = get_prompt_template("campaign-planner")
    assert "create lab protocols" in campaign_planner.prompt_text()
    platform_operator = get_prompt_template("platform-operator")
    assert "access secrets or bypass RBAC" in platform_operator.prompt_text()


def test_prompt_render_includes_artifacts_tools_and_schema() -> None:
    template = get_prompt_template("evidence-reviewer")

    rendered = template.render_task_prompt(
        objective="Check evidence provenance.",
        artifact_ids=["evidence-1"],
        allowed_tool_names=["summarize_literature"],
        output_schema={"type": "object", "required": ["summary"]},
        artifact_summaries=[{"artifact_id": "evidence-1", "summary": "PubMed summary."}],
    )

    assert "Check evidence provenance." in rendered
    assert "evidence-1" in rendered
    assert "summarize_literature" in rendered
    assert '"required": ["summary"]' in rendered
    assert "PubMed summary." in rendered
