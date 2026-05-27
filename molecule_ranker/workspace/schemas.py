from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ArtifactRecord(BaseModel):
    artifact_id: str
    workspace_id: str
    path: str
    artifact_type: str
    sha256: str
    size_bytes: int
    run_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_id", "workspace_id", "path", "artifact_type", "sha256")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class ProjectRun(BaseModel):
    run_id: str
    workspace_id: str
    run_dir: str
    disease_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    candidate_count: int = 0
    generated_candidate_count: int = 0
    target_count: int = 0
    top_candidates: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("run_id", "workspace_id", "run_dir", "disease_name")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class ProjectWorkspace(BaseModel):
    workspace_id: str
    name: str
    root_dir: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    runs: list[ProjectRun] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    codex_outputs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("workspace_id", "name", "root_dir")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value

    @model_validator(mode="after")
    def normalize_records(self) -> ProjectWorkspace:
        run_map = {run.run_id: run for run in self.runs}
        self.runs = sorted(run_map.values(), key=lambda run: run.created_at.isoformat())
        artifact_map = {artifact.artifact_id: artifact for artifact in self.artifacts}
        self.artifacts = sorted(artifact_map.values(), key=lambda artifact: artifact.artifact_id)
        return self

    @property
    def project_id(self) -> str:
        return self.workspace_id


class ProjectComparison(BaseModel):
    comparison_id: str
    workspace_id: str
    run_ids: list[str]
    disease_names: list[str]
    candidate_overlap: list[str]
    target_overlap: list[str]
    score_deltas: list[dict[str, Any]]
    run_summaries: list[dict[str, Any]]
    codex_summary: dict[str, Any] | None = None
    limitations: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


def path_as_str(path: Path) -> str:
    return str(path.resolve())
