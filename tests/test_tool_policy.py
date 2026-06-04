from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.policy import (
    ToolPolicyConfig,
    ToolPolicyContext,
    ToolPolicyEngine,
)
from molecule_ranker.tool_ecosystem.registry import hash_manifest
from molecule_ranker.tool_ecosystem.schemas import (
    ToolApproval,
    ToolManifest,
    ToolPackage,
    ToolSecurityScan,
)

NOW = datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_org_denylist_wins_over_project_allowlist() -> None:
    tool = _tool("plugin.policy.safe_summary", policy_tags=["codex_visible"])
    manifest = _manifest([tool])
    package = _package(manifest)
    engine = ToolPolicyEngine(
        ToolPolicyConfig(
            project_tool_allowlists={"project-1": {"plugin.policy.safe_summary"}},
            org_tool_denylists={"org-1": {"plugin.policy.safe_summary"}},
        )
    )

    decision = engine.evaluate_tool(
        tool,
        package,
        manifest,
        scan=_scan(package),
        approval=_approval(package),
        context=ToolPolicyContext(
            project_id="project-1",
            org_id="org-1",
            user_permissions={"plugin:run"},
        ),
    )

    assert decision.status == "blocked"
    assert "org-level tool denylist blocks package/tool" in decision.reasons


def test_high_risk_package_requires_admin_approval() -> None:
    tool = _tool("plugin.policy.safe_summary")
    manifest = _manifest([tool])
    package = _package(manifest)
    high_risk_scan = _scan(package, status="warning", risk_level="high")
    engine = ToolPolicyEngine.default()

    blocked = engine.can_approve_package(
        package,
        scan=high_risk_scan,
        context=ToolPolicyContext(
            approval_actor_user_id="approver-1",
            approval_actor_roles={"tool_approver"},
        ),
    )
    allowed = engine.can_approve_package(
        package,
        scan=high_risk_scan,
        context=ToolPolicyContext(
            approval_actor_user_id="admin-1",
            approval_actor_roles={"admin"},
        ),
    )

    assert blocked.status == "blocked"
    assert "package approval requires role: admin, tool_admin" in blocked.reasons
    assert allowed.status == "allowed"


def test_user_cannot_approve_own_high_risk_package_unless_admin_policy_allows() -> None:
    tool = _tool("plugin.policy.safe_summary")
    manifest = _manifest([tool])
    package = _package(manifest, metadata={"publisher_user_id": "user-1"})
    high_risk_scan = _scan(package, status="warning", risk_level="high")
    engine = ToolPolicyEngine.default()

    blocked = engine.can_approve_package(
        package,
        scan=high_risk_scan,
        context=ToolPolicyContext(
            approval_actor_user_id="user-1",
            approval_actor_roles={"admin"},
        ),
    )
    allowed = engine.can_approve_package(
        package,
        scan=high_risk_scan,
        context=ToolPolicyContext(
            approval_actor_user_id="user-1",
            approval_actor_roles={"admin"},
            admin_policy_allows_self_approval=True,
        ),
    )

    assert blocked.status == "blocked"
    assert "user cannot approve own high-risk package" in blocked.reasons
    assert allowed.status == "allowed"


def test_autonomy_limits_tool_side_effects() -> None:
    tool = _tool(
        "plugin.policy.external_writer",
        side_effect_level="external_write",
        requires_approval=True,
    )
    manifest = _manifest([tool], requested_network_access=[{"mode": "write", "domain": "api"}])
    package = _package(manifest)
    engine = ToolPolicyEngine.default()

    blocked = engine.evaluate_tool(
        tool,
        package,
        manifest,
        scan=_scan(package),
        approval=_approval(package),
        context=ToolPolicyContext(
            autonomy_level="execute_safe_tools",
            user_permissions={"plugin:run"},
            explicit_external_write_policy=True,
            approved_action_types={"external_write"},
        ),
    )
    allowed = engine.evaluate_tool(
        tool,
        package,
        manifest,
        scan=_scan(package),
        approval=_approval(package),
        context=ToolPolicyContext(
            autonomy_level="execute_with_approval",
            user_permissions={"plugin:run"},
            explicit_external_write_policy=True,
            approved_action_types={"external_write"},
        ),
    )

    assert blocked.status == "blocked"
    assert "tool side effect external_write exceeds autonomy maximum artifact_write" in (
        blocked.reasons
    )
    assert allowed.status == "allowed"


