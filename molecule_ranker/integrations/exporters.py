from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.integrations.credentials import redact_secret_values
from molecule_ranker.release import DATA_CONTRACT_VERSION

ExportPackageType = Literal[
    "review_dossier_package",
    "validation_handoff_package",
    "active_learning_batch_package",
    "candidate_summary_package",
    "generated_molecule_package",
    "assay_result_summary_package",
    "campaign_summary_package",
    "campaign_work_package_list_package",
]
ExportPackageFormat = Literal["json", "markdown", "csv_manifest", "zip"]

GENERATED_MOLECULE_WARNING = (
    "Generated molecules are computational hypotheses for expert review; they are not "
    "validated compounds and have no direct experimental evidence unless separately linked."
)

SECRET_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
FORBIDDEN_FIELD_MARKERS = (
    "device_command",
    "dose",
    "dosing",
    "instrument_command",
    "instrument_control",
    "lab_protocol",
    "patient_treatment",
    "protocol",
    "protocol_step",
    "protocol_steps",
    "reaction_condition",
    "reagent",
    "synthesis",
    "treatment_guidance",
)
FORBIDDEN_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsynthesis(?:\s+(?:route|instruction|instructions))?\b", re.I), "[omitted]"),
    (re.compile(r"\blab\s+protocols?\b", re.I), "[omitted]"),
    (re.compile(r"\bprotocols?\b", re.I), "[omitted]"),
    (re.compile(r"\breagents?\b", re.I), "[omitted]"),
    (re.compile(r"\breaction\s+conditions?\b", re.I), "[omitted]"),
    (re.compile(r"\b(?:dose|dosing|dosage)\b", re.I), "[omitted]"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg/kg|mg per kg|mg/day|mg daily)\b", re.I), "[omitted]"),
    (re.compile(r"\bpatient\s+treatment\b", re.I), "[omitted]"),
    (re.compile(r"\b(?:animal|human|patient)\s+dos(?:e|ing)\b", re.I), "[omitted]"),
    (re.compile(r"\bstep[- ]by[- ]step\b", re.I), "[omitted]"),
)


class ExportPermissionError(PermissionError):
    """Raised when an external write/export is requested without explicit permission."""


class ExportPackageResult(BaseModel):
    package_id: str
    package_type: ExportPackageType
    output_dir: str
    formats: list[ExportPackageFormat]
    files: list[str]
    manifest_path: str
    zip_path: str | None = None
    data_contract_version: str
    external_write_ready: bool = False
    target_metadata: dict[str, Any] = Field(default_factory=dict)
    sha256: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class ExportPackageOptions:
    package_type: ExportPackageType
    payload: dict[str, Any]
    output_dir: Path
    formats: tuple[ExportPackageFormat, ...] = ("json", "markdown", "csv_manifest", "zip")
    data_contract_version: str = DATA_CONTRACT_VERSION
    external_system_target: dict[str, Any] | None = None
    external_write: bool = False
    explicit_permission: bool = False


def create_export_package(
    package_type: ExportPackageType,
    payload: dict[str, Any],
    output_dir: str | Path,
    *,
    formats: list[ExportPackageFormat] | tuple[ExportPackageFormat, ...] | None = None,
    data_contract_version: str = DATA_CONTRACT_VERSION,
    external_system_target: dict[str, Any] | None = None,
    external_write: bool = False,
    explicit_permission: bool = False,
) -> ExportPackageResult:
    options = ExportPackageOptions(
        package_type=package_type,
        payload=payload,
        output_dir=Path(output_dir),
        formats=tuple(formats or ("json", "markdown", "csv_manifest", "zip")),
        data_contract_version=data_contract_version,
        external_system_target=external_system_target,
        external_write=external_write,
        explicit_permission=explicit_permission,
    )
    return build_export_package(options)


