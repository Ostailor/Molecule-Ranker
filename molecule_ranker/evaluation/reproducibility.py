from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker import __version__
from molecule_ranker.evaluation.reports import render_reproducibility_report
from molecule_ranker.evaluation.schemas import ReproducibilityManifest

MANIFEST_PATH = "reproducibility_manifest.json"
REPORT_PATH = "reproducibility_report.md"

_EXCLUDED_OUTPUTS = {
    MANIFEST_PATH,
    REPORT_PATH,
}
_CONFIG_NAMES = (
    "run_config.json",
    "config.json",
    "settings.json",
    "parameters.json",
)
_LOCK_NAMES = (
    "uv.lock",
    "poetry.lock",
    "requirements.txt",
    "requirements.lock",
    "Pipfile.lock",
)
_MODEL_HINTS = ("model", "surrogate", "checkpoint")
_CODEX_HINTS = ("codex_transcript", "codex-transcript", "transcript")
_INTEGRATION_HINTS = ("integration_payload", "external_payload", "webhook_payload")


@dataclass(frozen=True)
class ReproducibilityCheckReport:
    manifest_id: str
    run_dir: str
    status: str
    metrics: dict[str, bool | None]
    warnings: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        payload = {
            "manifest_id": self.manifest_id,
            "run_dir": self.run_dir,
            "status": self.status,
            "metrics": self.metrics,
            "warnings": self.warnings,
            "checked_at": self.checked_at,
            "metadata": self.metadata,
        }
        if mode == "json":
            payload["checked_at"] = self.checked_at.isoformat()
        return payload


def check_reproducibility(
    *,
    from_run: str | Path,
    expected_config_hash: str | None = None,
    rerun_dir: str | Path | None = None,
    code_version: str = __version__,
    artifact_contract_version: str = "evaluation-reproducibility.v1",
) -> ReproducibilityCheckReport:
    """Build a reproducibility manifest and report for a completed run directory."""

    run_dir = Path(from_run)
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {run_dir}")

    previous_manifest = _load_previous_manifest(run_dir)
    artifacts = _hash_artifacts(run_dir)
    config_hash = _config_hash(run_dir)
    random_seeds = _extract_random_seeds(run_dir)
    dependency_summary = _dependency_summary(run_dir)
    model_hashes = _special_artifact_hashes(run_dir, _MODEL_HINTS)
    codex_transcripts = _special_artifact_hashes(run_dir, _CODEX_HINTS)
    integration_payloads = _special_artifact_hashes(run_dir, _INTEGRATION_HINTS)
    output_hashes: dict[str, str] = {}
    output_hashes.update({f"model:{key}": value for key, value in model_hashes.items()})
    output_hashes.update(
        {f"integration_payload:{key}": value for key, value in integration_payloads.items()}
    )

    warnings: list[str] = []
    artifact_hash_match = _artifact_hashes_match(previous_manifest, artifacts, warnings)
    config_hash_match = expected_config_hash is None or config_hash == expected_config_hash
    if not config_hash_match:
        warnings.append("config_hash_mismatch")
    seed_reproducibility = bool(random_seeds)
    if not seed_reproducibility:
        warnings.append("missing_random_seed")
    deterministic_match = _deterministic_rerun_match(run_dir, rerun_dir, warnings)
    input_paths = _declared_input_paths(run_dir)
    input_exists = _input_artifacts_exist(run_dir, input_paths, warnings)
    if _codex_used(run_dir) and not codex_transcripts:
        warnings.append("missing_codex_transcript")

    metrics = {
        "artifact_hash_match": artifact_hash_match,
        "config_hash_match": config_hash_match,
        "seed_reproducibility": seed_reproducibility,
        "dependency_lock_hash_present": bool(dependency_summary.get("dependency_lock_hash")),
        "input_artifact_existence": input_exists,
        "deterministic_rerun_match": deterministic_match,
        "model_artifact_hash_present": bool(model_hashes),
        "codex_transcript_presence": (bool(codex_transcripts) if _codex_used(run_dir) else None),
        "external_integration_payload_hash_present": bool(integration_payloads),
    }
    status = "pass" if all(value is not False for value in metrics.values()) else "fail"

    manifest = ReproducibilityManifest(
        manifest_id=f"reproducibility-{run_dir.name}",
        run_id=run_dir.name,
        suite_id=None,
        code_version=code_version,
        artifact_contract_version=artifact_contract_version,
        config_hash=config_hash,
        input_artifact_hashes=artifacts,
        output_artifact_hashes=output_hashes,
        random_seeds=random_seeds,
        dependency_summary=dependency_summary,
        created_at=datetime.now(UTC),
        metadata={
            "status": status,
            "run_dir": str(run_dir),
            "expected_config_hash": expected_config_hash,
            "codex_transcript_hashes": codex_transcripts,
            "declared_input_artifact_paths": input_paths,
        },
    )
    report = ReproducibilityCheckReport(
        manifest_id=manifest.manifest_id,
        run_dir=str(run_dir),
        status=status,
        metrics=metrics,
        warnings=list(dict.fromkeys(warnings)),
        metadata={
            "manifest_path": str(run_dir / MANIFEST_PATH),
            "report_path": str(run_dir / REPORT_PATH),
            "code_version": code_version,
        },
    )
    _write_manifest(run_dir, manifest)
    _write_report(run_dir, manifest, report)
    return report


def load_reproducibility_manifest(run_dir: str | Path) -> ReproducibilityManifest:
    return ReproducibilityManifest.model_validate(_read_json(Path(run_dir) / MANIFEST_PATH))


def render_reproducibility_report_markdown(
    manifest: ReproducibilityManifest,
    report: ReproducibilityCheckReport,
) -> str:
    return render_reproducibility_report(manifest, report=report.model_dump(mode="json"))


