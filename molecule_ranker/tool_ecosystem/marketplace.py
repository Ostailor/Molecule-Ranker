from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from molecule_ranker import __version__
from molecule_ranker.tool_ecosystem.registry import (
    ToolRegistryV2,
    ToolRegistryV2Error,
    hash_manifest,
)
from molecule_ranker.tool_ecosystem.schemas import (
    ToolApproval,
    ToolManifest,
    ToolPackage,
    ToolSecurityScan,
)
from molecule_ranker.tool_ecosystem.security import has_blocking_findings, scan_tool_package

MarketplaceSource = Literal["local", "internal_registry"]
PackageLifecycleState = Literal[
    "discovered",
    "quarantined",
    "scanned",
    "pending_approval",
    "approved",
    "enabled",
    "deprecated",
    "disabled",
    "revoked",
]


class MarketplaceError(ValueError):
    """Raised when marketplace operations violate local registry policy."""


class MarketplacePackageState(BaseModel):
    package_id: str
    version: str
    lifecycle_state: PackageLifecycleState
    installed_path: str | None = None
    pinned_version: str | None = None
    enabled_project_ids: list[str] = Field(default_factory=list)
    enabled_org_ids: list[str] = Field(default_factory=list)
    disabled_project_ids: list[str] = Field(default_factory=list)
    disabled_org_ids: list[str] = Field(default_factory=list)
    revoked_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MarketplaceUsageAnalytics(BaseModel):
    package_id: str
    package_version: str | None = None
    total_invocations: int
    status_counts: dict[str, int]
    tool_counts: dict[str, int]
    artifact_count: int
    warning_count: int


class MarketplaceState(BaseModel):
    packages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    manifests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scans: dict[str, dict[str, Any]] = Field(default_factory=dict)
    approvals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    external_registry_enabled: bool = False


