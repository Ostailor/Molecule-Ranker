from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec, ToolSideEffectLevel
from molecule_ranker.tool_ecosystem.schemas import ToolManifest

ToolSandboxProfileName = Literal[
    "tool_read_only",
    "tool_artifact_write",
    "tool_project_write",
    "tool_external_read",
    "tool_external_write_requires_approval",
    "tool_codex_subprocess",
    "tool_engineering_only",
]

DENIED_PATHS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*credential*",
    "**/*credentials*",
    "credentials/**",
    "**/credentials/**",
    "**/*secret*",
    "secret/**",
    "**/secret/**",
    "secrets/**",
    "**/secrets/**",
    "**/*token*",
    "**/*api_key*",
    "**/*.pem",
    "**/*.key",
    ".aws/**",
    ".gcloud/**",
    ".kube/**",
    ".cache/**",
    "**/.cache/**",
    "cache/**",
    "**/cache/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
)
DEFAULT_READ_PATHS: tuple[str, ...] = (
    "artifacts/**",
    ".molecule-ranker/runtime-agent/**",
    ".omx/state/runtime_agents/**",
)
ARTIFACT_WRITE_PATHS: tuple[str, ...] = ("artifacts/**", "artifacts/runtime/**")
PROJECT_WRITE_PATHS: tuple[str, ...] = ("projects/**", ".molecule-ranker/projects/**")
TEMP_PATHS: tuple[str, ...] = (".molecule-ranker/tmp/**", "/tmp/molecule-ranker/**")
LOCAL_PRIVATE_NETWORK_PATTERN = re.compile(
    r"^(?:localhost|127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|169\.254\.|::1|fc|fd)",
    re.I,
)


class SandboxProfileError(ValueError):
    """Raised when requested sandbox settings violate tool ecosystem policy."""


class FilesystemPolicy(BaseModel):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    def denies(self, path: str) -> bool:
        return _matches_any(path, self.deny)

    def allows_write(self, path: str) -> bool:
        return not self.denies(path) and _matches_any(path, self.write)


class ToolSandboxProfile(BaseModel):
    profile_name: ToolSandboxProfileName
    filesystem: FilesystemPolicy
    network_allowlist: list[str] = Field(default_factory=list)
    environment_variables: list[str] = Field(default_factory=list)
    temp_directory_access: list[str] = Field(default_factory=list)
    artifact_directories: list[str] = Field(default_factory=list)
    cache_denial: bool = True
    secret_denial: bool = True
    timeout_seconds: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    requires_approval: bool = False
    codex_sandbox_mode: Literal["read-only", "workspace-write"]
    external_writes_enabled: bool = False
    block_local_private_network: bool = True
    admin_approved_network_wildcard: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_profile_safety(self) -> ToolSandboxProfile:
        if self.codex_sandbox_mode == "danger-full-access":  # type: ignore[comparison-overlap]
            raise ValueError("tool packages cannot request danger-full-access")
        forbidden_writes = [path for path in self.filesystem.write if path in {"", "/", "~", "."}]
        if self.profile_name != "tool_engineering_only":
            forbidden_writes.extend(
                path for path in self.filesystem.write if path in {"./**", "**", "*"}
            )
        if forbidden_writes:
            raise ValueError("tool sandbox profile cannot allow broad filesystem writes")
        if self.secret_denial and not any(
            "secret" in path.lower() for path in self.filesystem.deny
        ):
            raise ValueError("tool sandbox profile must deny secrets")
        if self.cache_denial and not any("cache" in path.lower() for path in self.filesystem.deny):
            raise ValueError("tool sandbox profile must deny caches")
        if not any(".env" in path for path in self.filesystem.deny):
            raise ValueError("tool sandbox profile must deny .env files")
        if (
            _has_network_wildcard(self.network_allowlist)
            and not self.admin_approved_network_wildcard
        ):
            raise ValueError("network wildcard requires admin approval")
        if self.block_local_private_network:
            blocked = [domain for domain in self.network_allowlist if _is_local_private(domain)]
            if blocked:
                raise ValueError("local/private network requires explicit allowlisting")
        if self.external_writes_enabled and not self.requires_approval:
            raise ValueError("external writes require approval")
        return self

    def to_codex_permission_profile_snippet(self) -> str:
        payload: dict[str, Any] = {
            "sandbox_mode": self.codex_sandbox_mode,
            "allowed_read_paths": self.filesystem.read,
            "allowed_write_paths": self.filesystem.write,
            "denied_paths": self.filesystem.deny,
            "network_enabled": bool(self.network_allowlist),
            "network_allowlist": self.network_allowlist,
            "environment_variables": self.environment_variables,
            "temp_directory_access": self.temp_directory_access,
            "artifact_directories": self.artifact_directories,
            "cache_denial": self.cache_denial,
            "secret_denial": self.secret_denial,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "external_writes_enabled": self.external_writes_enabled,
            "requires_approval": self.requires_approval,
            "block_local_private_network": self.block_local_private_network,
            "admin_approved_network_wildcard": self.admin_approved_network_wildcard,
            "notes": self.notes,
        }
        lines = [f"[profiles.{self.profile_name}]"]
        for key, value in payload.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
        snippet = "\n".join(lines)
        if "danger-full-access" in snippet:
            raise SandboxProfileError("managed tool profiles never generate danger-full-access")
        return snippet


