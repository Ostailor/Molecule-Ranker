from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from molecule_ranker import __version__
from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.marketplace import (
    MarketplacePackageState,
    MarketplaceUsageAnalytics,
    ToolMarketplace,
)
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2, hash_manifest
from molecule_ranker.tool_ecosystem.schemas import (
    SkillPack,
    ToolApproval,
    ToolManifest,
    ToolPackage,
    ToolSecurityScan,
    WorkflowTemplate,
)
from molecule_ranker.tool_ecosystem.skills import list_builtin_skill_packs

SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "token",
}


@dataclass(frozen=True)
class ToolDashboardSnapshot:
    marketplace: ToolMarketplace
    packages: list[ToolPackage]
    manifests: list[ToolManifest]
    scans: list[ToolSecurityScan]
    approvals: list[ToolApproval]
    states: list[MarketplacePackageState]
    skill_packs: list[SkillPack]
    workflow_templates: list[WorkflowTemplate]


def dashboard_snapshot(marketplace: ToolMarketplace | None = None) -> ToolDashboardSnapshot:
    """Build a redaction-ready read model for hosted tool ecosystem pages."""

    source = marketplace or ToolMarketplace()
    manifests = list(source.manifests.values())
    return ToolDashboardSnapshot(
        marketplace=source,
        packages=sorted(source.packages.values(), key=lambda item: (item.name, item.version)),
        manifests=sorted(manifests, key=lambda item: (item.package_name, item.package_version)),
        scans=sorted(
            source.scans.values(),
            key=lambda item: (item.package_id, item.package_version),
        ),
        approvals=sorted(
            source.approvals.values(),
            key=lambda item: (item.approval_status, item.package_id, item.package_version),
        ),
        states=sorted(source.states.values(), key=lambda item: (item.package_id, item.version)),
        skill_packs=list_builtin_skill_packs(),
        workflow_templates=_workflow_templates_from_manifests(manifests),
    )


