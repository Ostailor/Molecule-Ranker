from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from molecule_ranker.client.errors import (
    AuthenticationError,
    MoleculeRankerAPIError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from molecule_ranker.client.models import (
    EvaluationReportResponse,
    FeedbackSubmission,
    JobSummary,
    ListArtifactsResponse,
    ListJobsResponse,
    ListProjectsResponse,
)


class MoleculeRankerClient:
    """Synchronous SDK client for hosted molecule-ranker pilot APIs."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        *,
        service_token: str | None = None,
        session: Any | None = None,
        timeout: float = 30.0,
        api_prefix: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix.strip("/")
        self.timeout = timeout
        self._service_token = service_token
        self._owns_session = session is None
        self._session = session or httpx.Client(base_url=self.base_url, timeout=timeout)

    def authenticate(self, service_token: str) -> None:
        self._service_token = service_token

    def close(self) -> None:
        if self._owns_session and hasattr(self._session, "close"):
            self._session.close()

    def __enter__(self) -> MoleculeRankerClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def create_project(
        self,
        *,
        workspace_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", "/projects", json={"workspace_id": workspace_id, "name": name})

    def list_projects(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        filter: str | None = None,
        sort: str = "name",
    ) -> ListProjectsResponse:
        payload = self._request(
            "GET",
            "/projects",
            params={"limit": limit, "offset": offset, "filter": filter, "sort": sort},
        )
        return ListProjectsResponse.model_validate(payload)

    def submit_job(
        self,
        *,
        project_id: str,
        job_type: str,
        config: Mapping[str, Any] | None = None,
        priority: str = "normal",
        idempotency_key: str | None = None,
    ) -> JobSummary:
        payload = self._request(
            "POST",
            f"/projects/{project_id}/jobs",
            json={
                "job_type": job_type,
                "config": dict(config or {}),
                "priority": priority,
                "idempotency_key": idempotency_key,
            },
        )
        return JobSummary.model_validate(payload["job"])

    def poll_job(self, job_id: str) -> JobSummary:
        payload = self._request("GET", f"/platform/jobs/{job_id}")
        return JobSummary.model_validate(payload["job"])

    def list_jobs(
        self,
        *,
        project_id: str,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "-created_at",
    ) -> ListJobsResponse:
        payload = self._request(
            "GET",
            f"/projects/{project_id}/jobs",
            params={"status": status, "limit": limit, "offset": offset, "sort": sort},
        )
        return ListJobsResponse.model_validate(payload)

    def list_artifacts(
        self,
        *,
        project_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> ListArtifactsResponse:
        payload = self._request(
            "GET",
            f"/projects/{project_id}/artifacts",
            params={"limit": limit, "offset": offset},
        )
        return ListArtifactsResponse.model_validate(payload)

    def download_artifact(self, *, project_id: str, artifact_id: str) -> bytes:
        response = self._raw_request(
            "GET",
            f"/projects/{project_id}/artifacts/{artifact_id}/download",
        )
        if response.status_code >= 400:
            self._raise_error(response)
        return bytes(response.content)

    def submit_feedback(
        self,
        *,
        page_or_command: str,
        feedback_type: str,
        text: str,
        project_id: str | None = None,
        severity: str = "medium",
        artifact_refs: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> FeedbackSubmission:
        payload = self._request(
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
        return FeedbackSubmission.model_validate(payload["feedback"])

    def run_readiness(self) -> dict[str, Any]:
        return self._request("POST", "/pilot/readiness")["report"]

    def retrieve_evaluation_report(
        self,
        *,
        project_id: str,
        report_id: str,
    ) -> EvaluationReportResponse:
        payload = self._request(
            "GET",
            f"/projects/{project_id}/evaluation/reports/{report_id}",
        )
        return EvaluationReportResponse.model_validate(payload)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._raw_request(method, path, json=json, params=params)
        if response.status_code >= 400:
            self._raise_error(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise MoleculeRankerAPIError(
                status_code=response.status_code,
                message="API returned a non-object JSON payload.",
                request_id=response.headers.get("X-Request-ID"),
            )
        return payload

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._service_token}"} if self._service_token else {}
        clean_params = {key: value for key, value in (params or {}).items() if value is not None}
        clean_json = {key: value for key, value in (json or {}).items() if value is not None}
        url = self._path(path)
        kwargs: dict[str, Any] = {"headers": headers, "params": clean_params}
        if clean_json:
            kwargs["json"] = clean_json
        if self._owns_session:
            kwargs["timeout"] = self.timeout
        return self._session.request(method, url, **kwargs)

    def _path(self, path: str) -> str:
        normalized = "/" + path.lstrip("/")
        if not self.api_prefix:
            return normalized if not self._owns_session else normalized
        return f"/{self.api_prefix}{normalized}"

    def _raise_error(self, response: Any) -> None:
        request_id = response.headers.get("X-Request-ID")
        error_code: str | None = None
        message = response.text or "API request failed."
        details: Any | None = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            details = payload.get("detail")
            error = payload.get("error")
            if isinstance(error, dict):
                error_code = str(error.get("code") or "") or None
                message = str(error.get("message") or message)
                request_id = str(error.get("request_id") or request_id or "") or None
            elif payload.get("detail") is not None:
                message = str(payload["detail"])
        error_cls = MoleculeRankerAPIError
        if response.status_code == 401:
            error_cls = AuthenticationError
        elif response.status_code == 403:
            error_cls = PermissionDeniedError
        elif response.status_code == 404:
            error_cls = NotFoundError
        elif response.status_code == 422:
            error_cls = ValidationError
        raise error_cls(
            status_code=response.status_code,
            message=message,
            request_id=request_id,
            error_code=error_code,
            details=details,
        )
