from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.performance.profiler import PerformanceProfile

SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "service_token",
    "token",
)


def performance_profile_to_json(profile: PerformanceProfile) -> str:
    payload = redact_performance_payload(profile.model_dump(mode="json"))
    return json.dumps(payload, indent=2, sort_keys=True)


def render_performance_markdown(profile: PerformanceProfile) -> str:
    payload = redact_performance_payload(profile.model_dump(mode="json"))
    lines = [
        "# Performance Profile Report",
        "",
        f"- Profile ID: `{payload['profile_id']}`",
        f"- Created: `{payload['created_at']}`",
        f"- Version: `{payload['version']}`",
        f"- Workflow: `{payload['workflow']}`",
        f"- Environment: `{payload['environment']}`",
        f"- Live APIs enabled: `{payload['live_apis_enabled']}`",
        f"- Codex task timeout rate: `{payload['codex_task_metrics']['timeout_rate']}`",
        "",
        "## Measurements",
        "",
        "| Step | Duration ms | Source | Peak memory bytes |",
        "| --- | ---: | --- | ---: |",
    ]
    measurements = payload["measurements"]
    memory_usage = payload["memory_usage_by_step"]
    for step_name, measurement in measurements.items():
        memory = memory_usage.get(step_name, {})
        lines.append(
            f"| `{step_name}` | {measurement['duration_ms']} | "
            f"{measurement['source']} | {memory.get('peak_bytes', 0)} |"
        )
    if payload.get("metadata"):
        lines.extend(["", "## Metadata", "", "```json"])
        lines.append(json.dumps(payload["metadata"], indent=2, sort_keys=True))
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_performance_reports(
    profile: PerformanceProfile,
    output_dir: str | Path = ".",
) -> tuple[Path, Path]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "performance_report.json"
    markdown_path = target_dir / "performance_report.md"
    json_path.write_text(performance_profile_to_json(profile) + "\n")
    markdown_path.write_text(render_performance_markdown(profile))
    return json_path, markdown_path


def load_performance_profile(path: str | Path) -> PerformanceProfile:
    payload = json.loads(Path(path).read_text())
    return PerformanceProfile.model_validate(payload)


def write_performance_markdown_report(
    profile: PerformanceProfile,
    output_path: str | Path,
) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_performance_markdown(profile))
    return target


def redact_performance_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_performance_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_performance_payload(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


__all__ = [
    "load_performance_profile",
    "performance_profile_to_json",
    "redact_performance_payload",
    "render_performance_markdown",
    "write_performance_markdown_report",
    "write_performance_reports",
]
