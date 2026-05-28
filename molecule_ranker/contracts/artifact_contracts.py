from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ARTIFACT_CONTRACT_VERSION = "1.0"
ARTIFACT_SCHEMA_VERSION = "1.0"

ArtifactFormat = Literal["json", "markdown", "zip"]
Validator = Callable[[Path, "ArtifactContract", bool], "ArtifactValidationResult"]


@dataclass(frozen=True)
class ArtifactContract:
    filename: str
    artifact_type: str
    schema_version: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    compatibility_notes: str
    artifact_contract_version: str = ARTIFACT_CONTRACT_VERSION
    format: ArtifactFormat = "json"

    def validate(self, path: str | Path, *, migrate: bool = False) -> ArtifactValidationResult:
        return validate_artifact_file(path, migrate=migrate, contract=self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "artifact_contract_version": self.artifact_contract_version,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "compatibility_notes": self.compatibility_notes,
            "format": self.format,
        }


@dataclass(frozen=True)
class ArtifactValidationResult:
    path: Path
    artifact_type: str
    schema_version: str
    artifact_contract_version: str
    valid: bool
    errors: list[str]
    warnings: list[str]
    migrated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "artifact_contract_version": self.artifact_contract_version,
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "migrated": self.migrated,
        }


@dataclass(frozen=True)
class ArtifactDirectoryValidationReport:
    path: Path
    valid: bool
    artifact_count: int
    migrated_count: int
    results: list[ArtifactValidationResult]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "valid": self.valid,
            "artifact_count": self.artifact_count,
            "migrated_count": self.migrated_count,
            "results": [result.as_dict() for result in self.results],
        }


def _contract(
    filename: str,
    artifact_type: str,
    required_fields: tuple[str, ...],
    optional_fields: tuple[str, ...] = (),
    compatibility_notes: str = "",
    *,
    format: ArtifactFormat = "json",
) -> ArtifactContract:
    return ArtifactContract(
        filename=filename,
        artifact_type=artifact_type,
        schema_version=ARTIFACT_SCHEMA_VERSION,
        required_fields=required_fields,
        optional_fields=optional_fields,
        compatibility_notes=compatibility_notes
        or "V1.0 accepts legacy V0.9 JSON after adding contract metadata.",
        format=format,
    )


ARTIFACT_CONTRACTS: dict[str, ArtifactContract] = {
    "candidates.json": _contract(
        "candidates.json",
        "candidates",
        ("success", "disease", "targets", "candidates", "summary"),
        (
            "generated_molecule_hypotheses",
            "developability_assessments",
            "literature_evidence_summary",
            "limitations",
        ),
    ),
    "generated_candidates.json": _contract(
        "generated_candidates.json",
        "generated_candidates",
        ("success", "generation_enabled", "generated_count"),
        (
            "retained_count",
            "rejected_count",
            "objectives",
            "seeds",
            "retained_generated_molecules",
            "rejected_generated_molecules",
            "generation_config",
            "limitations",
        ),
    ),
    "generation_trace.json": _contract(
        "generation_trace.json",
        "generation_trace",
        ("seed_selection_trace", "generator_trace"),
        (
            "objective_building_trace",
            "validation_filtering_trace",
            "scoring_trace",
            "run_timestamp",
            "generator_version",
        ),
    ),
    "developability.json": _contract(
        "developability.json",
        "developability",
        ("success", "enabled"),
        (
            "assessed_existing_count",
            "assessed_generated_count",
            "risk_distribution",
            "limitations",
        ),
    ),
    "experimental_results.json": _contract(
        "experimental_results.json",
        "experimental_results",
        ("summary", "results"),
        ("limitations",),
    ),
    "experimental_evidence.json": _contract(
        "experimental_evidence.json",
        "experimental_evidence",
        ("success", "loaded_result_ids", "linked_result_ids"),
        ("candidate_summaries", "generated_summaries", "limitations"),
    ),
    "active_learning_batch.json": _contract(
        "active_learning_batch.json",
        "active_learning_batch",
        ("success", "suggestions"),
        ("strategy", "excluded_candidates", "limitations"),
    ),
    "review_queue.json": _contract(
        "review_queue.json",
        "review_queue",
        ("workspace_id", "review_items"),
        ("summary", "audit_events"),
    ),
    "codex_backbone.json": _contract(
        "codex_backbone.json",
        "codex_backbone",
        ("summary", "results"),
        ("guardrail_warnings", "limitations"),
    ),
    "integration_sync.json": _contract(
        "integration_sync.json",
        "integration_sync",
        ("sync_job", "records"),
        ("contract_report", "mapping_report", "artifact_manifest"),
    ),
    "report.md": _contract(
        "report.md",
        "report",
        ("# ",),
        (),
        "Markdown reports are validated by content and cannot be migrated in place safely.",
        format="markdown",
    ),
    "trace.json": _contract(
        "trace.json",
        "trace",
        ("success", "traces", "artifacts"),
        ("config", "limitations", "developability_run"),
    ),
    "project_export.zip": _contract(
        "project_export.zip",
        "project_export",
        ("exported_at", "project_id", "project", "artifact_manifest"),
        (
            "project_permissions",
            "project_runs",
            "audit_events",
        ),
        "ZIP exports validate the embedded project_export.json member.",
        format="zip",
    ),
}


def artifact_contract_for_path(path: str | Path) -> ArtifactContract | None:
    resolved = Path(path)
    if resolved.name in ARTIFACT_CONTRACTS:
        return ARTIFACT_CONTRACTS[resolved.name]
    if resolved.suffix == ".zip":
        return ARTIFACT_CONTRACTS.get("project_export.zip")
    return None


