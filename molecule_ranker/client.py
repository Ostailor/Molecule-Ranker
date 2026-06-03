from __future__ import annotations

from typing import Any

from molecule_ranker_sdk import (
    APIError,
    AuthenticationError,
    MoleculeRankerV2Client,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from molecule_ranker_sdk.models import ProjectWorkspace


class MoleculeRankerClient(MoleculeRankerV2Client):
    """Backward-compatible import path for the stable V2 SDK client."""

    def __init__(self, *, session: Any | None = None, **kwargs: Any) -> None:
        if session is not None and "http_client" not in kwargs:
            kwargs["http_client"] = session
        super().__init__(**kwargs)

    def create_project(
        self,
        *,
        workspace_id: str | None = None,
        name: str | None = None,
    ) -> Any:
        project: ProjectWorkspace = super().create_project(workspace_id=workspace_id, name=name)
        return project.model_dump(mode="json")


__all__ = [
    "APIError",
    "AuthenticationError",
    "MoleculeRankerClient",
    "NotFoundError",
    "PermissionDeniedError",
    "ValidationError",
]
