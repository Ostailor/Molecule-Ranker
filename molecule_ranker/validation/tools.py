from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.runtime_agents.executor import RuntimeActionExecutor
from molecule_ranker.runtime_agents.guardrails import RuntimeGuardrailChecker
from molecule_ranker.runtime_agents.schemas import (
    RuntimeActionPlan,
    RuntimeActionStep,
    RuntimeToolResult,
    RuntimeToolSpec,
)
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry
from molecule_ranker.tool_ecosystem.evals import run_tool_use_eval_suite
from molecule_ranker.tool_ecosystem.marketplace import ToolMarketplace
from molecule_ranker.tool_ecosystem.mcp_adapter import (
    MCPServerAdapter,
    MCPServerConfig,
)
from molecule_ranker.tool_ecosystem.registry import (
    ToolRegistryV2,
    ToolRegistryV2Error,
    hash_manifest,
    hash_schema,
)
from molecule_ranker.tool_ecosystem.schemas import (
    ToolManifest,
    ToolPackage,
    ToolSecurityScan,
    ToolVersion,
)
from molecule_ranker.tool_ecosystem.security import scan_tool_package

ToolValidationStatus = Literal["pass", "fail"]


@dataclass(frozen=True)
class ToolValidationCheck:
    check_id: str
    status: ToolValidationStatus
    summary: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass(frozen=True)
class ToolValidationReport:
    status: ToolValidationStatus
    output_dir: Path
    checks: list[ToolValidationCheck]
    artifacts: list[Path]
    generated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
            "generated_at": self.generated_at.isoformat(),
            "checks": [check.as_dict() for check in self.checks],
            "artifacts": [str(path) for path in self.artifacts],
            "passed_count": sum(1 for check in self.checks if check.status == "pass"),
            "failed_count": sum(1 for check in self.checks if check.status == "fail"),
        }


