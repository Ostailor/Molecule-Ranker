from __future__ import annotations

import json
from datetime import UTC, datetime

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.report_writer import ReportWriterAgent
from molecule_ranker.schemas import (
    Disease,
    EvidenceItem,
    MoleculeCandidate,
    ScoreBreakdown,
    Target,
)

RETRIEVED_AT = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


def _literature_item(
    evidence_type: str,
    *,
    record_id: str,
    claim_type: str,
    direction: str,
    snippet: str,
    study_type: str = "animal_preclinical",
    evidence_level: str = "medium",
) -> EvidenceItem:
    return EvidenceItem(
        source="PubMed",
        source_record_id=record_id,
        title=f"Literature paper {record_id}",
        url=f"https://pubmed.ncbi.nlm.nih.gov/{record_id}/",
        evidence_type=evidence_type,
        summary=f"PubMed record {record_id} mentions Rasagiline and MAOB.",
        confidence=0.7,
        retrieval_timestamp=RETRIEVED_AT,
        metadata={
            "citation": {
                "title": f"Literature paper {record_id}",
                "authors": ["A Author"],
                "journal": "Example Journal",
                "publication_date": "2021-01-01",
                "year": 2021,
                "doi": f"10.1000/{record_id}",
                "pmid": record_id,
                "pmcid": f"PMC{record_id}",
                "openalex_id": f"W{record_id}",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{record_id}/",
                "citation_text": f"Literature paper {record_id}. (2021) PMID:{record_id}",
            },
            "paper_id": f"pubmed:{record_id}",
            "pmid": record_id,
            "doi": f"10.1000/{record_id}",
            "pmcid": f"PMC{record_id}",
            "openalex_id": f"W{record_id}",
            "publication_type": "Journal Article",
            "study_type": study_type,
            "evidence_level": evidence_level,
            "supporting_snippet": snippet,
            "query_id": "lit-0001",
            "query_text": "Rasagiline AND MAOB",
            "claim_type": claim_type,
            "direction": direction,
            "is_retracted": False,
            "cited_by_count": 5,
            "candidate_name": "Rasagiline",
            "target_symbol": "MAOB",
            "disease_name": "Parkinson disease",
        },
    )


def _context(tmp_path) -> PipelineContext:
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
    long_abstract = "LONG_ABSTRACT_SENTENCE " * 80
    supportive = _literature_item(
        "literature_mechanism",
        record_id="12345",
        claim_type="mechanism_support",
        direction="supportive",
        snippet="Rasagiline inhibits MAOB in a Parkinson disease model.",
    )
    contradictory = _literature_item(
        "literature_contradictory",
        record_id="67890",
        claim_type="negative_or_contradictory",
        direction="contradictory",
        snippet="Rasagiline did not improve outcomes in this retrieved abstract.",
        evidence_level="contradictory",
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
            ),
            supportive,
            contradictory,
        ],
        score=0.8,
        score_breakdown=ScoreBreakdown(
            disease_target_relevance=0.8,
            molecule_target_evidence=0.8,
            mechanism_plausibility=0.8,
            clinical_precedence=0.1,
            safety_prior=0.6,
            data_quality=0.8,
            novelty_or_repurposing_value=0.6,
            literature_quality=0.5,
            final_score=0.8,
            confidence=0.7,
            explanation="Scored with literature evidence.",
        ),
    )
    return PipelineContext(
        disease_input="PD",
        disease=disease,
        targets=[target],
        candidates=[candidate],
        config={
            "results_dir": str(tmp_path),
            "ranker_config": {},
            "literature_evidence": {
                "queries_generated": 1,
                "queries_executed": 1,
                "papers_retrieved": 2,
                "unique_papers_retained": 2,
                "claims_extracted": 2,
                "sources_used": ["PubMed", "OpenAlex"],
                "warnings": ["OpenAlex enrichment skipped for one paper."],
                "strict_literature": False,
                "bundles": [
                    {
                        "query": {
                            "query_id": "lit-0001",
                            "query_type": "molecule_target",
                            "query_text": "Rasagiline AND MAOB",
                            "disease_name": "Parkinson disease",
                            "target_symbol": "MAOB",
                            "target_name": "Monoamine oxidase B",
                            "molecule_name": "Rasagiline",
                            "molecule_identifiers": {"chembl": "CHEMBL887"},
                            "max_results": 5,
                            "metadata": {},
                        },
                        "papers": [
                            {
                                "paper_id": "pubmed:12345",
                                "source": "PubMed",
                                "title": "Literature paper 12345",
                                "abstract": long_abstract,
                                "authors": ["A Author"],
                                "journal": "Example Journal",
                                "publication_date": "2021-01-01",
                                "year": 2021,
                                "doi": "10.1000/12345",
                                "pmid": "12345",
                                "pmcid": "PMC12345",
                                "openalex_id": "W12345",
                                "publication_type": "Journal Article",
                                "is_review": False,
                                "is_clinical": False,
                                "is_preclinical": True,
                                "is_retracted": False,
                                "cited_by_count": 5,
                                "url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
                                "retrieved_at": RETRIEVED_AT.isoformat(),
                                "metadata": {},
                            }
                        ],
                        "claims": [
                            {
                                "claim_id": "claim-1",
                                "paper_id": "pubmed:12345",
                                "candidate_name": "Rasagiline",
                                "target_symbol": "MAOB",
                                "disease_name": "Parkinson disease",
                                "claim_type": "mechanism_support",
                                "claim_text": "Cautious mechanism support.",
                                "supporting_snippet": (
                                    "Rasagiline inhibits MAOB in a Parkinson disease model."
                                ),
                                "confidence": 0.7,
                                "direction": "supportive",
                                "extraction_method": "rule_based",
                                "metadata": {"study_type": "animal_preclinical"},
                            }
                        ],
                        "warnings": [],
                        "metadata": {},
                    }
                ],
            },
        },
    )


