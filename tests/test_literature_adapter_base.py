from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.data_sources.health import AdapterHealthStatus
from molecule_ranker.literature.adapters.base import (
    LiteratureHealthCheckAdapter,
    LiteratureMetadataAdapter,
    LiteratureSearchAdapter,
)
from molecule_ranker.literature.errors import (
    CitationExtractionError,
    LiteratureParsingError,
    LiteratureRetrievalError,
)
from molecule_ranker.literature.schemas import LiteraturePaper, LiteratureQuery


class FakeSearchAdapter:
    source_name = "FakeSearch"

    def search(self, query: LiteratureQuery) -> list[LiteraturePaper]:
        return [
            LiteraturePaper(
                paper_id="fake:1",
                source=self.source_name,
                title=query.query_text,
                abstract=None,
                authors=[],
                journal=None,
                publication_date=None,
                year=None,
                doi=None,
                pmid="1",
                pmcid=None,
                openalex_id=None,
                publication_type=None,
                is_review=False,
                is_clinical=False,
                is_preclinical=False,
                is_retracted=None,
                cited_by_count=None,
                url=None,
                retrieved_at=datetime.now(UTC),
                metadata={},
            )
        ]


class FakeMetadataAdapter:
    source_name = "FakeMetadata"

    def enrich(self, papers: list[LiteraturePaper]) -> list[LiteraturePaper]:
        return [
            paper.model_copy(update={"metadata": {**paper.metadata, "enriched": True}})
            for paper in papers
        ]


class FakeHealthAdapter:
    def health_check(self, timeout_seconds: float | None = None) -> AdapterHealthStatus:
        return AdapterHealthStatus(
            source_name="FakeHealth",
            ok=True,
            endpoint="https://example.org/health",
            latency_ms=timeout_seconds,
        )


def _query() -> LiteratureQuery:
    return LiteratureQuery(
        query_id="q1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_identifiers={"chembl": "CHEMBL887"},
        query_text="Rasagiline AND MAOB",
        query_type="molecule_target",
        max_results=5,
        metadata={},
    )


def test_literature_adapter_protocols_accept_requested_methods() -> None:
    search_adapter: LiteratureSearchAdapter = FakeSearchAdapter()
    metadata_adapter: LiteratureMetadataAdapter = FakeMetadataAdapter()
    health_adapter: LiteratureHealthCheckAdapter = FakeHealthAdapter()

    papers = search_adapter.search(_query())
    enriched = metadata_adapter.enrich(papers)
    status = health_adapter.health_check(timeout_seconds=2.0)

    assert papers[0].source == "FakeSearch"
    assert enriched[0].metadata["enriched"] is True
    assert status.ok is True
    assert status.latency_ms == 2.0


@pytest.mark.parametrize(
    "error_type",
    [LiteratureRetrievalError, LiteratureParsingError, CitationExtractionError],
)
def test_literature_errors_are_clear_runtime_errors(error_type: type[RuntimeError]) -> None:
    with pytest.raises(error_type, match="clear failure"):
        raise error_type("clear failure")
