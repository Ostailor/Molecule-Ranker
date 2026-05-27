from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import Any

import requests

from molecule_ranker.integrations.connectors.base import (
    ConnectorCallRecorder,
    ConnectorError,
    ExternalConnector,
)
from molecule_ranker.integrations.credentials import redact_secret_values
from molecule_ranker.integrations.schemas import (
    ConnectorConfig,
    DataContract,
    ExternalRecordRef,
    IntegrationHealthStatus,
)
from molecule_ranker.integrations.validation import validate_record_against_contract

CredentialResolver = Callable[[str], str]

READ_METHODS = {"GET"}
WRITE_METHODS = {"POST", "PUT", "PATCH"}
FORBIDDEN_METHODS = {"DELETE", "CONNECT", "TRACE"}
SECRET_HEADER_MARKERS = {"authorization", "api-key", "apikey", "token", "secret", "password"}


class GenericRESTConnector(ExternalConnector):
    connector_name = "generic-rest"
    provider = "generic_rest"
    system_type = "generic_rest"
    capabilities = (
        "rest_list_records",
        "rest_get_record",
        "rest_export_record",
        "webhook_ingestion",
        "contract_validation",
    )
    limitations = ExternalConnector.limitations + (
        "Only declarative endpoint templates and JSON paths are supported.",
        "GET is allowed by default; POST, PUT, and PATCH require explicit write enablement.",
        "No dynamic Python eval or arbitrary code execution is supported.",
    )

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        recorder: ConnectorCallRecorder | None = None,
        http_client: Any | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        effective_config = config
        if config.mode == "dry_run" and not config.allow_writes:
            effective_config = config.model_copy(update={"mode": "read_only"})
        super().__init__(effective_config, recorder=recorder)
        self.http_client = http_client or requests.Session()
        self.credential_resolver = credential_resolver

    def health_check(self) -> IntegrationHealthStatus:
        def call() -> IntegrationHealthStatus:
            if not self._base_url(required=False):
                return self._health("unconfigured", "base_url is not configured.")
            endpoint = self._endpoint("health", required=False)
            if endpoint:
                try:
                    self._request("GET", endpoint)
                except Exception as exc:
                    return self._health("degraded", f"Generic REST health check failed: {exc}")
            return self._health("ok", "Generic REST connector is configured.")

        return self._call("health_check", call)

    def list_records(
        self,
        *,
        object_type: str = "record",
        params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._call(
            "list_records",
            lambda: self._list_records(object_type=object_type, params=params or {}, limit=limit),
        )

    def get_record(self, record_id: str, *, object_type: str = "record") -> dict[str, Any]:
        return self._call(
            "get_record",
            lambda: self._get_record(record_id, object_type=object_type),
        )

    def export_record(
        self,
        record: dict[str, Any],
        *,
        object_type: str = "record",
    ) -> dict[str, Any]:
        return self._call(
            "export_record",
            lambda: self._export_record(record, object_type=object_type),
            write=True,
            payload=record,
        )

    def parse_webhook(
        self,
        payload: dict[str, Any],
        *,
        object_type: str = "webhook_event",
    ) -> dict[str, Any]:
        return self._call(
            "parse_webhook",
            lambda: self._record_result(payload, object_type),
        )

    def redacted_headers(self) -> dict[str, str]:
        return _redact_headers(self._headers())

    def _list_records(
        self,
        *,
        object_type: str,
        params: dict[str, Any],
        limit: int | None,
    ) -> list[dict[str, Any]]:
        endpoint = self._endpoint("list_records", required=True)
        pagination = dict(self.config.config.get("pagination") or {})
        records: list[dict[str, Any]] = []
        next_params = dict(params)
        page_count = 0
        while True:
            response = self._request("GET", endpoint, params=next_params)
            page_records = self._extract_records(response)
            records.extend(self._record_result(record, object_type) for record in page_records)
            page_count += 1
            if limit is not None and len(records) >= limit:
                return records[:limit]
            next_value = self._next_page_value(response, pagination, current_params=next_params)
            if not next_value:
                return records
            next_params = {**next_params, **next_value}
            max_pages = int(pagination.get("max_pages") or 100)
            if page_count >= max_pages:
                raise ConnectorError("Generic REST pagination exceeded max_pages.")

    def _get_record(self, record_id: str, *, object_type: str) -> dict[str, Any]:
        endpoint = self._endpoint("get_record", required=True).format(record_id=record_id)
        response = self._request("GET", endpoint)
        raw = self._extract_record(response)
        return self._record_result(raw, object_type)

    def _export_record(self, record: dict[str, Any], *, object_type: str) -> dict[str, Any]:
        endpoint = self._endpoint("export_record", required=True)
        method = str(self.config.config.get("export_method") or "POST").upper()
        response = self._request(method, endpoint, json=record)
        raw = self._extract_record(response)
        return self._record_result(raw, object_type)

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        normalized_method = method.upper()
        self._validate_method(normalized_method)
        url = self._url(endpoint)
        headers = {**self._headers(), **dict(kwargs.pop("headers", {}) or {})}
        timeout = float(self.config.config.get("timeout_seconds") or 30)
        retries = int(self.config.config.get("retry_count") or 1)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = self.http_client.request(
                    normalized_method,
                    url,
                    headers=headers,
                    timeout=timeout,
                    **kwargs,
                )
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                return response.json() if hasattr(response, "json") else response
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                sleep(min(0.25 * (attempt + 1), 1.0))
        raise ConnectorError(f"Generic REST request failed: {last_error}") from last_error

    def _validate_method(self, method: str) -> None:
        if method in FORBIDDEN_METHODS or method not in READ_METHODS | WRITE_METHODS:
            raise ConnectorError(f"HTTP method {method} is not allowed for Generic REST.")
        if method in WRITE_METHODS:
            self._require_explicit_write_permission()

    def _headers(self) -> dict[str, str]:
        headers = {
            str(key): str(value)
            for key, value in dict(self.config.config.get("request_headers") or {}).items()
        }
        auth = dict(self.config.config.get("auth") or {})
        if auth.get("method"):
            method = str(auth["method"])
        else:
            method = "credential_ref" if self.config.credential_ref else "none"
        if method == "none":
            return headers
        token = self._auth_secret(auth)
        if method in {"bearer", "credential_ref"}:
            headers["Authorization"] = f"Bearer {token}"
        elif method == "api_key_header":
            header_name = str(auth.get("header_name") or "X-API-Key")
            headers[header_name] = token
        elif method == "basic":
            headers["Authorization"] = f"Basic {token}"
        else:
            raise ConnectorError(f"Unsupported Generic REST auth method: {method}")
        return headers

    def _auth_secret(self, auth: dict[str, Any]) -> str:
        env_var = auth.get("env_var")
        if env_var:
            value = os.environ.get(str(env_var))
            if not value:
                raise ConnectorError(f"Generic REST auth env var {env_var} is not set.")
            return value
        if self.config.credential_ref:
            ref = self.config.credential_ref
            if ref.backend == "env":
                if not ref.key_ref:
                    raise ConnectorError("Generic REST environment credential reference is empty.")
                value = os.environ.get(ref.key_ref)
                if not value:
                    raise ConnectorError(f"Generic REST auth env var {ref.key_ref} is not set.")
                return value
            if self.credential_resolver:
                return self.credential_resolver(ref.credential_id)
            raise ConnectorError(
                "Generic REST credential reference requires a resolver for non-env backends."
            )
        raise ConnectorError(
            "Generic REST auth is configured but no secret reference was provided."
        )

    def _extract_records(self, response: Any) -> list[dict[str, Any]]:
        path = self._response_path("records", default="items")
        value = _json_path(response, path)
        if value is None and isinstance(response, list):
            value = response
        if not isinstance(value, list):
            raise ConnectorError(f"Generic REST response path {path!r} did not resolve to a list.")
        return [item for item in value if isinstance(item, dict)]

    def _extract_record(self, response: Any) -> dict[str, Any]:
        path = self._response_path("record", default="")
        value = _json_path(response, path) if path else response
        if not isinstance(value, dict):
            raise ConnectorError("Generic REST record response did not resolve to an object.")
        return value

    def _record_result(self, raw: dict[str, Any], object_type: str) -> dict[str, Any]:
        payload = dict(raw)
        ref = self._external_ref(payload, object_type)
        payload.setdefault("source_record_id", ref.external_record_id)
        self._validate_payload(payload)
        artifact = self._store_payload_artifact(payload, object_type=object_type)
        return {
            "external_ref": ref,
            "payload": _public_payload(payload),
            "raw_payload_artifact": artifact,
        }

    def _external_ref(self, payload: dict[str, Any], object_type: str) -> ExternalRecordRef:
        response_paths = dict(self.config.config.get("response_paths") or {})
        id_paths = _as_list(response_paths.get("record_id") or ["id", "external_record_id"])
        url_paths = _as_list(response_paths.get("record_url") or ["url", "web_url", "webURL"])
        record_id = _first_path(payload, id_paths)
        if not record_id:
            raise ConnectorError("Generic REST record is missing an external record ID.")
        return ExternalRecordRef(
            external_system_id=self.config.connector_id,
            external_record_type=self._mapped_object_type(object_type),
            external_record_id=str(record_id),
            external_url=_string_or_none(_first_path(payload, url_paths)),
            external_version=_string_or_none(_first_path(payload, ["version", "updated_at"])),
            retrieved_at=datetime.now(UTC),
            metadata={"provider": "generic_rest"},
        )

    def _mapped_object_type(self, object_type: str) -> str:
        mappings = dict(self.config.config.get("object_type_mappings") or {})
        return str(mappings.get(object_type) or mappings.get("default") or object_type)

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        contract = self._data_contract()
        if contract is None:
            return
        issues = validate_record_against_contract(payload, contract)
        if issues:
            raise ConnectorError(f"Generic REST response failed data contract validation: {issues}")

    def _data_contract(self) -> DataContract | None:
        raw = self.config.config.get("data_contract")
        if not raw:
            return None
        if isinstance(raw, DataContract):
            return raw
        if isinstance(raw, dict):
            return DataContract.model_validate(raw)
        raise ConnectorError("Generic REST data_contract must be a DataContract object or dict.")

    def _store_payload_artifact(
        self,
        payload: dict[str, Any],
        *,
        object_type: str,
    ) -> dict[str, Any]:
        redacted_payload = _redact_payload(payload)
        serialized = json.dumps(redacted_payload, sort_keys=True, default=str).encode()
        digest = hashlib.sha256(serialized).hexdigest()
        artifact_id = f"artifact-{digest[:16]}"
        artifact_dir = Path(
            self.config.config.get("artifact_dir") or ".molecule-ranker/integration-artifacts"
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{artifact_id}.json"
        path.write_bytes(serialized)
        return {
            "artifact_id": artifact_id,
            "sha256": digest,
            "path": str(path),
            "size_bytes": len(serialized),
            "object_type": object_type,
        }

    def _endpoint(self, name: str, *, required: bool) -> str:
        endpoints = dict(self.config.config.get("endpoints") or {})
        value = endpoints.get(name) or self.config.config.get(f"{name}_endpoint")
        if required and not value:
            raise ConnectorError(f"Generic REST endpoint {name!r} is not configured.")
        return str(value or "")

    def _response_path(self, name: str, *, default: str) -> str:
        response_paths = dict(self.config.config.get("response_paths") or {})
        return str(
            response_paths.get(name)
            or self.config.config.get(f"{name}_json_path")
            or default
        )

    def _next_page_value(
        self,
        response: Any,
        pagination: dict[str, Any],
        *,
        current_params: dict[str, Any],
    ) -> dict[str, Any] | None:
        strategy = str(pagination.get("strategy") or "none")
        if strategy == "none":
            return None
        if strategy == "cursor":
            path = str(pagination.get("next_cursor_path") or "next_cursor")
            cursor = _json_path(response, path)
            if not cursor:
                return None
            return {str(pagination.get("cursor_param") or "cursor"): cursor}
        if strategy == "page":
            page_param = str(pagination.get("page_param") or "page")
            current_page = int(current_params.get(page_param) or pagination.get("start_page") or 1)
            total_pages = _json_path(
                response,
                str(pagination.get("total_pages_path") or "total_pages"),
            )
            if total_pages is not None and current_page >= int(total_pages):
                return None
            return {page_param: current_page + 1}
        raise ConnectorError(f"Unsupported Generic REST pagination strategy: {strategy}")

    def _base_url(self, *, required: bool) -> str:
        value = self.config.base_url or str(self.config.config.get("base_url") or "")
        if required and not value:
            raise ConnectorError("Generic REST base_url is not configured.")
        return value

    def _url(self, endpoint: str) -> str:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        return f"{self._base_url(required=True).rstrip('/')}/{endpoint.lstrip('/')}"

    def _health(self, status: str, message: str) -> IntegrationHealthStatus:
        return IntegrationHealthStatus(
            connector_id=self.config.connector_id,
            provider=self.config.provider,
            status=status,  # type: ignore[arg-type]
            message=message,
            capabilities=list(self.capabilities),
            limitations=list(self.limitations),
        )


def _json_path(payload: Any, path: str) -> Any:
    if path in {"", "."}:
        return payload
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _first_path(payload: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        value = _json_path(payload, path)
        if value not in (None, "", []):
            return value
    return None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if any(marker in key.lower() for marker in SECRET_HEADER_MARKERS):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = redact_secret_values(value)
    return redacted


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(marker in str(key).lower() for marker in SECRET_HEADER_MARKERS)
            else _redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return redact_secret_values(value)
    return value


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _redact_payload(payload)


def _string_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
