from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ReleaseStage = Literal[
    "local_productization",
    "hosted_alpha",
    "private_beta",
    "paid_pilot",
    "public_pilot",
]
PilotPlan = Literal["free_internal", "pilot", "admin"]
PilotUserStatus = Literal["invited", "active", "suspended", "cancelled"]


class ProductRelease(BaseModel):
    release_track: str
    release_version: str
    engine_version: str
    release_name: str
    release_stage: ReleaseStage
    enabled_user_features: list[str] = Field(default_factory=list)
    hidden_internal_features: list[str] = Field(default_factory=list)
    required_guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductFeatureFlag(BaseModel):
    flag_name: str
    description: str
    default_enabled: bool
    release_visible: bool
    admin_only: bool
    requires_payment: bool
    requires_approval: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class PilotUser(BaseModel):
    user_id: str
    email: str
    name: str | None = None
    organization_name: str | None = None
    role: str | None = None
    plan: PilotPlan
    status: PilotUserStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class PilotOrganization(BaseModel):
    organization_id: str
    name: str
    owner_user_id: str
    plan: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageLimit(BaseModel):
    plan: str
    max_projects: int
    max_runs_per_month: int
    max_codex_tasks_per_month: int
    max_generated_hypotheses_per_run: int
    max_result_bundle_exports_per_month: int
    max_storage_mb: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductDisclaimer(BaseModel):
    disclaimer_id: str
    location: str
    text: str
    required_acknowledgement: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "PilotOrganization",
    "PilotPlan",
    "PilotUser",
    "PilotUserStatus",
    "ProductDisclaimer",
    "ProductFeatureFlag",
    "ProductRelease",
    "ReleaseStage",
    "UsageLimit",
]
