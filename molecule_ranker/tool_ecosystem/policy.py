from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.runtime_agents.schemas import AutonomyLevel, RuntimeToolSpec
from molecule_ranker.tool_ecosystem.schemas import (
    ToolApproval,
    ToolManifest,
    ToolPackage,
    ToolPackageSource,
    ToolSecurityScan,
)

NetworkAccessLevel = Literal["none", "internal", "external_read", "external_write", "wildcard"]
FilesystemAccessLevel = Literal["none", "read_only", "artifact_write", "project_write", "broad"]
ToolPolicyDecisionStatus = Literal["allowed", "blocked", "approval_required"]

SIDE_EFFECT_RANK: dict[str, int] = {
    "none": 0,
    "external_read": 1,
    "artifact_write": 2,
    "db_write": 3,
    "codex_subprocess": 4,
    "external_write": 5,
}
NETWORK_RANK: dict[str, int] = {
    "none": 0,
    "internal": 1,
    "external_read": 2,
    "external_write": 3,
    "wildcard": 4,
}
FILESYSTEM_RANK: dict[str, int] = {
    "none": 0,
    "read_only": 1,
    "artifact_write": 2,
    "project_write": 3,
    "broad": 4,
}


class ToolPolicyContext(BaseModel):
    user_id: str | None = None
    user_roles: set[str] = Field(default_factory=set)
    user_permissions: set[str] = Field(default_factory=set)
    project_id: str | None = None
    org_id: str | None = None
    autonomy_level: AutonomyLevel = "suggest_only"
    codex_request: bool = False
    approval_actor_user_id: str | None = None
    approval_actor_roles: set[str] = Field(default_factory=set)
    admin_policy_allows_self_approval: bool = False
    explicit_external_write_policy: bool = False
    approved_action_types: set[str] = Field(default_factory=set)


class ToolPolicyDecision(BaseModel):
    status: ToolPolicyDecisionStatus
    reasons: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"


class ToolPolicyConfig(BaseModel):
    allowed_package_sources: set[ToolPackageSource] = Field(
        default_factory=lambda: {"built_in", "local", "internal_registry", "git", "file"}
    )
    disallowed_package_sources: set[ToolPackageSource] = Field(
        default_factory=lambda: {"external_registry"}
    )
    require_security_scan: bool = True
    require_package_approval: bool = True
    required_approval_roles: dict[str, set[str]] = Field(
        default_factory=lambda: {
            "low": {"tool_approver", "tool_admin", "admin"},
            "medium": {"tool_approver", "tool_admin", "admin"},
            "high": {"tool_admin", "admin"},
            "critical": {"admin"},
        }
    )
    allow_self_approval_high_risk: bool = False
    external_write_requires_explicit_policy: bool = True
    external_write_requires_approval: bool = True
    evidence_creation_requires_validator: bool = True
    assay_result_import_requires_validator: bool = True
    generated_molecule_requires_generation_pipeline: bool = True
    codex_visibility_requires_policy_tag: bool = True
    codex_visibility_tag: str = "codex_visible"
    project_tool_allowlists: dict[str, set[str]] = Field(default_factory=dict)
    org_tool_denylists: dict[str, set[str]] = Field(default_factory=dict)
    max_side_effect_by_autonomy: dict[str, str] = Field(
        default_factory=lambda: {
            "observe_only": "none",
            "suggest_only": "none",
            "execute_safe_tools": "artifact_write",
            "execute_with_approval": "external_write",
            "full_auto_restricted": "db_write",
        }
    )
    max_network_access_by_project: dict[str, NetworkAccessLevel] = Field(default_factory=dict)
    max_filesystem_access_by_project: dict[str, FilesystemAccessLevel] = Field(
        default_factory=dict
    )


