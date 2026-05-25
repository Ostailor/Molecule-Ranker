from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class CachedHttpResponse(BaseModel):
    """Previously retrieved successful real API response with request provenance."""

    response_json: dict[str, Any]
    source: str
    endpoint: str
    retrieved_at: datetime
    cache_key: str
    request_metadata: dict[str, Any]
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int | None = None

    @property
    def expired_at(self) -> datetime | None:
        if self.ttl_seconds is None:
            return None
        return self.retrieved_at + timedelta(seconds=self.ttl_seconds)

    def is_valid(self, *, ttl_seconds: int | None = None) -> bool:
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        if ttl is None:
            return True
        if ttl <= 0:
            return False
        return datetime.now(UTC) <= self.retrieved_at + timedelta(seconds=ttl)

    def provenance_metadata(self) -> dict[str, Any]:
        return {
            "mode": "cached-real-data",
            "source": self.source,
            "endpoint": self.endpoint,
            "retrieved_at": self.retrieved_at.isoformat(),
            "cache_key": self.cache_key,
            "ttl_seconds": self.ttl_seconds,
            "request_metadata": self.request_metadata,
            "response_metadata": self.response_metadata,
        }


class HttpResponseCache:
    """Filesystem cache for successful real public API JSON responses."""

    def __init__(self, cache_dir: Path, *, bypass_cache: bool = False) -> None:
        self.cache_dir = cache_dir
        self.bypass_cache = bypass_cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def build_key(
        self,
        *,
        source_name: str,
        endpoint: str,
        method: str,
        query_params: dict[str, Any] | None = None,
        graphql_variables: dict[str, Any] | None = None,
        request_body: Any = None,
    ) -> str:
        normalized_body = self._json_dumps(request_body)
        body_hash = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
        payload = {
            "source_name": source_name,
            "endpoint": endpoint,
            "method": method.upper(),
            "query_params": query_params or {},
            "graphql_variables": graphql_variables or {},
            "request_body_hash": body_hash,
        }
        digest = hashlib.sha256(self._json_dumps(payload).encode("utf-8")).hexdigest()
        safe_source = "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in source_name.lower()
        )
        return f"{safe_source}-{digest}"

    def get(
        self,
        cache_key: str,
        *,
        ttl_seconds: int | None = None,
    ) -> CachedHttpResponse | None:
        if self.bypass_cache:
            return None
        entry = self.get_entry(cache_key)
        if entry is None or not entry.is_valid(ttl_seconds=ttl_seconds):
            return None
        return entry

    def get_entry(self, cache_key: str) -> CachedHttpResponse | None:
        path = self._path(cache_key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        return CachedHttpResponse(**payload)

    def write_success(
        self,
        *,
        cache_key: str,
        response_json: dict[str, Any],
        source: str,
        endpoint: str,
        method: str,
        request_metadata: dict[str, Any],
        ttl_seconds: int | None,
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.bypass_cache:
            return
        entry = CachedHttpResponse(
            response_json=response_json,
            source=source,
            endpoint=endpoint,
            retrieved_at=datetime.now(UTC),
            cache_key=cache_key,
            request_metadata={"method": method.upper(), **request_metadata},
            response_metadata=response_metadata or {},
            ttl_seconds=ttl_seconds,
        )
        self._path(cache_key).write_text(entry.model_dump_json(indent=2) + "\n")

    def _path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.json"

    def _json_dumps(self, value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