def _hash_artifacts(run_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or _excluded(path, run_dir):
            continue
        hashes[_relative(path, run_dir)] = _file_hash(path)
    return hashes


def _artifact_hashes_match(
    previous_manifest: ReproducibilityManifest | None,
    current_hashes: Mapping[str, str],
    warnings: list[str],
) -> bool:
    if previous_manifest is None:
        return True
    expected = previous_manifest.input_artifact_hashes
    matched = True
    for relative_path, expected_hash in expected.items():
        current_hash = current_hashes.get(relative_path)
        if current_hash != expected_hash:
            warnings.append(f"artifact_hash_mismatch:{relative_path}")
            matched = False
    for relative_path in current_hashes:
        if relative_path not in expected:
            warnings.append(f"artifact_hash_new:{relative_path}")
            matched = False
    return matched


def _config_hash(run_dir: Path) -> str:
    config_path = _first_existing(run_dir, _CONFIG_NAMES)
    if config_path is None:
        return _hash_bytes(b"")
    if config_path.suffix.lower() == ".json":
        payload = _read_json(config_path)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return _hash_bytes(canonical.encode("utf-8"))
    return _file_hash(config_path)


def _extract_random_seeds(run_dir: Path) -> dict[str, Any]:
    seeds: dict[str, Any] = {}
    for config_name in _CONFIG_NAMES:
        path = run_dir / config_name
        if not path.exists() or path.suffix.lower() != ".json":
            continue
        _collect_seed_values(_read_json(path), seeds)
    return seeds


def _collect_seed_values(value: Any, seeds: dict[str, Any], prefix: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            next_prefix = f"{prefix}.{key_text}" if prefix else key_text
            if "seed" in key_text.lower() and _json_scalar(item):
                seeds[next_prefix] = item
            else:
                _collect_seed_values(item, seeds, next_prefix)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_seed_values(item, seeds, f"{prefix}[{index}]")


def _dependency_summary(run_dir: Path) -> dict[str, Any]:
    lock_path = _first_existing(run_dir, _LOCK_NAMES)
    if lock_path is None:
        return {"dependency_lock_hash": None, "dependency_lock_path": None}
    return {
        "dependency_lock_hash": _file_hash(lock_path),
        "dependency_lock_path": _relative(lock_path, run_dir),
    }


def _special_artifact_hashes(run_dir: Path, hints: tuple[str, ...]) -> dict[str, str]:
    matches: dict[str, str] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or _excluded(path, run_dir):
            continue
        name = path.name.lower()
        if any(hint in name for hint in hints):
            matches[_relative(path, run_dir)] = _file_hash(path)
    return matches


def _deterministic_rerun_match(
    run_dir: Path,
    rerun_dir: str | Path | None,
    warnings: list[str],
) -> bool | None:
    if rerun_dir is None:
        return None
    rerun_path = Path(rerun_dir)
    if not rerun_path.exists() or not rerun_path.is_dir():
        warnings.append("deterministic_rerun_missing")
        return False
    current = _hash_artifacts(run_dir)
    rerun = _hash_artifacts(rerun_path)
    match = current == rerun
    if not match:
        warnings.append("deterministic_rerun_hash_mismatch")
    return match


def _declared_input_paths(run_dir: Path) -> list[str]:
    declared: list[str] = []
    for path in sorted(run_dir.rglob("*.json")):
        if _excluded(path, run_dir):
            continue
        payload = _read_json(path)
        _collect_declared_input_paths(payload, declared)
    return list(dict.fromkeys(declared))


def _collect_declared_input_paths(value: Any, declared: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text in {"input_artifact_paths", "source_artifact_paths"} and isinstance(
                item,
                list,
            ):
                declared.extend(str(path) for path in item)
            else:
                _collect_declared_input_paths(item, declared)
    elif isinstance(value, list):
        for item in value:
            _collect_declared_input_paths(item, declared)


def _input_artifacts_exist(run_dir: Path, paths: list[str], warnings: list[str]) -> bool:
    if not paths:
        return True
    ok = True
    for declared in paths:
        path = Path(declared)
        resolved = path if path.is_absolute() else run_dir / path
        if not resolved.exists():
            warnings.append(f"missing_input_artifact:{declared}")
            ok = False
    return ok


def _codex_used(run_dir: Path) -> bool:
    for path in run_dir.rglob("*.json"):
        if _excluded(path, run_dir):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if "codex" in text:
            return True
    return False


def _load_previous_manifest(run_dir: Path) -> ReproducibilityManifest | None:
    path = run_dir / MANIFEST_PATH
    if not path.exists():
        return None
    return ReproducibilityManifest.model_validate(_read_json(path))


def _write_manifest(run_dir: Path, manifest: ReproducibilityManifest) -> None:
    _write_json(run_dir / MANIFEST_PATH, manifest.model_dump(mode="json"))


def _write_report(
    run_dir: Path,
    manifest: ReproducibilityManifest,
    report: ReproducibilityCheckReport,
) -> None:
    (run_dir / REPORT_PATH).write_text(
        render_reproducibility_report_markdown(manifest, report),
        encoding="utf-8",
    )


def _first_existing(run_dir: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        path = run_dir / name
        if path.exists() and path.is_file():
            return path
    return None


def _excluded(path: Path, run_dir: Path) -> bool:
    relative = _relative(path, run_dir)
    return relative in _EXCLUDED_OUTPUTS


def _relative(path: Path, run_dir: Path) -> str:
    return path.relative_to(run_dir).as_posix()


def _file_hash(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "ReproducibilityCheckReport",
    "ReproducibilityManifest",
    "check_reproducibility",
    "load_reproducibility_manifest",
    "render_reproducibility_report_markdown",
]
