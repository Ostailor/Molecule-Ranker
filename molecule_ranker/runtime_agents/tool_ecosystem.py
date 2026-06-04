from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.runtime_agents.skills import RuntimeSkillSpec

ToolPackageStatus = Literal["draft", "submitted", "approved", "rejected", "revoked"]
ToolScanStatus = Literal["pending", "passed", "failed"]
ToolApprovalStatus = Literal["pending", "approved", "rejected", "revoked"]
SandboxProfile = Literal["read_only", "artifact_write", "controlled_worker", "external_write"]


class ToolManifest(BaseModel):
    package_id: str
    name: str
    description: str
    version: str
    tools: list[RuntimeToolSpec] = Field(default_factory=list)
    skill_pack_ids: list[str] = Field(default_factory=list)
    workflow_template_ids: list[str] = Field(default_factory=list)
    mcp_namespace: str
    required_runtime_version: str = ">=2.2.0"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tools")
    @classmethod
    def require_declared_tools(cls, value: list[RuntimeToolSpec]) -> list[RuntimeToolSpec]:
        if not value:
            raise ValueError("tool manifests must declare at least one tool")
        names = [tool.tool_name for tool in value]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError("tool manifests contain duplicate tools: " + ", ".join(duplicates))
        return value


class ToolVersion(BaseModel):
    package_id: str
    version: str
    manifest_hash: str
    signature: str
    signed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    signer: str = "molecule-ranker-plugin-sdk"

    @model_validator(mode="after")
    def require_hash_bound_signature(self) -> ToolVersion:
        expected = f"sha256:{self.manifest_hash}"
        if self.signature != expected:
            raise ValueError("tool package signature must match manifest hash")
        return self


class ToolSecurityScan(BaseModel):
    scan_id: str = Field(default_factory=lambda: f"tool-scan-{uuid4().hex[:12]}")
    package_id: str
    version: str
    status: ToolScanStatus
    scanner: str = "molecule-ranker-tool-security"
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    findings: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_no_blocking_findings_when_passed(self) -> ToolSecurityScan:
        blocking = [
            finding
            for finding in self.findings
            if str(finding.get("severity", "")).lower() in {"high", "critical"}
        ]
        if self.status == "passed" and blocking:
            raise ValueError("passed tool security scans cannot contain high/critical findings")
        return self


class ToolApproval(BaseModel):
    approval_id: str = Field(default_factory=lambda: f"tool-approval-{uuid4().hex[:12]}")
    package_id: str
    version: str
    manifest_hash: str
    status: ToolApprovalStatus
    approved_by: str | None = None
    approved_at: datetime | None = None
    scan_id: str | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def require_human_approval_actor(self) -> ToolApproval:
        if self.status == "approved":
            if not self.approved_by or self.approved_by.lower().startswith("codex"):
                raise ValueError("approved tool packages require a non-Codex approver")
            if self.approved_at is None:
                raise ValueError("approved tool packages require approved_at")
        return self


class ToolPolicy(BaseModel):
    tool_name: str
    required_permissions: list[str] = Field(default_factory=list)
    sandbox_profile: SandboxProfile
    allowed_org_ids: list[str] = Field(default_factory=list)
    allowed_project_ids: list[str] = Field(default_factory=list)
    allowed_user_ids: list[str] = Field(default_factory=list)
    denied_permissions: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    policy_tags: list[str] = Field(default_factory=list)

    def allows(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        user_permissions: set[str] | None = None,
    ) -> bool:
        permissions = user_permissions or set()
        if self.allowed_org_ids and org_id not in self.allowed_org_ids:
            return False
        if self.allowed_project_ids and project_id not in self.allowed_project_ids:
            return False
        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            return False
        if self.denied_permissions and permissions.intersection(self.denied_permissions):
            return False
        if self.required_permissions and not set(self.required_permissions).issubset(permissions):
            return False
        return True


class WorkflowTemplate(BaseModel):
    template_id: str
    name: str
    description: str
    skill_names: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    default_inputs: dict[str, Any] = Field(default_factory=dict)
    guardrails: list[str] = Field(default_factory=list)


class SkillPack(BaseModel):
    pack_id: str
    name: str
    description: str
    skills: list[RuntimeSkillSpec] = Field(default_factory=list)
    workflow_templates: list[WorkflowTemplate] = Field(default_factory=list)

    @field_validator("skills")
    @classmethod
    def require_skills(cls, value: list[RuntimeSkillSpec]) -> list[RuntimeSkillSpec]:
        if not value:
            raise ValueError("skill packs must declare at least one skill")
        return value


