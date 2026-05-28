from __future__ import annotations

from html import escape
from typing import Any

from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def render_hosted_dashboard(
    *,
    database: PlatformDatabase,
    workspace_store: ProjectWorkspaceStore,
) -> str:
    workspace = workspace_store.load_or_create()
    health = database.health()
    audit_events = database.list_audit_events(limit=20)
    rows = "".join(
        "<tr>"
        f"<td>{_h(event.created_at.isoformat())}</td>"
        f"<td>{_h(event.event_type)}</td>"
        f"<td>{_h(event.actor_user_id or '')}</td>"
        f"<td>{_h(event.project_id or '')}</td>"
        f"<td>{_h(event.summary)}</td>"
        "</tr>"
        for event in audit_events
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>molecule-ranker V1.0</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;"
        "color:#182230;background:#f6f8fb;line-height:1.45}"
        "header,main{max-width:1180px;margin:0 auto;padding:18px}"
        "header{background:#fff;border-bottom:1px solid #d6deea}"
        "section{margin:18px 0}.grid{display:grid;grid-template-columns:"
        "repeat(auto-fit,minmax(180px,1fr));gap:10px}"
        ".metric{background:#fff;border:1px solid #d6deea;border-radius:6px;padding:12px}"
        ".notice{background:#fff8db;border:1px solid #d6a800;border-radius:6px;padding:12px}"
        "table{width:100%;border-collapse:collapse;background:#fff}"
        "th,td{border:1px solid #d6deea;padding:8px;text-align:left;vertical-align:top}"
        "th{background:#eef3f8}"
        "</style></head><body>"
        "<header><h1>molecule-ranker V1.0 hosted dashboard</h1></header>"
        "<main>"
        "<p class=\"notice\">Internal research platform. Source-backed evidence remains "
        "authoritative; Codex outputs are guarded assistant artifacts, not biomedical truth.</p>"
        "<section class=\"grid\">"
        f"{_metric('Project', workspace.workspace_id)}"
        f"{_metric('Runs', len(workspace.runs))}"
        f"{_metric('Artifacts', len(workspace.artifacts))}"
        f"{_metric('Users', health['users'])}"
        f"{_metric('Pending jobs', health['pending_jobs'])}"
        f"{_metric('Database', health['database'])}"
        "</section>"
        "<section><h2>Operational health</h2>"
        f"<pre>{_h(health)}</pre></section>"
        "<section><h2>Recent audit events</h2>"
        "<table><thead><tr><th>Created</th><th>Event</th><th>Actor</th>"
        "<th>Project</th><th>Summary</th></tr></thead><tbody>"
        f"{rows}</tbody></table></section>"
        "</main></body></html>\n"
    )


def _metric(label: str, value: Any) -> str:
    return f"<div class=\"metric\"><strong>{_h(label)}</strong><br>{_h(value)}</div>"


def _h(value: Any) -> str:
    return escape(str(value), quote=True)
