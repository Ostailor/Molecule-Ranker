from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import requests

from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.schemas import LiteraturePaper
from molecule_ranker.utils.http_cache import CachedHttpResponse, HttpResponseCache
from molecule_ranker.utils.retry import RetryMetadata, RetryPolicy, request_with_retries


class OpenAlexAdapter:
    """Optional OpenAlex adapter for public citation, open-access, and retraction metadata."""

    source_name = "OpenAlex"
    default_base_url = "https://api.openalex.org"

    def __init__(
        self,
        *,
        base_url: str = default_base_url,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
        session: requests.Session | None = None,
        cache: HttpResponseCache | None = None,
        use_cache: bool = False,
        cache_ttl_seconds: int = 24 * 60 * 60,
        mailto: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.session = session or requests.Session()
        self.cache = cache
        self.use_cache = use_cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.mailto = mailto
        self._last_response_provenance: dict[str, Any] = {"mode": "live"}

    def enrich_papers(self, papers: list[LiteraturePaper]) -> list[LiteraturePaper]:
        enriched: list[LiteraturePaper] = []
        for paper in papers:
            try:
                enriched.append(self._enrich_paper(paper))
            except ExternalDataUnavailableError:
                enriched.append(paper)
        return enriched

    def health_check(self, *, timeout_seconds: float = 5.0) -> AdapterHealthStatus:
        checked_at = datetime.now(UTC)
        endpoint = f"{self.base_url}/works"
        started = perf_counter()
        try:
            response = self.session.get(
                endpoint,
                params={"search": "rasagiline", "per-page": 1, **self._mailto_param()},
                timeout=min(timeout_seconds, self.timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("OpenAlex returned an unexpected health payload.")
        except Exception as exc:  # pragma: no cover - exact failures are source-dependent
            return AdapterHealthStatus(
                source_name=self.source_name,
                ok=False,
                endpoint=endpoint,
                checked_at=checked_at,
                latency_ms=self._elapsed_ms(started),
                error=str(exc),
                metadata={"probe": "works_search"},
            )
        return AdapterHealthStatus(
            source_name=self.source_name,
            ok=True,
            endpoint=endpoint,
            checked_at=checked_at,
            latency_ms=self._elapsed_ms(started),
            error=None,
            metadata={"probe": "works_search"},
        )

    def _enrich_paper(self, paper: LiteraturePaper) -> LiteraturePaper:
        filters = []
        if paper.doi:
            filters.append(f"doi:{paper.doi}")
        if paper.pmid:
            filters.append(f"pmid:https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}")
        if not filters:
            return paper
        payload = self._get(
            "works",
            {"filter": ",".join(filters), "per-page": 1, **self._mailto_param()},
        )
        results = payload.get("results", [])
        if not results:
            return paper
        work = results[0]
        metadata = {
            **paper.metadata,
            "openalex_id": work.get("id"),
            "openalex_response_provenance": dict(self._last_response_provenance),
        }
        return paper.model_copy(
            update={
                "citation_count": work.get("cited_by_count", paper.citation_count),
                "is_open_access": (work.get("open_access") or {}).get("is_oa"),
                "is_retracted": bool(work.get("is_retracted", paper.is_retracted)),
                "metadata": metadata,
            }
        )

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        cache_key = self._cache_key(url, params)
        retry_metadata = RetryMetadata()
        try:
            response, retry_metadata = request_with_retries(
                lambda: self.session.get(url, params=params, timeout=self.timeout_seconds),
                RetryPolicy(
                    max_retries=self.max_retries,
                    backoff_seconds=self.retry_delay_seconds,
                    jitter_seconds=min(max(self.retry_delay_seconds, 0.0), 0.25),
                ),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise ExternalDataUnavailableError(f"OpenAlex request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise ExternalDataUnavailableError("OpenAlex returned an unexpected payload.")
        response_metadata = self._response_metadata(url, response, retry_metadata)
        self._write_cache(cache_key, url, payload, response_metadata)
        self._last_response_provenance = response_metadata
        return payload

    def _mailto_param(self) -> dict[str, str]:
        return {"mailto": self.mailto} if self.mailto else {}

    def _cache_key(self, url: str, params: dict[str, Any]) -> str | None:
        if self.cache is None:
            return None
        return self.cache.build_key(
            source_name=self.source_name,
            endpoint=url,
            method="GET",
            query_params=params,
        )

    def _cached_response(self, cache_key: str | None) -> CachedHttpResponse | None:
        if not self.use_cache or self.cache is None or cache_key is None:
            return None
        return self.cache.get(cache_key, ttl_seconds=self.cache_ttl_seconds)

    def _write_cache(
        self,
        cache_key: str | None,
        url: str,
        payload: dict[str, Any],
        response_metadata: dict[str, Any],
    ) -> None:
        if self.cache is None or cache_key is None:
            return
        self.cache.write_success(
            cache_key=cache_key,
            response_json=payload,
            source=self.source_name,
            endpoint=url,
            method="GET",
            request_metadata={},
            ttl_seconds=self.cache_ttl_seconds,
            response_metadata=response_metadata,
        )

    def _response_metadata(
        self,
        url: str,
        response: requests.Response,
        retry_metadata: RetryMetadata,
    ) -> dict[str, Any]:
        return {
            "mode": "live",
            "source": self.source_name,
            "endpoint": url,
            "status_code": int(getattr(response, "status_code", 200)),
            "retry_count": retry_metadata.retry_count,
            "rate_limit_retry_count": retry_metadata.rate_limit_retry_count,
            "attempts": retry_metadata.attempts,
            "status_codes": list(retry_metadata.status_codes),
        }

    def _elapsed_ms(self, started: float) -> float:
        return round((perf_counter() - started) * 1000.0, 3)
