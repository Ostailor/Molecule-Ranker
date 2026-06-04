from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec

ToolPackageType = Literal[
    "internal",
    "mcp_server",
    "plugin",
    "skill_pack",
    "workflow_pack",
    "connector_pack",
]
ToolPackageSource = Literal[
    "built_in",
    "local",
    "internal_registry",
    "external_registry",
    "git",
    "file",
]
ToolPackageStatus = Literal[
    "discovered",
    "quarantined",
    "scanned",
    "approved",
    "rejected",
    "deprecated",
    "disabled",
]
ToolVersionStatus = Literal["active", "deprecated", "disabled", "rejected"]
ToolSecurityScanStatus = Literal["queued", "running", "passed", "failed", "warning"]
ToolRiskLevel = Literal["low", "medium", "high", "critical"]
ToolApprovalStatus = Literal["pending", "approved", "rejected", "revoked"]
ToolUsageInvoker = Literal["codex", "user", "workflow", "system"]


class ToolEcosystemSchema(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class ToolPackage(ToolEcosystemSchema):
    package_id: str
    name: str
    display_name: str
    description: str
    package_type: ToolPackageType
    version: str
    publisher: str
    source: ToolPackageSource
    status: ToolPackageStatus
    tool_count: int = Field(ge=0)
    skill_count: int = Field(ge=0)
    workflow_count: int = Field(ge=0)
    manifest_hash: str
    package_hash: str | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_package_lifecycle(self) -> ToolPackage:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")
        if self.status == "approved":
            if self.metadata.get("security_scan_status") != "passed":
                raise ValueError("approved tool packages require a passed security scan")
            if self.metadata.get("approval_status") != "approved":
                raise ValueError("approved tool packages require approval")
        if self.status == "scanned" and self.metadata.get("security_scan_status") not in {
            "passed",
            "warning",
        }:
            raise ValueError("scanned tool packages require passed or warning scan metadata")
        return self

    @property
    def quarantined_until_scanned_and_approved(self) -> bool:
        return self.status != "approved"


class ToolManifest(ToolEcosystemSchema):
    manifest_id: str
    package_id: str
    package_name: str
    package_version: str
    tools: list[RuntimeToolSpec] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    requested_filesystem_access: list[dict[str, Any]] = Field(default_factory=list)
    requested_network_access: list[dict[str, Any]] = Field(default_factory=list)
    requested_environment_variables: list[str] = Field(default_factory=list)
    external_domains: list[str] = Field(default_factory=list)
    side_effect_summary: dict[str, Any] = Field(default_factory=dict)
    scientific_guardrail_tags: list[str] = Field(default_factory=list)
    license: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tools")
    @classmethod
    def require_unique_tool_names(cls, value: list[RuntimeToolSpec]) -> list[RuntimeToolSpec]:
        names = [tool.tool_name for tool in value]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError("tool manifest contains duplicate tools: " + ", ".join(duplicates))
        return value


class ToolVersion(ToolEcosystemSchema):
    tool_version_id: str
    package_id: str
    tool_name: str
    version: str
    input_schema_hash: str
    output_schema_hash: str
    implementation_hash: str | None
    status: ToolVersionStatus
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolSecurityScan(ToolEcosystemSchema):
    scan_id: str
    package_id: str
    package_version: str
    status: ToolSecurityScanStatus
    findings: list[dict[str, Any]] = Field(default_factory=list)
    risk_level: ToolRiskLevel
    scanned_at: datetime
    scanner_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_scan_risk_consistency(self) -> ToolSecurityScan:
        if self.status == "passed" and self.risk_level in {"high", "critical"}:
            raise ValueError("passed tool scans cannot have high or critical risk")
        if self.status == "failed" and self.risk_level == "low":
            raise ValueError("failed tool scans must carry at least medium risk")
        return self


class ToolApproval(ToolEcosystemSchema):
    approval_id: str
    package_id: str
    package_version: str
    approved_by: str
    approval_status: ToolApprovalStatus
    rationale: str
    approved_permissions: list[str] = Field(default_factory=list)
    approved_filesystem_profile: str
    approved_network_domains: list[str] = Field(default_factory=list)
    approved_at: datetime | None
    expires_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_approval_lifecycle(self) -> ToolApproval:
        if self.approval_status == "approved" and self.approved_at is None:
            raise ValueError("approved tool approvals require approved_at")
        if self.expires_at is not None and self.approved_at is not None:
            if self.expires_at <= self.approved_at:
                raise ValueError("expires_at must be after approved_at")
        return self


class SkillPack(ToolEcosystemSchema):
    skill_pack_id: str
    package_id: str
    name: str
    version: str
    skills: list[dict[str, Any]] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowTemplate(ToolEcosystemSchema):
    workflow_template_id: str
    package_id: str
    name: str
    version: str
    description: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    approval_requirements: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    forbidden_outputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolUsageRecord(ToolEcosystemSchema):
    usage_id: str
    session_id: str | None
    project_id: str | None
    package_id: str
    tool_name: str
    tool_version: str
    invoked_by: ToolUsageInvoker
    status: str
    started_at: datetime
    completed_at: datetime | None
    artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_usage_timing(self) -> ToolUsageRecord:
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        return self


__all__ = [
    "SkillPack",
    "ToolApproval",
    "ToolApprovalStatus",
    "ToolEcosystemSchema",
    "ToolManifest",
    "ToolPackage",
    "ToolPackageSource",
    "ToolPackageStatus",
    "ToolPackageType",
    "ToolRiskLevel",
    "ToolSecurityScan",
    "ToolSecurityScanStatus",
    "ToolUsageInvoker",
    "ToolUsageRecord",
    "ToolVersion",
    "ToolVersionStatus",
    "WorkflowTemplate",
]