def test_v02_report_literature_sections_json_and_citations(tmp_path) -> None:
    updated = ReportWriterAgent().run(_context(tmp_path))
    report = updated.config["report_md"]

    assert "## Literature Evidence Summary" in report
    assert "- Literature sources used: OpenAlex, PubMed" in report
    assert "- Number of queries generated: 1" in report
    assert "- Number of evidence items attached: 2" in report
    assert "## Literature Query Audit" in report
    assert "lit-0001" in report
    assert "Rasagiline AND MAOB" in report
    assert "## Candidate Literature Evidence" in report
    assert "Supportive claims: 1" in report
    assert "Contradictory claims: 1" in report
    assert "PubMed endpoint: https://eutils.ncbi.nlm.nih.gov/entrez/eutils" in report
    assert "OpenAlex endpoint: https://api.openalex.org/works" in report
    assert "## Citations" in report
    assert "PMID:12345" in report
    assert "doi:10.1000/12345" in report
    assert "OpenAlex:W12345" in report
    assert "Retraction status: not retracted" in report
    assert "Rasagiline inhibits MAOB" in report
    assert "Safety/contradictory snippets" in report
    assert "did not improve" in report
    assert "Contradictory evidence" in report
    assert "LONG_ABSTRACT_SENTENCE" not in report

    output_dir = tmp_path / "parkinson-disease"
    payload = json.loads((output_dir / "candidates.json").read_text())
    assert payload["literature_evidence_summary"]["queries_generated"] == 1
    assert payload["literature_evidence_summary"]["evidence_items_attached"] == 2
    assert payload["literature_queries"][0]["query_id"] == "lit-0001"
    assert payload["literature_papers"][0]["pmid"] == "12345"
    assert "abstract" not in payload["literature_papers"][0]
    assert payload["extracted_claims"][0]["claim_type"] == "mechanism_support"
    assert payload["extracted_claims"][0]["supporting_snippet"].startswith("Rasagiline")
    assert payload["literature_papers"][0]["citation"]["pmid"] == "12345"


def test_v02_report_counts_candidate_papers_without_attached_claims(tmp_path) -> None:
    context = _context(tmp_path)
    candidate = context.candidates[0]
    candidate.evidence = [
        item for item in candidate.evidence if not item.evidence_type.startswith("literature_")
    ]
    literature_config = context.config["literature_evidence"]
    literature_config["claims_extracted"] = 0
    literature_config["bundles"][0]["claims"] = []

    updated = ReportWriterAgent().run(context)
    report = updated.config["report_md"]

    candidate_section = report.split("## Candidate Literature Evidence", maxsplit=1)[1].split(
        "## Citations", maxsplit=1
    )[0]
    assert "Total literature papers: 1" in candidate_section
    assert "Supportive claims: 0" in candidate_section
    assert "Literature paper 12345" in candidate_section
    assert "PubMed endpoint: https://eutils.ncbi.nlm.nih.gov/entrez/eutils" in report
