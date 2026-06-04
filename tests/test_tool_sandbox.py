from __future__ import annotations

import pytest

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.tool_ecosystem.sandbox import (
    SandboxProfileError,
    default_sandbox_profile,
    generate_codex_permission_profile_snippet,
    get_sandbox_profile,
    profile_for_tool,
)


def test_read_only_profile_denies_writes() -> None:
    profile = default_sandbox_profile()

    assert profile.profile_name == "tool_read_only"
    assert profile.codex_sandbox_mode == "read-only"
    assert profile.filesystem.write == []
    assert not profile.filesystem.allows_write("artifacts/result.json")
    assert profile.filesystem.denies(".env")
    assert profile.filesystem.denies(".cache/tool-cache.json")


def test_artifact_write_profile_limited_to_artifact_dir() -> None:
    profile = get_sandbox_profile("tool_artifact_write")

    assert profile.codex_sandbox_mode == "workspace-write"
    assert profile.filesystem.allows_write("artifacts/result.json")
    assert profile.filesystem.allows_write("artifacts/runtime/result.json")
    assert not profile.filesystem.allows_write("projects/project-1/state.json")
    assert not profile.filesystem.allows_write(".env")


def test_env_and_credentials_denied() -> None:
    profile = get_sandbox_profile("tool_project_write")

    assert profile.filesystem.denies(".env.local")
    assert profile.filesystem.denies("secrets/service-token.txt")
    assert profile.filesystem.denies("credentials/benchling.json")
    with pytest.raises(SandboxProfileError, match="environment variables"):
        get_sandbox_profile(
            "tool_engineering_only",
            environment_variables=["BENCHLING_API_TOKEN"],
        )


def test_network_wildcard_rejected_unless_admin_approved() -> None:
    with pytest.raises(SandboxProfileError, match="network wildcard"):
        get_sandbox_profile("tool_external_read", network_allowlist=["*"])

    profile = get_sandbox_profile(
        "tool_external_read",
        network_allowlist=["*"],
        admin_approved_network_wildcard=True,
    )

    assert profile.network_allowlist == ["*"]
    assert profile.admin_approved_network_wildcard is True


def test_external_write_profile_requires_approval() -> None:
    profile = profile_for_tool(
        _tool("external_write", requires_approval_by_default=True),
        admin_approved_network_wildcard=True,
    )

    assert profile.profile_name == "tool_external_write_requires_approval"
    assert profile.requires_approval is True
    assert profile.external_writes_enabled is True


def test_generate_codex_permission_profile_snippet() -> None:
    snippet = generate_codex_permission_profile_snippet(_tool("artifact_write"))

    assert "[profiles.tool_artifact_write]" in snippet
    assert 'sandbox_mode = "workspace-write"' in snippet
    assert "danger-full-access" not in snippet
    assert "denied_paths" in snippet


def _tool(
    side_effect_level: str,
    *,
    requires_approval_by_default: bool = False,
) -> RuntimeToolSpec:
    return RuntimeToolSpec(
        tool_name="plugin.sandbox.example",
        category="plugin",
        description="Sandbox profile test tool.",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=["plugin:run"],
        policy_tags=[],
        side_effect_level=side_effect_level,  # type: ignore[arg-type]
        requires_approval_by_default=requires_approval_by_default,
        idempotent=side_effect_level in {"none", "external_read"},
        metadata={},
    )
