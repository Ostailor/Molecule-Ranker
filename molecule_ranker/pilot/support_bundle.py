from __future__ import annotations

import hashlib
import json
import os
import platform
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker import __version__
from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.pilot_readiness import build_support_bundle_manifest
from molecule_ranker.platform.settings import PlatformSettings, validate_settings

BUNDLE_SCHEMA_VERSION = "support-bundle.v1.9"
MAX_TEXT_FILE_BYTES = 256_000
MAX_INCLUDED_TEXT_FILES = 20
MAX_HASHED_ARTIFACTS = 500

SENSITIVE_NAME_PARTS = {
    ".env",
    "apikey",
    "api_key",
    "cache",
    "credential",
    "credentials",
    "password",
    "secret",
    "service_token",
    "token",
}
SENSITIVE_JSON_KEY_PARTS = {
    "apikey",
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "service_token",
    "token",
}
CODEX_TRANSCRIPT_PARTS = {"codex_transcript", "transcript"}
RAW_ASSAY_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xls"}
RAW_ASSAY_NAME_PARTS = {"assay", "patient", "subject", "participant"}


@dataclass(frozen=True)
class SupportBundleResult:
    output_path: Path
    manifest: dict[str, Any]


def generate_support_bundle_manifest(
    root_dir: str | Path = ".",
    *,
    extra_files: list[str | Path] | None = None,
) -> dict[str, Any]:
    return build_support_bundle_manifest(root_dir, extra_files=extra_files)


def create_support_bundle(
    root_dir: str | Path = ".",
    *,
    output_path: str | Path,
    config: dict[str, Any] | None = None,
    include_codex_transcripts: bool = False,
    allow_raw_assay_files: bool = False,
    extra_trace_files: list[str | Path] | None = None,
) -> SupportBundleResult:
    root = Path(root_dir).resolve()
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    config_summary = _redacted_config(root, config=config)
    readiness_report = _readiness_report(root)
    job_summaries, error_summaries, platform_health = _platform_summaries(root)
    artifact_manifest_hashes = _artifact_manifest_hashes(root)
    validation_reports = _load_named_reports(root, ("validation", "release_validation"))
    security_audit_summary = _load_named_reports(root, ("security_audit", "security"))
    guardrail_benchmark_summary = _load_named_reports(root, ("guardrail", "guardrail_benchmark"))
    performance_profile_summary = _load_named_reports(root, ("performance_report", "performance"))

    manifest = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "version": __version__,
        "root_dir": str(root),
        "includes_environment_variables": False,
        "includes_cache_files": False,
        "includes_raw_assay_files": bool(allow_raw_assay_files),
        "includes_codex_transcripts": bool(include_codex_transcripts),
        "excluded": {
            "api_keys": True,
            "service_tokens": True,
            "passwords": True,
            "env_files": True,
            "cache_payloads": True,
            "full_copyrighted_articles": True,
            "raw_assay_files_unless_allowed": not allow_raw_assay_files,
            "codex_transcripts_unless_redacted_and_included": not include_codex_transcripts,
            "external_credentials": True,
            "patient_data": True,
        },
        "artifact_manifest_hashes": artifact_manifest_hashes,
        "sections": {
            "config_redacted": "config_redacted.json",
            "environment_summary": "environment_summary.json",
            "readiness_report": "readiness_report.json",
            "recent_job_summaries": "recent_job_summaries.json",
            "recent_error_summaries": "recent_error_summaries.json",
            "platform_health": "platform_health.json",
            "artifact_manifest_hashes": "artifact_manifest_hashes.json",
            "validation_reports": "validation_reports.json",
            "security_audit_summary": "security_audit_summary.json",
            "guardrail_benchmark_summary": "guardrail_benchmark_summary.json",
            "performance_profile_summary": "performance_profile_summary.json",
            "redacted_logs": "logs/",
            "redacted_traces": "traces/",
        },
    }

    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_json(archive, "manifest.json", manifest)
        _write_json(archive, "config_redacted.json", config_summary)
        _write_json(archive, "environment_summary.json", _environment_summary(root))
        _write_json(archive, "readiness_report.json", readiness_report)
        _write_json(archive, "recent_job_summaries.json", job_summaries)
        _write_json(archive, "recent_error_summaries.json", error_summaries)
        _write_json(archive, "platform_health.json", platform_health)
        _write_json(archive, "artifact_manifest_hashes.json", artifact_manifest_hashes)
        _write_json(archive, "validation_reports.json", validation_reports)
        _write_json(archive, "security_audit_summary.json", security_audit_summary)
        _write_json(archive, "guardrail_benchmark_summary.json", guardrail_benchmark_summary)
        _write_json(archive, "performance_profile_summary.json", performance_profile_summary)
        for path in _selected_log_files(root):
            _write_redacted_file(archive, root, path, prefix="logs")
        for path in _selected_trace_files(
            root,
            extra_trace_files=extra_trace_files,
            include_codex_transcripts=include_codex_transcripts,
            allow_raw_assay_files=allow_raw_assay_files,
        ):
            _write_redacted_file(archive, root, path, prefix="traces")
    return SupportBundleResult(output_path=output, manifest=manifest)


