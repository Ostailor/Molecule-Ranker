from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI

from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig
from molecule_ranker.server.dependencies import require_api_key
from molecule_ranker.server.routes import artifacts, codex, experiments, projects, review
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def create_app(
    *,
    root_dir: Path = Path("."),
    enable_codex_backbone: bool = False,
    codex_config: CodexBackboneConfig | None = None,
    codex_provider: Any | None = None,
    api_key: str | None = None,
) -> FastAPI:
    app = FastAPI(
        title="molecule-ranker local API",
        version="0.7.0",
        description="Local-only API for molecule-ranker project artifacts and Codex tasks.",
    )
    resolved_root = root_dir.resolve()
    config = codex_config or CodexBackboneConfig(
        enable_codex_backbone=enable_codex_backbone,
        codex_working_dir=resolved_root,
    )
    app.state.root_dir = resolved_root
    app.state.workspace_store = ProjectWorkspaceStore(resolved_root)
    app.state.enable_codex_backbone = bool(config.enable_codex_backbone)
    app.state.codex_config = config
    app.state.codex_provider = codex_provider
    app.state.api_key = api_key

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "local_only": True,
            "host": "127.0.0.1",
            "codex_enabled": bool(app.state.enable_codex_backbone),
        }

    dependencies = [Depends(require_api_key)]
    app.include_router(projects.router, dependencies=dependencies)
    app.include_router(artifacts.router, dependencies=dependencies)
    app.include_router(codex.router, dependencies=dependencies)
    app.include_router(review.router, dependencies=dependencies)
    app.include_router(experiments.router, dependencies=dependencies)
    return app


def run_local_server(
    *,
    root_dir: Path = Path("."),
    host: str = "127.0.0.1",
    port: int = 8765,
    enable_codex_backbone: bool = False,
    api_key: str | None = None,
) -> None:
    import uvicorn

    app = create_app(
        root_dir=root_dir,
        enable_codex_backbone=enable_codex_backbone,
        api_key=api_key,
    )
    uvicorn.run(app, host=host, port=port)
