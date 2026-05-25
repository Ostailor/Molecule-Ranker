from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.literature.extraction.claim_extractor import extract_claims
from molecule_ranker.literature.schemas import LiteraturePaper, LiteratureQuery


def _query(query_type: str = "molecule_target") -> LiteratureQuery:
    return LiteratureQuery(
        query_id="q1",
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_identifiers={"chembl": "CHEMBL887"},
        query_text="Rasagiline AND MAOB",
        query_type=query_type,  # type: ignore[arg-type]
        max_results=5,
        metadata={},
    )


def _paper(
    title: str,
    abstract: str | None,
    *,
    is_review: bool = False,
    is_retracted: bool | None = False,
) -> LiteraturePaper:
    return LiteraturePaper(
        paper_id="pubmed:1",
        source="PubMed",
        title=title,
        abstract=abstract,
        authors=[],
        journal=None,
        publication_date="2021-01-01",
        year=2021,
        doi=None,
        pmid="1",
        pmcid=None,
        openalex_id=None,
        publication_type="Review" if is_review else "Journal Article",
        is_review=is_review,
        is_clinical=False,
        is_preclinical=not is_review,
        is_retracted=is_retracted,
        cited_by_count=None,
        url="https://pubmed.ncbi.nlm.nih.gov/1/",
        retrieved_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        metadata={},
    )


def test_extracts_exact_supportive_molecule_target_claim() -> None:
    claims = extract_claims(
        paper=_paper(
            "Rasagiline binds MAOB",
            "Rasagiline inhibits Monoamine oxidase B in a Parkinson disease model.",
        ),
        query=_query("molecule_target"),
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_synonyms=[],
    )

    assert len(claims) == 1
    assert claims[0].claim_type == "molecule_target_interaction"
    assert claims[0].direction == "supportive"
    assert claims[0].confidence >= 0.8
    assert "inhibits" in claims[0].supporting_snippet.lower()


def test_extracts_clinical_claim() -> None:
    claims = extract_claims(
        paper=_paper(
            "Rasagiline clinical trial in Parkinson disease",
            "Patients in a randomized phase study reported efficacy and safety outcomes.",
        ),
        query=_query("clinical"),
        disease_name="Parkinson disease",
        target_symbol=None,
        target_name=None,
        molecule_name="Rasagiline",
        molecule_synonyms=[],
    )

    assert claims[0].claim_type == "clinical_support"
    assert claims[0].direction == "supportive"
    assert "randomized" in claims[0].supporting_snippet.lower()


def test_extracts_safety_concern_claim() -> None:
    claims = extract_claims(
        paper=_paper(
            "Rasagiline safety warning",
            "The abstract reports an adverse event and hepatotoxicity signal.",
        ),
        query=_query("safety"),
        disease_name="Parkinson disease",
        target_symbol=None,
        target_name=None,
        molecule_name="Rasagiline",
        molecule_synonyms=[],
    )

    assert claims[0].claim_type == "safety_concern"
    assert claims[0].direction == "safety_concern"
    assert "adverse event" in claims[0].supporting_snippet.lower()


def test_extracts_negative_claim() -> None:
    claims = extract_claims(
        paper=_paper(
            "Rasagiline in Parkinson disease",
            "The study failed to show benefit and did not improve patient outcomes.",
        ),
        query=_query("clinical"),
        disease_name="Parkinson disease",
        target_symbol=None,
        target_name=None,
        molecule_name="Rasagiline",
        molecule_synonyms=[],
    )

    assert claims[0].claim_type == "negative_or_contradictory"
    assert claims[0].direction == "contradictory"
    assert "failed to" in claims[0].supporting_snippet.lower()


def test_extracts_mention_only_claim_with_low_confidence() -> None:
    claims = extract_claims(
        paper=_paper(
            "Rasagiline and Parkinson disease",
            "This abstract mentions MAOB without a relation cue.",
        ),
        query=_query("molecule_target"),
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_synonyms=[],
    )

    assert claims[0].claim_type == "mention_only"
    assert claims[0].direction == "neutral"
    assert claims[0].confidence <= 0.4


def test_no_entity_overlap_produces_no_claim() -> None:
    claims = extract_claims(
        paper=_paper("Unrelated oncology paper", "No requested entities appear here."),
        query=_query("molecule_target"),
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_synonyms=[],
    )

    assert claims == []


def test_retracted_paper_produces_zero_confidence_warning_claim() -> None:
    claims = extract_claims(
        paper=_paper(
            "Retracted Rasagiline MAOB paper",
            "Rasagiline inhibits MAOB.",
            is_retracted=True,
        ),
        query=_query("molecule_target"),
        disease_name="Parkinson disease",
        target_symbol="MAOB",
        target_name="Monoamine oxidase B",
        molecule_name="Rasagiline",
        molecule_synonyms=[],
    )

    assert claims[0].confidence == 0
    assert claims[0].claim_type == "mention_only"
    assert claims[0].metadata["warnings"]