class ToolPolicyEngine:
    """Deterministic policy evaluator for governed tool packages and Codex visibility."""

    def __init__(self, config: ToolPolicyConfig | None = None) -> None:
        self.config = config or ToolPolicyConfig()

    @classmethod
    def default(cls) -> ToolPolicyEngine:
        return cls(ToolPolicyConfig())

    def evaluate_package(
        self,
        package: ToolPackage,
        manifest: ToolManifest,
        *,
        scan: ToolSecurityScan | None = None,
        approval: ToolApproval | None = None,
        context: ToolPolicyContext | None = None,
    ) -> ToolPolicyDecision:
        ctx = context or ToolPolicyContext()
        reasons: list[str] = []
        approvals: list[str] = []
        reasons.extend(self._package_source_reasons(package))
        reasons.extend(self._scope_reasons(package.package_id, None, ctx))
        if self.config.require_security_scan:
            reasons.extend(_security_scan_reasons(scan))
        if scan is not None and scan.risk_level == "critical":
            reasons.append("critical security scan findings block approval")
        if self.config.require_package_approval:
            if approval is None or approval.approval_status != "approved":
                approvals.append("tool_package_approval")
        reasons.extend(self._approval_role_reasons(package, scan, ctx))
        reasons.extend(self._network_reasons(manifest, ctx))
        reasons.extend(self._filesystem_reasons(manifest, ctx))
        for tool in manifest.tools:
            reasons.extend(self._biomedical_output_reasons(tool))
            if tool.side_effect_level == "external_write":
                external = self._external_write_reasons(tool, ctx)
                reasons.extend(external.blocking)
                approvals.extend(external.approvals)
        return _decision(reasons, approvals)

    def evaluate_tool(
        self,
        tool: RuntimeToolSpec,
        package: ToolPackage,
        manifest: ToolManifest,
        *,
        scan: ToolSecurityScan | None = None,
        approval: ToolApproval | None = None,
        context: ToolPolicyContext | None = None,
    ) -> ToolPolicyDecision:
        ctx = context or ToolPolicyContext()
        reasons: list[str] = []
        approvals: list[str] = []
        reasons.extend(self._package_source_reasons(package))
        reasons.extend(self._scope_reasons(package.package_id, tool.tool_name, ctx))
        if self.config.require_security_scan:
            reasons.extend(_security_scan_reasons(scan))
        if scan is not None and scan.risk_level == "critical":
            reasons.append("critical security scan findings block tool use")
        if self.config.require_package_approval:
            if approval is None or approval.approval_status != "approved":
                approvals.append("tool_package_approval")
        if tool.side_effect_level == "external_write":
            external = self._external_write_reasons(tool, ctx)
            reasons.extend(external.blocking)
            approvals.extend(external.approvals)
        reasons.extend(self._autonomy_reasons(tool, ctx))
        reasons.extend(self._biomedical_output_reasons(tool))
        reasons.extend(self._codex_visibility_reasons(tool, package, ctx))
        return _decision(reasons, approvals)

    def can_approve_package(
        self,
        package: ToolPackage,
        *,
        scan: ToolSecurityScan | None,
        context: ToolPolicyContext,
    ) -> ToolPolicyDecision:
        reasons = self._approval_role_reasons(package, scan, context)
        if self.config.require_security_scan:
            reasons.extend(_security_scan_reasons(scan))
        if scan is not None and scan.risk_level == "critical":
            reasons.append("critical security scan findings block approval")
        return _decision(reasons, [])

    def filter_codex_visible_tools(
        self,
        records: list[
            tuple[
                RuntimeToolSpec,
                ToolPackage,
                ToolManifest,
                ToolSecurityScan | None,
                ToolApproval | None,
            ]
        ],
        *,
        context: ToolPolicyContext,
    ) -> list[RuntimeToolSpec]:
        codex_context = context.model_copy(update={"codex_request": True})
        visible: list[RuntimeToolSpec] = []
        for tool, package, manifest, scan, approval in records:
            decision = self.evaluate_tool(
                tool,
                package,
                manifest,
                scan=scan,
                approval=approval,
                context=codex_context,
            )
            if decision.allowed:
                visible.append(tool)
        return visible

    def _package_source_reasons(self, package: ToolPackage) -> list[str]:
        reasons: list[str] = []
        if package.source in self.config.disallowed_package_sources:
            reasons.append(f"package source is disallowed: {package.source}")
        if package.source not in self.config.allowed_package_sources:
            reasons.append(f"package source is not allowed: {package.source}")
        return reasons

    def _scope_reasons(
        self,
        package_id: str,
        tool_name: str | None,
        context: ToolPolicyContext,
    ) -> list[str]:
        identities = {package_id}
        if tool_name:
            identities.add(tool_name)
        reasons: list[str] = []
        if context.org_id:
            denied = self.config.org_tool_denylists.get(context.org_id, set())
            if identities.intersection(denied):
                reasons.append("org-level tool denylist blocks package/tool")
        if context.project_id:
            allowed = self.config.project_tool_allowlists.get(context.project_id)
            if allowed is not None and not identities.intersection(allowed):
                reasons.append("project-level tool allowlist does not include package/tool")
        return reasons

    def _approval_role_reasons(
        self,
        package: ToolPackage,
        scan: ToolSecurityScan | None,
        context: ToolPolicyContext,
    ) -> list[str]:
        if scan is None:
            risk_level = "high"
        else:
            risk_level = scan.risk_level
        required_roles = self.config.required_approval_roles.get(risk_level, {"tool_admin"})
        actor_roles = set(context.approval_actor_roles or context.user_roles)
        reasons: list[str] = []
        if not actor_roles.intersection(required_roles):
            reasons.append(
                "package approval requires role: " + ", ".join(sorted(required_roles))
            )
        publisher_user_id = str(package.metadata.get("publisher_user_id") or "")
        actor_user_id = context.approval_actor_user_id or context.user_id
        high_risk = risk_level in {"high", "critical"}
        admin_override = (
            context.admin_policy_allows_self_approval
            or self.config.allow_self_approval_high_risk
        )
        if (
            high_risk
            and publisher_user_id
            and actor_user_id == publisher_user_id
            and not admin_override
        ):
            reasons.append("user cannot approve own high-risk package")
        return reasons

    def _network_reasons(
        self,
        manifest: ToolManifest,
        context: ToolPolicyContext,
    ) -> list[str]:
        if context.project_id is None:
            return []
        max_level = self.config.max_network_access_by_project.get(context.project_id)
        if max_level is None:
            return []
        requested = network_access_level(manifest)
        if NETWORK_RANK[requested] > NETWORK_RANK[max_level]:
            return [f"network access {requested} exceeds project maximum {max_level}"]
        return []

    def _filesystem_reasons(
        self,
        manifest: ToolManifest,
        context: ToolPolicyContext,
    ) -> list[str]:
        if context.project_id is None:
            return []
        max_level = self.config.max_filesystem_access_by_project.get(context.project_id)
        if max_level is None:
            return []
        requested = filesystem_access_level(manifest)
        if FILESYSTEM_RANK[requested] > FILESYSTEM_RANK[max_level]:
            return [f"filesystem access {requested} exceeds project maximum {max_level}"]
        return []

    def _external_write_reasons(
        self,
        tool: RuntimeToolSpec,
        context: ToolPolicyContext,
    ) -> _ExternalWritePolicy:
        reasons: list[str] = []
        approvals: list[str] = []
        if (
            self.config.external_write_requires_explicit_policy
            and not context.explicit_external_write_policy
        ):
            reasons.append("external write requires explicit policy")
        if self.config.external_write_requires_approval:
            if not tool.requires_approval_by_default:
                reasons.append("external write tool must require approval by default")
            if "external_write" not in context.approved_action_types:
                approvals.append("external_write")
        return _ExternalWritePolicy(blocking=reasons, approvals=approvals)

    def _autonomy_reasons(
        self,
        tool: RuntimeToolSpec,
        context: ToolPolicyContext,
    ) -> list[str]:
        maximum = self.config.max_side_effect_by_autonomy.get(
            context.autonomy_level,
            "none",
        )
        if SIDE_EFFECT_RANK[tool.side_effect_level] > SIDE_EFFECT_RANK[maximum]:
            return [
                f"tool side effect {tool.side_effect_level} exceeds autonomy maximum {maximum}"
            ]
        return []

    def _biomedical_output_reasons(self, tool: RuntimeToolSpec) -> list[str]:
        tags = set(tool.policy_tags)
        creates = {str(item) for item in _list_metadata(tool.metadata, "creates")}
        reasons: list[str] = []
        if self.config.evidence_creation_requires_validator:
            if "EvidenceItem" in creates or "evidence_creating" in tags:
                if "evidence_import_schema_validated" not in tags:
                    reasons.append("evidence creation requires importer validator")
        if self.config.assay_result_import_requires_validator:
            if "AssayResult" in creates or "assay_result_import" in tags:
                if "experimental_import_schema_validated" not in tags:
                    reasons.append("assay result import requires experimental validator")
        if self.config.generated_molecule_requires_generation_pipeline:
            if "GeneratedMolecule" in creates or "generated_molecule_creation" in tags:
                if "generation_pipeline_schema_validated" not in tags:
                    reasons.append("generated molecule creation requires generation pipeline")
        return reasons

    def _codex_visibility_reasons(
        self,
        tool: RuntimeToolSpec,
        package: ToolPackage,
        context: ToolPolicyContext,
    ) -> list[str]:
        if not context.codex_request:
            return []
        reasons: list[str] = []
        if package.status != "approved":
            reasons.append("Codex can only see approved packages")
        if not set(tool.required_permissions).issubset(context.user_permissions):
            reasons.append("Codex visibility requires user/tool permissions")
        if (
            self.config.codex_visibility_requires_policy_tag
            and self.config.codex_visibility_tag not in set(tool.policy_tags)
            and package.package_id != "builtins"
        ):
            reasons.append("Codex visibility requires policy tag")
        return reasons