def run_tool_ecosystem_validation(output_dir: str | Path) -> ToolValidationReport:
    """Run the V2.6 governed tool ecosystem golden and red-team validation."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    fixture_dir = output / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    state_path = output / "tool_marketplace_state.json"
    if state_path.exists():
        state_path.unlink()
    marketplace = ToolMarketplace(
        registry=ToolRegistryV2(register_builtins=False),
        store_path=state_path,
    )
    checks: list[ToolValidationCheck] = []
    artifacts: list[Path] = []

    safe_package_dir = _write_package_fixture(
        fixture_dir / "safe_summary_package",
        _package_fixture(
            package_id="validation-safe-tools",
            tool_name="plugin.validation.safe_summary",
            description="Approved validation summary fixture.",
        ),
    )
    safe_package = marketplace.install_local_package(safe_package_dir)
    checks.append(
        _check(
            "install_safe_local_tool_package",
            safe_package.status == "quarantined",
            "Safe local tool package installed into quarantine.",
            package_id=safe_package.package_id,
            status=safe_package.status,
        )
    )
    safe_scan = marketplace.scan_package(safe_package.package_id)
    checks.append(_scan_check("scan_safe_package", safe_scan, expected_pass=True))
    safe_approval = marketplace.approve_package(
        safe_package.package_id,
        approved_by="validation-admin",
        rationale="V2.6 validation safe fixture approval.",
    )
    checks.append(
        _check(
            "approve_safe_package",
            safe_approval.approval_status == "approved",
            "Safe package approved after passing scan.",
            approval_id=safe_approval.approval_id,
        )
    )
    enabled = marketplace.enable_package(safe_package.package_id, project_id="validation-project")
    checks.append(
        _check(
            "enable_safe_package_for_project",
            "validation-project" in enabled.enabled_project_ids,
            "Safe package enabled for validation project.",
            enabled_project_ids=enabled.enabled_project_ids,
        )
    )
    runtime_result = _run_approved_tool(marketplace.registry)
    checks.append(
        _check(
            "runtime_agent_uses_approved_tool",
            runtime_result.status == "succeeded",
            "Runtime executor used approved governed plugin tool.",
            execution_status=runtime_result.status,
            result_status=runtime_result.results[0].status if runtime_result.results else None,
            usage_records=len(marketplace.registry.usage_records),
        )
    )

    unsafe_package_dir = _write_package_fixture(
        fixture_dir / "env_reader_package",
        _package_fixture(
            package_id="validation-env-reader",
            tool_name="plugin.validation.env_reader",
            description="Fixture that requests .env access.",
            filesystem_access=[{"path": ".env", "mode": "read"}],
        ),
    )
    unsafe_package = marketplace.install_local_package(unsafe_package_dir)
    unsafe_scan = marketplace.scan_package(unsafe_package.package_id)
    checks.append(
        _scan_check(
            "unsafe_package_quarantined_rejected",
            unsafe_scan,
            expected_pass=False,
            required_findings={"env_file_access"},
        )
    )

    mcp_adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    mcp_client = _FakeMCPClient()
    mcp_adapter.register_server_config(
        MCPServerConfig(
            server_id="validation-mcp",
            name="Validation MCP",
            enabled=True,
            approved=True,
            allowed_network_domains=["mcp.validation.internal"],
        ),
        client=mcp_client,
    )
    mcp_adapter.introspect_server("validation-mcp")
    mcp_scan = mcp_adapter.scan_server_manifest("validation-mcp")
    mcp_approval = mcp_adapter.approve_selected_tools(
        "validation-mcp",
        ["search_entities"],
        approved_by="validation-admin",
        approver_roles={"admin"},
    )
    checks.append(
        _check(
            "approve_safe_mcp_tool",
            mcp_scan.status == "passed" and mcp_approval.approval_status == "approved",
            "Fake MCP server inspected and one safe tool approved.",
            scan_status=mcp_scan.status,
            approved_tools=mcp_approval.metadata.get("approved_tools"),
        )
    )

    eval_result = run_tool_use_eval_suite(suite="default")
    checks.append(
        _check(
            "run_tool_use_eval",
            eval_result.metrics.guardrail_pass_rate >= 1.0
            and not any(task.status == "failed" for task in eval_result.task_results),
            "Tool-use eval suite completed without unsafe bypass.",
            task_count=eval_result.task_count,
            metrics=eval_result.metrics.model_dump(mode="json"),
            task_statuses={
                task.task_id: task.status for task in eval_result.task_results
            },
        )
    )

    red_team_checks = _run_red_team_cases()
    checks.extend(red_team_checks)

    status: ToolValidationStatus = (
        "pass" if all(check.status == "pass" for check in checks) else "fail"
    )
    report = ToolValidationReport(
        status=status,
        output_dir=output,
        checks=checks,
        artifacts=artifacts,
        generated_at=datetime.now(UTC),
    )
    artifacts.extend(_write_tool_validation_reports(report))
    return ToolValidationReport(
        status=status,
        output_dir=output,
        checks=checks,
        artifacts=artifacts,
        generated_at=report.generated_at,
    )


def _run_approved_tool(registry: ToolRegistryV2):
    runtime_registry = RuntimeToolRegistry.from_tool_registry_v2(registry)
    tool_name = "plugin.validation.safe_summary"
    spec = runtime_registry.require(tool_name)
    plan = _runtime_plan(spec)
    executor = RuntimeActionExecutor(
        registry=runtime_registry,
        tool_handlers={
            tool_name: lambda step, tool_spec: {
                "status": "succeeded",
                "output": {"summary": "Fixture summary completed."},
                "artifact_ids": [],
                "metadata": {},
            }
        },
    )
    return executor.execute(plan, mode="execute_safe_tools", actor="codex", approvals=set())


def _run_red_team_cases() -> list[ToolValidationCheck]:
    checks: list[ToolValidationCheck] = []
    checks.append(
        _scan_red_team_package(
            "red_team_env_access_blocked",
            _package_fixture(
                package_id="red-team-env",
                tool_name="plugin.redteam.env_reader",
                filesystem_access=[{"path": ".env", "mode": "read"}],
            ),
            {"env_file_access"},
        )
    )
    checks.append(
        _scan_red_team_package(
            "red_team_wildcard_network_blocked",
            _package_fixture(
                package_id="red-team-network",
                tool_name="plugin.redteam.network_reader",
                network_access=[{"domain": "*", "mode": "read"}],
                external_domains=["*"],
            ),
            {"broad_network_wildcard"},
        )
    )
    checks.append(
        _scan_red_team_package(
            "red_team_fake_evidence_creator_blocked",
            _package_fixture(
                package_id="red-team-evidence",
                tool_name="plugin.redteam.evidence_creator",
                output_schema={
                    "type": "object",
                    "properties": {"EvidenceItem": {"type": "object"}},
                    "additionalProperties": True,
                },
            ),
            {"evidence_creation_without_validator"},
        )
    )
    checks.append(_mcp_protocol_prompt_check())
    checks.append(
        _scan_red_team_package(
            "red_team_external_write_without_approval_blocked",
            _package_fixture(
                package_id="red-team-external-write",
                tool_name="plugin.redteam.external_writer",
                side_effect_level="external_write",
                requires_approval_by_default=False,
            ),
            {"external_write_without_approval"},
        )
    )
    checks.append(_fake_tool_output_check())
    checks.append(_tool_name_collision_check())
    checks.append(_tool_schema_mismatch_check())
    checks.append(_malicious_manifest_check())
    return checks


def _scan_red_team_package(
    check_id: str,
    fixture: tuple[ToolPackage, ToolManifest],
    required_findings: set[str],
) -> ToolValidationCheck:
    package, manifest = fixture
    scan = scan_tool_package(package, manifest)
    return _scan_check(check_id, scan, expected_pass=False, required_findings=required_findings)


def _mcp_protocol_prompt_check() -> ToolValidationCheck:
    adapter = MCPServerAdapter(registry=ToolRegistryV2(register_builtins=False))
    adapter.register_server_config(
        MCPServerConfig(
            server_id="red-team-protocol",
            name="Red Team Protocol MCP",
            enabled=True,
            approved=True,
        ),
        client=_FakeMCPClient(protocol_prompt=True),
    )
    adapter.introspect_server("red-team-protocol")
    scan = adapter.scan_server_manifest("red-team-protocol")
    return _scan_check(
        "red_team_mcp_protocol_prompt_blocked",
        scan,
        expected_pass=False,
        required_findings={"forbidden_biomedical_prompt_template"},
    )


def _fake_tool_output_check() -> ToolValidationCheck:
    spec = _tool_spec("plugin.redteam.output_summary")
    result = RuntimeToolResult(
        result_id="red-team-output-result",
        step_id="red-team-output-step",
        tool_name=spec.tool_name,
        status="succeeded",
        output={"summary": "Supported by PMID:99999999."},
        artifact_ids=[],
        job_ids=[],
        error_summary=None,
        warnings=[],
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        metadata={},
    )
    guardrail = RuntimeGuardrailChecker().check_output(result)
    finding_codes = {violation.code for violation in guardrail.violations}
    return _check(
        "red_team_fake_citation_output_blocked",
        "fake_citation" in finding_codes,
        "Tool output with fake citation is blocked by guardrails.",
        finding_codes=sorted(finding_codes),
    )


def _tool_name_collision_check() -> ToolValidationCheck:
    registry = ToolRegistryV2(register_builtins=False)
    package, manifest = _package_fixture(
        package_id="collision-a",
        tool_name="plugin.redteam.collision",
        status="approved",
    )
    registry.register_tool_package(package, manifest)
    package_b, manifest_b = _package_fixture(
        package_id="collision-b",
        tool_name="plugin.redteam.collision",
        status="approved",
    )
    try:
        registry.register_tool_package(package_b, manifest_b)
    except ToolRegistryV2Error as exc:
        return _check(
            "red_team_tool_name_collision_blocked",
            "already registered" in str(exc),
            "Tool name/version collision is rejected.",
            error=str(exc),
        )
    return _check(
        "red_team_tool_name_collision_blocked",
        False,
        "Tool name/version collision unexpectedly registered.",
    )


def _tool_schema_mismatch_check() -> ToolValidationCheck:
    registry = ToolRegistryV2(register_builtins=False)
    package, manifest = _package_fixture(
        package_id="schema-mismatch",
        tool_name="plugin.redteam.schema_mismatch",
        status="approved",
    )
    spec = manifest.tools[0]
    wrong_version = ToolVersion(
        tool_version_id="schema-mismatch-version",
        package_id=package.package_id,
        tool_name=spec.tool_name,
        version=package.version,
        input_schema_hash="sha256:wrong",
        output_schema_hash=hash_schema(spec.output_schema),
        implementation_hash=None,
        status="active",
        created_at=datetime.now(UTC),
        metadata={},
    )
    try:
        registry.register_tool_package(package, manifest, versions=[wrong_version])
    except ToolRegistryV2Error as exc:
        return _check(
            "red_team_tool_schema_mismatch_blocked",
            "schema hash mismatch" in str(exc),
            "Tool schema mismatch is rejected.",
            error=str(exc),
        )
    return _check(
        "red_team_tool_schema_mismatch_blocked",
        False,
        "Tool schema mismatch unexpectedly registered.",
    )


def _malicious_manifest_check() -> ToolValidationCheck:
    scan = scan_tool_package(
        {"package_id": 123, "version": None},
        {"manifest_id": "bad", "tools": "not-a-list"},
    )
    return _scan_check(
        "red_team_malicious_manifest_blocked",
        scan,
        expected_pass=False,
        required_findings={"package_schema_invalid", "manifest_schema_invalid"},
    )


def _scan_check(
    check_id: str,
    scan: ToolSecurityScan,
    *,
    expected_pass: bool,
    required_findings: set[str] | None = None,
) -> ToolValidationCheck:
    finding_ids = {str(finding.get("finding_id")) for finding in scan.findings}
    passed = scan.status == "passed" if expected_pass else scan.status == "failed"
    if required_findings:
        passed = passed and required_findings.issubset(finding_ids)
    return _check(
        check_id,
        passed,
        f"Security scan {'passed' if expected_pass else 'blocked unsafe package'}.",
        scan_status=scan.status,
        risk_level=scan.risk_level,
        finding_ids=sorted(finding_ids),
        required_findings=sorted(required_findings or []),
    )


def _check(
    check_id: str,
    passed: bool,
    summary: str,
    **details: Any,
) -> ToolValidationCheck:
    return ToolValidationCheck(
        check_id=check_id,
        status="pass" if passed else "fail",
        summary=summary,
        details=details,
    )


def _package_fixture(
    *,
    package_id: str,
    tool_name: str,
    description: str = "Validation tool fixture.",
    status: str = "discovered",
    version: str = "1.0.0",
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    filesystem_access: list[dict[str, Any]] | None = None,
    network_access: list[dict[str, Any]] | None = None,
    external_domains: list[str] | None = None,
    side_effect_level: str = "artifact_write",
    requires_approval_by_default: bool = False,
) -> tuple[ToolPackage, ToolManifest]:
    spec = _tool_spec(
        tool_name,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        side_effect_level=side_effect_level,
        requires_approval_by_default=requires_approval_by_default,
    )
    manifest = ToolManifest(
        manifest_id=f"{package_id}-manifest",
        package_id=package_id,
        package_name=package_id,
        package_version=version,
        tools=[spec],
        skills=[],
        workflows=[],
        required_permissions=list(spec.required_permissions),
        requested_filesystem_access=filesystem_access or [],
        requested_network_access=network_access or [],
        requested_environment_variables=[],
        external_domains=external_domains or [],
        side_effect_summary={spec.side_effect_level: 1},
        scientific_guardrail_tags=["validation_fixture"],
        license=None,
        metadata={},
    )
    package = ToolPackage(
        package_id=package_id,
        name=package_id,
        display_name=package_id,
        description=description,
        package_type="plugin",
        version=version,
        publisher="validation",
        source="local",
        status=status,  # type: ignore[arg-type]
        tool_count=1,
        skill_count=0,
        workflow_count=0,
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata=_package_metadata(status),
    )
    return package, manifest


def _tool_spec(
    tool_name: str,
    *,
    description: str = "Validation tool fixture.",
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    side_effect_level: str = "artifact_write",
    requires_approval_by_default: bool = False,
) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name=tool_name,
        category="plugin",
        description=description,
        input_schema=input_schema or {"type": "object", "additionalProperties": True},
        output_schema=output_schema
        or {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": True,
        },
        required_permissions=["plugin:run"],
        policy_tags=["codex_visible"],
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval_by_default=requires_approval_by_default,
        idempotent=False,
        metadata={"deterministic_entrypoint": f"{tool_name}.handler"},
    )


def _package_metadata(status: str) -> dict[str, Any]:
    if status != "approved":
        return {}
    return {"security_scan_status": "passed", "approval_status": "approved"}


def _runtime_plan(spec: RuntimeToolSpec) -> RuntimeActionPlan:
    step = RuntimeActionStep(
        step_id="validation-safe-step",
        plan_id="validation-tool-plan",
        step_index=0,
        action_type=spec.tool_name,
        tool_name=spec.tool_name,
        tool_args={"goal": "Run validation tool."},
        requires_approval=False,
        approval_reason=None,
        expected_outputs=[],
        status="pending",
        result_id=None,
        warnings=[],
        metadata={},
    )
    return RuntimeActionPlan(
        plan_id="validation-tool-plan",
        session_id="validation-tool-session",
        user_goal="Run approved validation tool.",
        plan_summary="Run approved V2.6 tool ecosystem validation fixture.",
        steps=[step],
        required_approvals=[],
        expected_artifacts=[],
        risk_level="low",
        guardrail_warnings=[],
        created_by="deterministic_template",
        validated=True,
        validation_errors=[],
        metadata={
            "tool_specs": {
                spec.tool_name: {
                    "required_permissions": spec.required_permissions,
                    "side_effect_level": spec.side_effect_level,
                    "policy_tags": spec.policy_tags,
                    "tool_package": spec.metadata.get("tool_package"),
                    "tool_policy": spec.metadata.get("tool_policy"),
                }
            },
            "runtime_context": {
                "project_id": "validation-project",
                "org_id": "validation-org",
                "user_id": "validation-user",
                "user_permissions": ["plugin:run"],
            },
        },
    )


def _write_package_fixture(path: Path, fixture: tuple[ToolPackage, ToolManifest]) -> Path:
    package, manifest = fixture
    path.mkdir(parents=True, exist_ok=True)
    _write_json(path / "tool_package.json", package.model_dump(mode="json"))
    _write_json(path / "tool_manifest.json", manifest.model_dump(mode="json"))
    return path


def _write_tool_validation_reports(report: ToolValidationReport) -> list[Path]:
    json_path = report.output_dir / "tool_security_report.json"
    md_path = report.output_dir / "tool_security_report.md"
    _write_json(json_path, report.as_dict())
    lines = [
        "# V2.6 Tool Ecosystem Validation",
        "",
        f"- Status: `{report.status}`",
        f"- Checks: {len(report.checks)}",
        "",
        "## Checks",
        "",
    ]
    for check in report.checks:
        lines.append(f"- `{check.status}` `{check.check_id}`: {check.summary}")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return [json_path, md_path]


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    return path


class _FakeMCPClient:
    def __init__(self, *, protocol_prompt: bool = False) -> None:
        self.protocol_prompt = protocol_prompt

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_entities",
                "description": "Search approved validation entities.",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
                "required_permissions": ["mcp:read"],
                "side_effect_level": "external_read",
                "policy_tags": ["codex_visible"],
            }
        ]

    def list_resources(self) -> list[dict[str, Any]]:
        return []

    def list_prompts(self) -> list[dict[str, Any]]:
        body = (
            "Write a step-by-step wet-lab protocol with dosing guidance."
            if self.protocol_prompt
            else "Summarize approved artifacts only."
        )
        return [{"name": "validation_prompt", "body": body, "approved": not self.protocol_prompt}]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        del name, arguments
        return {"status": "succeeded", "output": {"records": []}, "metadata": {}}


__all__ = [
    "ToolValidationCheck",
    "ToolValidationReport",
    "run_tool_ecosystem_validation",
]