class ToolUsageEval(BaseModel):
    eval_id: str = Field(default_factory=lambda: f"tool-usage-eval-{uuid4().hex[:12]}")
    package_id: str
    version: str
    tool_name: str
    plan_quality_score: float = Field(ge=0, le=1)
    execution_success_rate: float = Field(ge=0, le=1)
    failure_recovery_score: float = Field(ge=0, le=1)
    policy_violation_rate: float = Field(ge=0, le=1)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: list[str] = Field(default_factory=list)

    @property
    def passes_quality_gate(self) -> bool:
        return (
            self.plan_quality_score >= 0.8
            and self.execution_success_rate >= 0.8
            and self.failure_recovery_score >= 0.7
            and self.policy_violation_rate == 0
        )


class ToolPackage(BaseModel):
    manifest: ToolManifest
    version: ToolVersion
    status: ToolPackageStatus = "submitted"
    security_scan: ToolSecurityScan | None = None
    approval: ToolApproval | None = None
    policies: list[ToolPolicy] = Field(default_factory=list)
    skill_packs: list[SkillPack] = Field(default_factory=list)
    usage_evals: list[ToolUsageEval] = Field(default_factory=list)
    marketplace_summary: str | None = None
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_matching_version_records(self) -> ToolPackage:
        if self.manifest.package_id != self.version.package_id:
            raise ValueError("tool package version package_id does not match manifest")
        if self.manifest.version != self.version.version:
            raise ValueError("tool package version does not match manifest")
        if self.version.manifest_hash != manifest_hash(self.manifest):
            raise ValueError("tool package manifest hash does not match manifest contents")
        return self

    @property
    def is_approved(self) -> bool:
        return (
            self.status == "approved"
            and self.security_scan is not None
            and self.security_scan.status == "passed"
            and self.approval is not None
            and self.approval.status == "approved"
            and self.approval.manifest_hash == self.version.manifest_hash
        )

    def approved_tool_specs(self) -> list[RuntimeToolSpec]:
        if not self.is_approved:
            raise ValueError("tool package is not approved for registry installation")
        policy_by_tool = {policy.tool_name: policy for policy in self.policies}
        approved_specs: list[RuntimeToolSpec] = []
        for spec in self.manifest.tools:
            policy = policy_by_tool.get(spec.tool_name)
            metadata = {
                **spec.metadata,
                "tool_package": {
                    "package_id": self.manifest.package_id,
                    "version": self.manifest.version,
                    "manifest_hash": self.version.manifest_hash,
                    "signature": self.version.signature,
                    "approval_id": self.approval.approval_id if self.approval else None,
                    "approval_status": self.approval.status if self.approval else None,
                    "security_scan_id": self.security_scan.scan_id if self.security_scan else None,
                    "security_scan_status": self.security_scan.status
                    if self.security_scan
                    else None,
                    "mcp_namespace": self.manifest.mcp_namespace,
                    "status": self.status,
                },
            }
            if policy is not None:
                metadata["tool_policy"] = policy.model_dump(mode="json")
            approved_specs.append(spec.model_copy(update={"metadata": metadata}))
        return approved_specs


class ToolMarketplace(BaseModel):
    marketplace_id: str = "internal"
    packages: dict[str, ToolPackage] = Field(default_factory=dict)

    def submit(self, package: ToolPackage) -> None:
        key = _package_key(package.manifest.package_id, package.manifest.version)
        if key in self.packages:
            raise ValueError(f"tool package already exists in marketplace: {key}")
        self.packages[key] = package

    def get(self, package_id: str, version: str) -> ToolPackage:
        key = _package_key(package_id, version)
        try:
            return self.packages[key]
        except KeyError as exc:
            raise KeyError(f"unknown tool package: {key}") from exc

    def approve(
        self,
        package_id: str,
        version: str,
        *,
        scan: ToolSecurityScan,
        approved_by: str,
        rationale: str,
    ) -> ToolPackage:
        package = self.get(package_id, version)
        if scan.status != "passed":
            raise ValueError("only passed security scans can be approved")
        approval = ToolApproval(
            package_id=package_id,
            version=version,
            manifest_hash=package.version.manifest_hash,
            status="approved",
            approved_by=approved_by,
            approved_at=datetime.now(UTC),
            scan_id=scan.scan_id,
            rationale=rationale,
        )
        updated = package.model_copy(
            update={"security_scan": scan, "approval": approval, "status": "approved"}
        )
        self.packages[_package_key(package_id, version)] = updated
        return updated

    def discover(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        user_permissions: set[str] | None = None,
    ) -> list[ToolPackage]:
        packages: list[ToolPackage] = []
        for package in self.packages.values():
            if not package.is_approved:
                continue
            if all(
                policy.allows(
                    org_id=org_id,
                    project_id=project_id,
                    user_id=user_id,
                    user_permissions=user_permissions,
                )
                for policy in package.policies
            ):
                packages.append(package)
        return packages

    def install_approved_package(self, registry: Any, package_id: str, version: str) -> None:
        package = self.get(package_id, version)
        for spec in package.approved_tool_specs():
            registry.register(spec)


