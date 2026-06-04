from __future__ import annotations

import json
from typing import Any

import pytest

from molecule_ranker.runtime_agents import (
    MCPGateway,
    PluginSDK,
    RuntimePlanValidationError,
    RuntimeToolSpec,
    ToolMarketplace,
    ToolPolicy,
    ToolSecurityScan,
    ToolUsageEval,
    manifest_hash,
)
from molecule_ranker.runtime_agents.planner import CodexRuntimePlanner
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry


def test_plugin_sdk_signs_hashes_and_installs_approved_tool_pack() -> None:
    spec = _packaged_spec("score_qc_report")
    manifest = PluginSDK.build_manifest(
        package_id="qc-tools",
        name="QC tools",
        description="Internal QC reporting tools.",
        version="1.0.0",
        tools=[spec],
        mcp_namespace="qc",
    )
    package = PluginSDK.package_tool_pack(
        manifest=manifest,
        policies=[
            ToolPolicy(
                tool_name="score_qc_report",
                required_permissions=["qc:run"],
                sandbox_profile="artifact_write",
                allowed_org_ids=["org-a"],
            )
        ],
    )
    marketplace = ToolMarketplace()
    marketplace.submit(package)
    approved = marketplace.approve(
        "qc-tools",
        "1.0.0",
        scan=ToolSecurityScan(package_id="qc-tools", version="1.0.0", status="passed"),
        approved_by="tool-admin",
        rationale="Internal deterministic reporting tool.",
    )
    registry = RuntimeToolRegistry.default()

    marketplace.install_approved_package(registry, "qc-tools", "1.0.0")

    installed = registry.require("score_qc_report")
    assert approved.is_approved is True
    assert package.version.manifest_hash == manifest_hash(manifest)
    assert package.version.signature == f"sha256:{package.version.manifest_hash}"
    assert installed.metadata["tool_package"]["approval_status"] == "approved"
    assert installed.metadata["tool_package"]["security_scan_status"] == "passed"
    assert registry.tool_allowed_in_context(
        installed,
        org_id="org-a",
        user_permissions={"qc:run"},
    )
    assert not registry.tool_allowed_in_context(
        installed,
        org_id="org-b",
        user_permissions={"qc:run"},
    )


def test_registry_rejects_unapproved_tool_pack_metadata() -> None:
    registry = RuntimeToolRegistry()

    with pytest.raises(ValueError, match="approved tool package"):
        registry.register(
            _packaged_spec(
                "unsafe_tool",
                metadata={
                    "tool_package": {
                        "package_id": "unsafe",
                        "version": "1.0.0",
                        "manifest_hash": "abc",
                        "signature": "sha256:abc",
                        "approval_status": "pending",
                        "security_scan_status": "passed",
                    }
                },
            )
        )


def test_mcp_gateway_exposes_only_policy_allowed_tools() -> None:
    registry = RuntimeToolRegistry.default()
    marketplace = _approved_marketplace_with_qc_tool()
    marketplace.install_approved_package(registry, "qc-tools", "1.0.0")
    gateway = MCPGateway(registry)

    org_a_tools = gateway.list_tools(org_id="org-a", user_permissions={"qc:run"})
    org_b_tools = gateway.list_tools(org_id="org-b", user_permissions={"qc:run"})

    assert any(tool["name"] == "score_qc_report" for tool in org_a_tools)
    assert not any(tool["name"] == "score_qc_report" for tool in org_b_tools)
    descriptor = gateway.get_tool("score_qc_report")
    assert descriptor["inputSchema"]["type"] == "object"
    assert descriptor["annotations"]["tool_package"]["package_id"] == "qc-tools"


