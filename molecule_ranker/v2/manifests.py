from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molecule_ranker import __version__
from molecule_ranker.v2.compatibility import V2CompatibilityMatrix
from molecule_ranker.v2.release_contracts import (
    V2_API_CONTRACT_VERSION,
    V2_API_ROUTES,
    V2_ARTIFACT_SCHEMAS,
    V2_CLI_COMMAND_GROUPS,
    V2_CONTRACT_VERSION,
    V2_DATABASE_SCHEMA_VERSION,
    V2_RELEASE_CONTRACTS,
    V2_SCHEMA_VERSION,
)


@dataclass(frozen=True)
class V2ArtifactValidationResult:
    artifact_type: str
    valid: bool
    errors: list[str]
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def validate_v2_artifact_payload(
    payload: dict[str, Any],
    artifact_type: str | None = None,
) -> V2ArtifactValidationResult:
    selected_type = artifact_type or str(payload.get("artifact_type") or "")
    contract = V2_ARTIFACT_SCHEMAS.get(selected_type)
    errors: list[str] = []
    warnings: list[str] = []
    if contract is None:
        return V2ArtifactValidationResult(
            artifact_type=selected_type or "unknown",
            valid=False,
            errors=[
                "No V2 artifact schema registered for "
                f"artifact_type={selected_type or 'unknown'}."
            ],
            warnings=[],
        )
    if payload.get("schema_version") != V2_SCHEMA_VERSION:
        errors.append(f"schema_version must be {V2_SCHEMA_VERSION}")
    if payload.get("contract_version") != V2_CONTRACT_VERSION:
        errors.append(f"contract_version must be {V2_CONTRACT_VERSION}")
    missing = [field for field in contract.required_fields if field not in payload]
    errors.extend(f"missing required field: {field}" for field in missing)
    if "artifact_contract_version" in payload and "contract_version" not in payload:
        warnings.append("V1 artifact_contract_version is deprecated; use contract_version.")
    return V2ArtifactValidationResult(
        artifact_type=contract.artifact_type,
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )


def validate_v2_release_contracts() -> dict[str, Any]:
    errors: list[str] = []
    if not all(route.startswith("/api/v2/") for route in V2_API_ROUTES):
        errors.append("All V2 API routes must start with /api/v2/.")
    if "/api/v2/version" not in V2_API_ROUTES:
        errors.append("V2 API routes must include /api/v2/version.")
    if "v2" not in V2_CLI_COMMAND_GROUPS:
        errors.append("V2 CLI command groups must include v2.")
    for contract in V2_RELEASE_CONTRACTS:
        if contract.schema_version != V2_SCHEMA_VERSION:
            errors.append(f"{contract.contract_id}: schema_version must be {V2_SCHEMA_VERSION}.")
        if contract.contract_version != V2_CONTRACT_VERSION:
            errors.append(
                f"{contract.contract_id}: contract_version must be {V2_CONTRACT_VERSION}."
            )
        if not contract.breaking_changes_documented:
            errors.append(f"{contract.contract_id}: breaking changes must be documented.")
    for artifact_type, contract in V2_ARTIFACT_SCHEMAS.items():
        if "schema_version" not in contract.required_fields:
            errors.append(f"{artifact_type}: schema_version is required.")
        if "contract_version" not in contract.required_fields:
            errors.append(f"{artifact_type}: contract_version is required.")
    compatibility = {
        key: policy.as_dict()
        for key, policy in V2CompatibilityMatrix.default().deprecations.items()
    }
    return {
        "name": "molecule-ranker",
        "version": __version__,
        "schema_version": V2_SCHEMA_VERSION,
        "contract_version": V2_CONTRACT_VERSION,
        "api_contract_version": V2_API_CONTRACT_VERSION,
        "valid": not errors,
        "errors": errors,
        "contract_count": len(V2_RELEASE_CONTRACTS),
        "artifact_schema_count": len(V2_ARTIFACT_SCHEMAS),
        "api_route_count": len(V2_API_ROUTES),
        "compatibility": compatibility,
    }


def export_v2_release_manifest() -> dict[str, Any]:
    matrix = V2CompatibilityMatrix.default()
    return {
        "name": "molecule-ranker",
        "version": __version__,
        "schema_version": V2_SCHEMA_VERSION,
        "contract_version": V2_CONTRACT_VERSION,
        "api_contract_version": V2_API_CONTRACT_VERSION,
        "database_schema_version": V2_DATABASE_SCHEMA_VERSION,
        "contracts": {
            contract.contract_id: contract.as_dict()
            for contract in sorted(V2_RELEASE_CONTRACTS, key=lambda item: item.contract_id)
        },
        "api_routes": list(V2_API_ROUTES),
        "artifact_schemas": {
            artifact_type: contract.as_dict()
            for artifact_type, contract in sorted(V2_ARTIFACT_SCHEMAS.items())
        },
        "cli_command_groups": list(V2_CLI_COMMAND_GROUPS),
        "compatibility_matrix": matrix.as_dict(),
    }


def write_v2_release_manifest(output: str | Path) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(export_v2_release_manifest(), indent=2, sort_keys=True) + "\n")
    return target
