from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from molecule_ranker.schemas import (
    Citation,
    EvidenceClaim,
    LiteratureEvidenceBundle,
    LiteratureEvidenceItem,
    LiteraturePaper,
    LiteratureQuery,
)


def test_literature_models_serialize_real_paper_provenance() -> None:
    query = LiteratureQuery(
        disease="Parkinson disease",
        molecule="rasagiline",
        target="MAOB",
        query_text="rasagiline AND (Parkinson disease OR MAOB)",
        max_results=5,
    )
    paper = LiteraturePaper(
        source="PubMed",
        source_record_id="12345",
        pmid="12345",
        doi="10.1000/example",
        title="Rasagiline and monoamine oxidase B in Parkinson disease",
        abstract=(
            "This review mentions rasagiline, monoamine oxidase B, and Parkinson disease."
        ),
        journal="Example Journal",
        publication_date=date(2020, 1, 2),
        publication_types=["Review"],
        authors=["Example A"],
        url="https://pubmed.ncbi.nlm.nih.gov/12345/",
    )
    citation = Citation.from_paper(paper)
    claim = EvidenceClaim(
        claim_type="molecule_disease_target_mention",
        text=(
            "PubMed paper 12345 mentions rasagiline with Parkinson disease and MAOB; "
            "this requires validation."
        ),
        matched_terms=["rasagiline", "Parkinson disease", "MAOB"],
        study_type="review",
        support_level="mentions",
    )
    item = LiteratureEvidenceItem(
        query=query,
        paper=paper,
        citation=citation,
        claims=[claim],
        quality_score=0.65,
    )
    bundle = LiteratureEvidenceBundle(
        candidate_name="rasagiline",
        query_count=1,
        items=[item],
        quality_score=0.65,
    )

    payload = bundle.model_dump(mode="json")

    assert payload["items"][0]["paper"]["pmid"] == "12345"
    assert payload["items"][0]["citation"]["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345/"
    assert payload["items"][0]["claims"][0]["support_level"] == "mentions"
    assert payload["absent_reason"] is None


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (
            lambda: LiteratureQuery(
                disease="Parkinson disease",
                query_text="rasagiline",
                max_results=0,
            ),
            "max_results",
        ),
        (
            lambda: LiteratureEvidenceItem(
                query=LiteratureQuery(disease="Disease", query_text="query"),
                paper=LiteraturePaper(
                    source="PubMed",
                    source_record_id="1",
                    title="Title",
                    abstract=None,
                    url="https://pubmed.ncbi.nlm.nih.gov/1/",
                ),
                citation=Citation(source="PubMed", source_record_id="1", title="Title"),
                claims=[],
                quality_score=1.2,
            ),
            "quality_score",
        ),
    ],
)
def test_literature_scores_and_limits_are_validated(factory, field_name: str) -> None:
    with pytest.raises(ValidationError) as error:
        factory()

    assert field_name in str(error.value)