def default_sandbox_profile() -> ToolSandboxProfile:
    return get_sandbox_profile("tool_read_only")


def get_sandbox_profile(
    profile_name: ToolSandboxProfileName,
    *,
    network_allowlist: list[str] | None = None,
    environment_variables: list[str] | None = None,
    admin_approved_network_wildcard: bool = False,
    allow_local_private_network: bool = False,
) -> ToolSandboxProfile:
    requested_network = network_allowlist or []
    if _has_network_wildcard(requested_network) and not admin_approved_network_wildcard:
        raise SandboxProfileError("network wildcard rejected unless admin-approved")
    block_local = not allow_local_private_network
    if block_local and any(_is_local_private(domain) for domain in requested_network):
        raise SandboxProfileError("local/private network denied unless explicitly allowlisted")

    if profile_name == "tool_read_only":
        return _profile(
            profile_name,
            read=list(DEFAULT_READ_PATHS),
            write=[],
            network=[],
            env=[],
            codex_sandbox_mode="read-only",
            timeout_seconds=60,
            max_output_bytes=256_000,
            notes=["Default read-only profile.", "No writes or network."],
        )
    if profile_name == "tool_artifact_write":
        return _profile(
            profile_name,
            read=list(DEFAULT_READ_PATHS),
            write=[*ARTIFACT_WRITE_PATHS, *TEMP_PATHS],
            network=[],
            env=[],
            codex_sandbox_mode="workspace-write",
            timeout_seconds=120,
            max_output_bytes=512_000,
            notes=["Writes limited to artifact and tool temp directories."],
        )
    if profile_name == "tool_project_write":
        return _profile(
            profile_name,
            read=[*DEFAULT_READ_PATHS, "projects/**"],
            write=[*ARTIFACT_WRITE_PATHS, *PROJECT_WRITE_PATHS, *TEMP_PATHS],
            network=[],
            env=[],
            codex_sandbox_mode="workspace-write",
            timeout_seconds=180,
            max_output_bytes=768_000,
            notes=["Project writes are limited to project, artifact, and temp directories."],
        )
    if profile_name == "tool_external_read":
        return _profile(
            profile_name,
            read=list(DEFAULT_READ_PATHS),
            write=[],
            network=requested_network,
            env=[],
            codex_sandbox_mode="read-only",
            timeout_seconds=180,
            max_output_bytes=512_000,
            block_local_private_network=block_local,
            admin_approved_network_wildcard=admin_approved_network_wildcard,
            notes=["Network read is limited to approved domains."],
        )
    if profile_name == "tool_external_write_requires_approval":
        return _profile(
            profile_name,
            read=list(DEFAULT_READ_PATHS),
            write=[*ARTIFACT_WRITE_PATHS, *TEMP_PATHS],
            network=requested_network,
            env=[],
            codex_sandbox_mode="workspace-write",
            timeout_seconds=180,
            max_output_bytes=512_000,
            requires_approval=True,
            external_writes_enabled=True,
            block_local_private_network=block_local,
            admin_approved_network_wildcard=admin_approved_network_wildcard,
            notes=["External writes require approval and approved network domains."],
        )
    if profile_name == "tool_codex_subprocess":
        return _profile(
            profile_name,
            read=["./**"],
            write=[*ARTIFACT_WRITE_PATHS, ".molecule-ranker/runtime-agent/**", *TEMP_PATHS],
            network=[],
            env=[],
            codex_sandbox_mode="workspace-write",
            timeout_seconds=300,
            max_output_bytes=1_000_000,
            requires_approval=True,
            notes=["Codex subprocess profile; secrets, caches, and credentials remain denied."],
        )
    if profile_name == "tool_engineering_only":
        return _profile(
            profile_name,
            read=["./**"],
            write=["./**"],
            network=[],
            env=environment_variables or [],
            codex_sandbox_mode="workspace-write",
            timeout_seconds=300,
            max_output_bytes=1_000_000,
            requires_approval=True,
            notes=[
                "Engineering-only repository profile.",
                "Not for biomedical evidence, assay, campaign, or stage-gate workflows.",
            ],
        )
    raise SandboxProfileError(f"unknown sandbox profile: {profile_name}")


