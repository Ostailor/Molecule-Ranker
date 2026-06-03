from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SDKModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class PaginationParams(SDKModel):
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class Pagination(SDKModel):
    limit: int = 100
    offset: int = 0
    count: int = 0

    @property
    def next_offset(self) -> int | None:
        return self.offset + self.count if self.count >= self.limit else None

    @property
    def previous_offset(self) -> int | None:
        if self.offset <= 0:
            return None
        return max(0, self.offset - self.limit)


class AuthTokenResponse(SDKModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int | None = None
    refresh_token: str | None = None
    user: dict[str, Any] | None = None


class User(SDKModel):
    user_id: str
    email: str
    display_name: str | None = None
    is_admin: bool = False
    roles: list[str] = Field(default_factory=list)


class ProjectSummary(SDKModel):
    workspace_id: str
    name: str | None = None
    root_dir: str | None = None
    run_count: int = 0
    artifact_count: int = 0


class ProjectWorkspace(SDKModel):
    workspace_id: str
    name: str | None = None
    root_dir: str | None = None
    runs: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ProjectListResponse(SDKModel):
    projects: list[ProjectSummary] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)


class RunRecord(SDKModel):
    run_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobCreateRequest(SDKModel):
    job_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    priority: str = "normal"
    idempotency_key: str | None = None


class JobRecord(SDKModel):
    job_id: str
    job_type: str
    status: str
    project_id: str | None = None
    priority: str | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobListResponse(SDKModel):
    jobs: list[JobRecord] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)


class ArtifactRecord(SDKModel):
    artifact_id: str
    workspace_id: str | None = None
    path: str | None = None
    artifact_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactListResponse(SDKModel):
    workspace_id: str | None = None
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    pagination: Pagination = Field(default_factory=Pagination)


class ArtifactDownload(SDKModel):
    artifact_id: str | None = None
    content: bytes
    content_type: str | None = None
    filename: str | None = None
    request_id: str | None = None


class FeedbackResponse(SDKModel):
    feedback_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationReportResponse(SDKModel):
    artifact_id: str
    report: dict[str, Any]
    evaluation_boundary: str


class IntegrationCatalogResponse(SDKModel):
    connectors: list[dict[str, Any]] = Field(default_factory=list)
    default_mode: str | None = None
    write_policy: str | None = None


class ComponentHealth(SDKModel):
    ok: bool | None = None
    component: str | None = None
    version: str | None = None


class AdminHealth(SDKModel):
    ok: bool | None = None
    database: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ReviewWorkspace(SDKModel):
    project_id: str | None = None
    workspace_id: str | None = None
    review_id: str | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentRecord(SDKModel):
    experiment_id: str | None = None
    project_id: str | None = None
    artifact_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelCard(SDKModel):
    project_id: str | None = None
    model_id: str | None = None
    schema_version: str | None = None
    contract_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphQueryResponse(SDKModel):
    project_id: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HypothesisRecord(SDKModel):
    hypothesis_id: str | None = None
    project_id: str | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CampaignRecord(SDKModel):
    campaign_id: str | None = None
    project_id: str | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def paginate_offsets(
    first_page: Pagination,
    *,
    max_pages: int | None = None,
) -> Iterator[int]:
    page = first_page
    yielded = 0
    while page.next_offset is not None:
        if max_pages is not None and yielded >= max_pages:
            return
        yielded += 1
        yield page.next_offset


def merge_pages(items: Sequence[Sequence[Any]]) -> list[Any]:
    merged: list[Any] = []
    for page in items:
        merged.extend(page)
    return merged
