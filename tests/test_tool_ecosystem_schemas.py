from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.schemas import (
    SkillPack,
    ToolApproval,
    ToolManifest,
    ToolPackage,
    ToolSecurityScan,
    ToolUsageRecord,
    ToolVersion,
    WorkflowTemplate,
)

NOW = datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_tool_ecosystem_schemas_accept_valid_payloads() -> None:
    tool = _tool_spec()
    package = ToolPackage(
        package_id="pkg-1",
        name="ranking-tools",
        display_name="Ranking Tools",
        description="Internal ranking tool pack.",
        package_type="internal",
        version="1.0.0",
        publisher="molecule-ranker",
        source="built_in",
        status="approved",
        tool_count=1,
        skill_count=1,
        workflow_count=1,
        manifest_hash="sha256:manifest",
        package_hash=None,
        created_at=NOW,
        updated_at=NOW,
        metadata={"security_scan_status": "passed", "approval_status": "approved"},
    )
    manifest = ToolManifest(
        manifest_id="manifest-1",
        package_id="pkg-1",
        package_name="ranking-tools",
        package_version="1.0.0",
        tools=[tool],
        skills=[{"name": "rank_and_review"}],
        workflows=[{"name": "rank-review-workflow"}],
        required_permissions=["run:create"],
        requested_filesystem_access=[{"profile": "artifact_write"}],
        requested_network_access=[{"domain": "internal.example"}],
        requested_environment_variables=["MOLECULE_RANKER_ENV"],
        external_domains=["internal.example"],
        side_effect_summary={"artifact_write": 1},
        scientific_guardrail_tags=["source_backed"],
        license=None,
        metadata={},
    )
    version = ToolVersion(
        tool_version_id="tool-version-1",
        package_id="pkg-1",
        tool_name="run_ranking",
        version="1.0.0",
        input_schema_hash="sha256:input",
        output_schema_hash="sha256:output",
        implementation_hash=None,
        status="active",
        created_at=NOW,
        metadata={},
    )
    scan = ToolSecurityScan(
        scan_id="scan-1",
        package_id="pkg-1",
        package_version="1.0.0",
        status="passed",
        findings=[],
        risk_level="low",
        scanned_at=NOW,
        scanner_version="scanner-1",
        metadata={},
    )
    approval = ToolApproval(
        approval_id="approval-1",
        package_id="pkg-1",
        package_version="1.0.0",
        approved_by="tool-admin",
        approval_status="approved",
        rationale="Approved for internal deterministic execution.",
        approved_permissions=["run:create"],
        approved_filesystem_profile="artifact_write",
        approved_network_domains=["internal.example"],
        approved_at=NOW,
        expires_at=NOW + timedelta(days=30),
        metadata={},
    )
    skill_pack = SkillPack(
        skill_pack_id="skill-pack-1",
        package_id="pkg-1",
        name="ranking-skills",
        version="1.0.0",
        skills=[{"name": "rank_and_review"}],
        required_tools=["run_ranking"],
        guardrails=["No fabricated evidence."],
        metadata={},
    )
    workflow = WorkflowTemplate(
        workflow_template_id="workflow-1",
        package_id="pkg-1",
        name="rank-review",
        version="1.0.0",
        description="Rank candidates and prepare review.",
        steps=[{"tool_name": "run_ranking"}],
        required_tools=["run_ranking"],
        required_permissions=["run:create"],
        approval_requirements=[],
        expected_artifacts=["ranking_run"],
        forbidden_outputs=["assay_results"],
        metadata={},
    )
    usage = ToolUsageRecord(
        usage_id="usage-1",
        session_id="session-1",
        project_id="project-1",
        package_id="pkg-1",
        tool_name="run_ranking",
        tool_version="1.0.0",
        invoked_by="codex",
        status="succeeded",
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=5),
        artifact_ids=["artifact-1"],
        warnings=[],
        metadata={},
    )

    assert package.quarantined_until_scanned_and_approved is False
    assert manifest.tools[0].tool_name == "run_ranking"
    assert version.status == "active"
    assert scan.status == "passed"
    assert approval.approved_at == NOW
    assert skill_pack.required_tools == ["run_ranking"]
    assert workflow.expected_artifacts == ["ranking_run"]
    assert usage.invoked_by == "codex"


