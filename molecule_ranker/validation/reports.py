from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from molecule_ranker.validation.schemas import GoldenValidationReport, GoldenWorkflowResult


def write_json_artifact(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def write_markdown_artifact(path: Path, title: str, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([f"# {title}", "", *lines, ""]) + "\n")
    return path


def render_workflow_summary(result: GoldenWorkflowResult) -> str:
    lines = [
        f"- Workflow: `{result.workflow_id}`",
        f"- Status: `{result.status}`",
        f"- Mode: `{result.mode}`",
        f"- Artifact directory: `{result.artifact_dir}`",
        f"- Artifacts: {len(result.artifacts)}",
        f"- Missing artifacts: {len(result.missing_artifacts)}",
        f"- Forbidden findings: {len(result.forbidden_findings)}",
    ]
    return "\n".join(lines)


def write_validation_report(path: Path, report: GoldenValidationReport) -> Path:
    lines = [
        f"- Status: `{report.status}`",
        f"- Workflow count: {report.workflow_count}",
        f"- Live validation: {str(report.live_validation).lower()}",
        "",
        "## Workflows",
        "",
    ]
    for result in report.results:
        lines.append(render_workflow_summary(result))
        lines.append("")
    return write_markdown_artifact(path, "V1.0 Golden Workflow Validation", lines)