def test_codex_planner_uses_dynamic_approved_catalog_and_blocks_wrong_org() -> None:
    registry = RuntimeToolRegistry.default()
    marketplace = _approved_marketplace_with_qc_tool()
    marketplace.install_approved_package(registry, "qc-tools", "1.0.0")
    codex = FakeCodexPlannerClient(
        _plan_payload(
            steps=[
                {
                    "step_id": "step-1",
                    "plan_id": "plan-1",
                    "step_index": 0,
                    "action_type": "score_qc_report",
                    "tool_name": "score_qc_report",
                    "tool_args": {"artifact_id": "artifact-1"},
                    "requires_approval": False,
                    "approval_reason": None,
                    "expected_outputs": ["qc_report"],
                    "status": "pending",
                    "result_id": None,
                    "warnings": [],
                    "metadata": {},
                }
            ]
        )
    )
    planner = CodexRuntimePlanner(registry=registry, codex_client=codex)

    plan = planner.plan(
        user_goal="Run QC report.",
        session_id="session-1",
        org_id="org-a",
        user_permissions={"qc:run"},
        current_artifacts=[{"artifact_id": "artifact-1"}],
    )

    assert plan.validated is True
    assert plan.steps[0].tool_name == "score_qc_report"
    assert plan.metadata["runtime_context"]["user_permissions"] == ["qc:run"]
    assert "approved_catalog_rules" in codex.prompts[0]
    assert "Codex cannot invent tools" in codex.prompts[0]

    with pytest.raises(RuntimePlanValidationError, match="not allowed"):
        planner.plan(
            user_goal="Run QC report.",
            session_id="session-2",
            org_id="org-b",
            user_permissions={"qc:run"},
            current_artifacts=[{"artifact_id": "artifact-1"}],
        )


def test_tool_usage_eval_quality_gate_requires_recovery_and_zero_policy_violations() -> None:
    passing = ToolUsageEval(
        package_id="qc-tools",
        version="1.0.0",
        tool_name="score_qc_report",
        plan_quality_score=0.9,
        execution_success_rate=0.95,
        failure_recovery_score=0.8,
        policy_violation_rate=0,
    )
    failing = passing.model_copy(update={"policy_violation_rate": 0.01})

    assert passing.passes_quality_gate is True
    assert failing.passes_quality_gate is False


class FakeCodexPlannerClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def plan(self, *, prompt: str, sandbox_mode: str, jsonl_output_path: str | None) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.payload)


def _approved_marketplace_with_qc_tool() -> ToolMarketplace:
    spec = _packaged_spec("score_qc_report")
    manifest = PluginSDK.build_manifest(
        package_id="qc-tools",
        name="QC tools",
        description="Internal QC reporting tools.",
        version="1.0.0",
        tools=[spec],
        mcp_namespace="qc",
    )
    package = PluginSDK.package_tool_pack(
        manifest=manifest,
        policies=[
            ToolPolicy(
                tool_name="score_qc_report",
                required_permissions=["qc:run"],
                sandbox_profile="artifact_write",
                allowed_org_ids=["org-a"],
            )
        ],
    )
    marketplace = ToolMarketplace()
    marketplace.submit(package)
    marketplace.approve(
        "qc-tools",
        "1.0.0",
        scan=ToolSecurityScan(package_id="qc-tools", version="1.0.0", status="passed"),
        approved_by="tool-admin",
        rationale="Internal deterministic reporting tool.",
    )
    return marketplace


def _packaged_spec(tool_name: str, metadata: dict[str, Any] | None = None) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name=tool_name,
        category="evaluation",
        description="Create a deterministic QC report.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=["qc:run"],
        policy_tags=["artifact_validation_required"],
        side_effect_level="artifact_write",
        requires_approval_by_default=False,
        idempotent=True,
        metadata={
            "deterministic_entrypoint": "molecule_ranker.qc.reports",
            "runtime_execution": "delegate_to_existing_module_or_cli",
            **(metadata or {}),
        },
    )


def _plan_payload(*, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "plan_id": "plan-1",
        "session_id": "session-1",
        "user_goal": "Test goal",
        "plan_summary": "Test plan",
        "steps": steps,
        "required_approvals": [],
        "expected_artifacts": [],
        "risk_level": "low",
        "guardrail_warnings": [],
        "created_by": "codex",
        "validated": False,
        "validation_errors": [],
        "metadata": {},
    }
