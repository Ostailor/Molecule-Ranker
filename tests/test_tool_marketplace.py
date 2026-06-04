from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.marketplace import MarketplaceError, ToolMarketplace
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2, hash_manifest
from molecule_ranker.tool_ecosystem.schemas import ToolManifest, ToolPackage

NOW = datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_install_package_quarantines_and_registers_manifest(tmp_path: Path) -> None:
    package_dir, package = _write_package(tmp_path)
    marketplace = ToolMarketplace(registry=ToolRegistryV2(register_builtins=False))

    installed = marketplace.install_local_package(package_dir)

    assert installed.package_id == package.package_id
    assert installed.status == "quarantined"
    assert marketplace.list_installed_packages() == [installed]
    assert marketplace.states[(package.package_id, package.version)].lifecycle_state == (
        "quarantined"
    )
    with pytest.raises(KeyError, match="not active"):
        marketplace.registry.resolve_tool("plugin.marketplace.safe_summary")


def test_scan_package_records_passed_security_scan(tmp_path: Path) -> None:
    package_dir, package = _write_package(tmp_path)
    marketplace = ToolMarketplace(registry=ToolRegistryV2(register_builtins=False))
    marketplace.install_local_package(package_dir)

    scan = marketplace.scan_package(package.package_id)

    assert scan.status == "passed"
    assert scan.risk_level == "low"
    assert scan.findings == []
    assert marketplace.view_security_scan(package.package_id) == scan
    assert marketplace.states[(package.package_id, package.version)].lifecycle_state == (
        "pending_approval"
    )


def test_approve_package_activates_approved_version(tmp_path: Path) -> None:
    package_dir, package = _write_package(tmp_path)
    marketplace = ToolMarketplace(registry=ToolRegistryV2(register_builtins=False))
    marketplace.install_local_package(package_dir)
    marketplace.scan_package(package.package_id)

    approval = marketplace.approve_package(package.package_id, approved_by="reviewer-1")

    resolved = marketplace.registry.resolve_tool("plugin.marketplace.safe_summary")
    assert approval.approval_status == "approved"
    assert resolved.metadata["tool_package"]["approval_status"] == "approved"
    assert marketplace.packages[(package.package_id, package.version)].status == "approved"
    assert marketplace.states[(package.package_id, package.version)].lifecycle_state == "approved"


def test_enable_package_per_project_controls_visibility(tmp_path: Path) -> None:
    package_dir, package = _write_package(tmp_path)
    marketplace = ToolMarketplace(registry=ToolRegistryV2(register_builtins=False))
    marketplace.install_local_package(package_dir)
    marketplace.scan_package(package.package_id)
    marketplace.approve_package(package.package_id)

    hidden = marketplace.registry.list_tools_visible_to_user(
        user_permissions={"plugin:run"},
        project_id="project-1",
    )
    state = marketplace.enable_package(package.package_id, project_id="project-1")
    visible = marketplace.registry.list_tools_visible_to_user(
        user_permissions={"plugin:run"},
        project_id="project-1",
    )
    other_project = marketplace.registry.list_tools_visible_to_user(
        user_permissions={"plugin:run"},
        project_id="project-2",
    )

    assert hidden == []
    assert state.lifecycle_state == "enabled"
    assert state.enabled_project_ids == ["project-1"]
    assert [tool.tool_name for tool in visible] == ["plugin.marketplace.safe_summary"]
    assert other_project == []

    marketplace.disable_package(package.package_id, project_id="project-1")
    disabled = marketplace.registry.list_tools_visible_to_user(
        user_permissions={"plugin:run"},
        project_id="project-1",
    )

    assert disabled == []


def test_revoke_package_disables_tools(tmp_path: Path) -> None:
    package_dir, package = _write_package(tmp_path)
    marketplace = ToolMarketplace(registry=ToolRegistryV2(register_builtins=False))
    marketplace.install_local_package(package_dir)
    marketplace.scan_package(package.package_id)
    marketplace.approve_package(package.package_id)
    marketplace.enable_package(package.package_id, project_id="project-1")

    revoked = marketplace.revoke_package(package.package_id)

    assert revoked.status == "disabled"
    assert marketplace.states[(package.package_id, package.version)].lifecycle_state == "revoked"
    with pytest.raises(KeyError, match="not active"):
        marketplace.registry.resolve_tool("plugin.marketplace.safe_summary")


def test_scan_blocks_unsafe_package_approval(tmp_path: Path) -> None:
    package_dir, package = _write_package(
        tmp_path,
        requested_environment_variables=["SECRET_TOKEN"],
    )
    marketplace = ToolMarketplace(registry=ToolRegistryV2(register_builtins=False))
    marketplace.install_local_package(package_dir)

    scan = marketplace.scan_package(package.package_id)

    assert scan.status == "failed"
    assert scan.risk_level == "critical"
    with pytest.raises(MarketplaceError, match="critical security findings"):
        marketplace.approve_package(package.package_id)


def _write_package(
    tmp_path: Path,
    *,
    requested_environment_variables: list[str] | None = None,
) -> tuple[Path, ToolPackage]:
    package_dir = tmp_path / "safe-package"
    package_dir.mkdir()
    spec = RuntimeToolSpec(
        tool_name="plugin.marketplace.safe_summary",
        category="plugin",
        description="Summarize an artifact safely.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=["plugin:run"],
        policy_tags=["no_evidence_creation"],
        side_effect_level="none",
        requires_approval_by_default=False,
        idempotent=True,
        metadata={"deterministic_entrypoint": "marketplace.safe_summary"},
    )
    manifest = ToolManifest(
        manifest_id="safe-package-manifest",
        package_id="safe-package",
        package_name="safe-package",
        package_version="1.0.0",
        tools=[spec],
        skills=[],
        workflows=[],
        required_permissions=["plugin:run"],
        requested_filesystem_access=[],
        requested_network_access=[],
        requested_environment_variables=requested_environment_variables or [],
        external_domains=[],
        side_effect_summary={"none": 1},
        scientific_guardrail_tags=["no_evidence_creation"],
        license=None,
        metadata={"requires_molecule_ranker": ">=2.4.0"},
    )
    package = ToolPackage(
        package_id="safe-package",
        name="safe-package",
        display_name="Safe Package",
        description="Safe local marketplace package.",
        package_type="plugin",
        version="1.0.0",
        publisher="internal",
        source="local",
        status="discovered",
        tool_count=1,
        skill_count=0,
        workflow_count=0,
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=NOW,
        updated_at=NOW,
        metadata={},
    )
    (package_dir / "tool_package.json").write_text(
        json.dumps(package.model_dump(mode="json")),
        encoding="utf-8",
    )
    (package_dir / "tool_manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json")),
        encoding="utf-8",
    )
    return package_dir, package
