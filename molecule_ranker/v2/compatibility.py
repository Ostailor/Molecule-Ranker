from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from molecule_ranker.v2.release_contracts import V2_CONTRACT_VERSION

CompatibilityStatus = Literal[
    "compatible",
    "migration_available",
    "supported_deprecated",
    "unsupported",
]


@dataclass(frozen=True)
class V2CompatibilityReport:
    source_contract_version: str
    target_contract_version: str
    artifact_type: str
    status: CompatibilityStatus
    migration_path: str | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class V2DeprecationPolicy:
    supported_contract: str
    status: CompatibilityStatus
    notes: str
    removal_policy: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class V2MigrationPolicy:
    source_contract_version: str
    target_contract_version: str
    artifact_types: tuple[str, ...]
    migration_path: str
    notes: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_types"] = list(self.artifact_types)
        return payload


class V2CompatibilityMatrix:
    def __init__(
        self,
        *,
        deprecations: dict[str, V2DeprecationPolicy],
        migrations: tuple[V2MigrationPolicy, ...],
    ) -> None:
        self.deprecations = dict(deprecations)
        self.migrations = tuple(migrations)

    @classmethod
    def default(cls) -> V2CompatibilityMatrix:
        return cls(
            deprecations={
                "api.v1": V2DeprecationPolicy(
                    supported_contract="api.v1",
                    status="supported_deprecated",
                    notes="/api/v1 remains supported for V2.0 clients with deprecation notes.",
                    removal_policy="No removal before a documented enterprise migration window.",
                ),
                "artifacts.v1": V2DeprecationPolicy(
                    supported_contract="artifacts.v1",
                    status="supported_deprecated",
                    notes=(
                        "V1 artifact metadata is accepted only through explicit "
                        "migration reports."
                    ),
                    removal_policy="No silent removal; migrations must stay deterministic.",
                ),
            },
            migrations=(
                V2MigrationPolicy(
                    source_contract_version="1.0",
                    target_contract_version=V2_CONTRACT_VERSION,
                    artifact_types=(
                        "generated_candidates",
                        "review_queue",
                        "codex_backbone",
                        "integration_sync",
                        "experimental_evidence",
                        "project_export",
                    ),
                    migration_path="molecule-ranker migrate artifacts --target-version 2.0",
                    notes="V1.x artifacts require explicit V2 schema_version and contract_version.",
                ),
            ),
        )

    def evaluate_artifact(self, payload: dict[str, Any]) -> V2CompatibilityReport:
        artifact_type = str(payload.get("artifact_type") or "unknown")
        source = str(
            payload.get("contract_version")
            or payload.get("artifact_contract_version")
            or payload.get("schema_version")
            or "unknown"
        )
        if payload.get("contract_version") == V2_CONTRACT_VERSION:
            return V2CompatibilityReport(
                source_contract_version=source,
                target_contract_version=V2_CONTRACT_VERSION,
                artifact_type=artifact_type,
                status="compatible",
                notes="Artifact already declares the V2.0 contract.",
        )
        for migration in self.migrations:
            if (
                source == migration.source_contract_version
                and artifact_type in migration.artifact_types
            ):
                return V2CompatibilityReport(
                    source_contract_version=source,
                    target_contract_version=migration.target_contract_version,
                    artifact_type=artifact_type,
                    status="migration_available",
                    migration_path=migration.migration_path,
                    notes=migration.notes,
                )
        return V2CompatibilityReport(
            source_contract_version=source,
            target_contract_version=V2_CONTRACT_VERSION,
            artifact_type=artifact_type,
            status="unsupported",
            notes=f"No V2 migration path is registered for artifact_type={artifact_type}.",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "deprecations": {
                key: policy.as_dict() for key, policy in sorted(self.deprecations.items())
            },
            "migrations": [policy.as_dict() for policy in self.migrations],
        }
