from __future__ import annotations

from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any
from xml.etree import ElementTree

import requests

from molecule_ranker.data_sources.errors import EvidenceRetrievalError, ExternalDataUnavailableError
from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.schemas import LiteraturePaper, LiteratureQuery
from molecule_ranker.utils.http_cache import CachedHttpResponse, HttpResponseCache
from molecule_ranker.utils.retry import RetryMetadata, RetryPolicy, request_with_retries


class PubMedAdapter:
    """NCBI E-utilities adapter for PubMed literature retrieval."""

    source_name = "PubMed"
    default_base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

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
        tool: str = "molecule-ranker",
        email: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.session = session or requests.Session()
        self.cache = cache
        self.use_cache = use_cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.tool = tool
        self.email = email
        self._last_response_provenance: dict[str, Any] = {"mode": "live"}
        self.last_trace_metadata: dict[str, Any] = self._empty_trace_metadata()

    def retrieve_papers(self, query: LiteratureQuery) -> list[LiteraturePaper]:
        pmids = self._search(query)
        if not pmids:
            return []
        papers = self._fetch(pmids)
        return [
            paper.model_copy(
                update={"metadata": {**paper.metadata, "query": query.query_text}}
            )
            for paper in papers
        ]

    def health_check(self, *, timeout_seconds: float = 5.0) -> AdapterHealthStatus:
        checked_at = datetime.now(UTC)
        endpoint = f"{self.base_url}/esearch.fcgi"
        started = perf_counter()
        try:
            response = self.session.get(
                endpoint,
                params={
                    "db": "pubmed",
                    "term": "rasagiline Parkinson disease",
                    "retmax": 1,
                    "retmode": "json",
                    "tool": self.tool,
                    **({"email": self.email} if self.email else {}),
                },
                timeout=min(timeout_seconds, self.timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise EvidenceRetrievalError("PubMed returned an unexpected health payload.")
        except Exception as exc:  # pragma: no cover - exact failures are source-dependent
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

    def _search(self, query: LiteratureQuery) -> list[str]:
        payload = self._get_json(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": query.query_text,
                "retmax": query.max_results,
                "retmode": "json",
                "sort": "relevance",
                "tool": self.tool,
                **({"email": self.email} if self.email else {}),
            },
        )
        ids = payload.get("esearchresult", {}).get("idlist", [])
        if not isinstance(ids, list):
            raise EvidenceRetrievalError("PubMed esearch returned an unexpected id list.")
        return [str(pmid) for pmid in ids if pmid]

    def _fetch(self, pmids: list[str]) -> list[LiteraturePaper]:
        xml_text = self._get_text(
            "efetch.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
                "tool": self.tool,
                **({"email": self.email} if self.email else {}),
            },
        )
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as exc:
            raise EvidenceRetrievalError("PubMed efetch returned invalid XML.") from exc
        papers = [self._paper_from_article(article) for article in root.findall(".//PubmedArticle")]
        self._record_page_metadata(records_fetched=len(papers))
        return papers

    def _paper_from_article(self, article: ElementTree.Element) -> LiteraturePaper:
        pmid = self._text(article, ".//MedlineCitation/PMID")
        title = self._text(article, ".//ArticleTitle") or "Untitled PubMed record"
        abstract = self._abstract(article)
        doi = self._doi(article)
        journal = self._text(article, ".//Journal/Title")
        publication_date = self._publication_date(article)
        publication_types = [
            self._clean_text(node)
            for node in article.findall(".//PublicationTypeList/PublicationType")
            if self._clean_text(node)
        ]
        authors = self._authors(article)
        source_record_id = pmid or doi or title
        return LiteraturePaper(
            source=self.source_name,
            source_record_id=source_record_id,
            pmid=pmid,
            doi=doi,
            title=title,
            abstract=abstract,
            journal=journal,
            publication_date=publication_date,
            publication_types=publication_types,
            authors=authors,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
            is_retracted=self._is_retracted(publication_types, title, abstract),
            metadata={"response_provenance": dict(self._last_response_provenance)},
        )

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = self._get(path, params=params, expect_json=True)
        if not isinstance(payload, dict):
            raise EvidenceRetrievalError("PubMed returned an unexpected JSON payload.")
        return payload

    def _get_text(self, path: str, params: dict[str, Any]) -> str:
        payload = self._get(path, params=params, expect_json=False)
        if not isinstance(payload, str):
            raise EvidenceRetrievalError("PubMed returned an unexpected text payload.")
        return payload

    def _get(self, path: str, *, params: dict[str, Any], expect_json: bool) -> dict[str, Any] | str:
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
            payload: dict[str, Any] | str = response.json() if expect_json else response.text
        except requests.RequestException as exc:
            retry_metadata = getattr(exc, "retry_metadata", retry_metadata)
            self._record_retry_metadata(retry_metadata)
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                if expect_json:
                    return cached.response_json
                return str(cached.response_json.get("text", ""))
            raise ExternalDataUnavailableError(f"PubMed request failed: {exc}") from exc
        except ValueError as exc:
            self._record_retry_metadata(retry_metadata)
            cached = self._cached_response(cache_key)
            if cached is not None:
                self._last_response_provenance = cached.provenance_metadata()
                return cached.response_json
            raise EvidenceRetrievalError("PubMed returned invalid JSON.") from exc
        self._record_retry_metadata(retry_metadata)
        response_metadata = self._response_metadata(url, response, retry_metadata)
        cache_payload = payload if isinstance(payload, dict) else {"text": payload}
        self._write_cache(cache_key, url, cache_payload, response_metadata)
        self._last_response_provenance = response_metadata
        return payload

    def _abstract(self, article: ElementTree.Element) -> str | None:
        parts = [
            self._clean_text(node)
            for node in article.findall(".//Abstract/AbstractText")
            if self._clean_text(node)
        ]
        return " ".join(parts) if parts else None

    def _doi(self, article: ElementTree.Element) -> str | None:
        for node in article.findall(".//ELocationID"):
            if str(node.attrib.get("EIdType", "")).lower() == "doi":
                return self._clean_text(node) or None
        for node in article.findall(".//ArticleId"):
            if str(node.attrib.get("IdType", "")).lower() == "doi":
                return self._clean_text(node) or None
        return None

    def _publication_date(self, article: ElementTree.Element) -> date | None:
        pub_date = article.find(".//JournalIssue/PubDate")
        if pub_date is None:
            return None
        year = self._text(pub_date, "Year")
        month = self._month_number(self._text(pub_date, "Month"))
        day = self._text(pub_date, "Day")
        if year is None:
            return None
        try:
            return date(int(year), month or 1, int(day or 1))
        except ValueError:
            return None

    def _authors(self, article: ElementTree.Element) -> list[str]:
        authors: list[str] = []
        for author in article.findall(".//AuthorList/Author"):
            last = self._text(author, "LastName")
            initials = self._text(author, "Initials")
            collective = self._text(author, "CollectiveName")
            if collective:
                authors.append(collective)
            elif last:
                authors.append(f"{last} {initials}".strip())
        return authors

    def _is_retracted(
        self,
        publication_types: list[str],
        title: str,
        abstract: str | None,
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

    def _empty_trace_metadata(self) -> dict[str, Any]:
        return {
            "pages_fetched": 0,
            "records_fetched": 0,
            "records_retained": 0,
            "truncated": False,
            "retry_count": 0,
            "rate_limit_retry_count": 0,
        }

    def _record_retry_metadata(self, metadata: RetryMetadata) -> None:
        self.last_trace_metadata["retry_count"] += metadata.retry_count
        self.last_trace_metadata["rate_limit_retry_count"] += metadata.rate_limit_retry_count

    def _record_page_metadata(self, *, records_fetched: int) -> None:
        self.last_trace_metadata["pages_fetched"] += 1
        self.last_trace_metadata["records_fetched"] += records_fetched
        self.last_trace_metadata["records_retained"] += records_fetched

    def _elapsed_ms(self, started: float) -> float:
        return round((perf_counter() - started) * 1000.0, 3)