def profile_for_tool(
    spec: RuntimeToolSpec,
    *,
    manifest: ToolManifest | None = None,
    admin_approved_network_wildcard: bool = False,
    allow_local_private_network: bool = False,
) -> ToolSandboxProfile:
    if _requests_danger_full_access(spec, manifest):
        raise SandboxProfileError("tool packages cannot request danger-full-access")
    profile_name = _profile_name_for_side_effect(spec.side_effect_level, spec.category)
    return get_sandbox_profile(
        profile_name,
        network_allowlist=_network_allowlist_for_tool(spec, manifest),
        environment_variables=_environment_variables_for_tool(spec, manifest),
        admin_approved_network_wildcard=admin_approved_network_wildcard,
        allow_local_private_network=allow_local_private_network,
    )


def generate_codex_permission_profile_snippet(
    spec: RuntimeToolSpec,
    *,
    manifest: ToolManifest | None = None,
    admin_approved_network_wildcard: bool = False,
    allow_local_private_network: bool = False,
) -> str:
    profile = profile_for_tool(
        spec,
        manifest=manifest,
        admin_approved_network_wildcard=admin_approved_network_wildcard,
        allow_local_private_network=allow_local_private_network,
    )
    return profile.to_codex_permission_profile_snippet()


def _profile(
    profile_name: ToolSandboxProfileName,
    *,
    read: list[str],
    write: list[str],
    network: list[str],
    env: list[str],
    codex_sandbox_mode: Literal["read-only", "workspace-write"],
    timeout_seconds: int,
    max_output_bytes: int,
    requires_approval: bool = False,
    external_writes_enabled: bool = False,
    block_local_private_network: bool = True,
    admin_approved_network_wildcard: bool = False,
    notes: list[str] | None = None,
) -> ToolSandboxProfile:
    return ToolSandboxProfile(
        profile_name=profile_name,
        filesystem=FilesystemPolicy(
            read=_dedupe([*read, *TEMP_PATHS]),
            write=_dedupe(write),
            deny=list(DENIED_PATHS),
        ),
        network_allowlist=_dedupe(network),
        environment_variables=_safe_environment_variables(env),
        temp_directory_access=list(TEMP_PATHS),
        artifact_directories=list(ARTIFACT_WRITE_PATHS),
        cache_denial=True,
        secret_denial=True,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        requires_approval=requires_approval,
        codex_sandbox_mode=codex_sandbox_mode,
        external_writes_enabled=external_writes_enabled,
        block_local_private_network=block_local_private_network,
        admin_approved_network_wildcard=admin_approved_network_wildcard,
        notes=notes or [],
    )