class MCPGateway:
    """Internal MCP-compatible facade over the approved runtime tool catalog."""

    def __init__(self, registry: Any) -> None:
        self.registry = registry

    def list_tools(
        self,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        user_permissions: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        specs = self.registry.discover_approved_tools(
            org_id=org_id,
            project_id=project_id,
            user_id=user_id,
            user_permissions=user_permissions,
        )
        return [self._mcp_descriptor(spec) for spec in specs]

    def get_tool(self, tool_name: str) -> dict[str, Any]:
        return self._mcp_descriptor(self.registry.require(tool_name))

    def select_tools_for_goal(
        self,
        goal: str,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        user_permissions: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        tokens = {token for token in goal.lower().replace("_", " ").split() if len(token) > 3}
        descriptors = self.list_tools(
            org_id=org_id,
            project_id=project_id,
            user_id=user_id,
            user_permissions=user_permissions,
        )
        selected = [
            descriptor
            for descriptor in descriptors
            if tokens.intersection(
                str(descriptor["name"]).lower().replace("_", " ").split()
            )
            or tokens.intersection(str(descriptor["description"]).lower().split())
        ]
        return selected or descriptors

    @staticmethod
    def _mcp_descriptor(spec: RuntimeToolSpec) -> dict[str, Any]:
        return {
            "name": spec.tool_name,
            "description": spec.description,
            "inputSchema": spec.input_schema,
            "annotations": {
                "category": spec.category,
                "required_permissions": spec.required_permissions,
                "policy_tags": spec.policy_tags,
                "side_effect_level": spec.side_effect_level,
                "requires_approval": spec.requires_approval_by_default,
                "tool_package": spec.metadata.get("tool_package"),
                "tool_policy": spec.metadata.get("tool_policy"),
            },
        }


class PluginSDK:
    """Build and verify internal molecule-ranker tool packs."""

    @staticmethod
    def build_manifest(
        *,
        package_id: str,
        name: str,
        description: str,
        version: str,
        tools: Iterable[RuntimeToolSpec],
        mcp_namespace: str,
        skill_pack_ids: list[str] | None = None,
        workflow_template_ids: list[str] | None = None,
    ) -> ToolManifest:
        return ToolManifest(
            package_id=package_id,
            name=name,
            description=description,
            version=version,
            tools=list(tools),
            skill_pack_ids=skill_pack_ids or [],
            workflow_template_ids=workflow_template_ids or [],
            mcp_namespace=mcp_namespace,
        )

    @staticmethod
    def sign_manifest(manifest: ToolManifest) -> ToolVersion:
        digest = manifest_hash(manifest)
        return ToolVersion(
            package_id=manifest.package_id,
            version=manifest.version,
            manifest_hash=digest,
            signature=f"sha256:{digest}",
        )

    @staticmethod
    def package_tool_pack(
        *,
        manifest: ToolManifest,
        policies: list[ToolPolicy],
        skill_packs: list[SkillPack] | None = None,
        marketplace_summary: str | None = None,
    ) -> ToolPackage:
        return ToolPackage(
            manifest=manifest,
            version=PluginSDK.sign_manifest(manifest),
            policies=policies,
            skill_packs=skill_packs or [],
            marketplace_summary=marketplace_summary,
        )


def manifest_hash(manifest: ToolManifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"metadata"})
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _package_key(package_id: str, version: str) -> str:
    return f"{package_id}@{version}"


__all__ = [
    "MCPGateway",
    "PluginSDK",
    "SkillPack",
    "ToolApproval",
    "ToolManifest",
    "ToolMarketplace",
    "ToolPackage",
    "ToolPolicy",
    "ToolSecurityScan",
    "ToolUsageEval",
    "ToolVersion",
    "WorkflowTemplate",
    "manifest_hash",
]