class _ExternalWritePolicy(BaseModel):
    blocking: list[str]
    approvals: list[str]


def network_access_level(manifest: ToolManifest) -> NetworkAccessLevel:
    has_external_read = False
    has_external_write = False
    for domain in manifest.external_domains:
        if domain == "*":
            return "wildcard"
        if domain:
            has_external_read = True
    for request in manifest.requested_network_access:
        domain = str(request.get("domain") or request.get("host") or "")
        mode = str(request.get("mode") or request.get("access") or "read").lower()
        if domain == "*" or request.get("wildcard") is True:
            return "wildcard"
        if mode in {"write", "external_write"}:
            has_external_write = True
        elif _is_internal_domain(domain):
            if not has_external_read:
                has_external_read = False
        elif domain:
            has_external_read = True
    if has_external_write:
        return "external_write"
    if has_external_read:
        return "external_read"
    if any(
        _is_internal_domain(str(item.get("domain") or ""))
        for item in manifest.requested_network_access
    ):
        return "internal"
    return "none"


def filesystem_access_level(manifest: ToolManifest) -> FilesystemAccessLevel:
    level: FilesystemAccessLevel = "none"
    for request in manifest.requested_filesystem_access:
        path = str(request.get("path") or request.get("root") or "")
        mode = str(request.get("mode") or request.get("access") or "read").lower()
        profile = str(request.get("profile") or "").lower()
        if path in {"*", "/", "/Users", "/home"} or ".." in path:
            return "broad"
        if mode in {"write", "read_write", "rw"}:
            if profile == "artifact" or "artifact" in path:
                level = _max_filesystem(level, "artifact_write")
            elif "project" in path or path.startswith("/project"):
                level = _max_filesystem(level, "project_write")
            else:
                level = _max_filesystem(level, "broad")
        else:
            level = _max_filesystem(level, "read_only")
    return level


