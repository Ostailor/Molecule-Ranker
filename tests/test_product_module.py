from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from molecule_ranker.product import (
    PAYMENTS_IMPLEMENTED,
    PRODUCTION_BILLING_ENABLED,
    FeatureAccessError,
    FeatureDisabledError,
    ProductDisclaimer,
    ProductFeatureFlag,
    ProductRelease,
    UsageLimitExceeded,
    build_default_product_release,
    check_usage_allowed,
    default_feature_flags,
    default_product_disclaimers,
    default_usage_limits,
    disclaimer_locations,
    get_feature_flag,
    get_plan_limits,
    hidden_internal_feature_flags,
    is_feature_enabled,
    pricing_model_payload,
    record_usage_event,
    release_default_flags,
    release_visible_feature_flags,
    require_feature,
    set_feature_flag,
    usage_limit_for_plan,
    usage_summary,
)
from molecule_ranker.product.disclaimers import REQUIRED_DISCLAIMER_PHRASES
from molecule_ranker.product.product_scope import (
    HIDDEN_ADMIN_RELEASE_V1_FEATURES,
    PILOT_VISIBLE_FEATURES,
    hidden_feature_admin_requirements,
    release_feature_names,
    release_v1_default_workflow,
)
from molecule_ranker.product.schemas import PilotOrganization, PilotUser, UsageLimit
from molecule_ranker.v3.governance_contract import REQUIRED_GUARDRAILS


