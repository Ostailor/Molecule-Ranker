from __future__ import annotations

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.literature_evidence import LiteratureEvidenceAgent
from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.literature.adapters.openalex_adapter import OpenAlexAdapter
from molecule_ranker.literature.adapters.pubmed_adapter import PubMedAdapter
from molecule_ranker.literature.errors import LiteratureParsingError, LiteratureRetrievalError
from molecule_ranker.literature.schemas import LiteraturePaper, LiteratureQuery
from molecule_ranker.schemas import Disease, EvidenceItem, MoleculeCandidate, Target

pytestmark = [pytest.mark.live, pytest.mark.network]


def test_live_pubmed_health_check() -> None:
    adapter = PubMedAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25)

    status = adapter.health_check(timeout_seconds=10)

    assert status.source_name == "PubMed"
    assert status.ok, f"PubMed health failed: {status.error}"
    assert status.endpoint.startswith("https://")
    assert status.checked_at.tzinfo is not None
    assert status.latency_ms is None or status.latency_ms >= 0


def test_live_pubmed_search_stable_query_returns_structural_papers() -> None:
    papers = _search_stable_pubmed_query(max_results=2)

    if not papers:
        pytest.skip("PubMed returned no papers for stable live literature query.")

    first = papers[0]
    assert first.pmid
    assert first.title
    assert first.source == "PubMed"
    assert first.retrieved_at.tzinfo is not None
    assert not any(paper.source.lower() == "fixture" for paper in papers)


def test_live_openalex_enrichment_for_pubmed_paper_if_available() -> None:
    papers = _search_stable_pubmed_query(max_results=3)
    paper = next((paper for paper in papers if paper.doi or paper.pmid or paper.pmcid), None)
    if paper is None:
        pytest.skip("PubMed returned no DOI/PMID/PMCID-bearing paper for OpenAlex smoke.")

    adapter = OpenAlexAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25)
    health = adapter.health_check(timeout_seconds=10)
    if not health.ok:
        pytest.skip(f"OpenAlex unavailable during optional enrichment smoke: {health.error}")

    try:
        enriched = adapter.enrich([paper])
    except ExternalDataUnavailableError as exc:
        pytest.skip(f"OpenAlex optional enrichment unavailable during smoke: {exc}")

    assert len(enriched) == 1
    assert isinstance(enriched[0], LiteraturePaper)
    assert enriched[0].paper_id == paper.paper_id
    assert enriched[0].title
    assert enriched[0].pmid == paper.pmid


def test_live_literature_evidence_agent_small_run() -> None:
    context = PipelineContext(
        disease_input="Alzheimer disease",
        disease=Disease(
            input_name="Alzheimer disease",
            canonical_name="Alzheimer disease",
            synonyms=[],
            identifiers={"mondo": "MONDO:0004975"},
        ),
        targets=[
            Target(
                symbol="APP",
                name="Amyloid beta precursor protein",
                identifiers={"ensembl": "ENSG00000142192"},
                disease_relevance_score=0.8,
                evidence=[
                    EvidenceItem(
                        source="Open Targets",
                        source_record_id="MONDO:0004975:APP",
                        title="Alzheimer disease APP association",
                        evidence_type="target_disease_association",
                        summary="Database disease-target association.",
                        confidence=0.8,
                    )
                ],
            )
        ],
        candidates=[
            MoleculeCandidate(
                name="Amyloid beta",
                molecule_type="peptide",
                identifiers={},
                known_targets=["APP"],
                mechanism_of_action="amyloid beta peptide associated with APP biology",
                evidence=[
                    EvidenceItem(
                        source="ChEMBL",
                        source_record_id="live-smoke-mechanism",
                        title="Seed mechanism evidence",
                        evidence_type="mechanism",
                        summary="Seed evidence for live literature smoke context.",
                        confidence=0.5,
                    )
                ],
            )
        ],
        config={
            "max_literature_queries": 2,
            "max_papers_per_query": 2,
            "max_targets_for_literature": 1,
            "max_candidates_for_literature": 1,
            "strict_literature": False,
        },
    )
    agent = LiteratureEvidenceAgent(
        PubMedAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25)
    )

    try:
        updated = agent.run(context)
    except (ExternalDataUnavailableError, LiteratureParsingError, LiteratureRetrievalError) as exc:
        pytest.skip(f"PubMed unavailable during live literature agent smoke: {exc}")

    literature_config = updated.config["literature_evidence"]
    assert literature_config["queries_generated"] <= 2
    assert literature_config["queries_executed"] <= 2
    assert literature_config["papers_retrieved"] >= 0
    assert literature_config["unique_papers_retained"] >= 0
    assert literature_config["claims_extracted"] >= 0
    assert literature_config["strict_literature"] is False
    assert "bundles" in literature_config


def _search_stable_pubmed_query(*, max_results: int) -> list[LiteraturePaper]:
    adapter = PubMedAdapter(timeout_seconds=10, max_retries=1, retry_delay_seconds=0.25)
    query = LiteratureQuery(
        query_id="live-pubmed-alzheimer-amyloid-beta",
        disease_name="Alzheimer disease",
        target_symbol=None,
        target_name=None,
        molecule_name=None,
        molecule_identifiers={},
        query_text="Alzheimer disease amyloid beta",
        query_type="molecule_disease",
        max_results=max_results,
        metadata={"test": "live_literature_smoke"},
    )
    try:
        return adapter.search(query)
    except (ExternalDataUnavailableError, LiteratureParsingError, LiteratureRetrievalError) as exc:
        pytest.skip(f"PubMed unavailable during live literature smoke: {exc}")