def test_tool_package_is_quarantined_until_scanned_and_approved() -> None:
    quarantined = _package(status="quarantined", metadata={})
    scanned = _package(status="scanned", metadata={"security_scan_status": "warning"})

    assert quarantined.quarantined_until_scanned_and_approved is True
    assert scanned.quarantined_until_scanned_and_approved is True

    with pytest.raises(ValidationError, match="passed security scan"):
        _package(status="approved", metadata={"approval_status": "approved"})

    with pytest.raises(ValidationError, match="require approval"):
        _package(status="approved", metadata={"security_scan_status": "passed"})


def test_tool_ecosystem_schemas_reject_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _package(created_at=datetime(2026, 6, 3, 12), updated_at=NOW)


def test_tool_ecosystem_schemas_reject_invalid_allowed_values() -> None:
    with pytest.raises(ValidationError):
        _package(package_type="public")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        _package(source="unknown")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        _package(status="enabled")  # type: ignore[arg-type]


def test_tool_manifest_rejects_duplicate_tool_names() -> None:
    with pytest.raises(ValidationError, match="duplicate tools"):
        ToolManifest(
            manifest_id="manifest-1",
            package_id="pkg-1",
            package_name="ranking-tools",
            package_version="1.0.0",
            tools=[_tool_spec(), _tool_spec()],
            skills=[],
            workflows=[],
            required_permissions=[],
            requested_filesystem_access=[],
            requested_network_access=[],
            requested_environment_variables=[],
            external_domains=[],
            side_effect_summary={},
            scientific_guardrail_tags=[],
            license=None,
            metadata={},
        )


def test_security_scan_and_approval_lifecycle_validation() -> None:
    with pytest.raises(ValidationError, match="high or critical"):
        ToolSecurityScan(
            scan_id="scan-1",
            package_id="pkg-1",
            package_version="1.0.0",
            status="passed",
            findings=[],
            risk_level="critical",
            scanned_at=NOW,
            scanner_version="scanner-1",
            metadata={},
        )

    with pytest.raises(ValidationError, match="approved_at"):
        ToolApproval(
            approval_id="approval-1",
            package_id="pkg-1",
            package_version="1.0.0",
            approved_by="tool-admin",
            approval_status="approved",
            rationale="Approved.",
            approved_permissions=["run:create"],
            approved_filesystem_profile="artifact_write",
            approved_network_domains=[],
            approved_at=None,
            expires_at=None,
            metadata={},
        )


def test_tool_usage_record_rejects_impossible_timing() -> None:
    with pytest.raises(ValidationError, match="completed_at"):
        ToolUsageRecord(
            usage_id="usage-1",
            session_id=None,
            project_id=None,
            package_id="pkg-1",
            tool_name="run_ranking",
            tool_version="1.0.0",
            invoked_by="workflow",
            status="failed",
            started_at=NOW,
            completed_at=NOW - timedelta(seconds=1),
            artifact_ids=[],
            warnings=[],
            metadata={},
        )


def _package(
    *,
    status: str = "approved",
    package_type: str = "internal",
    source: str = "built_in",
    created_at: datetime = NOW,
    updated_at: datetime = NOW,
    metadata: dict[str, str] | None = None,
) -> ToolPackage:
    return ToolPackage(
        package_id="pkg-1",
        name="ranking-tools",
        display_name="Ranking Tools",
        description="Internal ranking tool pack.",
        package_type=package_type,  # type: ignore[arg-type]
        version="1.0.0",
        publisher="molecule-ranker",
        source=source,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        tool_count=1,
        skill_count=0,
        workflow_count=0,
        manifest_hash="sha256:manifest",
        package_hash=None,
        created_at=created_at,
        updated_at=updated_at,
        metadata=metadata
        if metadata is not None
        else {"security_scan_status": "passed", "approval_status": "approved"},
    )


def _tool_spec() -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name="run_ranking",
        category="ranking",
        description="Run deterministic ranking.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        required_permissions=["run:create"],
        policy_tags=["source_backed"],
        side_effect_level="artifact_write",
        requires_approval_by_default=False,
        idempotent=True,
    )
