from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import requests

from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.literature.schemas import LiteraturePaper
from molecule_ranker.utils.http_cache import CachedHttpResponse, HttpResponseCache
from molecule_ranker.utils.retry import RetryMetadata, RetryPolicy, request_with_retries

__all__ = ["OpenAlexAdapter"]


class OpenAlexAdapter:
    """Optional OpenAlex enrichment adapter for citation/OA/retraction metadata."""

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
        required: bool = False,
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
        self.required = required

    def enrich(self, papers: list[LiteraturePaper]) -> list[LiteraturePaper]:
        enriched: list[LiteraturePaper] = []
        for paper in papers:
            try:
                enriched.append(self._enrich_one(paper))
            except ExternalDataUnavailableError:
                if self.required:
                    raise
                enriched.append(
                    self._with_warning(
                        paper,
                        "OpenAlex enrichment failed; preserving original PubMed paper.",
                    )
                )
        return enriched

    def health_check(self, timeout_seconds: float | None = None) -> AdapterHealthStatus:
        checked_at = datetime.now(UTC)
        started = perf_counter()
        endpoint = f"{self.base_url}/works"
        try:
            payload = self._get(
                "works",
                {"search": "rasagiline", "per-page": 1, **self._mailto_param()},
                timeout_seconds=timeout_seconds,
            )
            if not isinstance(payload.get("results"), list):
                raise ExternalDataUnavailableError("OpenAlex health response lacked results.")
        except Exception as exc:  # pragma: no cover - source-dependent
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

    def _enrich_one(self, paper: LiteraturePaper) -> LiteraturePaper:
        lookup_filter = self._lookup_filter(paper)
        if lookup_filter is None:
            return self._with_warning(
                paper,
                "OpenAlex enrichment skipped; DOI, PMID, and PMCID are unavailable.",
            )
        payload = self._get(
            "works",
            {"filter": lookup_filter, "per-page": 1, **self._mailto_param()},
        )
        results = payload.get("results", [])
        if not isinstance(results, list) or not results:
            return self._with_warning(
                paper,
                f"OpenAlex enrichment found no work for {lookup_filter}.",
            )
        work = results[0]
        if not isinstance(work, dict):
            return self._with_warning(paper, "OpenAlex enrichment returned an invalid work.")
        return self._merge_work(paper, work)

    def _merge_work(self, paper: LiteraturePaper, work: dict[str, Any]) -> LiteraturePaper:
        metadata = dict(paper.metadata)
        metadata["openalex"] = work
        metadata["open_access"] = dict(work.get("open_access") or {})
        metadata["landing_page_url"] = self._landing_page_url(work)
        metadata["concepts"] = list(work.get("concepts") or [])
        metadata["topics"] = list(work.get("topics") or [])

        publication_date = paper.publication_date or self._string_or_none(
            work.get("publication_date")
        )
        year = paper.year or self._int_or_none(work.get("publication_year"))
        return paper.model_copy(
            update={
                "openalex_id": self._normalize_openalex_id(work.get("id"))
                or paper.openalex_id,
                "cited_by_count": self._int_or_none(work.get("cited_by_count"))
                if work.get("cited_by_count") is not None
                else paper.cited_by_count,
                "is_retracted": bool(work.get("is_retracted"))
                if work.get("is_retracted") is not None
                else paper.is_retracted,
                "publication_date": publication_date,
                "year": year,
                "metadata": metadata,
            }
        )

    def _lookup_filter(self, paper: LiteraturePaper) -> str | None:
        if paper.doi:
            return f"doi:{self._normalize_doi(paper.doi)}"
        if paper.pmid:
            return f"pmid:{paper.pmid}"
        if paper.pmcid:
            return f"pmcid:{paper.pmcid}"
        return None

    def _get(
        self,
        path: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        cache_key = self._cache_key(url, params)
        cached = self._cached_response(cache_key)
        if cached is not None:
            return cached.response_json
        retry_metadata = RetryMetadata()
        try:
            response, retry_metadata = request_with_retries(
                lambda: self.session.get(
                    url,
                    params=params,
                    timeout=timeout_seconds or self.timeout_seconds,
                ),
                RetryPolicy(
                    max_retries=self.max_retries,
                    backoff_seconds=self.retry_delay_seconds,
                    jitter_seconds=min(max(self.retry_delay_seconds, 0.0), 0.25),
                ),
            )
            payload = response.json()
        except requests.RequestException as exc:
            raise ExternalDataUnavailableError(f"OpenAlex request failed: {exc}") from exc
        except ValueError as exc:
            raise ExternalDataUnavailableError("OpenAlex returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ExternalDataUnavailableError("OpenAlex returned a non-object JSON payload.")
        self._write_cache(
            cache_key,
            url,
            payload,
            request_metadata={"query_params": params},
            response_metadata=self._response_metadata(url, response, retry_metadata),
        )
        return payload

    def _with_warning(self, paper: LiteraturePaper, warning: str) -> LiteraturePaper:
        metadata = dict(paper.metadata)
        metadata["warnings"] = [*list(metadata.get("warnings", [])), warning]
        return paper.model_copy(update={"metadata": metadata})

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
        *,
        request_metadata: dict[str, Any],
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
            request_metadata=request_metadata,
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

    def _landing_page_url(self, work: dict[str, Any]) -> str | None:
        primary_location = work.get("primary_location")
        if isinstance(primary_location, dict):
            value = primary_location.get("landing_page_url")
            if isinstance(value, str) and value:
                return value
        return None

    def _normalize_openalex_id(self, value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        return value.rstrip("/").rsplit("/", maxsplit=1)[-1]

    def _normalize_doi(self, value: str) -> str:
        normalized = value.strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if normalized.lower().startswith(prefix):
                return normalized[len(prefix) :]
        return normalized

    def _string_or_none(self, value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    def _int_or_none(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _elapsed_ms(self, started: float) -> float:
        return round((perf_counter() - started) * 1000.0, 3)