def test_codex_visibility_restricted_by_policy() -> None:
    visible_tool = _tool("plugin.policy.codex_visible", policy_tags=["codex_visible"])
    untagged_tool = _tool("plugin.policy.untagged")
    no_permission_tool = _tool(
        "plugin.policy.hidden_permission",
        required_permissions=["hidden:run"],
        policy_tags=["codex_visible"],
    )
    unapproved_tool = _tool("plugin.policy.unapproved", policy_tags=["codex_visible"])
    manifest = _manifest([visible_tool, untagged_tool, no_permission_tool])
    package = _package(manifest)
    unapproved_manifest = _manifest([unapproved_tool], package_id="unapproved-package")
    unapproved_package = _package(unapproved_manifest, status="quarantined")
    engine = ToolPolicyEngine.default()

    visible = engine.filter_codex_visible_tools(
        [
            (visible_tool, package, manifest, _scan(package), _approval(package)),
            (untagged_tool, package, manifest, _scan(package), _approval(package)),
            (no_permission_tool, package, manifest, _scan(package), _approval(package)),
            (
                unapproved_tool,
                unapproved_package,
                unapproved_manifest,
                _scan(unapproved_package),
                None,
            ),
        ],
        context=ToolPolicyContext(
            codex_request=True,
            user_permissions={"plugin:run"},
            autonomy_level="execute_safe_tools",
        ),
    )

    assert [tool.tool_name for tool in visible] == ["plugin.policy.codex_visible"]


def test_external_write_requires_explicit_policy_and_approval() -> None:
    tool = _tool(
        "plugin.policy.external_writer",
        side_effect_level="external_write",
        requires_approval=True,
    )
    manifest = _manifest([tool])
    package = _package(manifest)
    engine = ToolPolicyEngine.default()

    decision = engine.evaluate_tool(
        tool,
        package,
        manifest,
        scan=_scan(package),
        approval=_approval(package),
        context=ToolPolicyContext(
            autonomy_level="execute_with_approval",
            user_permissions={"plugin:run"},
        ),
    )

    assert decision.status == "blocked"
    assert "external write requires explicit policy" in decision.reasons
    assert "external_write" in decision.required_approvals


def _tool(
    tool_name: str,
    *,
    required_permissions: list[str] | None = None,
    policy_tags: list[str] | None = None,
    side_effect_level: str = "none",
    requires_approval: bool = False,
    metadata: dict[str, object] | None = None,
) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name=tool_name,
        category="plugin",
        description="Policy test tool.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=required_permissions or ["plugin:run"],
        policy_tags=policy_tags or [],
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval_by_default=requires_approval,
        idempotent=side_effect_level in {"none", "external_read"},
        metadata=metadata or {},
    )


def _manifest(
    tools: list[RuntimeToolSpec],
    *,
    package_id: str = "policy-package",
    requested_network_access: list[dict[str, object]] | None = None,
) -> ToolManifest:
    return ToolManifest(
        manifest_id=f"{package_id}-manifest",
        package_id=package_id,
        package_name=package_id,
        package_version="1.0.0",
        tools=tools,
        skills=[],
        workflows=[],
        required_permissions=sorted(
            {permission for tool in tools for permission in tool.required_permissions}
        ),
        requested_filesystem_access=[],
        requested_network_access=requested_network_access or [],
        requested_environment_variables=[],
        external_domains=[],
        side_effect_summary={},
        scientific_guardrail_tags=["no_evidence_creation"],
        license=None,
        metadata={},
    )


def _package(
    manifest: ToolManifest,
    *,
    status: str = "approved",
    metadata: dict[str, object] | None = None,
) -> ToolPackage:
    package_metadata = {
        "security_scan_status": "passed",
        "approval_status": "approved",
        **(metadata or {}),
    }
    if status != "approved":
        package_metadata = metadata or {}
    return ToolPackage(
        package_id=manifest.package_id,
        name=manifest.package_name,
        display_name=manifest.package_name,
        description="Policy package.",
        package_type="plugin",
        version=manifest.package_version,
        publisher="internal",
        source="internal_registry",
        status=status,  # type: ignore[arg-type]
        tool_count=len(manifest.tools),
        skill_count=0,
        workflow_count=0,
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=NOW,
        updated_at=NOW,
        metadata=package_metadata,
    )


def _scan(
    package: ToolPackage,
    *,
    status: str = "passed",
    risk_level: str = "low",
) -> ToolSecurityScan:
    return ToolSecurityScan(
        scan_id=f"{package.package_id}-scan",
        package_id=package.package_id,
        package_version=package.version,
        status=status,  # type: ignore[arg-type]
        findings=[],
        risk_level=risk_level,  # type: ignore[arg-type]
        scanned_at=NOW,
        scanner_version="test",
        metadata={},
    )


def _approval(package: ToolPackage) -> ToolApproval:
    return ToolApproval(
        approval_id=f"{package.package_id}-approval",
        package_id=package.package_id,
        package_version=package.version,
        approved_by="admin",
        approval_status="approved",
        rationale="test",
        approved_permissions=["plugin:run"],
        approved_filesystem_profile="tool_read_only",
        approved_network_domains=[],
        approved_at=NOW,
        expires_at=None,
        metadata={},
    )
