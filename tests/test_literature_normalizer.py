from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.literature.normalizer import literature_evidence_item
from molecule_ranker.literature.schemas import EvidenceClaim, LiteraturePaper, LiteratureQuery


def _paper(
    *,
    pmid: str | None = "12345",
    doi: str | None = "10.1000/example",
    openalex_id: str | None = "W123",
    url: str | None = "https://pubmed.ncbi.nlm.nih.gov/12345/",
) -> LiteraturePaper:
    return LiteraturePaper(
        paper_id="pubmed:12345",
        source="PubMed",
        title="Rasagiline binds MAOB",
        abstract="Rasagiline inhibits MAOB in a Parkinson disease model.",
        authors=["A Author", "B Author"],
        journal="Example Journal",
        publication_date="2021-01-01",
        year=2021,
        doi=doi,
        pmid=pmid,
        pmcid="PMC123",
        openalex_id=openalex_id,
        publication_type="Journal Article",
        is_review=False,
        is_clinical=False,
        is_preclinical=True,
        is_retracted=False,
        cited_by_count=7,
        url=url,
        retrieved_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        metadata={},
    )


def _claim(
    *,
    claim_type: str = "molecule_target_interaction",
    direction: str = "supportive",
    supporting_snippet: str = "Rasagiline inhibits MAOB in a Parkinson disease model.",
) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id="claim-1",
        paper_id="pubmed:12345",
        candidate_name="Rasagiline",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        claim_type=claim_type,  # type: ignore[arg-type]
        claim_text="Cautious claim.",
        supporting_snippet=supporting_snippet,
        confidence=0.73,
        direction=direction,  # type: ignore[arg-type]
        extraction_method="rule_based_title_abstract_cues",
        metadata={
            "query_id": "lit-0001",
            "query_type": "molecule_target",
            "study_type": "animal_preclinical",
            "evidence_level": "medium",
            "relation_cues": {
                "supportive_mechanism": ["inhibits"],
                "clinical": [],
                "safety_concern": [],
                "negative_or_contradictory": [],
            },
        },
    )


def _query() -> LiteratureQuery:
    return LiteratureQuery(
        query_id="lit-0001",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_identifiers={"chembl": "CHEMBL887"},
        query_text="(Rasagiline) AND (MAOB)",
        query_type="molecule_target",
        max_results=5,
        metadata={},
    )


def test_literature_claim_to_evidence_item_preserves_provenance() -> None:
    item = literature_evidence_item(_paper(), _claim(), _query())

    assert item is not None
    assert item.source == "PubMed"
    assert item.source_record_id == "12345"
    assert item.title == "Rasagiline binds MAOB"
    assert item.url == "https://pubmed.ncbi.nlm.nih.gov/12345/"
    assert item.evidence_type == "literature_molecule_target"
    assert item.summary == (
        "PubMed record 12345 mentions Rasagiline and MAOB with relation cue 'inhibits'."
    )
    assert item.confidence == 0.73
    assert item.retrieval_timestamp == datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    assert item.metadata["citation"]["pmid"] == "12345"
    assert item.metadata["citation"]["doi"] == "10.1000/example"
    assert item.metadata["paper_id"] == "pubmed:12345"
    assert item.metadata["pmcid"] == "PMC123"
    assert item.metadata["openalex_id"] == "W123"
    assert item.metadata["study_type"] == "animal_preclinical"
    assert item.metadata["evidence_level"] == "medium"
    assert item.metadata["supporting_snippet"] == (
        "Rasagiline inhibits MAOB in a Parkinson disease model."
    )
    assert item.metadata["query_id"] == "lit-0001"
    assert item.metadata["query_text"] == "(Rasagiline) AND (MAOB)"
    assert item.metadata["claim_type"] == "molecule_target_interaction"
    assert item.metadata["direction"] == "supportive"
    assert item.metadata["is_retracted"] is False
    assert item.metadata["cited_by_count"] == 7


def test_uses_doi_record_id_and_url_when_pmid_absent() -> None:
    item = literature_evidence_item(
        _paper(pmid=None, url=None),
        _claim(claim_type="mechanism_support"),
        _query(),
    )

    assert item is not None
    assert item.source_record_id == "10.1000/example"
    assert item.url == "https://doi.org/10.1000/example"
    assert item.evidence_type == "literature_mechanism"


def test_uses_openalex_id_when_pmid_and_doi_absent() -> None:
    item = literature_evidence_item(
        _paper(pmid=None, doi=None, openalex_id="W123", url=None),
        _claim(claim_type="clinical_support"),
        _query(),
    )

    assert item is not None
    assert item.source_record_id == "W123"
    assert item.evidence_type == "literature_clinical"


def test_returns_none_without_source_record_id() -> None:
    item = literature_evidence_item(
        _paper(pmid=None, doi=None, openalex_id=None),
        _claim(),
        _query(),
    )

    assert item is None


def test_returns_none_without_supporting_snippet() -> None:
    item = literature_evidence_item(_paper(), _claim(supporting_snippet=""), _query())

    assert item is None


def test_maps_contradictory_safety_and_mention_evidence_types() -> None:
    contradictory = literature_evidence_item(
        _paper(),
        _claim(claim_type="negative_or_contradictory", direction="contradictory"),
        _query(),
    )
    safety = literature_evidence_item(
        _paper(),
        _claim(claim_type="safety_concern", direction="safety_concern"),
        _query(),
    )
    mention = literature_evidence_item(
        _paper(),
        _claim(claim_type="mention_only", direction="neutral"),
        _query(),
    )

    assert contradictory is not None
    assert contradictory.evidence_type == "literature_contradictory"
    assert safety is not None
    assert safety.evidence_type == "literature_safety"
    assert mention is not None
    assert mention.evidence_type == "literature_mention"


def test_supporting_snippet_is_capped() -> None:
    item = literature_evidence_item(
        _paper(),
        _claim(supporting_snippet="x" * 800),
        _query(),
    )

    assert item is not None
    assert len(item.metadata["supporting_snippet"]) == 500
