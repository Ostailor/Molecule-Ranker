from __future__ import annotations

import pytest

from molecule_ranker.data_sources.pubmed_adapter import PubMedAdapter
from molecule_ranker.schemas import LiteratureQuery

pytestmark = [pytest.mark.live, pytest.mark.network]


def test_live_pubmed_small_literature_query_returns_real_records() -> None:
    adapter = PubMedAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25)

    papers = adapter.retrieve_papers(
        LiteratureQuery(
            disease="Parkinson disease",
            molecule="rasagiline",
            target="MAOB",
            query_text="rasagiline AND Parkinson disease AND monoamine oxidase B",
            max_results=2,
        )
    )

    if not papers:
        pytest.skip("PubMed returned no records for the live smoke query.")

    assert all(paper.source == "PubMed" for paper in papers)
    assert all(paper.source_record_id for paper in papers)
    assert all(paper.title for paper in papers)
    assert not any(paper.source.lower() == "fixture" for paper in papers)
