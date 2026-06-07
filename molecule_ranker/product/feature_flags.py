from __future__ import annotations

from molecule_ranker.product.schemas import ProductFeatureFlag

RELEASE_V0_FLAG_DEFINITIONS: tuple[ProductFeatureFlag, ...] = (
    ProductFeatureFlag(
        flag_name="enable_hosted_app",
        description="Enable the hosted web application shell.",
        default_enabled=False,
        release_visible=False,
        admin_only=True,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_user_accounts",
        description="Enable pilot user accounts.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
    ProductFeatureFlag(
        flag_name="enable_organization_accounts",
        description="Enable pilot organization accounts.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
    ProductFeatureFlag(
        flag_name="enable_billing_placeholder",
        description="Show subscription and billing placeholders without payment processing.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
    ProductFeatureFlag(
        flag_name="enable_stripe_billing",
        description="Enable Stripe billing integration.",
        default_enabled=False,
        release_visible=False,
        admin_only=True,
        requires_payment=True,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_project_dashboard",
        description="Enable the pilot project dashboard.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
    ProductFeatureFlag(
        flag_name="enable_discovery_runs",
        description="Enable bounded discovery runs.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_result_bundle_viewer",
        description="Enable result bundle viewing.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
    ProductFeatureFlag(
        flag_name="enable_candidate_viewer",
        description="Enable ranked candidate viewing.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
    ProductFeatureFlag(
        flag_name="enable_generated_hypotheses",
        description="Enable limited generated hypothesis viewing for pilot users.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=True,
        metadata={"limit": "bounded_per_usage_limits"},
    ),
    ProductFeatureFlag(
        flag_name="enable_biologics_viewer",
        description="Enable biologics result viewing without generation controls.",
        default_enabled=False,
        release_visible=False,
        admin_only=True,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_antibody_generation",
        description="Enable antibody generation controls.",
        default_enabled=False,
        release_visible=False,
        admin_only=True,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_external_integrations",
        description="Enable external integration configuration.",
        default_enabled=False,
        release_visible=False,
        admin_only=True,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_external_writes",
        description="Enable writes to external systems.",
        default_enabled=False,
        release_visible=False,
        admin_only=True,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_admin_dashboard",
        description="Enable administrator dashboard surfaces.",
        default_enabled=True,
        release_visible=False,
        admin_only=True,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_codex_runtime",
        description="Enable approved Codex runtime task execution.",
        default_enabled=True,
        release_visible=False,
        admin_only=True,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_codex_full_auto",
        description="Enable fully autonomous Codex execution.",
        default_enabled=False,
        release_visible=False,
        admin_only=True,
        requires_payment=False,
        requires_approval=True,
    ),
    ProductFeatureFlag(
        flag_name="enable_exports",
        description="Enable result bundle exports with disclaimers.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
    ProductFeatureFlag(
        flag_name="enable_pdf_export",
        description="Enable PDF export when the PDF renderer is available.",
        default_enabled=False,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
        metadata={"enabled_only_if_pdf_renderer_exists": True},
    ),
    ProductFeatureFlag(
        flag_name="enable_usage_limits",
        description="Enable pilot usage limit enforcement and visibility.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
    ProductFeatureFlag(
        flag_name="enable_feedback_widget",
        description="Enable pilot feedback capture.",
        default_enabled=True,
        release_visible=True,
        admin_only=False,
        requires_payment=False,
        requires_approval=False,
    ),
)

DEFAULT_FEATURE_FLAGS = RELEASE_V0_FLAG_DEFINITIONS
_FLAG_INDEX: dict[str, ProductFeatureFlag] = {
    flag.flag_name: flag for flag in RELEASE_V0_FLAG_DEFINITIONS
}


class FeatureDisabledError(RuntimeError):
    pass


class FeatureAccessError(PermissionError):
    pass


def release_default_flags() -> dict[str, bool]:
    return {flag.flag_name: flag.default_enabled for flag in RELEASE_V0_FLAG_DEFINITIONS}


def get_feature_flag(flag_name: str) -> ProductFeatureFlag:
    try:
        return _FLAG_INDEX[flag_name].model_copy(deep=True)
    except KeyError as exc:
        raise KeyError(f"unknown feature flag: {flag_name}") from exc


def set_feature_flag(flags: dict[str, bool], flag_name: str, enabled: bool) -> dict[str, bool]:
    get_feature_flag(flag_name)
    updated = dict(flags)
    updated[flag_name] = enabled
    return updated


def is_feature_enabled(
    flag_name: str,
    flags: dict[str, bool] | None = None,
    *,
    is_admin: bool = False,
) -> bool:
    definition = get_feature_flag(flag_name)
    if definition.admin_only and not is_admin:
        return False
    active_flags = release_default_flags() if flags is None else flags
    return active_flags.get(flag_name, definition.default_enabled)


def require_feature(
    flag_name: str,
    flags: dict[str, bool] | None = None,
    *,
    is_admin: bool = False,
) -> None:
    definition = get_feature_flag(flag_name)
    if definition.admin_only and not is_admin:
        raise FeatureAccessError(f"feature requires admin access: {flag_name}")
    if not is_feature_enabled(flag_name, flags, is_admin=is_admin):
        raise FeatureDisabledError(f"feature is disabled: {flag_name}")


def default_feature_flags() -> list[ProductFeatureFlag]:
    return [flag.model_copy(deep=True) for flag in RELEASE_V0_FLAG_DEFINITIONS]


def release_visible_feature_flags() -> list[ProductFeatureFlag]:
    return [flag for flag in default_feature_flags() if flag.release_visible]


def hidden_internal_feature_flags() -> list[ProductFeatureFlag]:
    return [flag for flag in default_feature_flags() if not flag.release_visible]


__all__ = [
    "DEFAULT_FEATURE_FLAGS",
    "FeatureAccessError",
    "FeatureDisabledError",
    "RELEASE_V0_FLAG_DEFINITIONS",
    "default_feature_flags",
    "get_feature_flag",
    "hidden_internal_feature_flags",
    "is_feature_enabled",
    "release_default_flags",
    "release_visible_feature_flags",
    "require_feature",
    "set_feature_flag",
]
