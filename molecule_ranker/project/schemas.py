from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ArtifactRecord(BaseModel):
    artifact_id: str
    run_id: str | None = None
    path: str
    artifact_type: str
    sha256: str
    size_bytes: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectRun(BaseModel):
    run_id: str
    run_dir: str
    disease_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    candidate_count: int = 0
    generated_candidate_count: int = 0
    target_count: int = 0
    top_candidates: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectWorkspace(BaseModel):
    project_id: str
    root_dir: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    runs: list[ProjectRun] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dedupe_artifacts(self) -> ProjectWorkspace:
        deduped: dict[str, ArtifactRecord] = {}
        for artifact in self.artifacts:
            deduped[artifact.artifact_id] = artifact
        self.artifacts = list(deduped.values())
        return self


class MultiRunComparison(BaseModel):
    comparison_id: str
    run_ids: list[str]
    disease_names: list[str]
    candidate_overlap: list[str]
    target_overlap: list[str]
    score_deltas: list[dict[str, Any]]
    generated_candidate_counts: dict[str, int]
    differentiators: list[str]
    limitations: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
