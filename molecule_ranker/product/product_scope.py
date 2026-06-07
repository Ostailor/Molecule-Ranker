from __future__ import annotations

PILOT_VISIBLE_FEATURES: tuple[str, ...] = (
    "create_project",
    "run_discovery_workflow_from_disease_or_project_goal",
    "view_run_status",
    "view_result_bundle",
    "view_ranked_candidates",
    "view_evidence_provenance",
    "view_generated_hypotheses_if_enabled",
    "save_favorite_candidates",
    "add_notes",
    "export_result_bundle_markdown_json_pdf_if_available",
    "view_usage",
    "manage_account",
    "manage_subscription_placeholder",
)

HIDDEN_ADMIN_RELEASE_V1_FEATURES: tuple[str, ...] = (
    "raw_tool_marketplace",
    "mcp_server_admin",
    "policy_engine_internals",
    "full_governance_dashboard",
    "kill_switches",
    "red_team_suite",
    "deep_repair_internals",
    "generated_antibody_advanced_settings",
    "external_write_integrations",
    "write_approved_live_mode",
    "model_training_ui",
    "docking_advanced_ui",
    "campaign_copilot_autonomy_settings",
    "enterprise_sso",
    "multi_org_enterprise_admin",
)

RELEASE_V1_DEFAULT_WORKFLOW: dict[str, object] = {
    "workflow": "disease_to_result_bundle",
    "deployment_modes": ("dry_run", "read_only_live"),
    "generation": "optional_limited",
    "antibody_generation_enabled": False,
    "external_writes_enabled": False,
    "codex_autonomy": "execute_with_approval",
    "exports_enabled": True,
    "exports_require_disclaimer": True,
}

PRODUCT_POSITIONING = (
    "Molecule Ranker is a research hypothesis generation and evidence-ranking "
    "platform for researchers."
)

NOT_PRODUCT_CLAIMS: tuple[str, ...] = (
    "clinical_decision_tool",
    "cure_finder",
    "lab_protocol_generator",
    "synthesis_planner",
    "regulated_medical_product",
    "autonomous_drug_discovery_claim_engine",
)

USER_FACING_RELEASE_FEATURES = PILOT_VISIBLE_FEATURES
HIDDEN_INTERNAL_FEATURES = HIDDEN_ADMIN_RELEASE_V1_FEATURES


def release_feature_names() -> list[str]:
    return list(PILOT_VISIBLE_FEATURES)


def hidden_internal_feature_names() -> list[str]:
    return list(HIDDEN_ADMIN_RELEASE_V1_FEATURES)


def hidden_feature_admin_requirements() -> dict[str, bool]:
    return {feature: True for feature in HIDDEN_ADMIN_RELEASE_V1_FEATURES}


def release_v1_default_workflow() -> dict[str, object]:
    return dict(RELEASE_V1_DEFAULT_WORKFLOW)


__all__ = [
    "HIDDEN_ADMIN_RELEASE_V1_FEATURES",
    "HIDDEN_INTERNAL_FEATURES",
    "NOT_PRODUCT_CLAIMS",
    "PILOT_VISIBLE_FEATURES",
    "PRODUCT_POSITIONING",
    "RELEASE_V1_DEFAULT_WORKFLOW",
    "USER_FACING_RELEASE_FEATURES",
    "hidden_feature_admin_requirements",
    "hidden_internal_feature_names",
    "release_feature_names",
    "release_v1_default_workflow",
]
