from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.registry import hash_manifest
from molecule_ranker.tool_ecosystem.schemas import ToolManifest, ToolPackage

NOW = datetime(2026, 6, 3, 12, tzinfo=UTC)


def test_tool_cli_help_works() -> None:
    runner = CliRunner()

    for args in (
        ["tool", "--help"],
        ["tool", "package", "--help"],
        ["tool", "mcp", "--help"],
        ["tool", "skills", "list", "--help"],
        ["tool", "workflows", "list", "--help"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output


def test_tool_package_validate_install_scan_approve(tmp_path: Path) -> None:
    runner = CliRunner()
    package_dir = _write_package(tmp_path / "safe-tool-package")
    env = {"MOLECULE_RANKER_MARKETPLACE_STORE": str(tmp_path / "marketplace.json")}

    validate = runner.invoke(
        app,
        ["tool", "package", "validate", "--path", str(package_dir), "--json"],
        env=env,
    )
    install = runner.invoke(
        app,
        ["tool", "package", "install", "--path", str(package_dir), "--source", "local", "--json"],
        env=env,
    )
    scan = runner.invoke(
        app,
        ["tool", "package", "scan", "--package-id", "fixture-safe-tools", "--json"],
        env=env,
    )
    approve = runner.invoke(
        app,
        [
            "tool",
            "package",
            "approve",
            "--package-id",
            "fixture-safe-tools",
            "--approved-by",
            "admin-1",
            "--rationale",
            "Safe CLI fixture.",
            "--json",
        ],
        env=env,
    )

    assert validate.exit_code == 0, validate.output
    assert json.loads(validate.output)["status"] == "valid"
    assert install.exit_code == 0, install.output
    assert json.loads(install.output)["package"]["status"] == "quarantined"
    assert scan.exit_code == 0, scan.output
    assert json.loads(scan.output)["scan"]["status"] == "passed"
    assert approve.exit_code == 0, approve.output
    assert json.loads(approve.output)["approval"]["approval_status"] == "approved"


def test_tool_cli_hides_unapproved_package(tmp_path: Path) -> None:
    runner = CliRunner()
    package_dir = _write_package(tmp_path / "safe-tool-package")
    env = {"MOLECULE_RANKER_MARKETPLACE_STORE": str(tmp_path / "marketplace.json")}

    install = runner.invoke(
        app,
        ["tool", "package", "install", "--path", str(package_dir), "--json"],
        env=env,
    )
    listed = runner.invoke(app, ["tool", "list", "--json"], env=env)

    assert install.exit_code == 0, install.output
    assert listed.exit_code == 0, listed.output
    tool_names = {tool["tool_name"] for tool in json.loads(listed.output)["tools"]}
    assert "plugin.fixture.safe_summary" not in tool_names


def test_tool_mcp_fake_server_register_and_inspect(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "fake_mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "server_config": {
                    "server_id": "fake-mcp",
                    "name": "Fake MCP",
                    "enabled": True,
                    "approved": True,
                    "allowed_network_domains": ["mcp.internal"],
                },
                "tools": [
                    {
                        "name": "search_entities",
                        "description": "Search fake MCP entities.",
                        "input_schema": {"type": "object", "additionalProperties": True},
                        "output_schema": {"type": "object", "additionalProperties": True},
                        "required_permissions": ["mcp:read"],
                        "side_effect_level": "external_read",
                        "policy_tags": ["codex_visible"],
                    }
                ],
                "resources": [],
                "prompts": [{"name": "safe_summary", "body": "Summarize approved artifacts."}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = {"MOLECULE_RANKER_MCP_STORE": str(tmp_path / "mcp_store.json")}

    register = runner.invoke(
        app,
        ["tool", "mcp", "register", "--name", "Fake MCP", "--config", str(config_path), "--json"],
        env=env,
    )
    inspect = runner.invoke(
        app,
        ["tool", "mcp", "inspect", "--server-id", "fake-mcp", "--json"],
        env=env,
    )

    assert register.exit_code == 0, register.output
    assert json.loads(register.output)["server"]["config"]["server_id"] == "fake-mcp"
    assert inspect.exit_code == 0, inspect.output
    payload = json.loads(inspect.output)
    assert payload["package"]["status"] == "quarantined"
    assert payload["tools"][0]["tool_name"] == "mcp.fake_mcp.search_entities"


def _write_package(path: Path) -> Path:
    path.mkdir(parents=True)
    spec = RuntimeToolSpec(
        tool_name="plugin.fixture.safe_summary",
        category="plugin",
        description="Safe fixture summary tool.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=["plugin:run"],
        policy_tags=["codex_visible"],
        side_effect_level="artifact_write",
        requires_approval_by_default=False,
        idempotent=False,
        metadata={"deterministic_entrypoint": "fixture.safe_summary"},
    )
    manifest = ToolManifest(
        manifest_id="fixture-safe-tools-manifest",
        package_id="fixture-safe-tools",
        package_name="fixture-safe-tools",
        package_version="1.0.0",
        tools=[spec],
        skills=[],
        workflows=[],
        required_permissions=["plugin:run"],
        requested_filesystem_access=[],
        requested_network_access=[],
        requested_environment_variables=[],
        external_domains=[],
        side_effect_summary={"artifact_write": 1},
        scientific_guardrail_tags=["no_evidence_creation"],
        license=None,
        metadata={},
    )
    package = ToolPackage(
        package_id="fixture-safe-tools",
        name="fixture-safe-tools",
        display_name="Fixture Safe Tools",
        description="Safe fixture tool package.",
        package_type="plugin",
        version="1.0.0",
        publisher="tests",
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
    (path / "tool_manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (path / "tool_package.json").write_text(
        json.dumps(package.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
