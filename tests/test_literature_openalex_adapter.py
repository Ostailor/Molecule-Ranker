from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import requests

from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.literature.adapters.openalex_adapter import OpenAlexAdapter
from molecule_ranker.literature.schemas import LiteraturePaper


class MockResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


class QueueSession:
    def __init__(
        self,
        responses: list[MockResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = responses or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> MockResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        return self.responses.pop(0)


def _paper(**overrides: Any) -> LiteraturePaper:
    payload: dict[str, Any] = {
        "paper_id": "pubmed:12345",
        "source": "PubMed",
        "title": "PubMed title",
        "abstract": "PubMed abstract.",
        "authors": ["Example A"],
        "journal": "PubMed Journal",
        "publication_date": None,
        "year": None,
        "doi": "10.1000/example",
        "pmid": "12345",
        "pmcid": None,
        "openalex_id": None,
        "publication_type": "Journal Article",
        "is_review": False,
        "is_clinical": False,
        "is_preclinical": False,
        "is_retracted": None,
        "cited_by_count": None,
        "url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
        "retrieved_at": datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        "metadata": {"pubmed": {"pmid": "12345"}},
    }
    payload.update(overrides)
    return LiteraturePaper(**payload)


OPENALEX_WORK = {
    "id": "https://openalex.org/W123",
    "doi": "https://doi.org/10.1000/example",
    "pmid": "https://pubmed.ncbi.nlm.nih.gov/12345",
    "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12345",
    "display_name": "OpenAlex title should not replace PubMed title",
    "publication_date": "2021-03-04",
    "publication_year": 2021,
    "cited_by_count": 42,
    "is_retracted": True,
    "open_access": {"is_oa": True, "oa_status": "gold", "oa_url": "https://oa.example"},
    "primary_location": {"landing_page_url": "https://doi.org/10.1000/example"},
    "concepts": [{"display_name": "Neuroscience", "score": 0.91}],
    "topics": [{"display_name": "Parkinson Disease", "score": 0.8}],
}


def test_openalex_adapter_enriches_by_doi_and_preserves_pubmed_metadata() -> None:
    session = QueueSession([MockResponse({"results": [OPENALEX_WORK]})])

    enriched = OpenAlexAdapter(session=session).enrich([_paper()])  # type: ignore[arg-type]

    paper = enriched[0]
    assert paper.openalex_id == "W123"
    assert paper.cited_by_count == 42
    assert paper.is_retracted is True
    assert paper.publication_date == "2021-03-04"
    assert paper.year == 2021
    assert paper.title == "PubMed title"
    assert paper.abstract == "PubMed abstract."
    assert paper.url == "https://pubmed.ncbi.nlm.nih.gov/12345/"
    assert paper.metadata["open_access"]["is_oa"] is True
    assert paper.metadata["landing_page_url"] == "https://doi.org/10.1000/example"
    assert paper.metadata["concepts"][0]["display_name"] == "Neuroscience"
    assert paper.metadata["topics"][0]["display_name"] == "Parkinson Disease"
    assert paper.metadata["pubmed"]["pmid"] == "12345"
    assert session.calls[0]["params"]["filter"] == "doi:10.1000/example"


def test_openalex_adapter_enriches_by_pmid_when_doi_absent() -> None:
    session = QueueSession([MockResponse({"results": [OPENALEX_WORK]})])

    enriched = OpenAlexAdapter(session=session).enrich([_paper(doi=None)])  # type: ignore[arg-type]

    assert enriched[0].openalex_id == "W123"
    assert session.calls[0]["params"]["filter"] == "pmid:12345"


def test_openalex_adapter_optional_failure_preserves_paper_and_adds_warning() -> None:
    paper = _paper()
    session = QueueSession(error=requests.Timeout("timeout"))

    enriched = OpenAlexAdapter(session=session, required=False).enrich([paper])  # type: ignore[arg-type]

    assert enriched[0].model_dump(exclude={"metadata"}) == paper.model_dump(
        exclude={"metadata"}
    )
    assert "warnings" in enriched[0].metadata
    assert "OpenAlex enrichment failed" in enriched[0].metadata["warnings"][0]


def test_openalex_adapter_required_failure_raises() -> None:
    session = QueueSession(error=requests.Timeout("timeout"))

    with pytest.raises(ExternalDataUnavailableError):
        OpenAlexAdapter(session=session, required=True).enrich([_paper()])  # type: ignore[arg-type]