def _security_scan_reasons(scan: ToolSecurityScan | None) -> list[str]:
    if scan is None:
        return ["security scan is required"]
    if scan.status not in {"passed", "warning"}:
        return [f"security scan must pass before use: {scan.status}"]
    return []


def _decision(reasons: list[str], approvals: list[str]) -> ToolPolicyDecision:
    unique_reasons = _unique(reasons)
    unique_approvals = _unique(approvals)
    if unique_reasons:
        return ToolPolicyDecision(
            status="blocked",
            reasons=unique_reasons,
            required_approvals=unique_approvals,
        )
    if unique_approvals:
        return ToolPolicyDecision(
            status="approval_required",
            required_approvals=unique_approvals,
        )
    return ToolPolicyDecision(status="allowed")


def _max_filesystem(
    current: FilesystemAccessLevel,
    candidate: FilesystemAccessLevel,
) -> FilesystemAccessLevel:
    return candidate if FILESYSTEM_RANK[candidate] > FILESYSTEM_RANK[current] else current


def _list_metadata(metadata: dict[str, Any], key: str) -> list[Any]:
    value = metadata.get(key)
    return value if isinstance(value, list) else []


def _is_internal_domain(domain: str) -> bool:
    return domain.endswith(".internal") or domain.endswith(".local") or domain.startswith("10.")


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


__all__ = [
    "FilesystemAccessLevel",
    "NetworkAccessLevel",
    "ToolPolicyConfig",
    "ToolPolicyContext",
    "ToolPolicyDecision",
    "ToolPolicyDecisionStatus",
    "ToolPolicyEngine",
    "filesystem_access_level",
    "network_access_level",
]