def redact_text(value: str) -> str:
    return redact_secrets(value)


def redact_file(input_path: str | Path, output_path: str | Path) -> Path:
    source = Path(input_path)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    redacted = redact_text(source.read_text(encoding="utf-8", errors="replace"))
    target.write_text(redacted, encoding="utf-8")
    return target


def _redacted_config(root: Path, *, config: dict[str, Any] | None) -> dict[str, Any]:
    try:
        settings = PlatformSettings.from_environment()
        payload: dict[str, Any] = settings.redacted_model_dump()
        validation = validate_settings(settings)
    except Exception as exc:
        payload = {"root_dir": str(root), "settings_error": redact_text(str(exc))}
        validation = {"ok": False, "error": redact_text(str(exc))}
    if config:
        payload.update(_redact_json(config))
    payload["config_source"] = "redacted_summary"
    payload["validation"] = validation
    return _redact_json(payload)


def _environment_summary(root: Path) -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cwd": str(root),
        "pid": os.getpid(),
        "environment_variables_included": False,
    }


def _readiness_report(root: Path) -> dict[str, Any]:
    try:
        from molecule_ranker.pilot.readiness import PilotReadinessConfig, run_pilot_readiness_audit

        report = run_pilot_readiness_audit(
            PilotReadinessConfig.synthetic_dev(
                root_dir=root,
                database_path=root / ".molecule-ranker" / "platform.sqlite",
                artifact_storage_path=root / ".molecule-ranker" / "artifacts",
                backup_path=root / ".molecule-ranker" / "backups",
            )
        )
        return report.model_dump(mode="json")
    except Exception as exc:
        return {"status": "unavailable", "error": redact_text(str(exc))}


