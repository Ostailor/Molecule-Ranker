from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from molecule_ranker.runtime_agents.schemas import RuntimeAgentSession, RuntimeToolSpec
from molecule_ranker.runtime_agents.tool_registry import RuntimeToolRegistry

DEFAULT_CONTEXT_BUDGET_BYTES = 64_000
DEFAULT_ARTIFACT_SUMMARY_BYTES = 8_000
DEFAULT_POLICY_CONSTRAINTS = [
    "Use approved tools only.",
    "Do not invent biomedical evidence, assay results, citations, molecules, or scores.",
    (
        "Do not provide medical advice, protocols, synthesis instructions, dosing, or "
        "treatment guidance."
    ),
    (
        "External writes, stage gates, campaign advancement, destructive actions, and "
        "policy overrides require approval."
    ),
    "Respect RBAC, policy, guardrails, artifact validators, and audit logging.",
]
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|bearer|client[_-]?secret|password|secret|token)"
        r"\b\s*[:=]\s*(?:bearer\s+)?([^\s,;]+)"
    ),
    re.compile(r"(?i)\b(aws_access_key_id|aws_secret_access_key)\b\s*[:=]\s*([^\s,;]+)"),
)
SECRET_FIELD_NAMES = {
    "access_key",
    "api_key",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
UNSAFE_PATH_NAMES = {".env", ".env.local", ".env.production", ".env.development"}
UNSAFE_PATH_PARTS = {
    ".cache",
    ".pytest_cache",
    "__pycache__",
    "cache",
    "credentials",
    "secrets",
}
SUMMARY_PREVIEW_CHARS = 900


def build_runtime_context(
    session: RuntimeAgentSession,
    *,
    registry: RuntimeToolRegistry | None = None,
    max_bytes: int = DEFAULT_CONTEXT_BUDGET_BYTES,
) -> dict[str, Any]:
    """Build bounded, policy-safe planning context for Codex."""

    metadata = session.metadata
    permissions = set(_string_list(metadata.get("user_permissions")))
    artifact_manifest = _allowed_artifact_manifest(metadata, permissions)
    allowed_paths = {
        str(artifact["path"])
        for artifact in artifact_manifest
        if isinstance(artifact.get("path"), str)
    }
    selected_summaries = [
        summarize_artifact_for_planning(path)
        for path in _string_list(metadata.get("selected_artifact_paths"))
        if path in allowed_paths
    ]
    candidate_counts = _candidate_counts(metadata, artifact_manifest, selected_summaries)
    runtime_registry = registry or RuntimeToolRegistry.default()
    context = {
        "session": {
            "session_id": session.session_id,
            "project_id": session.project_id,
            "org_id": session.org_id,
            "user_id": session.user_id,
            "user_goal": redact_sensitive_context(session.user_goal),
            "autonomy_level": session.autonomy_level,
            "status": session.status,
        },
        "project_summary": _redact_json_like(metadata.get("project_summary", {})),
        "artifact_manifest": artifact_manifest,
        "available_tools": _available_tools(runtime_registry, permissions),
        "user_permissions": sorted(permissions),
        "policy_constraints": _policy_constraints(metadata),
        "recent_job_statuses": _bounded_json_list(metadata.get("recent_job_statuses"), limit=20),
        "selected_artifact_summaries": selected_summaries,
        "candidate_counts": candidate_counts,
        "hypothesis_counts": _redact_json_like(metadata.get("hypothesis_counts", {})),
        "campaign_status": _redact_json_like(metadata.get("campaign_status", {})),
        "evaluation_status": _redact_json_like(metadata.get("evaluation_status", {})),
        "known_warnings": _string_list(metadata.get("known_warnings")),
    }
    return enforce_context_budget(context, max_bytes=max_bytes)


def summarize_artifact_for_planning(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_ARTIFACT_SUMMARY_BYTES,
) -> dict[str, Any]:
    artifact_path = Path(path)
    summary: dict[str, Any] = {
        "path": str(artifact_path),
        "name": artifact_path.name,
        "exists": artifact_path.exists(),
        "truncated": False,
    }
    if _unsafe_path(artifact_path):
        summary.update({"excluded": True, "reason": "unsafe artifact path"})
        return summary
    if not artifact_path.exists() or not artifact_path.is_file():
        return summary

    size_bytes = artifact_path.stat().st_size
    raw = artifact_path.read_bytes()[: max_bytes + 1]
    text = raw.decode("utf-8", errors="replace")
    truncated = size_bytes > max_bytes
    summary.update(
        {
            "size_bytes": size_bytes,
            "truncated": truncated,
            "content_type": _content_type(artifact_path),
        }
    )
    redacted = redact_sensitive_context(text[:max_bytes])
    if artifact_path.suffix.lower() == ".json":
        summary.update(_summarize_json_text(redacted, truncated=truncated))
    else:
        summary["text_preview"] = redacted[:SUMMARY_PREVIEW_CHARS]
    return summary


def redact_sensitive_context(text: str) -> str:
    redacted = text
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    return redacted


def enforce_context_budget(context: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    budgeted = _redact_json_like(context)
    size = _json_size_bytes(budgeted)
    if size <= max_bytes:
        budgeted["context_budget"] = {
            "max_bytes": max_bytes,
            "size_bytes": size,
            "truncated": False,
        }
        return budgeted

    compact = dict(budgeted)
    compact["selected_artifact_summaries"] = [
        _compact_summary(summary) for summary in compact.get("selected_artifact_summaries", [])
    ]
    compact["recent_job_statuses"] = compact.get("recent_job_statuses", [])[:5]
    compact["artifact_manifest"] = compact.get("artifact_manifest", [])[:25]
    compact["context_budget"] = {
        "max_bytes": max_bytes,
        "size_bytes": _json_size_bytes(compact),
        "original_size_bytes": size,
        "truncated": True,
    }
    return compact


def extract_allowed_refs(context: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for artifact in context.get("artifact_manifest", []):
        if isinstance(artifact, dict) and isinstance(artifact.get("artifact_id"), str):
            refs.append(artifact["artifact_id"])
    return refs


def _allowed_artifact_manifest(
    metadata: dict[str, Any],
    user_permissions: set[str],
) -> list[dict[str, Any]]:
    artifacts = metadata.get("artifact_manifest", metadata.get("artifacts", []))
    allowed: list[dict[str, Any]] = []
    for artifact in _dict_list(artifacts):
        if not _artifact_allowed(artifact, user_permissions):
            continue
        artifact_path = artifact.get("path")
        if isinstance(artifact_path, str) and _unsafe_path(Path(artifact_path)):
            continue
        allowed.append(_safe_artifact_manifest_entry(artifact))
    return allowed


def _artifact_allowed(artifact: dict[str, Any], user_permissions: set[str]) -> bool:
    if artifact.get("authorized") is False:
        return False
    required_permissions = set(_string_list(artifact.get("required_permissions")))
    if required_permissions and not required_permissions.issubset(user_permissions):
        return False
    if artifact.get("artifact_type") == "raw_assay" and not artifact.get("redacted"):
        return False
    return True


def _safe_artifact_manifest_entry(artifact: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "artifact_id",
        "artifact_type",
        "candidate_count",
        "created_at",
        "hypothesis_count",
        "job_id",
        "path",
        "required_permissions",
        "status",
        "summary",
        "warnings",
    }
    return {
        key: _redact_json_like(value)
        for key, value in artifact.items()
        if key in allowed_keys and not _sensitive_key(key)
    }


def _available_tools(
    registry: RuntimeToolRegistry,
    user_permissions: set[str],
) -> list[dict[str, Any]]:
    specs = [
        spec
        for spec in registry.list_tools()
        if set(spec.required_permissions).issubset(user_permissions)
    ]
    return [_tool_for_context(spec) for spec in sorted(specs, key=lambda item: item.tool_name)]


def _tool_for_context(spec: RuntimeToolSpec) -> dict[str, Any]:
    return {
        "tool_name": spec.tool_name,
        "category": spec.category,
        "description": spec.description,
        "required_permissions": spec.required_permissions,
        "policy_tags": spec.policy_tags,
        "side_effect_level": spec.side_effect_level,
        "requires_approval_by_default": spec.requires_approval_by_default,
        "idempotent": spec.idempotent,
    }


def _policy_constraints(metadata: dict[str, Any]) -> list[str]:
    constraints = list(DEFAULT_POLICY_CONSTRAINTS)
    for item in _string_list(metadata.get("policy_constraints")):
        redacted = redact_sensitive_context(item)
        if redacted not in constraints:
            constraints.append(redacted)
    return constraints


def _candidate_counts(
    metadata: dict[str, Any],
    artifact_manifest: list[dict[str, Any]],
    selected_summaries: list[dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    raw_counts = metadata.get("candidate_counts")
    if isinstance(raw_counts, dict):
        for key, value in raw_counts.items():
            if isinstance(value, int):
                counts[str(key)] = value
    for artifact in artifact_manifest:
        artifact_id = artifact.get("artifact_id")
        candidate_count = artifact.get("candidate_count")
        if isinstance(artifact_id, str) and isinstance(candidate_count, int):
            counts[artifact_id] = candidate_count
    for index, summary in enumerate(selected_summaries):
        candidate_count = summary.get("candidate_count")
        if isinstance(candidate_count, int):
            counts[f"selected_artifact_{index}"] = candidate_count
    return counts


def _summarize_json_text(text: str, *, truncated: bool) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"text_preview": text[:SUMMARY_PREVIEW_CHARS]}
    redacted = _redact_json_like(parsed)
    summary: dict[str, Any] = {}
    if isinstance(redacted, dict):
        summary["top_level_keys"] = sorted(str(key) for key in redacted)[:50]
        for count_key in ("candidate_count", "hypothesis_count"):
            value = redacted.get(count_key)
            if isinstance(value, int):
                summary[count_key] = value
        warnings = redacted.get("warnings")
        if isinstance(warnings, list):
            summary["warnings"] = [str(item) for item in warnings[:10]]
        rows = redacted.get("rows")
        if isinstance(rows, list):
            summary["row_count"] = len(rows)
            summary["sample_rows"] = rows[:3]
        else:
            summary["json_preview"] = _bounded_value(redacted)
    elif isinstance(redacted, list):
        summary["item_count"] = len(redacted)
        summary["sample_items"] = redacted[:3]
    else:
        summary["json_preview"] = redacted
    if truncated and "sample_rows" not in summary and "sample_items" not in summary:
        summary["text_preview"] = text[:SUMMARY_PREVIEW_CHARS]
    return summary


def _redact_json_like(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_context(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _sensitive_key(str(key)):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_json_like(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json_like(item) for item in value[:100]]
    return value


def _bounded_value(value: Any) -> Any:
    text = json.dumps(value, sort_keys=True, default=str)
    if len(text) <= SUMMARY_PREVIEW_CHARS:
        return value
    return text[:SUMMARY_PREVIEW_CHARS]


def _compact_summary(summary: Any) -> Any:
    if not isinstance(summary, dict):
        return summary
    kept = {
        key: summary[key]
        for key in (
            "path",
            "name",
            "exists",
            "size_bytes",
            "truncated",
            "content_type",
            "top_level_keys",
            "candidate_count",
            "hypothesis_count",
            "row_count",
            "item_count",
            "warnings",
        )
        if key in summary
    }
    kept["compacted"] = True
    return kept


def _unsafe_path(path: Path) -> bool:
    if path.name in UNSAFE_PATH_NAMES:
        return True
    return any(part in UNSAFE_PATH_PARTS for part in path.parts)


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix in {".txt", ".md"}:
        return "text/plain"
    if suffix == ".csv":
        return "text/csv"
    return "application/octet-stream"


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SECRET_FIELD_NAMES or any(
        part in normalized for part in SECRET_FIELD_NAMES
    )


def _json_size_bytes(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, default=str).encode("utf-8"))


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _bounded_json_list(value: Any, *, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [_redact_json_like(item) for item in value[:limit]]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


__all__ = [
    "build_runtime_context",
    "enforce_context_budget",
    "extract_allowed_refs",
    "redact_sensitive_context",
    "summarize_artifact_for_planning",
]