def test_product_release_schema_accepts_release_v0_1_app_shell() -> None:
    release = ProductRelease(
        release_track="pilot_release",
        release_version="0.1.0",
        engine_version="3.0.0",
        release_name="Release V0.1 Hosted App Shell",
        release_stage="hosted_alpha",
        enabled_user_features=["view_ranked_candidates"],
        hidden_internal_features=["external_write_integrations"],
        required_guardrails=["no_medical_advice"],
        metadata={"payments_implemented": False},
    )

    assert release.release_track == "pilot_release"
    assert release.release_stage == "hosted_alpha"
    assert release.metadata["payments_implemented"] is False


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            ProductRelease,
            {
                "release_track": "pilot_release",
                "release_version": "0.0.0",
                "engine_version": "3.0.0",
                "release_name": "Release V0.0",
                "release_stage": "production",
            },
        ),
        (
            PilotUser,
            {
                "user_id": "user-1",
                "email": "user@example.com",
                "plan": "enterprise",
                "status": "active",
            },
        ),
        (
            PilotUser,
            {
                "user_id": "user-1",
                "email": "user@example.com",
                "plan": "pilot",
                "status": "pending",
            },
        ),
    ],
)
def test_product_schemas_reject_disallowed_values(model: type, payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model(**payload)


def test_product_schemas_capture_feature_tenant_usage_and_disclaimers() -> None:
    feature = ProductFeatureFlag(
        flag_name="source_backed_ranking",
        description="Allow source-backed ranking.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=True,
    )
    user = PilotUser(
        user_id="user-1",
        email="scientist@example.com",
        name=None,
        organization_name="Pilot Lab",
        role="researcher",
        plan="pilot",
        status="invited",
    )
    org = PilotOrganization(
        organization_id="org-1",
        name="Pilot Lab",
        owner_user_id=user.user_id,
        plan="pilot",
        status="invited",
    )
    limit = UsageLimit(
        plan="pilot",
        max_projects=10,
        max_runs_per_month=100,
        max_codex_tasks_per_month=25,
        max_generated_hypotheses_per_run=25,
        max_result_bundle_exports_per_month=50,
        max_storage_mb=4096,
    )
    disclaimer = ProductDisclaimer(
        disclaimer_id="result-bundle-export",
        location="export",
        text="Research use only.",
        required_acknowledgement=True,
    )

    assert feature.requires_payment is False
    assert user.plan == "pilot"
    assert org.owner_user_id == "user-1"
    assert limit.max_generated_hypotheses_per_run == 25
    assert disclaimer.required_acknowledgement is True


def test_default_release_preserves_dev_engine_guardrails_and_hides_internal_features() -> None:
    release = build_default_product_release()

    assert release.release_track == "pilot_release"
    assert release.release_version == "0.1.0"
    assert release.engine_version == "3.0.0"
    assert release.release_name == "Release V0.1 Hosted App Shell"
    assert release.release_stage == "hosted_alpha"
    assert set(REQUIRED_GUARDRAILS).issubset(release.required_guardrails)
    assert "view_ranked_candidates" in release.enabled_user_features
    assert "external_write_integrations" in release.hidden_internal_features
    assert release.metadata["payments_implemented"] is False
    assert release.metadata["production_deployment_enabled"] is False


def test_feature_flag_defaults_separate_release_visible_from_admin_internal() -> None:
    flags = default_feature_flags()
    visible = release_visible_feature_flags()
    hidden = hidden_internal_feature_flags()

    assert flags
    assert {flag.flag_name for flag in visible} >= {
        "enable_discovery_runs",
        "enable_generated_hypotheses",
        "enable_exports",
        "enable_result_bundle_viewer",
    }
    assert all(flag.release_visible for flag in visible)
    assert all(flag.admin_only for flag in hidden)
    assert get_feature_flag("enable_stripe_billing").requires_payment is True


def test_usage_limits_and_pricing_are_pilot_placeholders_without_payment_implementation() -> None:
    limits = default_usage_limits()
    pilot_limit = get_plan_limits("pilot")
    pricing = pricing_model_payload()

    assert {limit.plan for limit in limits} == {"free_internal", "pilot", "admin"}
    assert pilot_limit.max_projects == 10
    assert pilot_limit.max_runs_per_month == 50
    assert pilot_limit.max_generated_hypotheses_per_run == 100
    assert pilot_limit.max_result_bundle_exports_per_month == 100
    assert pilot_limit.max_storage_mb == 1000
    assert pilot_limit.max_codex_tasks_per_month == 500
    assert pilot_limit.metadata["intended_future_monthly_price_usd"] == 100
    assert pilot_limit.metadata["pricing_copy_finalized"] is False
    assert pilot_limit.metadata["stripe_integrated"] is False
    assert usage_limit_for_plan("pilot") == pilot_limit
    assert PAYMENTS_IMPLEMENTED is False
    assert PRODUCTION_BILLING_ENABLED is False
    assert pricing["payments_implemented"] is False
    assert pricing["production_billing_enabled"] is False


def test_default_disclaimers_block_clinical_lab_and_synthesis_positioning() -> None:
    disclaimers = default_product_disclaimers()
    combined_text = " ".join(disclaimer.text for disclaimer in disclaimers).lower()

    assert {disclaimer.location for disclaimer in disclaimers} >= {
        "landing_page",
        "signup",
        "checkout_later",
        "dashboard",
        "run_creation",
        "generated_hypotheses",
        "result_export",
        "api_response",
    }
    assert disclaimer_locations() == [
        "landing_page",
        "signup",
        "checkout_later",
        "dashboard",
        "run_creation",
        "generated_hypotheses",
        "result_export",
        "api_response",
    ]
    assert all(phrase in combined_text for phrase in REQUIRED_DISCLAIMER_PHRASES)
    assert "not medical advice" in combined_text
    assert "not clinical decision support" in combined_text
    assert "not a regulated medical product" in combined_text
    assert "no patient treatment guidance" in combined_text
    assert "no dosing" in combined_text
    assert "no lab protocols" in combined_text
    assert "no synthesis instructions" in combined_text
    assert "generated molecules and antibodies are computational hypotheses" in combined_text
    assert "evidence and provenance should be independently reviewed" in combined_text


def test_visible_product_scope_contains_only_pilot_safe_features() -> None:
    visible_features = release_feature_names()
    forbidden_visible_features = set(HIDDEN_ADMIN_RELEASE_V1_FEATURES)

    assert visible_features == list(PILOT_VISIBLE_FEATURES)
    assert visible_features == [
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
    ]
    assert forbidden_visible_features.isdisjoint(visible_features)


def test_hidden_release_v1_features_require_admin_access() -> None:
    admin_requirements = hidden_feature_admin_requirements()

    assert set(admin_requirements) == set(HIDDEN_ADMIN_RELEASE_V1_FEATURES)
    assert all(admin_requirements.values())
    assert admin_requirements["external_write_integrations"] is True
    assert admin_requirements["write_approved_live_mode"] is True


def test_release_v1_default_workflow_disables_external_writes_and_antibody_generation() -> None:
    workflow = release_v1_default_workflow()

    assert workflow["workflow"] == "disease_to_result_bundle"
    assert workflow["deployment_modes"] == ("dry_run", "read_only_live")
    assert workflow["generation"] == "optional_limited"
    assert workflow["external_writes_enabled"] is False
    assert workflow["antibody_generation_enabled"] is False
    assert workflow["codex_autonomy"] == "execute_with_approval"
    assert workflow["exports_enabled"] is True
    assert workflow["exports_require_disclaimer"] is True


def test_release_v0_feature_defaults_are_safe() -> None:
    flags = release_default_flags()

    assert flags["enable_hosted_app"] is False
    assert flags["enable_billing_placeholder"] is True
    assert flags["enable_stripe_billing"] is False
    assert flags["enable_discovery_runs"] is True
    assert flags["enable_result_bundle_viewer"] is True
    assert flags["enable_generated_hypotheses"] is True
    assert get_feature_flag("enable_generated_hypotheses").metadata["limit"] == (
        "bounded_per_usage_limits"
    )
    assert flags["enable_antibody_generation"] is False
    assert flags["enable_external_writes"] is False
    assert flags["enable_codex_runtime"] is True
    assert flags["enable_codex_full_auto"] is False
    assert flags["enable_feedback_widget"] is True


def test_risky_feature_flags_are_disabled_by_default() -> None:
    flags = release_default_flags()

    risky_flags = {
        "enable_hosted_app",
        "enable_stripe_billing",
        "enable_antibody_generation",
        "enable_external_integrations",
        "enable_external_writes",
        "enable_codex_full_auto",
        "enable_biologics_viewer",
        "enable_pdf_export",
    }

    assert all(flags[flag_name] is False for flag_name in risky_flags)
    assert is_feature_enabled("enable_external_writes", flags, is_admin=True) is False
    assert is_feature_enabled("enable_antibody_generation", flags, is_admin=True) is False


def test_admin_only_feature_flags_are_blocked_for_normal_user() -> None:
    flags = release_default_flags()

    assert is_feature_enabled("enable_codex_runtime", flags, is_admin=False) is False
    assert is_feature_enabled("enable_codex_runtime", flags, is_admin=True) is True

    with pytest.raises(FeatureAccessError):
        require_feature("enable_codex_runtime", flags, is_admin=False)

    with pytest.raises(FeatureDisabledError):
        require_feature("enable_external_writes", flags, is_admin=True)


def test_set_feature_flag_returns_updated_copy() -> None:
    flags = release_default_flags()
    updated = set_feature_flag(flags, "enable_pdf_export", True)

    assert flags["enable_pdf_export"] is False
    assert updated["enable_pdf_export"] is True
    assert is_feature_enabled("enable_pdf_export", updated, is_admin=False) is True


def test_generated_hypothesis_disclaimer_exists() -> None:
    generated_disclaimer = next(
        disclaimer
        for disclaimer in default_product_disclaimers()
        if disclaimer.location == "generated_hypotheses"
    )

    text = generated_disclaimer.text.lower()
    assert generated_disclaimer.required_acknowledgement is True
    assert "generated hypotheses" in text
    assert "generated molecules" in text
    assert "generated antibodies" in text
    assert "computational hypotheses" in text
    assert "not medical advice" in text


def test_forbidden_product_claims_do_not_appear_in_disclaimers_or_legal_docs() -> None:
    product_text = " ".join(disclaimer.text for disclaimer in default_product_disclaimers())
    legal_text = " ".join(
        path.read_text(encoding="utf-8") for path in Path("docs/legal").glob("*.md")
    )
    combined_text = f"{product_text}\n{legal_text}".lower()

    forbidden_claims = (
        "cures disease",
        "clinically validated",
        "is a clinical decision tool",
        "clinical decision support tool",
        "is a regulated medical product",
        "safe and effective",
        "proven efficacy",
        "validated activity",
        "validated binding",
        "guaranteed manufacturability",
        "guaranteed developability",
    )

    assert all(claim not in combined_text for claim in forbidden_claims)


def test_required_legal_docs_exist_and_include_research_use_boundary() -> None:
    docs_dir = Path("docs/legal")
    required_files = {
        "research_use_disclaimer.md",
        "terms_skeleton.md",
        "privacy_skeleton.md",
        "acceptable_use_policy.md",
        "pilot_agreement_notes.md",
    }

    assert {path.name for path in docs_dir.glob("*.md")} >= required_files

    for filename in required_files:
        text = (docs_dir / filename).read_text(encoding="utf-8").lower()
        assert "research use only" in text
        assert "not medical advice" in text
        assert "not clinical decision support" in text
        assert "not a regulated medical product" in text


def test_pilot_usage_limits_are_enforced() -> None:
    user = PilotUser(
        user_id="pilot-user",
        email="pilot@example.com",
        plan="pilot",
        status="active",
    )

    for _ in range(10):
        record_usage_event(user, "create_project", {})

    check = check_usage_allowed(user, "create_project")
    assert check.allowed is False
    assert check.limit == 10
    assert check.used == 10
    assert check.remaining == 0

    with pytest.raises(UsageLimitExceeded) as exc_info:
        record_usage_event(user, "create_project", {})

    assert "usage limit reached for create_project" in str(exc_info.value)
    assert "plan pilot allows 10" in str(exc_info.value)


def test_usage_summary_reports_counts_limits_and_remaining() -> None:
    user = PilotUser(
        user_id="pilot-user",
        email="pilot@example.com",
        plan="pilot",
        status="active",
    )

    record_usage_event(user, "run_discovery", {})
    record_usage_event(user, "generate_hypotheses", {"amount": 5})
    record_usage_event(user, "export_result", {})
    record_usage_event(user, "codex_task", {"amount": 3})
    record_usage_event(user, "storage_write", {"storage_mb": 25})

    summary = usage_summary(user)

    assert summary["plan"] == "pilot"
    assert summary["admin_bypass"] is False
    assert summary["usage"]["run_discovery"] == 1
    assert summary["usage"]["generate_hypotheses"] == 5
    assert summary["usage"]["export_result"] == 1
    assert summary["usage"]["codex_task"] == 3
    assert summary["usage"]["storage_write"] == 25
    assert summary["limits"]["run_discovery"] == 50
    assert summary["limits"]["storage_write"] == 1000
    assert summary["remaining"]["storage_write"] == 975
    assert summary["events_count"] == 5


def test_admin_usage_bypass_allows_internal_activity() -> None:
    user = PilotUser(
        user_id="admin-user",
        email="admin@example.com",
        plan="admin",
        status="active",
    )

    for _ in range(11):
        record_usage_event(user, "create_project", {})

    check = check_usage_allowed(user, "create_project")
    summary = usage_summary(user)

    assert check.allowed is True
    assert check.remaining is None
    assert summary["admin_bypass"] is True
    assert summary["remaining"]["create_project"] is None


def test_blocked_usage_returns_clear_error() -> None:
    user = PilotUser(
        user_id="pilot-user",
        email="pilot@example.com",
        plan="pilot",
        status="active",
        metadata={"usage": {"counts": {"run_discovery": 50}, "events": []}},
    )

    check = check_usage_allowed(user, "run_discovery")

    assert check.allowed is False
    assert check.error == (
        "usage limit reached for run_discovery: plan pilot allows 50, current usage is 50"
    )
