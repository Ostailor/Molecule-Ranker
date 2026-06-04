from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.registry import hash_manifest
from molecule_ranker.tool_ecosystem.schemas import ToolManifest, ToolPackage, ToolSecurityScan
from molecule_ranker.tool_ecosystem.security import (
    ToolSecurityScannerConfig,
    has_blocking_findings,
    scan_tool_package,
)

NOW = datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_secret_path_blocked() -> None:
    package, manifest = _package(
        requested_filesystem_access=[{"path": "/secure/secret/api_key.txt", "mode": "read"}]
    )

    scan = scan_tool_package(package, manifest)

    assert scan.status == "failed"
    assert scan.risk_level == "critical"
    assert _finding_ids(scan) >= {"secret_path_access"}
    assert has_blocking_findings(scan)


def test_wildcard_network_high_or_critical() -> None:
    package, manifest = _package(external_domains=["*"])

    blocked = scan_tool_package(package, manifest)
    admin_reviewed = scan_tool_package(
        package,
        manifest,
        config=ToolSecurityScannerConfig(admin_approved_network=True),
    )

    assert blocked.risk_level == "critical"
    assert blocked.status == "failed"
    assert "broad_network_wildcard" in _finding_ids(blocked)
    assert admin_reviewed.risk_level == "high"
    assert admin_reviewed.status == "warning"


def test_external_write_without_approval_critical() -> None:
    spec = _tool(
        side_effect_level="external_write",
        requires_approval_by_default=False,
    )
    package, manifest = _package(tools=[spec])

    scan = scan_tool_package(package, manifest)

    assert scan.risk_level == "critical"
    assert "external_write_without_approval" in _finding_ids(scan)


def test_evidence_creator_plugin_without_validator_critical() -> None:
    spec = _tool(
        output_schema={
            "type": "object",
            "properties": {"evidence_item": {"type": "object"}},
        },
    )
    package, manifest = _package(tools=[spec])

    scan = scan_tool_package(package, manifest)

    assert scan.status == "failed"
    assert scan.risk_level == "critical"
    assert "evidence_creation_without_validator" in _finding_ids(scan)


def test_safe_package_passes() -> None:
    package, manifest = _package()

    scan = scan_tool_package(package, manifest)

    assert scan.status == "passed"
    assert scan.risk_level == "low"
    assert scan.findings == []


def _package(
    *,
    tools: list[RuntimeToolSpec] | None = None,
    requested_filesystem_access: list[dict[str, object]] | None = None,
    external_domains: list[str] | None = None,
) -> tuple[ToolPackage, ToolManifest]:
    manifest = ToolManifest(
        manifest_id="security-test-manifest",
        package_id="security-test-package",
        package_name="security-test-package",
        package_version="1.0.0",
        tools=tools or [_tool()],
        skills=[],
        workflows=[],
        required_permissions=["plugin:run"],
        requested_filesystem_access=requested_filesystem_access or [],
        requested_network_access=[],
        requested_environment_variables=[],
        external_domains=external_domains or [],
        side_effect_summary={"none": 1},
        scientific_guardrail_tags=["no_evidence_creation"],
        license=None,
        metadata={},
    )
    package = ToolPackage(
        package_id=manifest.package_id,
        name=manifest.package_name,
        display_name="Security Test Package",
        description="Security scanner fixture package.",
        package_type="plugin",
        version=manifest.package_version,
        publisher="internal",
        source="local",
        status="discovered",
        tool_count=len(manifest.tools),
        skill_count=0,
        workflow_count=0,
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=NOW,
        updated_at=NOW,
        metadata={},
    )
    return package, manifest


def _tool(
    *,
    side_effect_level: str = "none",
    requires_approval_by_default: bool = False,
    output_schema: dict[str, object] | None = None,
) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name="plugin.security.safe_tool",
        category="plugin",
        description="Safe security scanner test tool.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema=output_schema or {"type": "object", "additionalProperties": True},
        required_permissions=["plugin:run"],
        policy_tags=["no_evidence_creation"],
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval_by_default=requires_approval_by_default,
        idempotent=True,
        metadata={"deterministic_entrypoint": "security.safe_tool"},
    )


def _finding_ids(scan: ToolSecurityScan) -> set[str]:
    return {
        finding["finding_id"]
        for finding in scan.findings
        if isinstance(finding, dict)
    }
