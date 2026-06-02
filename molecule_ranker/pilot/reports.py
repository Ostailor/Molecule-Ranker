from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.pilot.schemas import PilotReadinessReport


def render_pilot_readiness_markdown(report: PilotReadinessReport) -> str:
    lines = [
        "# Enterprise Pilot Readiness Audit",
        "",
        f"- Report ID: `{report.report_id}`",
        f"- Created: `{report.created_at.isoformat()}`",
        f"- Version: `{report.version}`",
        f"- Environment: `{report.environment}`",
        f"- Summary: {report.passed_count} pass, {report.warning_count} warn, "
        f"{report.failed_count} fail",
        "",
        "## Checks",
        "",
        "| Check | Status | Message |",
        "| --- | --- | --- |",
    ]
    for check in report.checks:
        lines.append(
            f"| `{check['check_id']}` | {check['status']} | {check['message']} |"
        )
    if report.blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in report.blockers)
    if report.recommendations:
        lines.extend(["", "## Recommendations", ""])
        lines.extend(f"- {item}" for item in report.recommendations)
    lines.append("")
    return "\n".join(lines)


def write_pilot_readiness_report(report: PilotReadinessReport, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".json":
        target.write_text(report.model_dump_json(indent=2) + "\n")
    else:
        target.write_text(render_pilot_readiness_markdown(report))
    return target


def pilot_readiness_report_to_json(report: PilotReadinessReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)


__all__ = [
    "pilot_readiness_report_to_json",
    "render_pilot_readiness_markdown",
    "write_pilot_readiness_report",
]
