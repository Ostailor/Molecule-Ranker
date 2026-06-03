from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

CodexPermissionProfileName = Literal[
    "read_only_runtime",
    "workspace_write_runtime",
    "integration_readonly_runtime",
    "engineering_runtime",
]

CODEX_PERMISSION_PROFILE_NAMES: tuple[CodexPermissionProfileName, ...] = (
    "read_only_runtime",
    "workspace_write_runtime",
    "integration_readonly_runtime",
    "engineering_runtime",
)
DEFAULT_DENIED_PATHS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    ".cache/**",
    "**/.cache/**",
    "cache/**",
    "**/cache/**",
    "secrets/**",
    "**/secrets/**",
    "secret/**",
    "**/secret/**",
    "credentials/**",
    "**/credentials/**",
    ".aws/**",
    ".gcloud/**",
    ".kube/**",
    "*.pem",
    "*.key",
    "*credentials*",
)
DEFAULT_RUNTIME_READ_PATHS: tuple[str, ...] = (
    "artifacts/**",
    ".molecule-ranker/runtime-agent/**",
    ".omx/state/runtime_agents/**",
)
DEFAULT_INTEGRATION_DOMAINS: tuple[str, ...] = (
    "api.openalex.org",
    "eutils.ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
)


class CodexPermissionProfile(BaseModel):
    profile_name: CodexPermissionProfileName
    sandbox_mode: Literal["read-only", "workspace-write"]
    allowed_read_paths: list[str] = Field(default_factory=list)
    allowed_write_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    network_enabled: bool
    network_allowlist: list[str] = Field(default_factory=list)
    block_local_private_network: bool = True
    biomedical_runtime: bool = True
    notes: list[str] = Field(default_factory=list)
    toml_snippet: str


def generate_codex_permission_profile(
    profile_name: CodexPermissionProfileName,
    *,
    runtime_work_dir: str = ".molecule-ranker/runtime-agent/work",
    allowed_artifact_dir: str = "artifacts/runtime",
    approved_domains: list[str] | None = None,
    allow_local_private_network: bool = False,
) -> CodexPermissionProfile:
    denied_paths = list(DEFAULT_DENIED_PATHS)
    network_domains = _dedupe(approved_domains or [])
    block_local = not allow_local_private_network

    if profile_name == "read_only_runtime":
        profile = _profile(
            profile_name=profile_name,
            sandbox_mode="read-only",
            allowed_read_paths=list(DEFAULT_RUNTIME_READ_PATHS),
            allowed_write_paths=[],
            denied_paths=denied_paths,
            network_enabled=False,
            network_allowlist=[],
            block_local_private_network=True,
            biomedical_runtime=True,
            notes=[
                "Read artifacts and runtime state only.",
                "No writes.",
                "No network unless a different profile explicitly enables it.",
            ],
        )
    elif profile_name == "workspace_write_runtime":
        profile = _profile(
            profile_name=profile_name,
            sandbox_mode="workspace-write",
            allowed_read_paths=list(DEFAULT_RUNTIME_READ_PATHS),
            allowed_write_paths=[
                _glob_dir(runtime_work_dir),
                _glob_dir(allowed_artifact_dir),
            ],
            denied_paths=denied_paths,
            network_enabled=False,
            network_allowlist=[],
            block_local_private_network=True,
            biomedical_runtime=True,
            notes=[
                "Write only runtime working directory and allowed artifact directory.",
                "Deny environment files, secrets, caches, and credentials.",
            ],
        )
    elif profile_name == "integration_readonly_runtime":
        domains = network_domains or list(DEFAULT_INTEGRATION_DOMAINS)
        profile = _profile(
            profile_name=profile_name,
            sandbox_mode="read-only",
            allowed_read_paths=list(DEFAULT_RUNTIME_READ_PATHS),
            allowed_write_paths=[],
            denied_paths=denied_paths,
            network_enabled=True,
            network_allowlist=domains,
            block_local_private_network=block_local,
            biomedical_runtime=True,
            notes=[
                "Read selected artifacts.",
                "Network access is limited to explicit approved integration domains.",
                "External writes remain disabled.",
            ],
        )
    elif profile_name == "engineering_runtime":
        profile = _profile(
            profile_name=profile_name,
            sandbox_mode="workspace-write",
            allowed_read_paths=["./**"],
            allowed_write_paths=["./**"],
            denied_paths=denied_paths,
            network_enabled=False,
            network_allowlist=[],
            block_local_private_network=True,
            biomedical_runtime=False,
            notes=[
                "Repository write profile for engineering tasks.",
                "Separate from biomedical runtime profiles.",
                "Secrets remain denied.",
            ],
        )
    else:
        raise ValueError(f"Unknown Codex permission profile: {profile_name}")

    if "danger-full-access" in profile.toml_snippet or profile.sandbox_mode == "danger-full-access":
        raise ValueError("Managed Codex profiles never generate danger-full-access.")
    return profile


def _profile(
    *,
    profile_name: CodexPermissionProfileName,
    sandbox_mode: Literal["read-only", "workspace-write"],
    allowed_read_paths: list[str],
    allowed_write_paths: list[str],
    denied_paths: list[str],
    network_enabled: bool,
    network_allowlist: list[str],
    block_local_private_network: bool,
    biomedical_runtime: bool,
    notes: list[str],
) -> CodexPermissionProfile:
    payload = {
        "profile_name": profile_name,
        "sandbox_mode": sandbox_mode,
        "allowed_read_paths": _dedupe(allowed_read_paths),
        "allowed_write_paths": _dedupe(allowed_write_paths),
        "denied_paths": _dedupe(denied_paths),
        "network_enabled": network_enabled,
        "network_allowlist": _dedupe(network_allowlist),
        "block_local_private_network": block_local_private_network,
        "biomedical_runtime": biomedical_runtime,
        "notes": notes,
    }
    payload["toml_snippet"] = _toml_snippet(payload)
    return CodexPermissionProfile.model_validate(payload)


def _toml_snippet(payload: dict[str, object]) -> str:
    profile_name = str(payload["profile_name"])
    return "\n".join(
        [
            f"[profiles.{profile_name}]",
            f"sandbox_mode = {_toml_string(str(payload['sandbox_mode']))}",
            f"allowed_read_paths = {_toml_array(payload['allowed_read_paths'])}",
            f"allowed_write_paths = {_toml_array(payload['allowed_write_paths'])}",
            f"denied_paths = {_toml_array(payload['denied_paths'])}",
            f"network_enabled = {_toml_bool(bool(payload['network_enabled']))}",
            f"network_allowlist = {_toml_array(payload['network_allowlist'])}",
            "external_writes_enabled = false",
            (
                "block_local_private_network = "
                f"{_toml_bool(bool(payload['block_local_private_network']))}"
            ),
            f"biomedical_runtime = {_toml_bool(bool(payload['biomedical_runtime']))}",
            f"notes = {_toml_array(payload['notes'])}",
            "",
        ]
    )


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_array(raw: object) -> str:
    if not isinstance(raw, list):
        return "[]"
    return "[" + ", ".join(_toml_string(str(item)) for item in raw) + "]"


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _glob_dir(path: str) -> str:
    cleaned = path.rstrip("/")
    if cleaned.endswith("**"):
        return cleaned
    return f"{cleaned}/**"


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "CODEX_PERMISSION_PROFILE_NAMES",
    "DEFAULT_DENIED_PATHS",
    "CodexPermissionProfile",
    "CodexPermissionProfileName",
    "generate_codex_permission_profile",
]
