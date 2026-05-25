from __future__ import annotations

from datetime import date

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.literature_evidence import LiteratureEvidenceAgent
from molecule_ranker.schemas import (
    Disease,
    EvidenceItem,
    LiteraturePaper,
    MoleculeCandidate,
    Target,
)


class FakeLiteratureSource:
    source_name = "Fake PubMed"

    def __init__(self, papers: list[LiteraturePaper]) -> None:
        self.papers = papers
        self.queries: list[str] = []

    def retrieve_papers(self, query):
        self.queries.append(query.query_text)
        return self.papers


def _context(papers: list[LiteraturePaper]) -> tuple[PipelineContext, FakeLiteratureSource]:
    source = FakeLiteratureSource(papers)
    disease = Disease(
        input_name="PD",
        canonical_name="Parkinson disease",
        synonyms=["Parkinson's disease"],
        identifiers={"mondo": "MONDO:0005180"},
    )
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.8,
        evidence=[],
    )
    candidate = MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        development_status="approved",
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
    context = PipelineContext(
        disease_input="PD",
        disease=disease,
        targets=[target],
        candidates=[candidate],
        config={"max_literature_queries_per_candidate": 2, "max_literature_results_per_query": 3},
    )
    return context, source


def _paper(
    pmid: str,
    title: str,
    abstract: str | None,
    publication_types: list[str] | None = None,
) -> LiteraturePaper:
    return LiteraturePaper(
        source="PubMed",
        source_record_id=pmid,
        pmid=pmid,
        title=title,
        abstract=abstract,
        publication_date=date(2021, 1, 1),
        publication_types=publication_types or ["Journal Article"],
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    )


def test_literature_agent_attaches_only_claims_supported_by_retrieved_paper_text() -> None:
    supported = _paper(
        "1",
        "Rasagiline is associated with monoamine oxidase B in Parkinson disease models",
        "The abstract mentions rasagiline, MAOB, and Parkinson disease in cautious terms.",
        ["Journal Article"],
    )
    unsupported = _paper(
        "2",
        "Unrelated oncology review",
        "This abstract mentions neither the candidate nor the disease.",
        ["Review"],
    )
    duplicate = supported.model_copy()
    context, source = _context([supported, unsupported, duplicate])

    updated = LiteratureEvidenceAgent(source).run(context)

    bundle = updated.candidates[0].literature_evidence
    assert bundle is not None
    assert bundle.absent_reason is None
    assert bundle.query_count == 2
    assert len(bundle.items) == 1
    assert bundle.items[0].paper.pmid == "1"
    assert bundle.items[0].claims
    claim = bundle.items[0].claims[0]
    assert claim.support_level == "mentions"
    assert claim.study_type == "preclinical"
    assert "mentions rasagiline" in claim.text.lower()
    assert "requires validation" in claim.text.lower()
    assert "cures" not in claim.text.lower()
    assert any("Rasagiline" in query or "Parkinson disease" in query for query in source.queries)


def test_literature_agent_labels_absent_evidence_without_fabricating_claims() -> None:
    context, source = _context([])

    updated = LiteratureEvidenceAgent(source).run(context)

    bundle = updated.candidates[0].literature_evidence
    assert bundle is not None
    assert bundle.items == []
    assert bundle.quality_score == 0
    assert bundle.absent_reason == "No literature records with conservative supported claims."
    assert any(
        "literature evidence is absent" in warning.lower()
        for warning in updated.candidates[0].warnings
    )
