from __future__ import annotations

from typing import Any

import pytest
import requests

from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.data_sources.pubmed_adapter import PubMedAdapter
from molecule_ranker.schemas import LiteratureQuery


class MockResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        text: str | None = None,
        status_code: int = 200,
    ) -> None:
        self.payload = payload or {}
        self.text = text or ""
        self.content = self.text.encode()
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


class QueueSession:
    def __init__(self, responses: list[MockResponse] | None = None, error: Exception | None = None):
        self.responses = responses or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> MockResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        return self.responses.pop(0)


PUBMED_XML = """\
<?xml version="1.0" ?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <Journal>
          <Title>Example Journal</Title>
          <JournalIssue>
            <PubDate><Year>2020</Year><Month>Jan</Month><Day>02</Day></PubDate>
          </JournalIssue>
        </Journal>
        <ArticleTitle>Rasagiline and monoamine oxidase B in Parkinson disease</ArticleTitle>
        <Abstract>
          <AbstractText>This review mentions rasagiline, MAOB, and Parkinson disease.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author><LastName>Example</LastName><Initials>A</Initials></Author>
        </AuthorList>
        <PublicationTypeList><PublicationType>Review</PublicationType></PublicationTypeList>
        <ELocationID EIdType="doi">10.1000/example</ELocationID>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_pubmed_adapter_searches_eutilities_and_parses_papers() -> None:
    session = QueueSession(
        [
            MockResponse({"esearchresult": {"idlist": ["12345"]}}),
            MockResponse(text=PUBMED_XML),
        ]
    )
    adapter = PubMedAdapter(session=session)  # type: ignore[arg-type]

    papers = adapter.retrieve_papers(
        LiteratureQuery(
            disease="Parkinson disease",
            molecule="rasagiline",
            target="MAOB",
            query_text="rasagiline AND (Parkinson disease OR MAOB)",
            max_results=3,
        )
    )

    assert len(papers) == 1
    paper = papers[0]
    assert paper.source == "PubMed"
    assert paper.source_record_id == "12345"
    assert paper.pmid == "12345"
    assert paper.doi == "10.1000/example"
    assert paper.title == "Rasagiline and monoamine oxidase B in Parkinson disease"
    assert paper.abstract == "This review mentions rasagiline, MAOB, and Parkinson disease."
    assert paper.journal == "Example Journal"
    assert paper.publication_types == ["Review"]
    assert paper.authors == ["Example A"]
    assert paper.url == "https://pubmed.ncbi.nlm.nih.gov/12345/"
    assert session.calls[0]["params"]["db"] == "pubmed"
    assert session.calls[0]["params"]["term"] == "rasagiline AND (Parkinson disease OR MAOB)"
    assert session.calls[0]["params"]["retmax"] == 3
    assert session.calls[1]["params"]["id"] == "12345"


def test_pubmed_adapter_returns_empty_when_search_has_no_ids() -> None:
    session = QueueSession([MockResponse({"esearchresult": {"idlist": []}})])
    adapter = PubMedAdapter(session=session)  # type: ignore[arg-type]

    papers = adapter.retrieve_papers(
        LiteratureQuery(disease="Unknown", query_text="unlikely query", max_results=5)
    )

    assert papers == []
    assert len(session.calls) == 1


def test_pubmed_adapter_raises_clear_source_error_on_network_failure() -> None:
    adapter = PubMedAdapter(session=QueueSession(error=requests.Timeout("timeout")))  # type: ignore[arg-type]

    with pytest.raises(ExternalDataUnavailableError, match="PubMed request failed"):
        adapter.retrieve_papers(
            LiteratureQuery(disease="Parkinson disease", query_text="rasagiline", max_results=1)
        )
