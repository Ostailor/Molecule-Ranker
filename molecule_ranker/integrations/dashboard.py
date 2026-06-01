from __future__ import annotations

from html import escape
from typing import Any


def render_integration_dashboard(summary: dict[str, Any]) -> str:
    connector_rows = "".join(
        "<tr>"
        f"<td>{_h(connector.get('connector_id'))}</td>"
        f"<td>{_h(connector.get('name'))}</td>"
        f"<td>{_h(connector.get('provider'))}</td>"
        f"<td>{_h(connector.get('mode'))}</td>"
        f"<td>{_h(connector.get('allow_writes'))}</td>"
        "</tr>"
        for connector in summary.get("connectors", [])
    )
    sync_rows = "".join(
        "<tr>"
        f"<td>{_h(job.get('sync_job_id'))}</td>"
        f"<td>{_h(job.get('connector_id'))}</td>"
        f"<td>{_h(job.get('direction'))}</td>"
        f"<td>{_h(job.get('status'))}</td>"
        f"<td>{_h(job.get('rows_seen'))}</td>"
        "</tr>"
        for job in summary.get("sync_jobs", [])
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>molecule-ranker V1.5 integrations</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;"
        "color:#182230;background:#f6f8fb;line-height:1.45}"
        "header,main{max-width:1180px;margin:0 auto;padding:18px}"
        "header{background:#fff;border-bottom:1px solid #d6deea}"
        ".notice{background:#fff8db;border:1px solid #d6a800;border-radius:6px;padding:12px}"
        "table{width:100%;border-collapse:collapse;background:#fff;margin:12px 0}"
        "th,td{border:1px solid #d6deea;padding:8px;text-align:left;vertical-align:top}"
        "th{background:#eef3f8}"
        "</style></head><body>"
        "<header><h1>molecule-ranker V1.5 integrations</h1></header>"
        "<main>"
        "<p class=\"notice\">External integrations default to read-only, dry-run, or sandbox "
        "operation. Secrets are never displayed. Write/export paths require explicit opt-in.</p>"
        "<section><h2>Connectors</h2><table><thead><tr><th>ID</th><th>Name</th>"
        "<th>Provider</th><th>Mode</th><th>Writes</th></tr></thead><tbody>"
        f"{connector_rows}</tbody></table></section>"
        "<section><h2>Sync jobs</h2><table><thead><tr><th>ID</th><th>Connector</th>"
        "<th>Direction</th><th>Status</th><th>Rows</th></tr></thead><tbody>"
        f"{sync_rows}</tbody></table></section>"
        "</main></body></html>\n"
    )


def _h(value: Any) -> str:
    return escape(str(value or ""), quote=True)
