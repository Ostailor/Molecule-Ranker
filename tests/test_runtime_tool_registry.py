from __future__ import annotations

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

EXPECTED_TOOL_NAMES = {
    "create_project",
    "list_projects",
    "show_project",
    "register_artifacts",
    "run_ranking",
    "rerun_ranking",
    "summarize_ranking",
    "run_generation",
    "run_design_loop",
    "benchmark_generation",
    "run_developability",
    "assess_developability_artifact",
    "retrieve_biologics",
    "validate_antibody_sequence",
    "assess_antibody_developability",
    "assess_antibody_novelty",
    "build_biologics_report",
    "run_literature_update",
    "summarize_literature",
    "import_assay_results",
    "link_assay_results",
    "summarize_assay_results",
    "build_graph",
    "query_graph",
    "detect_contradictions",
    "detect_staleness",
    "generate_hypotheses",
    "rank_hypotheses",
    "create_research_questions",
    "build_portfolio_candidates",
    "optimize_portfolio",
    "run_scenarios",
    "create_campaign",
    "plan_campaign",
    "replan_campaign",
    "review_structure_artifacts",
    "run_structure_validation",
    "build_model_dataset",
    "run_model_validation",
    "run_benchmark",
    "freeze_prospective_predictions",
    "run_guardrail_benchmark",
    "run_reproducibility_check",
    "create_review_workspace",
    "create_dossier",
    "create_validation_handoff",
    "add_review_comment",
    "request_followup",
    "health_check_integration",
    "dry_run_sync",
    "run_sync_write_enabled",
    "run_readiness",
    "generate_support_bundle",
    "run_release_check",
    "summarize_artifacts",
    "explain_failure",
    "plan_followup",
    "draft_memo",
    "summarize_biologic_candidate",
    "explain_antibody_liabilities",
    "draft_biologics_review_questions",
    "summarize_antigen_context",
    "draft_biologics_campaign_summary",
}


def test_default_runtime_tool_registry_contains_exact_approved_tools() -> None:
    registry = RuntimeToolRegistry.default()

    assert set(registry.tool_names()) == EXPECTED_TOOL_NAMES
    assert len(registry.list_tools()) == len(EXPECTED_TOOL_NAMES)
    assert registry.get("run_ranking") is not None
    assert registry.require("run_ranking").tool_name == "run_ranking"


def test_all_default_runtime_tool_specs_are_valid_permissioned_and_schema_backed() -> None:
    registry = RuntimeToolRegistry.default()

    for spec in registry.list_tools():
        assert isinstance(spec, RuntimeToolSpec)
        assert spec.input_schema["type"] == "object"
        assert spec.output_schema["type"] == "object"
        assert spec.required_permissions
        assert spec.side_effect_level in {
            "none",
            "artifact_write",
            "db_write",
            "external_read",
            "external_write",
            "codex_subprocess",
        }
        assert spec.metadata["deterministic_entrypoint"]
        assert spec.metadata["runtime_execution"] == "delegate_to_existing_module_or_cli"


def test_external_write_tools_require_explicit_approval() -> None:
    registry = RuntimeToolRegistry.default()
    external_write_tools = [
        spec for spec in registry.list_tools() if spec.side_effect_level == "external_write"
    ]

    assert [spec.tool_name for spec in external_write_tools] == ["run_sync_write_enabled"]
    for spec in external_write_tools:
        assert spec.requires_approval_by_default is True
        assert "external_write" in spec.policy_tags
        assert "approval_required" in spec.policy_tags


def test_codex_tools_cannot_approve_stage_gates_or_campaign_advancement() -> None:
    registry = RuntimeToolRegistry.default()
    codex_tools = registry.by_category("codex")

    assert {spec.tool_name for spec in codex_tools} == {
        "summarize_artifacts",
        "explain_failure",
        "plan_followup",
        "draft_memo",
        "summarize_biologic_candidate",
        "explain_antibody_liabilities",
        "draft_biologics_review_questions",
        "summarize_antigen_context",
        "draft_biologics_campaign_summary",
    }
    for spec in codex_tools:
        assert spec.side_effect_level == "codex_subprocess"
        assert spec.required_permissions == ["codex:run"]
        assert "assistant_output_only" in spec.policy_tags
        assert "stage_gate" not in spec.policy_tags
        assert "campaign_advance" not in spec.policy_tags
        assert "approve" not in spec.tool_name
        assert spec.requires_approval_by_default is False


def test_registry_rejects_duplicate_tool_registration() -> None:
    registry = RuntimeToolRegistry()
    spec = RuntimeToolRegistry.default().require("run_ranking")

    registry.register(spec)

    try:
        registry.register(spec)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("Expected duplicate registration to fail.")
