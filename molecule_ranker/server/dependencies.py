from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request

from molecule_ranker.codex_backbone.provider import CodexBackboneProvider
from molecule_ranker.workspace.store import ProjectWorkspaceStore


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    expected = getattr(request.app.state, "api_key", None)
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key.")


def codex_provider(request: Request) -> Any:
    if not bool(request.app.state.enable_codex_backbone):
        raise HTTPException(
            status_code=403,
            detail="Codex backbone is disabled. Set enable_codex_backbone=True to opt in.",
        )
    provider = request.app.state.codex_provider
    if provider is not None:
        return provider
    return CodexBackboneProvider(request.app.state.codex_config)


def workspace_store(request: Request) -> ProjectWorkspaceStore:
    return request.app.state.workspace_store
