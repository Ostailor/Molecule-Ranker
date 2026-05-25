from __future__ import annotations

from datetime import UTC, date, datetime

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.report_writer import ReportWriterAgent
from molecule_ranker.schemas import (
    Disease,
    EvidenceClaim,
    EvidenceItem,
    LiteratureEvidenceBundle,
    LiteratureEvidenceItem,
    LiteraturePaper,
    LiteratureQuery,
    MoleculeCandidate,
    ScoreBreakdown,
    Target,
)


def test_report_includes_literature_evidence_section_with_citations(tmp_path) -> None:
    disease = Disease(
        input_name="PD",
        canonical_name="Parkinson disease",
        identifiers={"mondo": "MONDO:0005180"},
    )
    target = Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.8,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id="MONDO:MAOB",
                title="Association",
                evidence_type="target_disease_association",
                summary="Association.",
                confidence=0.8,
            )
        ],
    )
    paper = LiteraturePaper(
        source="PubMed",
        source_record_id="12345",
        pmid="12345",
        doi="10.1000/example",
        title="Rasagiline and monoamine oxidase B in Parkinson disease",
        abstract="This abstract mentions rasagiline, MAOB, and Parkinson disease.",
        journal="Example Journal",
        publication_date=date(2020, 1, 1),
        publication_types=["Review"],
        url="https://pubmed.ncbi.nlm.nih.gov/12345/",
    )
    literature = LiteratureEvidenceBundle(
        candidate_name="Rasagiline",
        query_count=1,
        quality_score=0.7,
        items=[
            LiteratureEvidenceItem(
                query=LiteratureQuery(
                    disease="Parkinson disease",
                    molecule="Rasagiline",
                    target="MAOB",
                    query_text="Rasagiline AND Parkinson disease AND MAOB",
                ),
                paper=paper,
                citation=paper.to_citation(),
                claims=[
                    EvidenceClaim(
                        claim_type="molecule_disease_target_mention",
                        text=(
                            "PubMed paper 12345 mentions Rasagiline with Parkinson disease "
                            "and MAOB; this requires validation."
                        ),
                        matched_terms=["Rasagiline", "Parkinson disease", "MAOB"],
                        study_type="review",
                        support_level="mentions",
                    )
                ],
                quality_score=0.7,
            )
        ],
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
                summary="Mechanism evidence.",
                confidence=0.8,
                retrieval_timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            )
        ],
        literature_evidence=literature,
        score=0.8,
        score_breakdown=ScoreBreakdown(
            disease_target_relevance=0.8,
            molecule_target_evidence=0.8,
            mechanism_plausibility=0.8,
            clinical_precedence=0.8,
            safety_prior=0.8,
            data_quality=0.8,
            novelty_or_repurposing_value=0.8,
            literature_quality=0.7,
            final_score=0.8,
            confidence=0.8,
            explanation="Scored with literature evidence.",
        ),
    )
    context = PipelineContext(
        disease_input="PD",
        disease=disease,
        targets=[target],
        candidates=[candidate],
        config={"results_dir": str(tmp_path), "ranker_config": {}},
    )

    updated = ReportWriterAgent().run(context)
    report = updated.config["report_md"]

    assert "## Literature Evidence" in report
    assert "Literature quality: 0.700" in report
    assert "PubMed paper 12345 mentions Rasagiline" in report
    assert "Study type: review" in report
    assert "Rasagiline and monoamine oxidase B in Parkinson disease" in report
    assert "PMID:12345" in report
    assert "https://pubmed.ncbi.nlm.nih.gov/12345/" in report
    literature_section = report.split("## Literature Evidence", maxsplit=1)[1].split(
        "## Summary", maxsplit=1
    )[0]
    assert "cures" not in literature_section.lower()
