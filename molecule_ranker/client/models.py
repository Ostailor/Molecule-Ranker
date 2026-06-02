from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Pagination(BaseModel):
    limit: int
    offset: int
    count: int
    next_offset: int | None = None
    previous_offset: int | None = None


class ProjectSummary(BaseModel):
    workspace_id: str
    name: str
    root_dir: str | None = None
    run_count: int = 0
    artifact_count: int = 0


class JobSummary(BaseModel):
    job_id: str
    org_id: str = "default"
    project_id: str | None = None
    requested_by_user_id: str
    job_type: str
    status: str
    priority: str = "normal"
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result_artifact_ids: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactSummary(BaseModel):
    artifact_id: str
    workspace_id: str | None = None
    path: str
    artifact_type: str
    sha256: str
    size_bytes: int
    run_id: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    relative_path: str | None = None


class FeedbackSubmission(BaseModel):
    feedback_id: str
    user_id: str
    project_id: str | None = None
    page_or_command: str
    feedback_type: str
    severity: str
    text: str
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListProjectsResponse(BaseModel):
    projects: list[ProjectSummary]
    pagination: Pagination


class ListJobsResponse(BaseModel):
    jobs: list[JobSummary]
    pagination: Pagination


class ListArtifactsResponse(BaseModel):
    workspace_id: str
    artifacts: list[ArtifactSummary]
    pagination: Pagination


class EvaluationReportResponse(BaseModel):
    artifact_id: str
    report: dict[str, Any]
    evaluation_boundary: str