def validate_artifact_file(
    path: str | Path,
    *,
    migrate: bool = False,
    contract: ArtifactContract | None = None,
) -> ArtifactValidationResult:
    artifact_path = Path(path)
    selected = contract or artifact_contract_for_path(artifact_path)
    if selected is None:
        return ArtifactValidationResult(
            path=artifact_path,
            artifact_type="unknown",
            schema_version="",
            artifact_contract_version="",
            valid=True,
            errors=[],
            warnings=["No V1.0 artifact contract registered for this path."],
        )
    if selected.format == "markdown":
        return _validate_markdown_artifact(artifact_path, selected)
    if selected.format == "zip":
        return _validate_zip_artifact(artifact_path, selected)
    return _validate_json_artifact(artifact_path, selected, migrate=migrate)


def validate_artifact_directory(
    path: str | Path,
    *,
    migrate: bool = False,
) -> ArtifactDirectoryValidationReport:
    root = Path(path)
    results: list[ArtifactValidationResult] = []
    if not root.exists() or not root.is_dir():
        return ArtifactDirectoryValidationReport(
            path=root,
            valid=False,
            artifact_count=0,
            migrated_count=0,
            results=[
                ArtifactValidationResult(
                    path=root,
                    artifact_type="directory",
                    schema_version="",
                    artifact_contract_version="",
                    valid=False,
                    errors=["artifact directory does not exist"],
                    warnings=[],
                )
            ],
        )
    for child in sorted(root.iterdir()):
        if not child.is_file() or artifact_contract_for_path(child) is None:
            continue
        results.append(validate_artifact_file(child, migrate=migrate))
    return ArtifactDirectoryValidationReport(
        path=root,
        valid=all(result.valid for result in results),
        artifact_count=len(results),
        migrated_count=sum(1 for result in results if result.migrated),
        results=results,
    )


def with_artifact_contract_metadata(
    payload: dict[str, Any],
    artifact_type: str,
    *,
    schema_version: str = ARTIFACT_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "schema_version": schema_version,
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        **payload,
    }


def _validate_json_artifact(
    path: Path,
    contract: ArtifactContract,
    *,
    migrate: bool,
) -> ArtifactValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    migrated = False
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return _result(path, contract, False, [f"invalid JSON: {exc}"], [], migrated=False)
    if not isinstance(payload, dict):
        return _result(
            path,
            contract,
            False,
            ["artifact JSON must be an object"],
            [],
            migrated=False,
        )
    if _needs_contract_migration(payload):
        if migrate:
            payload = with_artifact_contract_metadata(payload, contract.artifact_type)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            migrated = True
            warnings.append("added V1.0 artifact contract metadata")
        else:
            warnings.append("legacy artifact missing V1.0 contract metadata")
    else:
        if payload.get("artifact_type") != contract.artifact_type:
            errors.append(f"artifact_type must be {contract.artifact_type}")
        if str(payload.get("schema_version")) != contract.schema_version:
            errors.append(f"schema_version must be {contract.schema_version}")
        if str(payload.get("artifact_contract_version")) != contract.artifact_contract_version:
            errors.append(
                f"artifact_contract_version must be {contract.artifact_contract_version}"
            )
    errors.extend(_missing_required_fields(payload, contract.required_fields))
    return _result(path, contract, not errors, errors, warnings, migrated=migrated)


def _validate_markdown_artifact(path: Path, contract: ArtifactContract) -> ArtifactValidationResult:
    if not path.exists():
        return _result(path, contract, False, ["artifact file does not exist"], [], migrated=False)
    text = path.read_text(errors="ignore")
    errors = [
        f"missing required content: {field}"
        for field in contract.required_fields
        if field not in text
    ]
    return _result(path, contract, not errors, errors, [], migrated=False)


def _validate_zip_artifact(path: Path, contract: ArtifactContract) -> ArtifactValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            if "project_export.json" not in archive.namelist():
                errors.append("missing required member: project_export.json")
                return _result(path, contract, False, errors, warnings, migrated=False)
            payload = json.loads(archive.read("project_export.json"))
    except Exception as exc:
        return _result(path, contract, False, [f"invalid ZIP export: {exc}"], [], migrated=False)
    if not isinstance(payload, dict):
        errors.append("project_export.json must contain an object")
        return _result(path, contract, False, errors, warnings, migrated=False)
    if _needs_contract_migration(payload):
        warnings.append("legacy project export missing V1.0 contract metadata")
    else:
        if payload.get("artifact_type") != contract.artifact_type:
            errors.append(f"artifact_type must be {contract.artifact_type}")
        if str(payload.get("schema_version")) != contract.schema_version:
            errors.append(f"schema_version must be {contract.schema_version}")
        if str(payload.get("artifact_contract_version")) != contract.artifact_contract_version:
            errors.append(
                f"artifact_contract_version must be {contract.artifact_contract_version}"
            )
    errors.extend(_missing_required_fields(payload, contract.required_fields))
    return _result(path, contract, not errors, errors, warnings, migrated=False)


def _needs_contract_migration(payload: dict[str, Any]) -> bool:
    return (
        "artifact_type" not in payload
        or "schema_version" not in payload
        or "artifact_contract_version" not in payload
    )


def _missing_required_fields(
    payload: dict[str, Any],
    required_fields: tuple[str, ...],
) -> list[str]:
    return [f"missing required field: {field}" for field in required_fields if field not in payload]


def _result(
    path: Path,
    contract: ArtifactContract,
    valid: bool,
    errors: list[str],
    warnings: list[str],
    *,
    migrated: bool,
) -> ArtifactValidationResult:
    return ArtifactValidationResult(
        path=path,
        artifact_type=contract.artifact_type,
        schema_version=contract.schema_version,
        artifact_contract_version=contract.artifact_contract_version,
        valid=valid,
        errors=errors,
        warnings=warnings,
        migrated=migrated,
    )
