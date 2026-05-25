from __future__ import annotations

from datetime import date

from molecule_ranker.schemas import (
    Citation,
    EvidenceClaim,
    EvidenceItem,
    LiteratureEvidenceBundle,
    LiteratureEvidenceItem,
    LiteraturePaper,
    LiteratureQuery,
    MoleculeCandidate,
    Target,
)
from molecule_ranker.scoring.scorer import TransparentEvidenceScorer


def _target() -> Target:
    return Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.8,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id="MONDO:MAOB",
                title="Target association",
                evidence_type="target_disease_association",
                summary="Association.",
                confidence=0.8,
            )
        ],
    )


def _candidate(literature: LiteratureEvidenceBundle | None) -> MoleculeCandidate:
    return MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887", "pubchem_cid": "3052776"},
        known_targets=["MAOB"],
        development_status="approved",
        mechanism_of_action="MAOB inhibitor",
        evidence=[
            EvidenceItem(
                source="ChEMBL",
                source_record_id="mec-1",
                title="Mechanism",
                evidence_type="mechanism",
                summary="Mechanism evidence.",
                confidence=0.8,
            )
        ],
        literature_evidence=literature,
    )


def _literature_bundle(quality: float) -> LiteratureEvidenceBundle:
    paper = LiteraturePaper(
        source="PubMed",
        source_record_id="12345",
        pmid="12345",
        title="Rasagiline and monoamine oxidase B in Parkinson disease",
        abstract="This abstract mentions rasagiline, MAOB, and Parkinson disease.",
        publication_date=date(2020, 1, 1),
        publication_types=["Clinical Trial"],
        citation_count=12,
        url="https://pubmed.ncbi.nlm.nih.gov/12345/",
    )
    return LiteratureEvidenceBundle(
        candidate_name="Rasagiline",
        query_count=1,
        quality_score=quality,
        items=[
            LiteratureEvidenceItem(
                query=LiteratureQuery(
                    disease="Parkinson disease",
                    molecule="Rasagiline",
                    target="MAOB",
                    query_text="Rasagiline AND Parkinson disease AND MAOB",
                ),
                paper=paper,
                citation=Citation.from_paper(paper),
                claims=[
                    EvidenceClaim(
                        claim_type="molecule_disease_target_mention",
                        text=(
                            "PubMed paper 12345 mentions Rasagiline with Parkinson disease "
                            "and MAOB; this requires validation."
                        ),
                        matched_terms=["Rasagiline", "Parkinson disease", "MAOB"],
                        study_type="clinical",
                        support_level="mentions",
                    )
                ],
                quality_score=quality,
            )
        ],
    )


def test_literature_quality_influences_score_when_real_paper_claims_exist() -> None:
    targets = [_target()]
    without_literature = TransparentEvidenceScorer().score([_candidate(None)], targets, top=1)[0]
    with_literature = TransparentEvidenceScorer().score(
        [_candidate(_literature_bundle(0.85))], targets, top=1
    )[0]

    assert with_literature.score_breakdown is not None
    assert without_literature.score_breakdown is not None
    assert with_literature.score_breakdown.literature_quality == 0.85
    assert with_literature.score is not None
    assert without_literature.score is not None
    assert with_literature.score > without_literature.score
    assert "literature evidence" in with_literature.score_breakdown.explanation.lower()
