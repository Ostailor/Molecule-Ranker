from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from molecule_ranker.project.comparison import compare_project_runs
from molecule_ranker.project.workspace import ProjectWorkspaceStore


def run_project_api_server(root_dir: Path, *, host: str, port: int) -> None:
    store = ProjectWorkspaceStore(root_dir)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
            parsed = urlparse(self.path)
            try:
                workspace = store.load_or_create()
                if parsed.path == "/health":
                    self._json({"ok": True})
                elif parsed.path == "/project":
                    self._json(workspace.model_dump(mode="json"))
                elif parsed.path == "/runs":
                    self._json({"runs": [run.model_dump(mode="json") for run in workspace.runs]})
                elif parsed.path == "/artifacts":
                    self._json(
                        {
                            "artifacts": [
                                artifact.model_dump(mode="json")
                                for artifact in workspace.artifacts
                            ]
                        }
                    )
                elif parsed.path == "/comparison":
                    run_ids = parse_qs(parsed.query).get("run_id", [])
                    runs = [
                        run for run in workspace.runs if not run_ids or run.run_id in run_ids
                    ]
                    self._json(compare_project_runs(runs).model_dump(mode="json"))
                else:
                    self._json({"error": "not found"}, status=404)
            except Exception as exc:  # pragma: no cover - defensive server boundary
                self._json({"error": str(exc)}, status=500)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, payload: dict[str, object], *, status: int = 200) -> None:
            data = json.dumps(payload, indent=2, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    ThreadingHTTPServer((host, port), Handler).serve_forever()
