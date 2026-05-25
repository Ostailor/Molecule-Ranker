from __future__ import annotations

from datetime import UTC, datetime

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.literature_evidence import LiteratureEvidenceAgent
from molecule_ranker.data_sources.errors import ExternalDataUnavailableError
from molecule_ranker.literature.schemas import LiteraturePaper, LiteratureQuery
from molecule_ranker.schemas import Disease, EvidenceItem, MoleculeCandidate, Target


class FakePubMedSearch:
    source_name = "PubMed"

    def __init__(self, papers_by_query_type: dict[str, list[LiteraturePaper]]) -> None:
        self.papers_by_query_type = papers_by_query_type
        self.queries: list[LiteratureQuery] = []

    def search(self, query: LiteratureQuery) -> list[LiteraturePaper]:
        self.queries.append(query)
        return self.papers_by_query_type.get(query.query_type, [])


class FailingPubMedSearch:
    source_name = "PubMed"

    def search(self, query: LiteratureQuery) -> list[LiteraturePaper]:
        raise ExternalDataUnavailableError("PubMed unavailable")


class FakeOpenAlexEnricher:
    source_name = "OpenAlex"

    def enrich(self, papers: list[LiteraturePaper]) -> list[LiteraturePaper]:
        return [
            paper.model_copy(update={"cited_by_count": 11, "openalex_id": "W1"})
            for paper in papers
        ]


def _paper(
    paper_id: str,
    *,
    title: str,
    abstract: str | None,
    pmid: str,
) -> LiteraturePaper:
    return LiteraturePaper(
        paper_id=paper_id,
        source="PubMed",
        title=title,
        abstract=abstract,
        authors=["Example A"],
        journal="Example Journal",
        publication_date="2021-03-04",
        year=2021,
        doi=None,
        pmid=pmid,
        pmcid=None,
        openalex_id=None,
        publication_type="Journal Article",
        is_review=False,
        is_clinical=False,
        is_preclinical=True,
        is_retracted=False,
        cited_by_count=None,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        retrieved_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        metadata={},
    )


def _context(*, strict_literature: bool = False) -> PipelineContext:
    disease = Disease(
        input_name="PD",
        canonical_name="Parkinson disease",
        synonyms=["Parkinson's disease"],
        identifiers={"mondo": "MONDO:0005180"},
    )
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        identifiers={"ensembl": "ENSG_MAOB"},
        disease_relevance_score=0.9,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id="MONDO:MAOB",
                title="Disease-target association",
                evidence_type="target_disease_association",
                summary="Database association.",
                confidence=0.8,
            )
        ],
    )
    candidate = MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        mechanism_of_action="MAOB inhibitor",
        evidence=[
            EvidenceItem(
                source="ChEMBL",
                source_record_id="mec-1",
                title="Mechanism",
                evidence_type="mechanism",
                summary="ChEMBL mechanism.",
                confidence=0.8,
            )
        ],
    )
    return PipelineContext(
        disease_input="PD",
        disease=disease,
        targets=[target],
        candidates=[candidate],
        config={
            "strict_literature": strict_literature,
            "max_literature_queries": 10,
            "max_papers_per_query": 3,
            "max_targets_for_literature": 1,
            "max_candidates_for_literature": 1,
        },
    )


def test_literature_agent_generates_queries_attaches_and_deduplicates_evidence() -> None:
    shared = _paper(
        "pubmed:1",
        title="Rasagiline and MAOB in Parkinson disease",
        abstract="Rasagiline mentions MAOB and Parkinson disease in a model.",
        pmid="1",
    )
    disease_target = _paper(
        "pubmed:2",
        title="MAOB and Parkinson disease association",
        abstract="MAOB is associated with Parkinson disease in retrieved literature.",
        pmid="2",
    )
    source = FakePubMedSearch(
        {
            "molecule_target": [shared],
            "molecule_disease": [shared],
            "disease_target": [disease_target],
        }
    )

    updated = LiteratureEvidenceAgent(source, FakeOpenAlexEnricher()).run(_context())

    assert len(source.queries) > 0
    assert {query.query_type for query in source.queries} >= {
        "disease_target",
        "molecule_target",
        "molecule_disease",
    }
    literature_config = updated.config["literature_evidence"]
    assert literature_config["queries_generated"] == len(source.queries)
    assert literature_config["papers_retrieved"] == 3
    assert literature_config["unique_papers_retained"] == 2
    assert literature_config["claims_extracted"] >= 2
    assert literature_config["bundles"]
    assert any(item.source == "PubMed" for item in updated.candidates[0].evidence)
    assert any(item.source == "PubMed" for item in updated.targets[0].evidence)
    assert updated.candidates[0].literature_evidence is not None
    assert updated.candidates[0].literature_evidence.items


def test_literature_agent_optional_source_failure_warns_without_fake_evidence() -> None:
    updated = LiteratureEvidenceAgent(FailingPubMedSearch()).run(
        _context(strict_literature=False)
    )

    literature_config = updated.config["literature_evidence"]
    assert literature_config["failures"]
    assert literature_config["claims_extracted"] == 0
    assert literature_config["unique_papers_retained"] == 0
    assert all(item.source != "PubMed" for item in updated.candidates[0].evidence)
    assert any("PubMed unavailable" in warning for warning in updated.config["warnings"])


def test_literature_agent_strict_source_failure_stops_pipeline() -> None:
    with pytest.raises(ExternalDataUnavailableError):
        LiteratureEvidenceAgent(FailingPubMedSearch()).run(_context(strict_literature=True))
