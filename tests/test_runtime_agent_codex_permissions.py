from __future__ import annotations

import json

from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.runtime_agents.codex_permissions import (
    CODEX_PERMISSION_PROFILE_NAMES,
    DEFAULT_DENIED_PATHS,
    generate_codex_permission_profile,
)


def test_profiles_deny_secrets() -> None:
    for profile_name in CODEX_PERMISSION_PROFILE_NAMES:
        profile = generate_codex_permission_profile(profile_name)
        denied = set(profile.denied_paths)

        assert ".env" in denied
        assert ".env.*" in denied
        assert ".cache/**" in denied
        assert "secrets/**" in denied
        assert "credentials/**" in denied
        assert set(DEFAULT_DENIED_PATHS).issubset(denied)


def test_profiles_never_generate_danger_full_access() -> None:
    for profile_name in CODEX_PERMISSION_PROFILE_NAMES:
        profile = generate_codex_permission_profile(profile_name)

        assert profile.sandbox_mode != "danger-full-access"
        assert "danger-full-access" not in profile.toml_snippet


def test_network_allowlist_is_explicit() -> None:
    read_only = generate_codex_permission_profile("read_only_runtime")
    integration = generate_codex_permission_profile("integration_readonly_runtime")
    custom = generate_codex_permission_profile(
        "integration_readonly_runtime",
        approved_domains=["benchling.example.com"],
    )

    assert read_only.network_enabled is False
    assert read_only.network_allowlist == []
    assert read_only.block_local_private_network is True
    assert integration.network_enabled is True
    assert integration.network_allowlist
    assert integration.block_local_private_network is True
    assert custom.network_allowlist == ["benchling.example.com"]


def test_workspace_write_profile_restricts_writes() -> None:
    profile = generate_codex_permission_profile(
        "workspace_write_runtime",
        runtime_work_dir=".runtime-agent/work",
        allowed_artifact_dir="artifacts/runtime",
    )

    assert profile.sandbox_mode == "workspace-write"
    assert profile.allowed_write_paths == [".runtime-agent/work/**", "artifacts/runtime/**"]
    assert "molecule_ranker/**" not in profile.allowed_write_paths


def test_engineering_profile_is_separate_from_biomedical_runtime() -> None:
    profile = generate_codex_permission_profile("engineering_runtime")

    assert profile.biomedical_runtime is False
    assert profile.allowed_write_paths == ["./**"]
    assert profile.network_enabled is False
    assert ".env" in profile.denied_paths


def test_codex_permissions_cli_generates_profile() -> None:
    result = CliRunner().invoke(
        app,
        ["codex", "permissions", "generate", "--profile", "read_only_runtime"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile_name"] == "read_only_runtime"
    assert payload["sandbox_mode"] == "read-only"
    assert payload["network_enabled"] is False
    assert "danger-full-access" not in payload["toml_snippet"]
