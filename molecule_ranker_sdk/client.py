from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol, Self

from molecule_ranker_sdk.errors import (
    APIError,
    AuthenticationError,
    ConflictError,
    MoleculeRankerSDKError,
    NotFoundError,
    PermissionDeniedError,
    RetryExhaustedError,
    ValidationError,
)
from molecule_ranker_sdk.models import (
    AdminHealth,
    ArtifactDownload,
    ArtifactListResponse,
    AuthTokenResponse,
    CampaignRecord,
    ComponentHealth,
    EvaluationReportResponse,
    ExperimentRecord,
    FeedbackResponse,
    GraphQueryResponse,
    HypothesisRecord,
    IntegrationCatalogResponse,
    JobCreateRequest,
    JobListResponse,
    JobRecord,
    ModelCard,
    Pagination,
    PaginationParams,
    ProjectListResponse,
    ProjectSummary,
    ProjectWorkspace,
    ReviewWorkspace,
    RunRecord,
    User,
)

RETRYABLE_STATUS_CODES = {502, 503, 504}


class ResponseProtocol(Protocol):
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    text: str

    def json(self) -> Any: ...


class MoleculeRankerV2Client:
    """Stable SDK client for the molecule-ranker `/api/v2` enterprise API."""

    def __init__(
        self,
        *,
        base_url: str = "http://testserver",
        service_token: str | None = None,
        access_token: str | None = None,
        http_client: Any | None = None,
        session: Any | None = None,
        api_prefix: str = "/api/v2",
        timeout: float = 30.0,
        max_get_retries: int = 2,
        retry_backoff_seconds: float = 0.05,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_prefix = "/" + api_prefix.strip("/")
        self.service_token = service_token
        self.access_token = access_token
        self.timeout = timeout
        self.max_get_retries = max_get_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.last_request_id: str | None = None
        self._owns_http_client = http_client is None and session is None
        self._http_client = http_client or session or self._create_http_client()

    def close(self) -> None:
        close = getattr(self._http_client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def login(self, email: str, password: str) -> AuthTokenResponse:
        payload = self._json(
            "POST",
            "/auth/login",
            json={"email": email, "password": password},
            use_auth=False,
        )
        token = AuthTokenResponse.model_validate(payload)
        self.access_token = token.access_token
        return token

    def me(self) -> User:
        payload = self._json("GET", "/auth/me")
        return User.model_validate(payload.get("user", payload))

    def set_service_token(self, service_token: str) -> None:
        self.service_token = service_token
        self.access_token = None

    def create_project(
        self, *, workspace_id: str | None = None, name: str | None = None
    ) -> ProjectWorkspace:
        payload = self._json("POST", "/projects", json={"workspace_id": workspace_id, "name": name})
        return ProjectWorkspace.model_validate(payload)

    def list_projects(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        filter: str | None = None,
        sort: str = "name",
    ) -> ProjectListResponse:
        payload = self._json(
            "GET",
            "/projects",
            params={"limit": limit, "offset": offset, "filter": filter, "sort": sort},
        )
        return ProjectListResponse.model_validate(payload)

    def iter_projects(
        self, *, limit: int = 100, filter: str | None = None
    ) -> Iterator[ProjectSummary]:
        offset = 0
        while True:
            page = self.list_projects(limit=limit, offset=offset, filter=filter)
            yield from page.projects
            if page.pagination.count < page.pagination.limit:
                return
            offset += page.pagination.count

    def get_project(self, project_id: str) -> ProjectWorkspace:
        return ProjectWorkspace.model_validate(self._json("GET", f"/projects/{project_id}"))

    def list_runs(self, project_id: str) -> list[RunRecord]:
        project = self.get_project(project_id)
        return [RunRecord.model_validate(run) for run in project.runs]

    def submit_job(
        self,
        *,
        project_id: str,
        job_type: str,
        config: Mapping[str, Any] | None = None,
        priority: str = "normal",
        idempotency_key: str | None = None,
    ) -> JobRecord:
        request = JobCreateRequest(
            job_type=job_type,
            config=dict(config or {}),
            priority=priority,
            idempotency_key=idempotency_key,
        )
        return self.create_job(project_id=project_id, request=request)

    def create_job(self, *, project_id: str, request: JobCreateRequest) -> JobRecord:
        headers = {"Idempotency-Key": request.idempotency_key} if request.idempotency_key else None
        payload = self._json(
            "POST",
            f"/projects/{project_id}/jobs",
            json=request.model_dump(mode="json", exclude_none=True),
            headers=headers,
            idempotency_key=request.idempotency_key,
        )
        return JobRecord.model_validate(payload["job"])

    def list_project_jobs(
        self,
        project_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "-created_at",
    ) -> JobListResponse:
        payload = self._json(
            "GET",
            f"/projects/{project_id}/jobs",
            params={"status": status, "limit": limit, "offset": offset, "sort": sort},
        )
        return JobListResponse.model_validate(payload)

    def list_jobs(self, *, project_id: str, limit: int = 100, offset: int = 0) -> JobListResponse:
        return self.list_project_jobs(project_id, limit=limit, offset=offset)

    def iter_project_jobs(self, project_id: str, *, limit: int = 100) -> Iterator[JobRecord]:
        offset = 0
        while True:
            page = self.list_project_jobs(project_id, limit=limit, offset=offset)
            yield from page.jobs
            if page.pagination.count < page.pagination.limit:
                return
            offset += page.pagination.count

    def get_job(self, job_id: str) -> JobRecord:
        payload = self._json("GET", f"/platform/jobs/{job_id}")
        return JobRecord.model_validate(payload["job"])

    def poll_job(self, job_id: str) -> JobRecord:
        return self.get_job(job_id)

    def list_artifacts(
        self,
        *,
        project_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> ArtifactListResponse:
        payload = self._json(
            "GET",
            f"/projects/{project_id}/artifacts",
            params={"limit": limit, "offset": offset},
        )
        return ArtifactListResponse.model_validate(payload)

    def download_artifact(self, *, project_id: str, artifact_id: str) -> bytes:
        response = self._request("GET", f"/projects/{project_id}/artifacts/{artifact_id}/download")
        return response.content

    def download_artifact_with_metadata(
        self,
        *,
        project_id: str,
        artifact_id: str,
    ) -> ArtifactDownload:
        response = self._request("GET", f"/projects/{project_id}/artifacts/{artifact_id}/download")
        return ArtifactDownload(
            artifact_id=response.headers.get("X-Artifact-ID"),
            content=response.content,
            content_type=response.headers.get("content-type"),
            filename=_filename_from_disposition(response.headers.get("content-disposition")),
            request_id=self.last_request_id,
        )

    def submit_feedback(
        self,
        *,
        project_id: str | None,
        page_or_command: str,
        feedback_type: str,
        severity: str,
        text: str,
        artifact_refs: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> FeedbackResponse:
        payload = self._json(
            "POST",
            "/feedback",
            json={
                "project_id": project_id,
                "page_or_command": page_or_command,
                "feedback_type": feedback_type,
                "severity": severity,
                "text": text,
                "artifact_refs": artifact_refs or [],
                "metadata": dict(metadata or {}),
            },
        )
        return FeedbackResponse.model_validate(payload["feedback"])

    def retrieve_evaluation_report(
        self,
        *,
        project_id: str,
        report_id: str,
    ) -> EvaluationReportResponse:
        payload = self._json("GET", f"/projects/{project_id}/evaluation/reports/{report_id}")
        return EvaluationReportResponse.model_validate(payload)

    def run_readiness(self) -> dict[str, Any]:
        return dict(self._json("POST", "/pilot/readiness").get("report", {}))

    def review_health(self) -> ComponentHealth:
        return ComponentHealth.model_validate(self._json("GET", "/review/health"))

    def create_review_workspace(
        self,
        *,
        project_id: str,
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ReviewWorkspace:
        job = self.submit_job(
            project_id=project_id,
            job_type="review_export",
            config={"run_id": run_id, "metadata": dict(metadata or {})},
        )
        return ReviewWorkspace(project_id=project_id, workspace_id=project_id, status=job.status)

    def experiments_health(self) -> ComponentHealth:
        return ComponentHealth.model_validate(self._json("GET", "/experiments/health"))

    def import_experiment_result(
        self,
        *,
        project_id: str,
        artifact_id: str,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ExperimentRecord:
        job = self.submit_job(
            project_id=project_id,
            job_type="experiment_import",
            config={"artifact_id": artifact_id, "metadata": dict(metadata or {})},
            idempotency_key=idempotency_key,
        )
        return ExperimentRecord(
            project_id=project_id, artifact_id=artifact_id, metadata=job.metadata
        )

    def integration_catalog(self) -> IntegrationCatalogResponse:
        return IntegrationCatalogResponse.model_validate(self._json("GET", "/integrations/catalog"))

    def list_integrations(self) -> dict[str, Any]:
        return dict(self._json("GET", "/integrations/systems"))

    def enqueue_integration_sync(
        self,
        *,
        external_system_id: str,
        payload: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            self._json(
                "POST",
                f"/integrations/systems/{external_system_id}/sync",
                json=dict(payload or {}),
                idempotency_key=idempotency_key,
            )
        )

    def submit_model_job(
        self, *, project_id: str, job_type: str, config: Mapping[str, Any] | None = None
    ) -> JobRecord:
        return self._submit_domain_job(
            project_id=project_id, domain="model", job_type=job_type, config=config
        )

    def get_model_card(self, *, project_id: str, model_id: str) -> ModelCard:
        return ModelCard(project_id=project_id, model_id=model_id)

    def submit_graph_job(
        self, *, project_id: str, job_type: str, config: Mapping[str, Any] | None = None
    ) -> JobRecord:
        return self._submit_domain_job(
            project_id=project_id, domain="graph", job_type=job_type, config=config
        )

    def query_graph(
        self, *, project_id: str, query: str, project_ids: list[str] | None = None
    ) -> GraphQueryResponse:
        job = self._submit_domain_job(
            project_id=project_id,
            domain="graph",
            job_type="graph_query",
            config={"query": query, "project_ids": project_ids or [project_id]},
        )
        return GraphQueryResponse(
            project_id=project_id, metadata={"job_id": job.job_id, "status": job.status}
        )

    def submit_hypothesis_job(
        self,
        *,
        project_id: str,
        job_type: str,
        config: Mapping[str, Any] | None = None,
    ) -> JobRecord:
        return self._submit_domain_job(
            project_id=project_id, domain="hypothesis", job_type=job_type, config=config
        )

    def review_hypothesis(
        self,
        *,
        project_id: str,
        hypothesis_id: str,
        decision: str,
        rationale: str,
    ) -> HypothesisRecord:
        payload = self._json(
            "POST",
            f"/projects/{project_id}/hypotheses/{hypothesis_id}/review",
            json={"decision": decision, "rationale": rationale, "human_review_approved": True},
        )
        return HypothesisRecord.model_validate(payload.get("hypothesis", payload))

    def submit_campaign_job(
        self,
        *,
        project_id: str,
        job_type: str,
        config: Mapping[str, Any] | None = None,
    ) -> JobRecord:
        return self._submit_domain_job(
            project_id=project_id, domain="campaign", job_type=job_type, config=config
        )

    def approve_campaign_stage_gate(
        self,
        *,
        project_id: str,
        campaign_id: str,
        stage_gate_id: str,
        rationale: str,
    ) -> CampaignRecord:
        payload = self._json(
            "POST",
            f"/projects/{project_id}/campaigns/{campaign_id}/stage-gates/{stage_gate_id}/approve",
            json={"decision": "approved", "rationale": rationale},
        )
        return CampaignRecord.model_validate(payload.get("campaign", payload))

    def submit_evaluation_job(
        self,
        *,
        project_id: str,
        job_type: str,
        config: Mapping[str, Any] | None = None,
    ) -> JobRecord:
        return self._submit_domain_job(
            project_id=project_id, domain="evaluation", job_type=job_type, config=config
        )

    def admin_health(self) -> AdminHealth:
        payload = self._json("GET", "/admin/health")
        return AdminHealth.model_validate(payload | {"details": payload})

    def ops_health(self) -> AdminHealth:
        payload = self._json("GET", "/ops/health")
        return AdminHealth.model_validate(payload | {"details": payload})

    def paginate(
        self,
        fetch_page: Callable[[PaginationParams], Any],
        *,
        limit: int = 100,
    ) -> Iterator[Any]:
        offset = 0
        while True:
            page = fetch_page(PaginationParams(limit=limit, offset=offset))
            items = _items_from_page(page)
            yield from items
            pagination = getattr(
                page, "pagination", Pagination(limit=limit, offset=offset, count=0)
            )
            if pagination.count < pagination.limit:
                return
            offset += pagination.count

    def _submit_domain_job(
        self,
        *,
        project_id: str,
        domain: str,
        job_type: str,
        config: Mapping[str, Any] | None = None,
    ) -> JobRecord:
        payload = self._json(
            "POST",
            f"/projects/{project_id}/{domain}/jobs",
            json={"job_type": job_type, "config": dict(config or {})},
        )
        return JobRecord.model_validate(payload["job"])

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        if not response.content:
            return {}
        return response.json()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        use_auth: bool = True,
        idempotency_key: str | None = None,
    ) -> ResponseProtocol:
        method = method.upper()
        request_headers = self._headers(headers=headers, use_auth=use_auth)
        attempts = self.max_get_retries + 1 if method == "GET" or idempotency_key else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._http_client.request(
                    method,
                    self._url(path),
                    **self._request_kwargs(
                        params=params,
                        json=json,
                        headers=request_headers,
                    ),
                )
                self.last_request_id = (
                    _header(response.headers, "x-request-id") or request_headers["X-Request-ID"]
                )
                if response.status_code < 400:
                    return response
                if not self._should_retry(
                    method, response.status_code, attempt, attempts, idempotency_key
                ):
                    raise self._api_error(response)
            except MoleculeRankerSDKError:
                raise
            except Exception as exc:
                last_error = exc
                if method != "GET" and not idempotency_key:
                    raise MoleculeRankerSDKError(str(exc), request_id=self.last_request_id) from exc
            if attempt < attempts - 1:
                time.sleep(self.retry_backoff_seconds * (attempt + 1))
        raise RetryExhaustedError(
            f"{method} {path} failed after {attempts} attempts.",
            request_id=self.last_request_id,
        ) from last_error

    def _headers(self, *, headers: Mapping[str, str] | None, use_auth: bool) -> dict[str, str]:
        request_headers = {"Accept": "application/json", "X-Request-ID": f"sdk-{uuid.uuid4().hex}"}
        token = self.service_token or self.access_token
        if use_auth and token:
            request_headers["Authorization"] = f"Bearer {token}"
        if headers:
            request_headers.update(dict(headers))
        return request_headers

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        normalized = "/" + path.lstrip("/")
        if not normalized.startswith("/api/"):
            normalized = f"{self.api_prefix}{normalized}"
        return f"{self.base_url}{normalized}" if self.base_url else normalized

    def _should_retry(
        self,
        method: str,
        status_code: int,
        attempt: int,
        attempts: int,
        idempotency_key: str | None,
    ) -> bool:
        if status_code not in RETRYABLE_STATUS_CODES or attempt >= attempts - 1:
            return False
        return method == "GET" or bool(idempotency_key)

    def _api_error(self, response: ResponseProtocol) -> APIError:
        body = _response_body(response)
        error = body.get("error") if isinstance(body, dict) else None
        message = _error_message(body, response)
        request_id = _header(response.headers, "x-request-id")
        error_code = (
            str(error.get("code")) if isinstance(error, dict) and error.get("code") else None
        )
        kwargs = {
            "status_code": response.status_code,
            "request_id": request_id,
            "error_code": error_code,
            "response_body": body,
        }
        if response.status_code == 401:
            return AuthenticationError(message, **kwargs)
        if response.status_code == 403:
            return PermissionDeniedError(message, **kwargs)
        if response.status_code == 404:
            return NotFoundError(message, **kwargs)
        if response.status_code == 409:
            return ConflictError(message, **kwargs)
        if response.status_code == 422:
            return ValidationError(message, **kwargs)
        return APIError(message, **kwargs)

    def _create_http_client(self) -> Any:
        import httpx

        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def _request_kwargs(
        self,
        *,
        params: Mapping[str, Any] | None,
        json: Mapping[str, Any] | None,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "params": _drop_none(params),
            "json": json,
            "headers": headers,
        }
        if not _is_starlette_test_client(self._http_client):
            kwargs["timeout"] = self.timeout
        return kwargs


def _drop_none(values: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in dict(values or {}).items() if value is not None}


def _is_starlette_test_client(client: Any) -> bool:
    return client.__class__.__module__.startswith("starlette.testclient")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _response_body(response: ResponseProtocol) -> Any:
    try:
        return response.json()
    except Exception:
        return {"detail": response.text}


def _error_message(body: Any, response: ResponseProtocol) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if body.get("detail"):
            return str(body["detail"])
    return f"molecule-ranker API returned HTTP {response.status_code}."


def _items_from_page(page: Any) -> list[Any]:
    for attr in (
        "projects",
        "jobs",
        "artifacts",
        "reviews",
        "experiments",
        "integrations",
        "models",
        "graphs",
        "hypotheses",
        "campaigns",
        "evaluations",
    ):
        value = getattr(page, attr, None)
        if isinstance(value, list):
            return value
    return []


def _filename_from_disposition(disposition: str | None) -> str | None:
    if not disposition:
        return None
    for part in disposition.split(";"):
        part = part.strip()
        if part.startswith("filename="):
            return part.removeprefix("filename=").strip("\"'")
    return None
