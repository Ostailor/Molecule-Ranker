from __future__ import annotations

from typing import Any

from molecule_ranker.product.product_scope import (
    hidden_internal_feature_names,
    release_feature_names,
)
from molecule_ranker.product.schemas import ProductRelease
from molecule_ranker.v3.governance_contract import REQUIRED_GUARDRAILS

DEFAULT_RELEASE_TRACK = "pilot_release"
DEFAULT_RELEASE_VERSION = "0.2.0"
DEFAULT_ENGINE_VERSION = "3.0.0"
DEFAULT_RELEASE_NAME = "Release V0.2 Auth, Users, Organizations, Permissions"
DEFAULT_RELEASE_STAGE = "hosted_alpha_auth"


def build_default_product_release(metadata: dict[str, Any] | None = None) -> ProductRelease:
    return ProductRelease(
        release_track=DEFAULT_RELEASE_TRACK,
        release_version=DEFAULT_RELEASE_VERSION,
        engine_version=DEFAULT_ENGINE_VERSION,
        release_name=DEFAULT_RELEASE_NAME,
        release_stage=DEFAULT_RELEASE_STAGE,
        enabled_user_features=release_feature_names(),
        hidden_internal_features=hidden_internal_feature_names(),
        required_guardrails=list(REQUIRED_GUARDRAILS),
        metadata={
            "dev_track": "Dev V3.0 internal engine",
            "release_track_goal": "Release V1.0 paid pilot app",
            "auth_implemented": True,
            "organizations_implemented": True,
            "role_checks_implemented": True,
            "billing_implemented": False,
            "stripe_implemented": False,
            "live_engine_execution_enabled": False,
            "production_deployment_enabled": False,
            "external_writes_enabled": False,
            "payments_implemented": False,
            **(metadata or {}),
        },
    )


__all__ = [
    "DEFAULT_ENGINE_VERSION",
    "DEFAULT_RELEASE_NAME",
    "DEFAULT_RELEASE_STAGE",
    "DEFAULT_RELEASE_TRACK",
    "DEFAULT_RELEASE_VERSION",
    "build_default_product_release",
]
