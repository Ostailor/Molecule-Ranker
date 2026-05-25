from __future__ import annotations

from datetime import UTC, datetime

from molecule_ranker.literature.extraction.evidence_classifier import classify_evidence
from molecule_ranker.literature.schemas import EvidenceClaim, LiteraturePaper


def _paper(
    *,
    title: str,
    abstract: str | None,
    publication_type: str | None = "Journal Article",
    is_review: bool = False,
    is_clinical: bool = False,
    is_preclinical: bool = False,
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
        publication_type=publication_type,
        is_review=is_review,
        is_clinical=is_clinical,
        is_preclinical=is_preclinical,
        is_retracted=False,
        cited_by_count=None,
        url=None,
        retrieved_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        metadata={},
    )


def _claim(
    *,
    claim_type: str = "clinical_support",
    direction: str = "supportive",
    metadata: dict[str, object] | None = None,
) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id="claim-1",
        paper_id="pubmed:1",
        candidate_name="Rasagiline",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        claim_type=claim_type,  # type: ignore[arg-type]
        claim_text="Cautious extracted claim.",
        supporting_snippet="Rasagiline was evaluated in Parkinson disease patients.",
        confidence=0.8,
        direction=direction,  # type: ignore[arg-type]
        extraction_method="test",
        metadata=metadata or {"query_type": "clinical"},
    )


def test_randomized_trial_classification() -> None:
    classified = classify_evidence(
        _paper(
            title="Randomized clinical trial in Parkinson disease",
            abstract="Patients were enrolled in a phase 2 placebo controlled study.",
            publication_type="Randomized Controlled Trial",
            is_clinical=True,
        ),
        _claim(
            metadata={
                "query_type": "clinical",
                "matched_entities": {
                    "disease": ["Parkinson disease"],
                    "molecule": ["Rasagiline"],
                    "target": [],
                },
            }
        ),
    )

    assert classified.metadata["study_type"] == "clinical_trial"
    assert classified.metadata["evidence_level"] == "high"
    assert classified.metadata["clinical_relevance"] == "direct_disease"


def test_review_classification() -> None:
    classified = classify_evidence(
        _paper(
            title="Review of MAOB in Parkinson disease",
            abstract="This review summarizes clinical and preclinical literature.",
            publication_type="Review",
            is_review=True,
        ),
        _claim(claim_type="molecule_disease_association"),
    )

    assert classified.metadata["study_type"] == "review"
    assert classified.metadata["evidence_level"] == "medium"


def test_animal_study_classification() -> None:
    classified = classify_evidence(
        _paper(
            title="Rasagiline in a mouse model",
            abstract="Mice were used to evaluate Parkinson disease model outcomes.",
            is_preclinical=True,
        ),
        _claim(
            metadata={
                "query_type": "molecule_disease",
                "matched_entities": {
                    "disease": ["Parkinson disease"],
                    "molecule": ["Rasagiline"],
                    "target": [],
                },
            }
        ),
    )

    assert classified.metadata["study_type"] == "animal_preclinical"
    assert classified.metadata["evidence_level"] == "medium"


def test_in_vitro_classification() -> None:
    classified = classify_evidence(
        _paper(
            title="Rasagiline assay in cultured cells",
            abstract="An in vitro cell line assay measured MAOB activity.",
            is_preclinical=True,
        ),
        _claim(claim_type="molecule_target_interaction"),
    )

    assert classified.metadata["study_type"] == "in_vitro"
    assert classified.metadata["evidence_level"] == "low"


def test_computational_study_classification() -> None:
    classified = classify_evidence(
        _paper(
            title="In silico docking of Rasagiline to MAOB",
            abstract="Computational molecular docking was performed.",
        ),
        _claim(claim_type="molecule_target_interaction"),
    )

    assert classified.metadata["study_type"] == "computational"
    assert classified.metadata["evidence_level"] == "low"
    assert classified.metadata["clinical_relevance"] == "target_only"


def test_unknown_fallback() -> None:
    classified = classify_evidence(
        _paper(title="Unclear literature record", abstract=None, publication_type=None),
        _claim(
            claim_type="mention_only",
            direction="neutral",
            metadata={"query_type": "molecule_target", "matched_entities": {}},
        ),
    )

    assert classified.metadata["study_type"] == "unknown"
    assert classified.metadata["evidence_level"] == "mention_only"
    assert classified.metadata["clinical_relevance"] == "unclear"