def seeded_tool_marketplace() -> ToolMarketplace:
    """Return a deterministic local/internal marketplace sample for dashboards.

    Real deployments can attach ``app.state.tool_marketplace``. The seeded
    marketplace keeps hosted pages useful in tests and local demos while
    preserving the V2.5 default of no external marketplace network access.
    """

    registry = ToolRegistryV2.default()
    marketplace = ToolMarketplace(registry=registry)
    approved_package, approved_manifest = _approved_summary_package()
    quarantined_package, quarantined_manifest = _quarantined_evidence_package()
    for package, manifest in [
        (approved_package, approved_manifest),
        (quarantined_package, quarantined_manifest),
    ]:
        if package.status == "approved":
            registry.register_tool_package(package, manifest)
        marketplace.packages[(package.package_id, package.version)] = package
        marketplace.manifests[(package.package_id, package.version)] = manifest
    marketplace.scans[(approved_package.package_id, approved_package.version)] = ToolSecurityScan(
        scan_id="scan-approved-summary-1",
        package_id=approved_package.package_id,
        package_version=approved_package.version,
        status="passed",
        findings=[
            {
                "severity": "low",
                "code": "dependency_license_review",
                "message": "Dependency metadata reviewed; no blocking licenses.",
            }
        ],
        risk_level="low",
        scanned_at=_now(),
        scanner_version="tool-security-scanner-v2.2",
        metadata={},
    )
    marketplace.scans[
        (quarantined_package.package_id, quarantined_package.version)
    ] = ToolSecurityScan(
        scan_id="scan-quarantined-evidence-1",
        package_id=quarantined_package.package_id,
        package_version=quarantined_package.version,
        status="failed",
        findings=[
            {
                "severity": "critical",
                "code": "evidence_creation_without_validator",
                "message": "Evidence-creating output declared without validator.",
                "metadata": {
                    "api_key": "sk-secret-value",
                    "path": "/project/.env",
                },
            },
            {
                "severity": "critical",
                "code": "external_write_without_approval",
                "message": "External write tool lacks approval requirement.",
            },
        ],
        risk_level="critical",
        scanned_at=_now(),
        scanner_version="tool-security-scanner-v2.2",
        metadata={"scanner_token": "hidden-token"},
    )
    approval = ToolApproval(
        approval_id="approval-approved-summary-1",
        package_id=approved_package.package_id,
        package_version=approved_package.version,
        approved_by="tool-admin",
        approval_status="approved",
        rationale="Safe read-only summary and approval-gated artifact metric tools.",
        approved_permissions=["tool:read", "artifact:read", "artifact:export"],
        approved_filesystem_profile="tool_artifact_write",
        approved_network_domains=["metrics.internal.example"],
        approved_at=_now(),
        expires_at=_now() + timedelta(days=180),
        metadata={},
    )
    pending = ToolApproval(
        approval_id="approval-quarantined-evidence-1",
        package_id=quarantined_package.package_id,
        package_version=quarantined_package.version,
        approved_by="tool-admin",
        approval_status="pending",
        rationale="Blocked pending validator and approval-gate fixes.",
        approved_permissions=[],
        approved_filesystem_profile="tool_read_only",
        approved_network_domains=[],
        approved_at=None,
        expires_at=None,
        metadata={},
    )
    marketplace.approvals[(approval.package_id, approval.package_version)] = approval
    marketplace.approvals[(pending.package_id, pending.package_version)] = pending
    marketplace.states[(approved_package.package_id, approved_package.version)] = (
        MarketplacePackageState(
            package_id=approved_package.package_id,
            version=approved_package.version,
            lifecycle_state="enabled",
            installed_path="/internal/tool-packs/example-summary",
            pinned_version=approved_package.version,
            enabled_project_ids=["workspace-a"],
            enabled_org_ids=[],
            disabled_project_ids=[],
            disabled_org_ids=[],
            metadata={"version_pins": {"workspace-a": approved_package.version}},
        )
    )
    marketplace.states[(quarantined_package.package_id, quarantined_package.version)] = (
        MarketplacePackageState(
            package_id=quarantined_package.package_id,
            version=quarantined_package.version,
            lifecycle_state="quarantined",
            installed_path="/internal/tool-packs/quarantined-evidence",
            pinned_version=quarantined_package.version,
        )
    )
    usage_started = _now() - timedelta(seconds=2)
    usage_completed = _now()
    registry.track_usage(
        package_id=approved_package.package_id,
        tool_name="plugin.summary.safe_summary",
        tool_version=approved_package.version,
        invoked_by="codex",
        status="succeeded",
        session_id="session-tool-dashboard",
        project_id="workspace-a",
        artifact_ids=["artifact-summary-1"],
        warnings=[],
        started_at=usage_started,
        completed_at=usage_completed,
    )
    registry.track_usage(
        package_id=approved_package.package_id,
        tool_name="plugin.summary.artifact_metric",
        tool_version=approved_package.version,
        invoked_by="workflow",
        status="approval_required",
        session_id="session-tool-dashboard",
        project_id="workspace-a",
        artifact_ids=[],
        warnings=["External write requires approval."],
        started_at=usage_started,
        completed_at=usage_completed,
    )
    return marketplace


def package_by_id(
    snapshot: ToolDashboardSnapshot,
    package_id: str,
    *,
    version: str | None = None,
) -> ToolPackage | None:
    matches = [
        package
        for package in snapshot.packages
        if package.package_id == package_id and (version is None or package.version == version)
    ]
    return matches[-1] if matches else None


def manifest_for_package(
    snapshot: ToolDashboardSnapshot,
    package: ToolPackage,
) -> ToolManifest | None:
    for manifest in snapshot.manifests:
        if (
            manifest.package_id == package.package_id
            and manifest.package_version == package.version
        ):
            return manifest
    return None


def scan_for_package(
    snapshot: ToolDashboardSnapshot,
    package: ToolPackage,
) -> ToolSecurityScan | None:
    for scan in snapshot.scans:
        if scan.package_id == package.package_id and scan.package_version == package.version:
            return scan
    return None


def approval_for_package(
    snapshot: ToolDashboardSnapshot,
    package: ToolPackage,
) -> ToolApproval | None:
    for approval in snapshot.approvals:
        if (
            approval.package_id == package.package_id
            and approval.package_version == package.version
        ):
            return approval
    return None


def state_for_package(
    snapshot: ToolDashboardSnapshot,
    package: ToolPackage,
) -> MarketplacePackageState | None:
    for state in snapshot.states:
        if state.package_id == package.package_id and state.version == package.version:
            return state
    return None