def _profile_name_for_side_effect(
    side_effect: ToolSideEffectLevel,
    category: str,
) -> ToolSandboxProfileName:
    if category == "engineering":
        return "tool_engineering_only"
    if side_effect == "artifact_write":
        return "tool_artifact_write"
    if side_effect == "db_write":
        return "tool_project_write"
    if side_effect == "external_read":
        return "tool_external_read"
    if side_effect == "external_write":
        return "tool_external_write_requires_approval"
    if side_effect == "codex_subprocess":
        return "tool_codex_subprocess"
    return "tool_read_only"


def _network_allowlist_for_tool(
    spec: RuntimeToolSpec,
    manifest: ToolManifest | None,
) -> list[str]:
    values: list[str] = []
    if manifest is not None:
        values.extend(manifest.external_domains)
        for request in manifest.requested_network_access:
            values.extend(
                str(request[key])
                for key in ("domain", "host", "url", "cidr", "pattern")
                if isinstance(request.get(key), str)
            )
    policy = spec.metadata.get("tool_policy")
    if isinstance(policy, dict) and isinstance(policy.get("network_allowlist"), list):
        values.extend(str(item) for item in policy["network_allowlist"])
    return _dedupe(values)


def _environment_variables_for_tool(
    spec: RuntimeToolSpec,
    manifest: ToolManifest | None,
) -> list[str]:
    values: list[str] = []
    if manifest is not None:
        values.extend(manifest.requested_environment_variables)
    policy = spec.metadata.get("tool_policy")
    if isinstance(policy, dict) and isinstance(policy.get("environment_variables"), list):
        values.extend(str(item) for item in policy["environment_variables"])
    return _safe_environment_variables(values)


def _safe_environment_variables(values: list[str]) -> list[str]:
    unsafe = [
        value
        for value in values
        if re.search(r"(?:secret|credential|token|api[_-]?key|private[_-]?key)", value, re.I)
    ]
    if unsafe:
        raise SandboxProfileError("secret-like environment variables are denied")
    return _dedupe(values)


def _requests_danger_full_access(
    spec: RuntimeToolSpec,
    manifest: ToolManifest | None,
) -> bool:
    text = json.dumps(
        {
            "metadata": spec.metadata,
            "manifest": manifest.model_dump(mode="json") if manifest else None,
        },
        default=str,
        sort_keys=True,
    )
    return "danger-full-access" in text


def _has_network_wildcard(values: list[str]) -> bool:
    return any(
        value.strip().lower() in {"*", "*.*", "0.0.0.0/0", "::/0"}
        or value.strip().startswith("*.")
        or value.strip().endswith("/*")
        for value in values
    )


def _is_local_private(value: str) -> bool:
    host = value.strip().lower().split("://", maxsplit=1)[-1].split("/", maxsplit=1)[0]
    host = host.split(":", maxsplit=1)[0]
    return bool(LOCAL_PRIVATE_NETWORK_PATTERN.match(host))


def _matches_any(path: str, patterns: list[str]) -> bool:
    normalized = path.strip("/")
    for pattern in patterns:
        cleaned = pattern.strip("/")
        if cleaned.endswith("/**"):
            prefix = cleaned[:-3].strip("/")
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return True
        elif cleaned == normalized or cleaned == path:
            return True
        elif cleaned.startswith("**/") and normalized.endswith(cleaned[3:]):
            return True
        elif "*" in cleaned and re.fullmatch(re.escape(cleaned).replace("\\*", ".*"), normalized):
            return True
    return False


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(str(item)) for item in value) + "]"
    return json.dumps(str(value))


__all__ = [
    "FilesystemPolicy",
    "SandboxProfileError",
    "ToolSandboxProfile",
    "ToolSandboxProfileName",
    "default_sandbox_profile",
    "generate_codex_permission_profile_snippet",
    "get_sandbox_profile",
    "profile_for_tool",
]