class ToolMarketplace:
    """Local/internal workflow marketplace for governed tool packages.

    The marketplace intentionally has no external network integration. Local
    package installation reads package manifests from disk, moves packages into
    quarantine, records security scans and approvals, then mirrors approved
    packages into ``ToolRegistryV2``.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistryV2 | None = None,
        store_path: Path | None = None,
        external_registry_enabled: bool = False,
    ) -> None:
        self.registry = registry or ToolRegistryV2.default()
        self.store_path = store_path
        self.external_registry_enabled = external_registry_enabled
        self.packages: dict[tuple[str, str], ToolPackage] = {}
        self.manifests: dict[tuple[str, str], ToolManifest] = {}
        self.scans: dict[tuple[str, str], ToolSecurityScan] = {}
        self.approvals: dict[tuple[str, str], ToolApproval] = {}
        self.states: dict[tuple[str, str], MarketplacePackageState] = {}
        if store_path is not None and store_path.exists():
            self._load(store_path)

    def list_available_packages(
        self,
        *,
        include_installed: bool = True,
        source: MarketplaceSource = "local",
    ) -> list[ToolPackage]:
        if source != "local" and not self.external_registry_enabled:
            raise MarketplaceError("external registry support is disabled by default")
        packages = list(self.packages.values())
        if include_installed:
            return sorted(packages, key=lambda package: (package.name, package.version))
        installed_keys = {
            key for key, state in self.states.items() if state.lifecycle_state != "discovered"
        }
        return sorted(
            [package for key, package in self.packages.items() if key not in installed_keys],
            key=lambda package: (package.name, package.version),
        )

    def list_installed_packages(self) -> list[ToolPackage]:
        installed: list[ToolPackage] = []
        for key, state in self.states.items():
            if state.lifecycle_state == "discovered":
                continue
            package = self.packages.get(key)
            if package is not None:
                installed.append(package)
        return sorted(installed, key=lambda package: (package.name, package.version))

    def install_local_package(self, package_path: Path) -> ToolPackage:
        package, manifest = load_local_package(package_path)
        package = package.model_copy(
            update={
                "source": "local",
                "status": "quarantined",
                "manifest_hash": hash_manifest(manifest),
                "package_hash": package.package_hash or _hash_package_path(package_path),
                "updated_at": datetime.now(UTC),
                "metadata": {
                    **package.metadata,
                    "marketplace_lifecycle": "quarantined",
                    "installed_path": str(package_path),
                },
            }
        )
        key = _key(package.package_id, package.version)
        if key in self.packages:
            raise MarketplaceError(
                f"package version already installed: {package.package_id}@{package.version}"
            )
        self.registry.register_tool_package(package, manifest)
        self.packages[key] = package
        self.manifests[key] = manifest
        self.states[key] = MarketplacePackageState(
            package_id=package.package_id,
            version=package.version,
            lifecycle_state="quarantined",
            installed_path=str(package_path),
            pinned_version=package.version,
        )
        self._save()
        return package

    def uninstall_package(self, package_id: str, *, version: str | None = None) -> None:
        key = self._resolve_key(package_id, version)
        package = self.packages.get(key)
        if package is not None and package.status == "approved":
            self.registry.revoke_package(package.package_id, package.version)
        self.packages.pop(key, None)
        self.manifests.pop(key, None)
        self.scans.pop(key, None)
        self.approvals.pop(key, None)
        self.states.pop(key, None)
        self._save()

    def scan_package(self, package_id: str, *, version: str | None = None) -> ToolSecurityScan:
        key = self._resolve_key(package_id, version)
        package = self.packages[key]
        manifest = self.manifests[key]
        scan = scan_tool_package(package, manifest)
        package_status = "scanned" if scan.status == "passed" else "quarantined"
        self.packages[key] = package.model_copy(
            update={
                "status": package_status,
                "updated_at": datetime.now(UTC),
                "metadata": {
                    **package.metadata,
                    "security_scan_status": scan.status,
                    "marketplace_lifecycle": "scanned"
                    if scan.status == "passed"
                    else "quarantined",
                },
            }
        )
        self.scans[key] = scan
        self.states[key] = self.states[key].model_copy(
            update={
                "lifecycle_state": "pending_approval"
                if scan.status == "passed"
                else "quarantined"
            }
        )
        self._sync_registry_package(key)
        self._save()
        return scan

    def approve_package(
        self,
        package_id: str,
        *,
        version: str | None = None,
        approved_by: str = "marketplace-admin",
        rationale: str = "Approved through local/internal marketplace.",
    ) -> ToolApproval:
        key = self._resolve_key(package_id, version)
        package = self.packages[key]
        manifest = self.manifests[key]
        scan = self.scans.get(key)
        if scan is None:
            raise MarketplaceError("package must be scanned before approval")
        if has_blocking_findings(scan):
            raise MarketplaceError("package has critical security findings")
        if scan.status != "passed":
            raise MarketplaceError("package must pass security scan before approval")
        approval = ToolApproval(
            approval_id=f"tool-approval-{uuid4().hex[:12]}",
            package_id=package.package_id,
            package_version=package.version,
            approved_by=approved_by,
            approval_status="approved",
            rationale=rationale,
            approved_permissions=list(manifest.required_permissions),
            approved_filesystem_profile="none",
            approved_network_domains=list(manifest.external_domains),
            approved_at=datetime.now(UTC),
            expires_at=None,
            metadata={"marketplace": "local_internal"},
        )
        approved_package = package.model_copy(
            update={
                "status": "approved",
                "updated_at": datetime.now(UTC),
                "metadata": {
                    **package.metadata,
                    "security_scan_status": "passed",
                    "approval_status": "approved",
                    "marketplace_lifecycle": "approved",
                    "enabled": False,
                },
            }
        )
        self.packages[key] = approved_package
        self.approvals[key] = approval
        self.states[key] = self.states[key].model_copy(update={"lifecycle_state": "approved"})
        self._sync_registry_package(key)
        self.registry.activate_approved_package(package.package_id, package.version)
        self._apply_enablement_policy(key)
        self._save()
        return approval

    def revoke_package(self, package_id: str, *, version: str | None = None) -> ToolPackage:
        key = self._resolve_key(package_id, version)
        package = self.packages[key]
        revoked = package.model_copy(
            update={
                "status": "disabled",
                "updated_at": datetime.now(UTC),
                "metadata": {
                    **package.metadata,
                    "approval_status": "revoked",
                    "marketplace_lifecycle": "revoked",
                    "enabled": False,
                },
            }
        )
        self.packages[key] = revoked
        state = self.states[key]
        self.states[key] = state.model_copy(
            update={
                "lifecycle_state": "revoked",
                "enabled_project_ids": [],
                "enabled_org_ids": [],
                "revoked_at": datetime.now(UTC),
            }
        )
        self.registry.revoke_package(package.package_id, package.version)
        self._save()
        return revoked

    def enable_package(
        self,
        package_id: str,
        *,
        version: str | None = None,
        project_id: str | None = None,
        org_id: str | None = None,
    ) -> MarketplacePackageState:
        key = self._resolve_key(package_id, version)
        package = self.packages[key]
        if package.status != "approved":
            raise MarketplaceError("only approved packages can be enabled")
        state = self.states[key]
        project_ids = _append_unique(state.enabled_project_ids, project_id)
        org_ids = _append_unique(state.enabled_org_ids, org_id)
        self.states[key] = state.model_copy(
            update={
                "lifecycle_state": "enabled",
                "enabled_project_ids": project_ids,
                "enabled_org_ids": org_ids,
                "disabled_project_ids": [
                    item for item in state.disabled_project_ids if item != project_id
                ],
                "disabled_org_ids": [item for item in state.disabled_org_ids if item != org_id],
            }
        )
        self.packages[key] = package.model_copy(
            update={
                "metadata": {
                    **package.metadata,
                    "marketplace_lifecycle": "enabled",
                    "enabled": True,
                }
            }
        )
        self._apply_enablement_policy(key)
        self._save()
        return self.states[key]

    def disable_package(
        self,
        package_id: str,
        *,
        version: str | None = None,
        project_id: str | None = None,
        org_id: str | None = None,
    ) -> MarketplacePackageState:
        key = self._resolve_key(package_id, version)
        state = self.states[key]
        package = self.packages[key]
        if project_id is None and org_id is None:
            self.packages[key] = package.model_copy(
                update={
                    "status": "disabled",
                    "updated_at": datetime.now(UTC),
                    "metadata": {
                        **package.metadata,
                        "marketplace_lifecycle": "disabled",
                        "enabled": False,
                    },
                }
            )
            self.states[key] = state.model_copy(
                update={
                    "lifecycle_state": "disabled",
                    "enabled_project_ids": [],
                    "enabled_org_ids": [],
                }
            )
            self.registry.revoke_package(package.package_id, package.version)
        else:
            self.states[key] = state.model_copy(
                update={
                    "enabled_project_ids": [
                        item for item in state.enabled_project_ids if item != project_id
                    ],
                    "enabled_org_ids": [item for item in state.enabled_org_ids if item != org_id],
                    "disabled_project_ids": _append_unique(
                        state.disabled_project_ids, project_id
                    ),
                    "disabled_org_ids": _append_unique(state.disabled_org_ids, org_id),
                }
            )
            self._apply_enablement_policy(key)
        self._save()
        return self.states[key]

    def pin_package_version(
        self,
        package_id: str,
        version: str,
        *,
        project_id: str | None = None,
        org_id: str | None = None,
    ) -> MarketplacePackageState:
        key = self._resolve_key(package_id, version)
        state = self.states[key]
        pins = dict(state.metadata.get("version_pins", {}))
        scope = project_id or org_id or "global"
        pins[scope] = version
        self.states[key] = state.model_copy(
            update={"pinned_version": version, "metadata": {**state.metadata, "version_pins": pins}}
        )
        self._save()
        return self.states[key]

    def check_compatibility(self, package_id: str, *, version: str | None = None) -> dict[str, Any]:
        key = self._resolve_key(package_id, version)
        manifest = self.manifests[key]
        required = manifest.metadata.get("requires_molecule_ranker")
        compatible = True
        reason = "No explicit molecule-ranker version constraint."
        if isinstance(required, str) and required:
            compatible = _compatible_with_current_version(required)
            reason = (
                f"Current molecule-ranker {__version__} satisfies {required}."
                if compatible
                else f"Current molecule-ranker {__version__} does not satisfy {required}."
            )
        return {
            "package_id": key[0],
            "package_version": key[1],
            "compatible": compatible,
            "reason": reason,
            "external_registry_enabled": self.external_registry_enabled,
        }

    def view_security_scan(
        self, package_id: str, *, version: str | None = None
    ) -> ToolSecurityScan | None:
        return self.scans.get(self._resolve_key(package_id, version))

    def view_usage_analytics(
        self, package_id: str, *, version: str | None = None
    ) -> MarketplaceUsageAnalytics:
        key = self._resolve_key(package_id, version)
        records = [
            record
            for record in self.registry.usage_records
            if record.package_id == key[0] and (version is None or record.tool_version == key[1])
        ]
        status_counts: dict[str, int] = {}
        tool_counts: dict[str, int] = {}
        artifact_count = 0
        warning_count = 0
        for record in records:
            status_counts[record.status] = status_counts.get(record.status, 0) + 1
            tool_counts[record.tool_name] = tool_counts.get(record.tool_name, 0) + 1
            artifact_count += len(record.artifact_ids)
            warning_count += len(record.warnings)
        return MarketplaceUsageAnalytics(
            package_id=key[0],
            package_version=key[1],
            total_invocations=len(records),
            status_counts=status_counts,
            tool_counts=tool_counts,
            artifact_count=artifact_count,
            warning_count=warning_count,
        )

    def _resolve_key(self, package_id: str, version: str | None) -> tuple[str, str]:
        if version is not None:
            key = _key(package_id, version)
            if key not in self.packages:
                raise MarketplaceError(f"unknown package version: {package_id}@{version}")
            return key
        matches = sorted(key for key in self.packages if key[0] == package_id)
        if not matches:
            raise MarketplaceError(f"unknown package: {package_id}")
        pinned = [
            key
            for key in matches
            if self.states.get(key) is not None
            and self.states[key].pinned_version == key[1]
        ]
        return pinned[-1] if pinned else matches[-1]

    def _sync_registry_package(self, key: tuple[str, str]) -> None:
        if key not in self.registry.packages:
            self.registry.register_tool_package(self.packages[key], self.manifests[key])
            return
        self.registry.packages[key] = self.packages[key]
        self.registry.manifests[key] = self.manifests[key]

    def _apply_enablement_policy(self, key: tuple[str, str]) -> None:
        manifest = self.manifests[key]
        state = self.states[key]
        enabled = state.lifecycle_state == "enabled"
        allowed_projects = state.enabled_project_ids if enabled else ["__marketplace_disabled__"]
        allowed_orgs = state.enabled_org_ids if enabled else []
        for spec in manifest.tools:
            version = self.registry.active_versions.get(spec.tool_name)
            if version is None:
                continue
            runtime_key = (spec.tool_name, version)
            runtime_spec = self.registry.runtime_specs.get(runtime_key)
            if runtime_spec is None:
                continue
            existing_policy = runtime_spec.metadata.get("tool_policy")
            if not isinstance(existing_policy, dict):
                existing_policy = {}
            self.registry.runtime_specs[runtime_key] = runtime_spec.model_copy(
                update={
                    "metadata": {
                        **runtime_spec.metadata,
                        "tool_policy": {
                            **existing_policy,
                            "allowed_project_ids": allowed_projects,
                            "allowed_org_ids": allowed_orgs,
                            "disabled_project_ids": state.disabled_project_ids,
                            "disabled_org_ids": state.disabled_org_ids,
                            "marketplace_enabled": enabled,
                        },
                    }
                }
            )

    def _load(self, store_path: Path) -> None:
        raw = MarketplaceState.model_validate(json.loads(store_path.read_text(encoding="utf-8")))
        self.external_registry_enabled = raw.external_registry_enabled
        self.packages = {
            _split_key(key): ToolPackage.model_validate(value)
            for key, value in raw.packages.items()
        }
        self.manifests = {
            _split_key(key): ToolManifest.model_validate(value)
            for key, value in raw.manifests.items()
        }
        self.scans = {
            _split_key(key): ToolSecurityScan.model_validate(value)
            for key, value in raw.scans.items()
        }
        self.approvals = {
            _split_key(key): ToolApproval.model_validate(value)
            for key, value in raw.approvals.items()
        }
        self.states = {
            _split_key(key): MarketplacePackageState.model_validate(value)
            for key, value in raw.states.items()
        }
        for key, package in self.packages.items():
            manifest = self.manifests[key]
            if key not in self.registry.packages:
                self.registry.register_tool_package(package, manifest)
            elif package.status == "approved":
                self.registry.packages[key] = package
            if package.status == "approved":
                try:
                    self.registry.activate_approved_package(package.package_id, package.version)
                except ToolRegistryV2Error:
                    pass
                self._apply_enablement_policy(key)

    def _save(self) -> None:
        if self.store_path is None:
            return
        state = MarketplaceState(
            packages={
                _format_key(key): package.model_dump(mode="json")
                for key, package in self.packages.items()
            },
            manifests={
                _format_key(key): manifest.model_dump(mode="json")
                for key, manifest in self.manifests.items()
            },
            scans={
                _format_key(key): scan.model_dump(mode="json") for key, scan in self.scans.items()
            },
            approvals={
                _format_key(key): approval.model_dump(mode="json")
                for key, approval in self.approvals.items()
            },
            states={
                _format_key(key): state.model_dump(mode="json")
                for key, state in self.states.items()
            },
            external_registry_enabled=self.external_registry_enabled,
        )
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def load_local_package(package_path: Path) -> tuple[ToolPackage, ToolManifest]:
    if package_path.is_file():
        raw = json.loads(package_path.read_text(encoding="utf-8"))
        return _load_package_payload(raw, package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise MarketplaceError(f"local package path does not exist: {package_path}")
    package_file = package_path / "tool_package.json"
    manifest_file = package_path / "tool_manifest.json"
    combined_file = package_path / "manifest.json"
    if package_file.exists() and manifest_file.exists():
        package = ToolPackage.model_validate(
            json.loads(package_file.read_text(encoding="utf-8"))
        )
        manifest = ToolManifest.model_validate(
            json.loads(manifest_file.read_text(encoding="utf-8"))
        )
        return package, manifest
    if combined_file.exists():
        return _load_package_payload(
            json.loads(combined_file.read_text(encoding="utf-8")),
            combined_file,
        )
    raise MarketplaceError(
        "local package must contain tool_package.json and tool_manifest.json, or manifest.json"
    )


def _load_package_payload(
    raw: Any,
    source_path: Path,
) -> tuple[ToolPackage, ToolManifest]:
    if not isinstance(raw, dict):
        raise MarketplaceError("local package manifest must be a JSON object")
    if "package" in raw and "manifest" in raw:
        package = ToolPackage.model_validate(raw["package"])
        manifest = ToolManifest.model_validate(raw["manifest"])
        return package, manifest
    manifest = ToolManifest.model_validate(raw)
    package = ToolPackage(
        package_id=manifest.package_id,
        name=manifest.package_name,
        display_name=manifest.package_name,
        description=f"Local package from {source_path.name}.",
        package_type="plugin",
        version=manifest.package_version,
        publisher="local",
        source="local",
        status="discovered",
        tool_count=len(manifest.tools),
        skill_count=len(manifest.skills),
        workflow_count=len(manifest.workflows),
        manifest_hash=hash_manifest(manifest),
        package_hash=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={},
    )
    return package, manifest


def _hash_package_path(package_path: Path) -> str:
    digest = hashlib.sha256()
    if package_path.is_file():
        digest.update(package_path.read_bytes())
        return "sha256:" + digest.hexdigest()
    for path in sorted(item for item in package_path.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(package_path)).encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _compatible_with_current_version(required: str) -> bool:
    if required.startswith("^"):
        major = required[1:].split(".", maxsplit=1)[0]
        return __version__.split(".", maxsplit=1)[0] == major
    if required.startswith(">="):
        return _version_tuple(__version__) >= _version_tuple(required[2:])
    return required == __version__


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for raw in value.split("."):
        digits = "".join(char for char in raw if char.isdigit())
        parts.append(int(digits or "0"))
    return tuple(parts)


def _append_unique(values: list[str], value: str | None) -> list[str]:
    if value is None or value in values:
        return values
    return [*values, value]


def _key(package_id: str, version: str) -> tuple[str, str]:
    return (package_id, version)


def _format_key(key: tuple[str, str]) -> str:
    return f"{key[0]}@{key[1]}"


def _split_key(value: str) -> tuple[str, str]:
    package_id, version = value.rsplit("@", maxsplit=1)
    return package_id, version


__all__ = [
    "MarketplaceError",
    "MarketplacePackageState",
    "MarketplaceUsageAnalytics",
    "PackageLifecycleState",
    "ToolMarketplace",
    "ToolPackage",
    "load_local_package",
]