def _platform_summaries(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    database_path = root / ".molecule-ranker" / "platform.sqlite"
    if not database_path.exists():
        return [], [], {"ok": False, "reason": "platform database not found"}
    try:
        from molecule_ranker.platform.database import PlatformDatabase
        from molecule_ranker.platform.jobs import PlatformJobQueue

        database = PlatformDatabase(root, db_path=database_path, initialize=False)
        health = _redact_json(database.health())
        jobs = PlatformJobQueue(database).list_jobs(limit=25)
        job_summaries = [_job_summary(job) for job in jobs]
        error_summaries = [
            {
                "job_id": item["job_id"],
                "job_type": item["job_type"],
                "status": item["status"],
                "error_summary": item["error_summary"],
            }
            for item in job_summaries
            if item.get("error_summary")
        ]
        return job_summaries, error_summaries, health
    except Exception as exc:
        return [], [], {"ok": False, "error": redact_text(str(exc))}


def _job_summary(job: Any) -> dict[str, Any]:
    payload = job.model_dump(mode="json") if hasattr(job, "model_dump") else dict(job)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return _redact_json(
        {
            "job_id": payload.get("job_id"),
            "job_type": payload.get("job_type"),
            "status": payload.get("status"),
            "project_id": payload.get("project_id"),
            "created_at": payload.get("created_at"),
            "started_at": payload.get("started_at"),
            "completed_at": payload.get("completed_at"),
            "attempts": payload.get("attempts"),
            "error_summary": payload.get("error_summary"),
            "progress": metadata.get("progress"),
            "heartbeat_at": metadata.get("heartbeat_at"),
        }
    )


def _artifact_manifest_hashes(root: Path) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    artifact_dirs = (root / "results", root / "artifacts", root / ".molecule-ranker" / "artifacts")
    for directory in artifact_dirs:
        if directory.exists():
            candidates.extend(path for path in sorted(directory.rglob("*")) if path.is_file())
    hashes: list[dict[str, Any]] = []
    for path in candidates[:MAX_HASHED_ARTIFACTS]:
        if not _is_safe_diagnostic_path(path, allow_raw_assay_files=False):
            continue
        hashes.append(
            {
                "path": _safe_relative(root, path),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return hashes


def _load_named_reports(root: Path, name_parts: tuple[str, ...]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        if not _is_safe_diagnostic_path(path, allow_raw_assay_files=False):
            continue
        lowered = path.name.lower()
        if not any(part in lowered for part in name_parts):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            payload = {"error": redact_text(str(exc))}
        reports.append(
            {
                "path": _safe_relative(root, path),
                "sha256": _sha256_file(path),
                "summary": _summarize_payload(_redact_json(payload)),
            }
        )
        if len(reports) >= 25:
            break
    return reports


def _selected_log_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for directory in (root / "logs", root / ".molecule-ranker" / "logs"):
        if directory.exists():
            candidates.extend(path for path in sorted(directory.rglob("*")) if path.is_file())
    return [
        path
        for path in candidates
        if _is_safe_diagnostic_path(path, allow_raw_assay_files=False)
        and path.suffix.lower() in {".log", ".txt"}
    ][:MAX_INCLUDED_TEXT_FILES]


def _selected_trace_files(
    root: Path,
    *,
    extra_trace_files: list[str | Path] | None,
    include_codex_transcripts: bool,
    allow_raw_assay_files: bool,
) -> list[Path]:
    candidates: list[Path] = []
    for directory in (root / "traces", root / ".molecule-ranker" / "traces"):
        if directory.exists():
            candidates.extend(path for path in sorted(directory.rglob("*")) if path.is_file())
    candidates.extend(Path(path) for path in extra_trace_files or [])
    selected: list[Path] = []
    for path in candidates:
        if not _is_safe_diagnostic_path(
            path,
            allow_raw_assay_files=allow_raw_assay_files,
            include_codex_transcripts=include_codex_transcripts,
        ):
            continue
        if path.suffix.lower() not in {".log", ".txt", ".trace", ".json"}:
            continue
        selected.append(path)
        if len(selected) >= MAX_INCLUDED_TEXT_FILES:
            break
    return selected


def _write_redacted_file(archive: zipfile.ZipFile, root: Path, path: Path, *, prefix: str) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_FILE_BYTES]
    relative = _safe_relative(root, path).replace("/", "_")
    archive.writestr(f"{prefix}/{relative}.redacted.txt", redact_text(text))


def _write_json(archive: zipfile.ZipFile, name: str, payload: Any) -> None:
    encoded = json.dumps(_redact_json(payload), indent=2, sort_keys=True, default=str)
    archive.writestr(
        name,
        redact_text(encoded) + "\n",
    )


def _is_safe_diagnostic_path(
    path: Path,
    *,
    allow_raw_assay_files: bool,
    include_codex_transcripts: bool = False,
) -> bool:
    lowered_parts = [part.lower() for part in path.parts]
    lowered_name = path.name.lower()
    joined = "/".join(lowered_parts)
    if any(part in joined for part in SENSITIVE_NAME_PARTS):
        return False
    if any(part in joined for part in {"cache", ".cache", "__pycache__"}):
        return False
    if path.name.startswith(".env"):
        return False
    if not include_codex_transcripts and any(part in joined for part in CODEX_TRANSCRIPT_PARTS):
        return False
    if not allow_raw_assay_files and path.suffix.lower() in RAW_ASSAY_SUFFIXES:
        if any(part in lowered_name for part in RAW_ASSAY_NAME_PARTS):
            return False
    if "patient" in joined or "phi" in joined:
        return False
    return True


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_JSON_KEY_PARTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _summarize_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        summary: dict[str, Any] = {}
        for key in ("status", "summary", "workflow", "created_at", "version"):
            if key in payload:
                summary[key] = payload[key]
        if "summary" in payload and isinstance(payload["summary"], dict):
            summary["summary"] = payload["summary"]
        return summary or {"keys": sorted(str(key) for key in payload)[:20]}
    if isinstance(payload, list):
        return {"item_count": len(payload)}
    return {"value": str(payload)[:200]}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().name


__all__ = [
    "SupportBundleResult",
    "create_support_bundle",
    "generate_support_bundle_manifest",
    "redact_file",
    "redact_text",
]
