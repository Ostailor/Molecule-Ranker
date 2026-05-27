from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from molecule_ranker.project.comparison import compare_project_runs
from molecule_ranker.project.schemas import ProjectWorkspace


def generate_project_dashboard(workspace: ProjectWorkspace, output_dir: Path) -> Path:
    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("index.html").write_text(_page("Project Dashboard", _body(workspace)))
    root.joinpath("project.json").write_text(
        json.dumps(workspace.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    return root


def _body(workspace: ProjectWorkspace) -> str:
    comparison_html = ""
    if len(workspace.runs) >= 2:
        comparison = compare_project_runs(workspace.runs)
        comparison_html = (
            "<h2>Multi-run comparison</h2>"
            f"<p>Shared candidates: {_h(', '.join(comparison.candidate_overlap) or 'none')}</p>"
            f"<p>Shared targets: {_h(', '.join(comparison.target_overlap) or 'none')}</p>"
            "<ul>"
            + "".join(f"<li>{_h(item)}</li>" for item in comparison.differentiators)
            + "</ul>"
        )
    rows = []
    for run in workspace.runs:
        rows.append(
            "<tr>"
            f"<td>{_h(run.run_id)}</td>"
            f"<td>{_h(run.disease_name)}</td>"
            f"<td>{run.candidate_count}</td>"
            f"<td>{run.generated_candidate_count}</td>"
            f"<td>{run.target_count}</td>"
            f"<td>{_h(run.run_dir)}</td>"
            "</tr>"
        )
    artifact_rows = []
    for artifact in workspace.artifacts:
        artifact_rows.append(
            "<tr>"
            f"<td>{_h(artifact.artifact_id)}</td>"
            f"<td>{_h(artifact.run_id or '')}</td>"
            f"<td>{_h(artifact.artifact_type)}</td>"
            f"<td>{artifact.size_bytes}</td>"
            f"<td><code>{_h(artifact.sha256[:12])}</code></td>"
            "</tr>"
        )
    return (
        f"<h2>{_h(workspace.project_id)}</h2>"
        "<p class=\"notice\">Codex-assisted project views are grounded in local artifacts. "
        "They do not create biomedical evidence or modify molecule scores.</p>"
        "<section class=\"grid\">"
        f"{_metric('Runs', str(len(workspace.runs)))}"
        f"{_metric('Artifacts', str(len(workspace.artifacts)))}"
        f"{_metric('Updated', workspace.updated_at.isoformat())}"
        "</section>"
        "<h2>Runs</h2>"
        "<table><thead><tr><th>Run ID</th><th>Disease</th><th>Candidates</th>"
        "<th>Generated</th><th>Targets</th><th>Directory</th></tr></thead><tbody>"
        f"{''.join(rows)}"
        "</tbody></table>"
        f"{comparison_html}"
        "<h2>Artifact registry</h2>"
        "<table><thead><tr><th>Artifact ID</th><th>Run ID</th><th>Type</th>"
        "<th>Bytes</th><th>SHA-256</th></tr></thead><tbody>"
        f"{''.join(artifact_rows)}"
        "</tbody></table>"
    )


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_h(title)}</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;"
        "color:#1f2937;background:#f7f9fb;line-height:1.45}"
        "header,main{max-width:1180px;margin:0 auto;padding:20px}"
        "header{background:#fff;border-bottom:1px solid #d8dee8}"
        ".notice{background:#fff8db;border:1px solid #d6a800;padding:12px;border-radius:6px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}"
        ".metric{background:#fff;border:1px solid #d8dee8;border-radius:6px;padding:12px}"
        "table{width:100%;border-collapse:collapse;background:#fff;margin:12px 0}"
        "th,td{border:1px solid #d8dee8;padding:8px;text-align:left;vertical-align:top}"
        "th{background:#eef3f8}code{word-break:break-word}"
        "</style></head><body>"
        "<header><h1>molecule-ranker Project Dashboard</h1></header>"
        f"<main>{body}</main></body></html>\n"
    )


def _metric(label: str, value: str) -> str:
    return f"<div class=\"metric\"><strong>{_h(label)}</strong><br>{_h(value)}</div>"


def _h(value: Any) -> str:
    return escape(str(value), quote=True)
