from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from molecule_ranker.literature.schemas import (
    Citation,
    EvidenceClaim,
    LiteratureEvidenceBundle,
    LiteraturePaper,
    LiteratureQuery,
)


def test_literature_module_schemas_preserve_external_ids_and_serialize() -> None:
    query = LiteratureQuery(
        query_id="q-rasagiline-maob",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_identifiers={"chembl": "CHEMBL887", "pubchem_cid": "3052776"},
        query_text="Rasagiline AND Parkinson disease AND MAOB",
        query_type="molecule_target",
        max_results=5,
        metadata={"source": "rule_based_query_builder"},
    )
    paper = LiteraturePaper(
        paper_id="pubmed:12345",
        source="PubMed",
        title="Rasagiline and monoamine oxidase B in Parkinson disease",
        abstract="The source-provided abstract mentions rasagiline and MAOB.",
        authors=["Example A"],
        journal="Example Journal",
        publication_date="2020-01-02",
        year=2020,
        doi="10.1000/example",
        pmid="12345",
        pmcid="PMC12345",
        openalex_id="W12345",
        publication_type="Review",
        is_review=True,
        is_clinical=False,
        is_preclinical=False,
        is_retracted=False,
        cited_by_count=12,
        url="https://pubmed.ncbi.nlm.nih.gov/12345/",
        retrieved_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        metadata={"external_ids": {"pubmed": "12345", "openalex": "W12345"}},
    )
    claim = EvidenceClaim(
        claim_id="claim-1",
        paper_id=paper.paper_id,
        candidate_name="Rasagiline",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        claim_type="mention_only",
        claim_text="The paper mentions Rasagiline with MAOB and Parkinson disease.",
        supporting_snippet="source-provided abstract mentions rasagiline and MAOB",
        confidence=0.6,
        direction="neutral",
        extraction_method="rule_based_title_abstract_match",
        metadata={"matched_terms": ["Rasagiline", "MAOB"]},
    )
    citation = Citation(
        title=paper.title,
        authors=paper.authors,
        journal=paper.journal,
        publication_date=paper.publication_date,
        year=paper.year,
        doi=paper.doi,
        pmid=paper.pmid,
        pmcid=paper.pmcid,
        openalex_id=paper.openalex_id,
        url=paper.url,
        citation_text="Example A. Rasagiline and monoamine oxidase B in Parkinson disease. 2020.",
    )
    bundle = LiteratureEvidenceBundle(
        query=query,
        papers=[paper],
        claims=[claim],
        warnings=[],
        metadata={"citation": citation.model_dump(mode="json")},
    )

    payload = bundle.model_dump(mode="json")

    assert payload["query"]["molecule_identifiers"]["chembl"] == "CHEMBL887"
    assert payload["papers"][0]["pmid"] == "12345"
    assert payload["papers"][0]["pmcid"] == "PMC12345"
    assert payload["papers"][0]["openalex_id"] == "W12345"
    assert payload["papers"][0]["retrieved_at"].endswith(("Z", "+00:00"))
    assert payload["claims"][0]["supporting_snippet"] == (
        "source-provided abstract mentions rasagiline and MAOB"
    )
    assert "full_text" not in payload["papers"][0]


@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        (
            lambda: LiteratureQuery.model_validate(
                {
                    "query_id": "q1",
                    "disease_name": "Disease",
                    "target_symbol": None,
                    "target_name": None,
                    "molecule_name": None,
                    "molecule_identifiers": {},
                    "query_text": "Disease",
                    "query_type": "unsupported",
                    "max_results": 5,
                    "metadata": {},
                }
            ),
            "query_type",
        ),
        (
            lambda: LiteratureQuery(
                query_id="q1",
                disease_name="Disease",
                target_symbol=None,
                target_name=None,
                molecule_name=None,
                molecule_identifiers={},
                query_text="Disease",
                query_type="clinical",
                max_results=0,
                metadata={},
            ),
            "max_results",
        ),
        (
            lambda: EvidenceClaim(
                claim_id="claim-1",
                paper_id="paper-1",
                candidate_name=None,
                target_symbol=None,
                disease_name="Disease",
                claim_type="mention_only",
                claim_text="Mentions disease.",
                supporting_snippet="Mentions disease.",
                confidence=1.1,
                direction="neutral",
                extraction_method="rule_based",
                metadata={},
            ),
            "confidence",
        ),
        (
            lambda: EvidenceClaim.model_validate(
                {
                    "claim_id": "claim-1",
                    "paper_id": "paper-1",
                    "candidate_name": None,
                    "target_symbol": None,
                    "disease_name": "Disease",
                    "claim_type": "invented",
                    "claim_text": "Mentions disease.",
                    "supporting_snippet": "Mentions disease.",
                    "confidence": 0.5,
                    "direction": "neutral",
                    "extraction_method": "rule_based",
                    "metadata": {},
                }
            ),
            "claim_type",
        ),
        (
            lambda: EvidenceClaim.model_validate(
                {
                    "claim_id": "claim-1",
                    "paper_id": "paper-1",
                    "candidate_name": None,
                    "target_symbol": None,
                    "disease_name": "Disease",
                    "claim_type": "mention_only",
                    "claim_text": "Mentions disease.",
                    "supporting_snippet": "Mentions disease.",
                    "confidence": 0.5,
                    "direction": "invented",
                    "extraction_method": "rule_based",
                    "metadata": {},
                }
            ),
            "direction",
        ),
    ],
)
def test_literature_module_schema_validation(factory, expected: str) -> None:
    with pytest.raises(ValidationError) as error:
        factory()

    assert expected in str(error.value)


def test_literature_paper_requires_timezone_aware_retrieved_at() -> None:
    with pytest.raises(ValidationError, match="retrieved_at"):
        LiteraturePaper(
            paper_id="pubmed:12345",
            source="PubMed",
            title="Title",
            abstract=None,
            authors=[],
            journal=None,
            publication_date=None,
            year=None,
            doi=None,
            pmid="12345",
            pmcid=None,
            openalex_id=None,
            publication_type=None,
            is_review=False,
            is_clinical=False,
            is_preclinical=False,
            is_retracted=None,
            cited_by_count=None,
            url="https://pubmed.ncbi.nlm.nih.gov/12345/",
            retrieved_at=datetime(2026, 5, 25, 12, 0),
            metadata={},
        )


def test_literature_paper_rejects_full_text_storage() -> None:
    with pytest.raises(ValidationError, match="full_text"):
        LiteraturePaper.model_validate(
            {
                "paper_id": "pubmed:12345",
                "source": "PubMed",
                "title": "Title",
                "abstract": None,
                "authors": [],
                "journal": None,
                "publication_date": None,
                "year": None,
                "doi": None,
                "pmid": "12345",
                "pmcid": None,
                "openalex_id": None,
                "publication_type": None,
                "is_review": False,
                "is_clinical": False,
                "is_preclinical": False,
                "is_retracted": None,
                "cited_by_count": None,
                "url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
                "retrieved_at": datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
                "metadata": {},
                "full_text": "Do not store copyrighted article bodies.",
            }
        )