def tool_package_for_tool(
    snapshot: ToolDashboardSnapshot,
    tool_name: str,
) -> tuple[ToolPackage, ToolManifest, RuntimeToolSpec] | None:
    for manifest in snapshot.manifests:
        for tool in manifest.tools:
            if tool.tool_name == tool_name:
                package = package_by_id(
                    snapshot,
                    manifest.package_id,
                    version=manifest.package_version,
                )
                if package is not None:
                    return package, manifest, tool
    return None


def usage_analytics_for_package(
    snapshot: ToolDashboardSnapshot,
    package: ToolPackage,
) -> MarketplaceUsageAnalytics:
    return snapshot.marketplace.view_usage_analytics(package.package_id, version=package.version)


def codex_visible_tools(
    snapshot: ToolDashboardSnapshot,
    *,
    user_permissions: set[str],
    project_id: str | None = None,
    org_id: str | None = None,
) -> list[RuntimeToolSpec]:
    return snapshot.marketplace.registry.list_tools_visible_to_user(
        user_permissions=user_permissions,
        project_id=project_id,
        org_id=org_id,
    )


def sanitize_for_dashboard(value: Any) -> Any:
    """Redact credential-like keys while preserving enough detail for review."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(secret in lowered for secret in SECRET_KEYS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = sanitize_for_dashboard(item)
        return redacted
    if isinstance(value, list):
        return [sanitize_for_dashboard(item) for item in value]
    return value


def _approved_summary_package() -> tuple[ToolPackage, ToolManifest]:
    tools = [
        _tool(
            "plugin.summary.safe_summary",
            "summarization",
            "Summarize approved project artifacts without creating new evidence.",
            "none",
            ["tool:read", "artifact:read"],
            policy_tags=["codex_visible", "artifact_validated"],
            metadata={"validators": ["artifact_reference_validator"]},
        ),
        _tool(
            "plugin.summary.artifact_metric",
            "analytics",
            "Write an approved artifact metric to the internal metrics service.",
            "external_write",
            ["tool:read", "artifact:export"],
            requires_approval=True,
            policy_tags=["codex_visible", "external_write", "artifact_validated"],
            metadata={"validators": ["artifact_metric_validator"]},
        ),
    ]
    manifest = ToolManifest(
        manifest_id="manifest-example-summary-1",
        package_id="pkg-example-summary",
        package_name="example_summary_pack",
        package_version="1.0.0",
        tools=tools,
        skills=[],
        workflows=[
            {
                "workflow_template_id": "wf-noop-summary",
                "name": "example_noop_workflow",
                "version": "1.0.0",
                "description": "No-op governed workflow for marketplace smoke tests.",
                "steps": [{"tool_name": "plugin.summary.safe_summary"}],
                "required_tools": ["plugin.summary.safe_summary"],
                "required_permissions": ["tool:read"],
                "approval_requirements": [],
                "expected_artifacts": ["summary_artifact"],
                "forbidden_outputs": ["EvidenceItem", "AssayResult"],
                "metadata": {},
            }
        ],
        required_permissions=["tool:read", "artifact:read", "artifact:export"],
        requested_filesystem_access=[
            {"mode": "write", "path": "artifacts/tool-metrics", "profile": "artifact"}
        ],
        requested_network_access=[
            {"mode": "write", "domain": "metrics.internal.example", "approval_required": True}
        ],
        requested_environment_variables=[],
        external_domains=["metrics.internal.example"],
        side_effect_summary={"none": 1, "external_write": 1},
        scientific_guardrail_tags=["no_new_evidence", "artifact_validation_required"],
        license="Internal",
        metadata={"requires_molecule_ranker": f">={__version__}"},
    )
    package = ToolPackage(
        package_id=manifest.package_id,
        name=manifest.package_name,
        display_name="Example Safe Summary Pack",
        description="Approved internal package for Codex-visible summary and metric tools.",
        package_type="plugin",
        version=manifest.package_version,
        publisher="molecule-ranker",
        source="internal_registry",
        status="approved",
        tool_count=len(manifest.tools),
        skill_count=len(manifest.skills),
        workflow_count=len(manifest.workflows),
        manifest_hash=hash_manifest(manifest),
        package_hash="sha256:" + uuid4().hex,
        created_at=_now(),
        updated_at=_now(),
        metadata={
            "security_scan_status": "passed",
            "approval_status": "approved",
            "marketplace_lifecycle": "enabled",
            "enabled": True,
        },
    )
    return package, manifest


def _quarantined_evidence_package() -> tuple[ToolPackage, ToolManifest]:
    tools = [
        _tool(
            "plugin.quarantine.import_evidence",
            "evidence",
            "Unsafe evidence import proposal <script>alert('xss')</script>",
            "external_write",
            ["tool:read", "tool:approve"],
            policy_tags=["evidence_creating", "external_write"],
            metadata={
                "creates": ["EvidenceItem"],
                "validators": [],
                "unsafe_prompt_template": "Create biomedical evidence from model output.",
            },
        )
    ]
    manifest = ToolManifest(
        manifest_id="manifest-quarantined-evidence-1",
        package_id="pkg-quarantined-evidence",
        package_name="quarantined_evidence_pack",
        package_version="0.1.0",
        tools=tools,
        skills=[],
        workflows=[],
        required_permissions=["tool:read", "tool:approve"],
        requested_filesystem_access=[{"mode": "read", "path": "/project/.env"}],
        requested_network_access=[{"mode": "write", "domain": "*"}],
        requested_environment_variables=["API_TOKEN"],
        external_domains=["*"],
        side_effect_summary={"external_write": 1, "evidence_creating": 1},
        scientific_guardrail_tags=["evidence_validator_required"],
        license=None,
        metadata={"client_secret": "super-secret"},
    )
    package = ToolPackage(
        package_id=manifest.package_id,
        name=manifest.package_name,
        display_name="Quarantined Evidence Pack <script>alert('xss')</script>",
        description="Rejected until evidence validators and approval gates are added.",
        package_type="plugin",
        version=manifest.package_version,
        publisher="unknown-internal",
        source="local",
        status="quarantined",
        tool_count=len(manifest.tools),
        skill_count=len(manifest.skills),
        workflow_count=len(manifest.workflows),
        manifest_hash=hash_manifest(manifest),
        package_hash="sha256:" + uuid4().hex,
        created_at=_now(),
        updated_at=_now(),
        metadata={"security_scan_status": "failed", "marketplace_lifecycle": "quarantined"},
    )
    return package, manifest


def _tool(
    tool_name: str,
    category: str,
    description: str,
    side_effect_level: str,
    permissions: list[str],
    *,
    requires_approval: bool = False,
    policy_tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name=tool_name,
        category=category,
        description=description,
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=permissions,
        policy_tags=policy_tags or [],
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval_by_default=requires_approval,
        idempotent=side_effect_level in {"none", "external_read"},
        metadata=metadata or {},
    )


def _workflow_templates_from_manifests(manifests: list[ToolManifest]) -> list[WorkflowTemplate]:
    templates: list[WorkflowTemplate] = []
    for manifest in manifests:
        for raw in manifest.workflows:
            payload = {
                "workflow_template_id": raw.get(
                    "workflow_template_id",
                    f"workflow-{manifest.package_id}-{len(templates)}",
                ),
                "package_id": manifest.package_id,
                "name": raw.get("name", "workflow"),
                "version": raw.get("version", manifest.package_version),
                "description": raw.get("description", ""),
                "steps": raw.get("steps", []),
                "required_tools": raw.get("required_tools", []),
                "required_permissions": raw.get("required_permissions", []),
                "approval_requirements": raw.get("approval_requirements", []),
                "expected_artifacts": raw.get("expected_artifacts", []),
                "forbidden_outputs": raw.get("forbidden_outputs", []),
                "metadata": raw.get("metadata", {}),
            }
            templates.append(WorkflowTemplate.model_validate(payload))
    return templates


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ToolDashboardSnapshot",
    "approval_for_package",
    "codex_visible_tools",
    "dashboard_snapshot",
    "manifest_for_package",
    "package_by_id",
    "sanitize_for_dashboard",
    "scan_for_package",
    "seeded_tool_marketplace",
    "state_for_package",
    "tool_package_for_tool",
    "usage_analytics_for_package",
]