def build_export_package(options: ExportPackageOptions) -> ExportPackageResult:
    if options.external_write and not options.explicit_permission:
        raise ExportPermissionError("External write/export requires explicit permission.")
    output_dir = options.output_dir
    if output_dir.exists():
        if output_dir.is_file():
            raise ValueError(f"Export package output path is a file: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    package_id = f"export-pkg-{uuid.uuid4().hex[:16]}"
    package_payload = _package_payload(options, package_id)
    files: list[str] = []
    if "json" in options.formats:
        _write_json(output_dir / "package.json", package_payload)
        files.append("package.json")
    if "markdown" in options.formats:
        _write_text(output_dir / "package.md", render_package_markdown(package_payload))
        files.append("package.md")

    manifest_rows = _manifest_rows(output_dir, files)
    _write_manifest_csv(output_dir / "manifest.csv", manifest_rows)
    files.append("manifest.csv")
    _write_json(
        output_dir / "manifest.json",
        {
            "package_id": package_id,
            "package_type": options.package_type,
            "data_contract_version": options.data_contract_version,
            "files": _manifest_rows(output_dir, files),
        },
    )
    files.append("manifest.json")

    zip_path: Path | None = None
    if "zip" in options.formats:
        zip_path = output_dir.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in files:
                archive.write(output_dir / relative, arcname=relative)

    sha256 = {row["path"]: row["sha256"] for row in _manifest_rows(output_dir, files)}
    if zip_path is not None:
        sha256[zip_path.name] = _sha256_path(zip_path)
    return ExportPackageResult(
        package_id=package_id,
        package_type=options.package_type,
        output_dir=str(output_dir),
        formats=list(options.formats),
        files=files,
        manifest_path=str(output_dir / "manifest.csv"),
        zip_path=str(zip_path) if zip_path else None,
        data_contract_version=options.data_contract_version,
        external_write_ready=options.external_write and options.explicit_permission,
        target_metadata=_sanitize_json(options.external_system_target or {}),
        sha256=sha256,
    )


def render_package_markdown(package_payload: dict[str, Any]) -> str:
    metadata = package_payload["metadata"]
    lines = [
        f"# {metadata['package_type']}",
        "",
        f"- Package ID: `{metadata['package_id']}`",
        f"- Data contract version: `{metadata['data_contract_version']}`",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- External write ready: `{metadata['external_write_ready']}`",
        "",
    ]
    target = metadata.get("external_system_target") or {}
    if target:
        lines.extend(["## External Target", ""])
        for key, value in sorted(target.items()):
            lines.append(f"- {key}: `{value}`")
        lines.append("")
    warnings = package_payload.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    lines.extend(["## Contents", ""])
    lines.append("```json")
    lines.append(json.dumps(package_payload["content"], indent=2, sort_keys=True))
    lines.append("```")
    return _sanitize_text("\n".join(lines).rstrip() + "\n")


def package_manifest(path: str | Path) -> list[dict[str, str]]:
    root = Path(path)
    files = sorted(item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file())
    return _manifest_rows(root, files)


def _package_payload(options: ExportPackageOptions, package_id: str) -> dict[str, Any]:
    content = _normalize_content(options.package_type, options.payload)
    return {
        "metadata": {
            "package_id": package_id,
            "package_type": options.package_type,
            "data_contract_version": options.data_contract_version,
            "generated_at": datetime.now(UTC).isoformat(),
            "external_write_ready": options.external_write and options.explicit_permission,
            "external_system_target": _sanitize_json(options.external_system_target or {}),
        },
        "warnings": _package_warnings(options.package_type, content),
        "content": content,
    }


def _normalize_content(package_type: ExportPackageType, payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_json(payload)
    if package_type == "generated_molecule_package":
        generated = sanitized.get("generated_molecules")
        if generated is None:
            generated = sanitized.get("molecules") or sanitized.get("items") or [sanitized]
        sanitized["generated_molecules"] = [
            {
                **item,
                "hypothesis_label": "computational_hypothesis",
                "warning": GENERATED_MOLECULE_WARNING,
            }
            for item in _as_dicts(generated)
        ]
    if package_type == "assay_result_summary_package":
        results = sanitized.get("assay_results") or sanitized.get("results") or [sanitized]
        sanitized["assay_results"] = [_assay_summary(item) for item in _as_dicts(results)]
    return sanitized


def _package_warnings(
    package_type: ExportPackageType,
    content: dict[str, Any],
) -> list[str]:
    warnings = [
        "External writes are not performed by export package generation.",
        "Package content is for research-system handoff and expert review only.",
    ]
    if package_type == "generated_molecule_package" or content.get("generated_molecules"):
        warnings.append(GENERATED_MOLECULE_WARNING)
    if package_type == "assay_result_summary_package":
        warnings.append("Experimental results include assay context and QC status when supplied.")
    if package_type in {"campaign_summary_package", "campaign_work_package_list_package"}:
        warnings.append(
            "Campaign packages are research-management handoffs, not lab protocols or "
            "synthesis instructions."
        )
    return [_sanitize_text(warning) for warning in warnings]


def _assay_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "assay_result_id": row.get("assay_result_id") or row.get("result_id"),
        "candidate_id": row.get("candidate_id"),
        "molecule_name": row.get("molecule_name") or row.get("candidate_name"),
        "assay_name": row.get("assay_name") or _nested(row, "assay_context", "assay_name"),
        "endpoint_name": row.get("endpoint_name")
        or _nested(row, "assay_context", "endpoint", "name"),
        "outcome": row.get("outcome") or row.get("outcome_label"),
        "value": row.get("value") or row.get("measured_value"),
        "unit": row.get("unit") or row.get("normalized_unit"),
        "qc_status": row.get("qc_status") or row.get("validation_status"),
        "assay_context": row.get("assay_context") or {},
        "source_record_id": row.get("source_record_id") or row.get("external_record_id"),
        "provenance": row.get("provenance") or {},
    }


def _as_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in (_as_dict(item) for item in value) if item]
    item = _as_dict(value)
    return [item] if item else []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {}


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _sanitize_json(value.model_dump(mode="json"))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if any(marker in lowered for marker in SECRET_FIELD_MARKERS):
                continue
            if any(marker in lowered for marker in FORBIDDEN_FIELD_MARKERS):
                continue
            sanitized[key] = _sanitize_json(raw_value)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    text = redact_secret_values(redact_secrets(value))
    for pattern, replacement in FORBIDDEN_TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize_json(payload), indent=2, sort_keys=True) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_sanitize_text(text))


def _write_manifest_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "sha256", "size_bytes"])
        writer.writeheader()
        writer.writerows(rows)


def _manifest_rows(root: Path, files: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for relative in sorted(files):
        path = root / relative
        if not path.exists() or not path.is_file():
            continue
        rows.append(
            {
                "path": relative,
                "sha256": _sha256_path(path),
                "size_bytes": str(path.stat().st_size),
            }
        )
    return rows


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ExportPackageFormat",
    "ExportPackageOptions",
    "ExportPackageResult",
    "ExportPackageType",
    "ExportPermissionError",
    "GENERATED_MOLECULE_WARNING",
    "build_export_package",
    "create_export_package",
    "package_manifest",
    "render_package_markdown",
]
