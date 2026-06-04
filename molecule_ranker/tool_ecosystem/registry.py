from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker import __version__
from molecule_ranker.runtime_agents.schemas import RuntimeToolSpec
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry
from molecule_ranker.tool_ecosystem.schemas import (
    ToolManifest,
    ToolPackage,
    ToolUsageRecord,
    ToolVersion,
)

NAMESPACE_RE = re.compile(r"^(?:builtins|mcp|plugin|connector)\.[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
BUILTINS_PACKAGE_ID = "builtins"
BUILTINS_PACKAGE_VERSION = __version__


class ToolRegistryV2Error(ValueError):
    """Raised when governed tool registry operations violate policy."""


class ToolRegistryV2:
    """Governed tool registry layered over the V2 runtime tool registry.

    V2.1 runtime tools remain available through ``RuntimeToolRegistry``. This
    registry mirrors them under namespace-qualified names and only exposes
    package tools after manifest validation, scan/approval metadata, and version
    activation.
    """

    def __init__(self, *, register_builtins: bool = True) -> None:
        self.packages: dict[tuple[str, str], ToolPackage] = {}
        self.manifests: dict[tuple[str, str], ToolManifest] = {}
        self.tool_versions: dict[tuple[str, str], ToolVersion] = {}
        self.runtime_specs: dict[tuple[str, str], RuntimeToolSpec] = {}
        self.active_versions: dict[str, str] = {}
        self.quarantined_packages: set[tuple[str, str]] = set()
        self.disabled_tools: set[tuple[str, str]] = set()
        self.usage_records: list[ToolUsageRecord] = []
        self._legacy_aliases: dict[str, str] = {}
        if register_builtins:
            self.register_builtin_tools()

    @classmethod
    def default(cls) -> ToolRegistryV2:
        return cls(register_builtins=True)

    def register_builtin_tools(self) -> None:
        specs = [_qualify_builtin_spec(spec) for spec in RuntimeToolRegistry.default().list_tools()]
        package = ToolPackage(
            package_id=BUILTINS_PACKAGE_ID,
            name="builtins",
            display_name="Built-in molecule-ranker tools",
            description="First-party deterministic molecule-ranker runtime tools.",
            package_type="internal",
            version=BUILTINS_PACKAGE_VERSION,
            publisher="molecule-ranker",
            source="built_in",
            status="approved",
            tool_count=len(specs),
            skill_count=0,
            workflow_count=0,
            manifest_hash="pending",
            package_hash=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            metadata={"security_scan_status": "passed", "approval_status": "approved"},
        )
        manifest = ToolManifest(
            manifest_id="builtins-manifest",
            package_id=package.package_id,
            package_name=package.name,
            package_version=package.version,
            tools=specs,
            skills=[],
            workflows=[],
            required_permissions=sorted(
                {permission for spec in specs for permission in spec.required_permissions}
            ),
            requested_filesystem_access=[],
            requested_network_access=[],
            requested_environment_variables=[],
            external_domains=[],
            side_effect_summary=_side_effect_summary(specs),
            scientific_guardrail_tags=["first_party", "deterministic"],
            license=None,
            metadata={"source_registry": "RuntimeToolRegistry.default"},
        )
        package = package.model_copy(update={"manifest_hash": hash_manifest(manifest)})
        versions = [_tool_version_for_spec(package, spec) for spec in specs]
        self.register_tool_package(package, manifest, versions=versions)
        for spec in specs:
            legacy_name = str(spec.metadata.get("legacy_tool_name") or spec.tool_name)
            self._legacy_aliases[legacy_name] = spec.tool_name

    def register_tool_package(
        self,
        package: ToolPackage,
        manifest: ToolManifest,
        *,
        versions: list[ToolVersion] | None = None,
    ) -> None:
        self.validate_manifest(package, manifest)
        package_key = (package.package_id, package.version)
        if package_key in self.packages:
            raise ToolRegistryV2Error(
                f"tool package version already registered: {package.package_id}@{package.version}"
            )
        self.packages[package_key] = package
        self.manifests[package_key] = manifest
        if package.status != "approved":
            self.quarantined_packages.add(package_key)
            return
        active_versions = versions or [
            _tool_version_for_spec(package, spec) for spec in manifest.tools
        ]
        self._activate_package_tools(package, manifest, active_versions)

    def validate_manifest(self, package: ToolPackage, manifest: ToolManifest) -> None:
        if manifest.package_id != package.package_id:
            raise ToolRegistryV2Error("manifest package_id does not match package")
        if manifest.package_version != package.version:
            raise ToolRegistryV2Error("manifest package_version does not match package")
        if manifest.package_name != package.name:
            raise ToolRegistryV2Error("manifest package_name does not match package")
        if package.tool_count != len(manifest.tools):
            raise ToolRegistryV2Error("package tool_count does not match manifest tools")
        if package.skill_count != len(manifest.skills):
            raise ToolRegistryV2Error("package skill_count does not match manifest skills")
        if package.workflow_count != len(manifest.workflows):
            raise ToolRegistryV2Error("package workflow_count does not match manifest workflows")
        observed_hash = hash_manifest(manifest)
        if package.manifest_hash != observed_hash:
            raise ToolRegistryV2Error("manifest hash mismatch")
        for spec in manifest.tools:
            self._validate_runtime_spec(spec)

    def activate_approved_package(self, package_id: str, package_version: str) -> None:
        package_key = (package_id, package_version)
        package = self.packages.get(package_key)
        manifest = self.manifests.get(package_key)
        if package is None or manifest is None:
            raise ToolRegistryV2Error(f"unknown tool package: {package_id}@{package_version}")
        if package.status != "approved":
            raise ToolRegistryV2Error("only approved tool packages can be activated")
        self._activate_package_tools(
            package,
            manifest,
            [_tool_version_for_spec(package, spec) for spec in manifest.tools],
        )
        self.quarantined_packages.discard(package_key)

    def disable_tool(self, tool_name: str, version: str | None = None) -> None:
        resolved_name = self._resolve_alias(tool_name)
        resolved_version = version or self.active_versions.get(resolved_name)
        if resolved_version is None:
            raise ToolRegistryV2Error(f"unknown active tool: {tool_name}")
        self.disabled_tools.add((resolved_name, resolved_version))
        if self.active_versions.get(resolved_name) == resolved_version:
            del self.active_versions[resolved_name]
        version_record = self.tool_versions.get((resolved_name, resolved_version))
        if version_record is not None:
            self.tool_versions[(resolved_name, resolved_version)] = version_record.model_copy(
                update={"status": "disabled"}
            )

    def revoke_package(self, package_id: str, package_version: str) -> None:
        package_key = (package_id, package_version)
        package = self.packages.get(package_key)
        if package is None:
            raise ToolRegistryV2Error(f"unknown tool package: {package_id}@{package_version}")
        self.packages[package_key] = package.model_copy(update={"status": "disabled"})
        manifest = self.manifests.get(package_key)
        if manifest is None:
            return
        for spec in manifest.tools:
            version = self.active_versions.get(spec.tool_name, package_version)
            self.disable_tool(spec.tool_name, version=version)

    def list_tools_visible_to_user(
        self,
        *,
        user_permissions: set[str] | list[str] | None = None,
        project_id: str | None = None,
        org_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[RuntimeToolSpec]:
        permissions = set(user_permissions or [])
        visible: list[RuntimeToolSpec] = []
        for tool_name, version in sorted(self.active_versions.items()):
            spec = self.runtime_specs[(tool_name, version)]
            if not include_disabled and (tool_name, version) in self.disabled_tools:
                continue
            if not set(spec.required_permissions).issubset(permissions):
                continue
            if not _policy_allows(
                spec.metadata.get("tool_policy"),
                org_id=org_id,
                project_id=project_id,
                user_permissions=permissions,
            ):
                continue
            visible.append(spec)
        return visible

    def resolve_tool(
        self,
        tool_name: str,
        *,
        version: str | None = None,
        include_disabled: bool = False,
    ) -> RuntimeToolSpec:
        resolved_name = self._resolve_alias(tool_name)
        resolved_version = version or self.active_versions.get(resolved_name)
        if resolved_version is None:
            raise KeyError(f"tool is not active: {tool_name}")
        key = (resolved_name, resolved_version)
        if not include_disabled and key in self.disabled_tools:
            raise ToolRegistryV2Error(f"tool is disabled: {resolved_name}@{resolved_version}")
        try:
            return self.runtime_specs[key]
        except KeyError as exc:
            raise KeyError(f"unknown tool version: {resolved_name}@{resolved_version}") from exc

    def generate_runtime_tool_specs(
        self, package_id: str, package_version: str
    ) -> list[RuntimeToolSpec]:
        package_key = (package_id, package_version)
        package = self.packages.get(package_key)
        manifest = self.manifests.get(package_key)
        if package is None or manifest is None:
            raise ToolRegistryV2Error(f"unknown tool package: {package_id}@{package_version}")
        if package.status != "approved":
            raise ToolRegistryV2Error("unapproved packages cannot generate runtime tool specs")
        return [self._runtime_spec_from_manifest_tool(package, spec) for spec in manifest.tools]

    def to_runtime_tool_registry(self) -> RuntimeToolRegistry:
        registry = RuntimeToolRegistry()
        for tool_name, version in sorted(self.active_versions.items()):
            if (tool_name, version) not in self.disabled_tools:
                registry.register(self.runtime_specs[(tool_name, version)])
        return registry

    def record_usage(self, usage: ToolUsageRecord) -> None:
        key = (self._resolve_alias(usage.tool_name), usage.tool_version)
        if key in self.disabled_tools:
            raise ToolRegistryV2Error(f"disabled tool cannot record execution: {usage.tool_name}")
        if key not in self.runtime_specs:
            raise ToolRegistryV2Error(f"unknown tool usage target: {usage.tool_name}")
        self.usage_records.append(usage)

    def track_usage(
        self,
        *,
        package_id: str,
        tool_name: str,
        tool_version: str,
        invoked_by: str,
        status: str,
        session_id: str | None = None,
        project_id: str | None = None,
        artifact_ids: list[str] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> ToolUsageRecord:
        usage = ToolUsageRecord(
            usage_id=f"tool-usage-{uuid4().hex[:12]}",
            session_id=session_id,
            project_id=project_id,
            package_id=package_id,
            tool_name=tool_name,
            tool_version=tool_version,
            invoked_by=invoked_by,  # type: ignore[arg-type]
            status=status,
            started_at=started_at or datetime.now(UTC),
            completed_at=completed_at,
            artifact_ids=artifact_ids or [],
            warnings=warnings or [],
            metadata=metadata or {},
        )
        self.record_usage(usage)
        return usage

    def _activate_package_tools(
        self,
        package: ToolPackage,
        manifest: ToolManifest,
        versions: list[ToolVersion],
    ) -> None:
        version_by_tool = {version.tool_name: version for version in versions}
        missing = sorted(
            spec.tool_name for spec in manifest.tools if spec.tool_name not in version_by_tool
        )
        if missing:
            raise ToolRegistryV2Error("missing tool versions for: " + ", ".join(missing))
        for spec in manifest.tools:
            version = version_by_tool[spec.tool_name]
            self._validate_tool_version(spec, version)
            key = (spec.tool_name, version.version)
            existing = self.tool_versions.get(key)
            if existing is not None:
                if (
                    existing.input_schema_hash != version.input_schema_hash
                    or existing.output_schema_hash != version.output_schema_hash
                    or existing.implementation_hash != version.implementation_hash
                ):
                    raise ToolRegistryV2Error("schema hash mismatch for existing tool version")
                raise ToolRegistryV2Error(
                    f"tool version already registered: {spec.tool_name}@{version.version}"
                )
            runtime_spec = self._runtime_spec_from_manifest_tool(package, spec, version=version)
            RuntimeToolRegistry([runtime_spec])
            self.tool_versions[key] = version.model_copy(update={"status": "active"})
            self.runtime_specs[key] = runtime_spec
            self.active_versions[spec.tool_name] = version.version
            self.disabled_tools.discard(key)

    def _runtime_spec_from_manifest_tool(
        self,
        package: ToolPackage,
        spec: RuntimeToolSpec,
        *,
        version: ToolVersion | None = None,
    ) -> RuntimeToolSpec:
        tool_version = version or _tool_version_for_spec(package, spec)
        return spec.model_copy(
            update={
                "metadata": {
                    **spec.metadata,
                    "tool_package": {
                        "package_id": package.package_id,
                        "version": package.version,
                        "manifest_hash": package.manifest_hash,
                        "signature": f"sha256:{package.manifest_hash}",
                        "approval_status": package.metadata.get("approval_status"),
                        "security_scan_status": package.metadata.get("security_scan_status"),
                        "status": package.status,
                    },
                    "tool_version": tool_version.model_dump(mode="json"),
                }
            }
        )

    def _validate_runtime_spec(self, spec: RuntimeToolSpec) -> None:
        if not is_namespace_qualified(spec.tool_name):
            raise ToolRegistryV2Error(f"tool name must be namespace-qualified: {spec.tool_name}")
        RuntimeToolRegistry([spec])

    def _validate_tool_version(self, spec: RuntimeToolSpec, version: ToolVersion) -> None:
        if version.tool_name != spec.tool_name:
            raise ToolRegistryV2Error("tool version name does not match manifest tool")
        if version.input_schema_hash != hash_schema(spec.input_schema):
            raise ToolRegistryV2Error("input schema hash mismatch")
        if version.output_schema_hash != hash_schema(spec.output_schema):
            raise ToolRegistryV2Error("output schema hash mismatch")

    def _resolve_alias(self, tool_name: str) -> str:
        return self._legacy_aliases.get(tool_name, tool_name)


def is_namespace_qualified(tool_name: str) -> bool:
    return bool(NAMESPACE_RE.fullmatch(tool_name))


def hash_schema(schema: dict[str, Any]) -> str:
    return _hash_json(schema)


def hash_manifest(manifest: ToolManifest) -> str:
    payload = manifest.model_dump(mode="json", exclude={"metadata"})
    return _hash_json(payload)


def _hash_json(payload: Any) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _tool_version_for_spec(package: ToolPackage, spec: RuntimeToolSpec) -> ToolVersion:
    return ToolVersion(
        tool_version_id=f"{package.package_id}:{spec.tool_name}:{package.version}",
        package_id=package.package_id,
        tool_name=spec.tool_name,
        version=package.version,
        input_schema_hash=hash_schema(spec.input_schema),
        output_schema_hash=hash_schema(spec.output_schema),
        implementation_hash=_implementation_hash(spec),
        status="active",
        created_at=datetime.now(UTC),
        metadata={},
    )


def _implementation_hash(spec: RuntimeToolSpec) -> str | None:
    entrypoint = spec.metadata.get("deterministic_entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        return None
    return _hash_json({"deterministic_entrypoint": entrypoint})


def _qualify_builtin_spec(spec: RuntimeToolSpec) -> RuntimeToolSpec:
    qualified_name = f"builtins.{spec.category}.{spec.tool_name}"
    return spec.model_copy(
        update={
            "tool_name": qualified_name,
            "metadata": {
                **spec.metadata,
                "legacy_tool_name": spec.tool_name,
                "namespace": "builtins",
            },
        }
    )


def _side_effect_summary(specs: list[RuntimeToolSpec]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for spec in specs:
        summary[spec.side_effect_level] = int(summary.get(spec.side_effect_level, 0)) + 1
    return summary


def _policy_allows(
    raw_policy: Any,
    *,
    org_id: str | None,
    project_id: str | None,
    user_permissions: set[str],
) -> bool:
    if not isinstance(raw_policy, dict):
        return True
    allowed_orgs = _string_set(raw_policy.get("allowed_org_ids"))
    allowed_projects = _string_set(raw_policy.get("allowed_project_ids"))
    disabled_orgs = _string_set(raw_policy.get("disabled_org_ids"))
    disabled_projects = _string_set(raw_policy.get("disabled_project_ids"))
    required_permissions = _string_set(raw_policy.get("required_permissions"))
    if disabled_orgs and org_id in disabled_orgs:
        return False
    if disabled_projects and project_id in disabled_projects:
        return False
    if allowed_orgs and org_id not in allowed_orgs:
        return False
    if allowed_projects and project_id not in allowed_projects:
        return False
    if required_permissions and not required_permissions.issubset(user_permissions):
        return False
    return True


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


__all__ = [
    "BUILTINS_PACKAGE_ID",
    "ToolManifest",
    "ToolPackage",
    "ToolRegistryV2",
    "ToolRegistryV2Error",
    "ToolUsageRecord",
    "ToolVersion",
    "hash_manifest",
    "hash_schema",
    "is_namespace_qualified",
]
