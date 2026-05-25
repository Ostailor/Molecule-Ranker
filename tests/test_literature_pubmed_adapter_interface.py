from __future__ import annotations

from typing import Any

import requests

from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.literature.adapters.pubmed_adapter import PubMedAdapter
from molecule_ranker.literature.errors import LiteratureParsingError
from molecule_ranker.literature.schemas import LiteratureQuery
from molecule_ranker.utils.http_cache import HttpResponseCache


class MockResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        text: str | None = None,
        status_code: int = 200,
        json_error: Exception | None = None,
    ) -> None:
        self.payload = payload or {}
        self.text = text or ""
        self.content = self.text.encode()
        self.status_code = status_code
        self.json_error = json_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        if self.json_error:
            raise self.json_error
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


PUBMED_XML = """\
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <Journal>
          <Title>Example Journal</Title>
          <JournalIssue>
            <PubDate><Year>2021</Year><Month>Mar</Month><Day>04</Day></PubDate>
          </JournalIssue>
        </Journal>
        <ArticleTitle>Rasagiline and monoamine oxidase B in Parkinson disease</ArticleTitle>
        <Abstract>
          <AbstractText>Source abstract mentions rasagiline and MAOB.</AbstractText>
        </Abstract>
        <AuthorList><Author><LastName>Example</LastName><Initials>A</Initials></Author></AuthorList>
        <PublicationTypeList><PublicationType>Clinical Trial</PublicationType></PublicationTypeList>
        <ELocationID EIdType="doi">10.1000/example</ELocationID>
        <ArticleIdList><ArticleId IdType="pmc">PMC12345</ArticleId></ArticleIdList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def _query() -> LiteratureQuery:
    return LiteratureQuery(
        query_id="q1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_identifiers={"chembl": "CHEMBL887"},
        query_text="Rasagiline AND Parkinson disease AND MAOB",
        query_type="molecule_target",
        max_results=2,
        metadata={},
    )


def test_literature_pubmed_adapter_search_returns_new_literature_schema() -> None:
    session = QueueSession(
        [
            MockResponse({"esearchresult": {"idlist": ["12345"]}}),
            MockResponse(text=PUBMED_XML),
        ]
    )

    papers = PubMedAdapter(
        session=session,  # type: ignore[arg-type]
        email="maintainer@example.org",
        api_key="test-api-key",
        request_rate_per_second=0,
    ).search(_query())

    assert len(papers) == 1
    paper = papers[0]
    assert paper.paper_id == "pubmed:12345"
    assert paper.source == "PubMed"
    assert paper.pmid == "12345"
    assert paper.pmcid == "PMC12345"
    assert paper.doi == "10.1000/example"
    assert paper.publication_date == "2021-03-04"
    assert paper.year == 2021
    assert paper.publication_type == "Clinical Trial"
    assert paper.is_clinical is True
    assert paper.abstract == "Source abstract mentions rasagiline and MAOB."
    assert paper.retrieved_at.tzinfo is not None
    assert session.calls[0]["params"]["term"] == _query().query_text
    assert session.calls[0]["params"]["tool"] == "molecule-ranker"
    assert session.calls[0]["params"]["email"] == "maintainer@example.org"
    assert session.calls[0]["params"]["api_key"] == "test-api-key"
    assert session.calls[1]["params"]["id"] == "12345"


def test_literature_pubmed_adapter_returns_empty_list_when_esearch_has_no_pmids() -> None:
    session = QueueSession([MockResponse({"esearchresult": {"idlist": []}})])

    papers = PubMedAdapter(session=session, request_rate_per_second=0).search(_query())  # type: ignore[arg-type]

    assert papers == []
    assert len(session.calls) == 1


def test_literature_pubmed_adapter_raises_external_data_unavailable_on_api_failure() -> None:
    session = QueueSession(error=requests.Timeout("timeout"))

    try:
        PubMedAdapter(session=session, request_rate_per_second=0).search(_query())  # type: ignore[arg-type]
    except ExternalDataUnavailableError as exc:
        assert "PubMed request failed" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("Expected ExternalDataUnavailableError")


def test_literature_pubmed_adapter_raises_literature_parsing_error_for_bad_xml() -> None:
    session = QueueSession(
        [
            MockResponse({"esearchresult": {"idlist": ["12345"]}}),
            MockResponse(text="<not valid"),
        ]
    )

    try:
        PubMedAdapter(session=session, request_rate_per_second=0).search(_query())  # type: ignore[arg-type]
    except LiteratureParsingError as exc:
        assert "invalid XML" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("Expected LiteratureParsingError")


def test_literature_pubmed_adapter_raises_literature_parsing_error_for_bad_json() -> None:
    session = QueueSession([MockResponse(json_error=ValueError("bad json"))])

    try:
        PubMedAdapter(session=session, request_rate_per_second=0).search(_query())  # type: ignore[arg-type]
    except LiteratureParsingError as exc:
        assert "invalid JSON" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("Expected LiteratureParsingError")


def test_literature_pubmed_adapter_cache_writes_only_successful_responses(tmp_path) -> None:
    cache = HttpResponseCache(tmp_path)
    success_session = QueueSession(
        [
            MockResponse({"esearchresult": {"idlist": ["12345"]}}),
            MockResponse(text=PUBMED_XML),
        ]
    )
    PubMedAdapter(
        session=success_session,  # type: ignore[arg-type]
        cache=cache,
        request_rate_per_second=0,
    ).search(_query())
    successful_cache_entries = sorted(tmp_path.glob("*.json"))
    assert len(successful_cache_entries) == 2

    failing_session = QueueSession([MockResponse(status_code=500)])
    try:
        PubMedAdapter(
            session=failing_session,  # type: ignore[arg-type]
            cache=cache,
            max_retries=0,
            request_rate_per_second=0,
        ).search(_query())
    except ExternalDataUnavailableError:
        pass
    else:  # pragma: no cover - assertion branch
        raise AssertionError("Expected ExternalDataUnavailableError")

    assert sorted(tmp_path.glob("*.json")) == successful_cache_entries
