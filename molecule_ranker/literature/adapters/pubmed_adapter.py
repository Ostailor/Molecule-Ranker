from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any
from xml.etree import ElementTree

import requests

from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.literature.errors import LiteratureParsingError
from molecule_ranker.literature.schemas import LiteraturePaper, LiteratureQuery
from molecule_ranker.utils.http_cache import CachedHttpResponse, HttpResponseCache
from molecule_ranker.utils.retry import RetryMetadata, RetryPolicy, request_with_retries

__all__ = ["PubMedAdapter"]


@dataclass(frozen=True)
class _TextResponse:
    text: str
    url: str
    params: dict[str, Any]
    cache_key: str | None
    response_metadata: dict[str, Any]
    from_cache: bool = False


class PubMedAdapter:
    """NCBI E-utilities PubMed adapter for the literature module."""

    source_name = "PubMed"
    default_base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(
        self,
        *,
        base_url: str = default_base_url,
        tool: str = "molecule-ranker",
        email: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
        session: requests.Session | None = None,
        cache: HttpResponseCache | None = None,
        use_cache: bool = False,
        cache_ttl_seconds: int = 24 * 60 * 60,
        request_rate_per_second: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.tool = tool
        self.email = email or os.getenv("NCBI_EMAIL")
        self.api_key = api_key or os.getenv("NCBI_API_KEY")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.session = session or requests.Session()
        self.cache = cache
        self.use_cache = use_cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.request_rate_per_second = (
            request_rate_per_second
            if request_rate_per_second is not None
            else (10.0 if self.api_key else 3.0)
        )
        self._last_request_at: float | None = None

    def search(self, query: LiteratureQuery) -> list[LiteraturePaper]:
        pmids = self._search_pmids(query)
        if not pmids:
            return []
        return self._fetch_papers(pmids, query=query)

    def health_check(self, timeout_seconds: float | None = None) -> AdapterHealthStatus:
        checked_at = datetime.now(UTC)
        started = perf_counter()
        endpoint = f"{self.base_url}/esearch.fcgi"
        try:
            payload = self._get_json(
                "esearch.fcgi",
                {
                    "db": "pubmed",
                    "term": "rasagiline Parkinson disease",
                    "retmax": 1,
                    "retmode": "json",
                    **self._common_params(),
                },
                timeout_seconds=timeout_seconds,
            )
            if not isinstance(payload.get("esearchresult"), dict):
                raise LiteratureParsingError("PubMed health response lacked esearchresult.")
        except Exception as exc:  # pragma: no cover - source-dependent
            return AdapterHealthStatus(
                source_name=self.source_name,
                ok=False,
                endpoint=endpoint,
                checked_at=checked_at,
                latency_ms=self._elapsed_ms(started),
                error=str(exc),
                metadata={"probe": "pubmed_esearch"},
            )
        return AdapterHealthStatus(
            source_name=self.source_name,
            ok=True,
            endpoint=endpoint,
            checked_at=checked_at,
            latency_ms=self._elapsed_ms(started),
            error=None,
            metadata={"probe": "pubmed_esearch"},
        )

    def _search_pmids(self, query: LiteratureQuery) -> list[str]:
        payload = self._get_json(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": query.query_text,
                "retmax": query.max_results,
                "retmode": "json",
                "sort": "relevance",
                **self._common_params(),
            },
        )
        ids = payload.get("esearchresult", {}).get("idlist", [])
        if not isinstance(ids, list):
            raise LiteratureParsingError("PubMed esearch returned an invalid idlist.")
        return [str(pmid) for pmid in ids[: query.max_results] if pmid]

    def _fetch_papers(
        self,
        pmids: list[str],
        *,
        query: LiteratureQuery,
    ) -> list[LiteraturePaper]:
        response = self._get_text_response(
            "efetch.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
                "rettype": "abstract",
                **self._common_params(),
            },
        )
        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise LiteratureParsingError("PubMed efetch returned invalid XML.") from exc
        papers = [
            self._paper_from_article(article, query=query)
            for article in root.findall(".//PubmedArticle")
        ]
        if not response.from_cache:
            self._write_cache(
                response.cache_key,
                response.url,
                {"text": response.text},
                request_metadata={"query_params": response.params},
                response_metadata=response.response_metadata,
            )
        return papers

    def _paper_from_article(
        self,
        article: ElementTree.Element,
        *,
        query: LiteratureQuery,
    ) -> LiteraturePaper:
        pmid = self._text(article, ".//MedlineCitation/PMID")
        title = self._text(article, ".//ArticleTitle") or "Untitled PubMed record"
        abstract = self._abstract(article)
        publication_types = self._publication_types(article)
        publication_date = self._publication_date(article)
        year = publication_date.year if publication_date else None
        doi = self._article_id(article, "doi") or self._elocation_id(article, "doi")
        pmcid = self._article_id(article, "pmc")
        paper_id = f"pubmed:{pmid}" if pmid else f"pubmed:{title}"
        return LiteraturePaper(
            paper_id=paper_id,
            source=self.source_name,
            title=title,
            abstract=abstract,
            authors=self._authors(article),
            journal=self._text(article, ".//Journal/Title"),
            publication_date=publication_date.isoformat() if publication_date else None,
            year=year,
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            openalex_id=None,
            publication_type=publication_types[0] if publication_types else None,
            is_review=self._contains_publication_type(publication_types, "review"),
            is_clinical=self._is_clinical(title, abstract, publication_types),
            is_preclinical=self._is_preclinical(title, abstract, publication_types),
            is_retracted=self._is_retracted(title, abstract, publication_types),
            cited_by_count=None,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
            retrieved_at=datetime.now(UTC),
            metadata={
                "query_id": query.query_id,
                "query_text": query.query_text,
                "query_type": query.query_type,
                "publication_types": publication_types,
                "raw_source": self._raw_metadata(article),
            },
        )

    def _get_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        url = self._url(path)
        cache_key = self._cache_key(url, params)
        cached = self._cached_response(cache_key)
        if cached is not None:
            return cached.response_json
        response, retry_metadata = self._request(
            url,
            params=params,
            timeout_seconds=timeout_seconds,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LiteratureParsingError("PubMed returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise LiteratureParsingError("PubMed returned a non-object JSON payload.")
        self._write_cache(
            cache_key,
            url,
            payload,
            request_metadata={"query_params": params},
            response_metadata=self._response_metadata(url, response, retry_metadata),
        )
        return payload

    def _get_text_response(self, path: str, params: dict[str, Any]) -> _TextResponse:
        url = self._url(path)
        cache_key = self._cache_key(url, params)
        cached = self._cached_response(cache_key)
        if cached is not None:
            return _TextResponse(
                text=str(cached.response_json.get("text", "")),
                url=url,
                params=params,
                cache_key=cache_key,
                response_metadata=cached.provenance_metadata(),
                from_cache=True,
            )
        response, retry_metadata = self._request(url, params=params)
        return _TextResponse(
            text=response.text,
            url=url,
            params=params,
            cache_key=cache_key,
            response_metadata=self._response_metadata(url, response, retry_metadata),
        )

    def _request(
        self,
        url: str,
        *,
        params: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> tuple[requests.Response, RetryMetadata]:
        try:
            self._respect_rate_limit()
            return request_with_retries(
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
        except requests.RequestException as exc:
            raise ExternalDataUnavailableError(f"PubMed request failed: {exc}") from exc

    def _common_params(self) -> dict[str, str]:
        params = {"tool": self.tool}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _respect_rate_limit(self) -> None:
        if self.request_rate_per_second is None or self.request_rate_per_second <= 0:
            return
        now = time.monotonic()
        min_interval = 1.0 / self.request_rate_per_second
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
        self._last_request_at = time.monotonic()

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

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

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

    def _abstract(self, article: ElementTree.Element) -> str | None:
        parts = [
            self._clean_text(node)
            for node in article.findall(".//Abstract/AbstractText")
            if self._clean_text(node)
        ]
        return " ".join(parts) if parts else None

    def _publication_types(self, article: ElementTree.Element) -> list[str]:
        return [
            self._clean_text(node)
            for node in article.findall(".//PublicationTypeList/PublicationType")
            if self._clean_text(node)
        ]

    def _publication_date(self, article: ElementTree.Element) -> date | None:
        pub_date = article.find(".//JournalIssue/PubDate")
        if pub_date is None:
            pub_date = article.find(".//ArticleDate")
        if pub_date is None:
            return None
        year = self._text(pub_date, "Year")
        month = self._month_number(self._text(pub_date, "Month")) or 1
        day = self._text(pub_date, "Day") or "1"
        if year is None:
            return None
        try:
            return date(int(year), month, int(day))
        except ValueError:
            return None

    def _authors(self, article: ElementTree.Element) -> list[str]:
        authors: list[str] = []
        for author in article.findall(".//AuthorList/Author"):
            collective = self._text(author, "CollectiveName")
            last_name = self._text(author, "LastName")
            initials = self._text(author, "Initials")
            if collective:
                authors.append(collective)
            elif last_name:
                authors.append(f"{last_name} {initials}".strip())
        return authors

    def _article_id(self, article: ElementTree.Element, id_type: str) -> str | None:
        for node in article.findall(".//ArticleId"):
            if str(node.attrib.get("IdType", "")).lower() == id_type.lower():
                return self._clean_text(node) or None
        return None

    def _elocation_id(self, article: ElementTree.Element, id_type: str) -> str | None:
        for node in article.findall(".//ELocationID"):
            if str(node.attrib.get("EIdType", "")).lower() == id_type.lower():
                return self._clean_text(node) or None
        return None

    def _raw_metadata(self, article: ElementTree.Element) -> dict[str, Any]:
        return {
            "pmid": self._text(article, ".//MedlineCitation/PMID"),
            "article_ids": [
                {
                    "id_type": node.attrib.get("IdType"),
                    "value": self._clean_text(node),
                }
                for node in article.findall(".//ArticleId")
                if self._clean_text(node)
            ],
            "publication_status": self._text(article, ".//MedlineCitation/MedlineJournalInfo"),
        }

    def _contains_publication_type(self, publication_types: list[str], term: str) -> bool:
        return any(
            term.lower() in publication_type.lower()
            for publication_type in publication_types
        )

    def _is_clinical(
        self,
        title: str,
        abstract: str | None,
        publication_types: list[str],
    ) -> bool:
        text = " ".join([title, abstract or "", *publication_types]).lower()
        return "clinical trial" in text or "randomized" in text or "phase " in text

    def _is_preclinical(
        self,
        title: str,
        abstract: str | None,
        publication_types: list[str],
    ) -> bool:
        text = " ".join([title, abstract or "", *publication_types]).lower()
        return any(
            term in text
            for term in ("preclinical", "mouse", "mice", "cell", "in vitro", "model")
        )

    def _is_retracted(
        self,
        title: str,
        abstract: str | None,
        publication_types: list[str],
    ) -> bool:
        text = " ".join([title, abstract or "", *publication_types]).lower()
        return "retracted publication" in text or "retraction of publication" in text

    def _text(self, node: ElementTree.Element, path: str) -> str | None:
        child = node.find(path)
        return self._clean_text(child) if child is not None else None

    def _clean_text(self, node: ElementTree.Element | None) -> str:
        if node is None:
            return ""
        return " ".join("".join(node.itertext()).split())

    def _month_number(self, value: str | None) -> int | None:
        if not value:
            return None
        if value.isdigit():
            return int(value)
        months = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        return months.get(value[:3].lower())

    def _elapsed_ms(self, started: float) -> float:
        return round((perf_counter() - started) * 1000.0, 3)
