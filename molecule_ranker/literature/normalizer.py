from __future__ import annotations

from typing import Any

from molecule_ranker.literature.schemas import Citation, EvidenceClaim, LiteraturePaper
from molecule_ranker.literature.schemas import LiteratureQuery as ModuleLiteratureQuery
from molecule_ranker.schemas import EvidenceItem

MAX_SNIPPET_LENGTH = 500

EVIDENCE_TYPE_BY_CLAIM_TYPE = {
    "disease_target_association": "literature_disease_target",
    "molecule_target_interaction": "literature_molecule_target",
    "molecule_disease_association": "literature_molecule_disease",
    "mechanism_support": "literature_mechanism",
    "clinical_support": "literature_clinical",
    "safety_concern": "literature_safety",
    "negative_or_contradictory": "literature_contradictory",
    "mention_only": "literature_mention",
}


def literature_evidence_item(
    paper: LiteraturePaper,
    claim: EvidenceClaim,
    query: ModuleLiteratureQuery | None = None,
) -> EvidenceItem | None:
    """Convert a retrieved literature paper and extracted claim into an EvidenceItem."""

    source_record_id = _source_record_id(paper)
    supporting_snippet = _snippet(claim.supporting_snippet)
    if not source_record_id or not supporting_snippet:
        return None

    return EvidenceItem(
        source=paper.source,
        source_record_id=source_record_id,
        title=paper.title,
        url=_url(paper),
        evidence_type=_evidence_type(claim),
        summary=_summary(paper, claim, source_record_id),
        confidence=claim.confidence,
        retrieval_timestamp=paper.retrieved_at,
        metadata={
            "citation": _citation(paper).model_dump(mode="json"),
            "paper_id": paper.paper_id,
            "pmid": paper.pmid,
            "doi": paper.doi,
            "pmcid": paper.pmcid,
            "openalex_id": paper.openalex_id,
            "publication_type": paper.publication_type,
            "study_type": claim.metadata.get("study_type"),
            "evidence_level": claim.metadata.get("evidence_level"),
            "supporting_snippet": supporting_snippet,
            "query_id": claim.metadata.get("query_id")
            or (query.query_id if query is not None else None),
            "query_text": (
                query.query_text if query is not None else claim.metadata.get("query_text")
            ),
            "claim_type": claim.claim_type,
            "direction": claim.direction,
            "is_retracted": paper.is_retracted,
            "cited_by_count": paper.cited_by_count,
            "candidate_name": claim.candidate_name,
            "target_symbol": claim.target_symbol,
            "disease_name": claim.disease_name,
        },
    )


def _source_record_id(paper: LiteraturePaper) -> str | None:
    return paper.pmid or paper.doi or paper.openalex_id


def _snippet(value: str) -> str:
    return " ".join(value.split())[:MAX_SNIPPET_LENGTH]


def _url(paper: LiteraturePaper) -> str | None:
    if paper.url:
        return paper.url
    if paper.pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{paper.pmid}/"
    if paper.doi:
        return f"https://doi.org/{paper.doi}"
    return None


def _evidence_type(claim: EvidenceClaim) -> str:
    if claim.direction == "contradictory":
        return "literature_contradictory"
    if claim.direction == "safety_concern":
        return "literature_safety"
    return EVIDENCE_TYPE_BY_CLAIM_TYPE.get(claim.claim_type, "literature_mention")


def _summary(
    paper: LiteraturePaper,
    claim: EvidenceClaim,
    source_record_id: str,
) -> str:
    cue = _first_relation_cue(claim.metadata.get("relation_cues"))
    terms = _summary_terms(claim)
    record = f"{paper.source} record {source_record_id}"
    if cue and terms:
        return f"{record} mentions {terms} with relation cue '{cue}'."
    if terms:
        return f"{record} mentions {terms}; this requires validation."
    return f"{record} has a cautiously extracted literature claim; this requires validation."


def _summary_terms(claim: EvidenceClaim) -> str:
    if claim.claim_type in {"molecule_target_interaction", "mechanism_support"}:
        terms = [term for term in (claim.candidate_name, claim.target_symbol) if term]
    elif claim.claim_type in {"molecule_disease_association", "clinical_support"}:
        terms = [term for term in (claim.candidate_name, claim.disease_name) if term]
    elif claim.claim_type == "disease_target_association":
        terms = [term for term in (claim.disease_name, claim.target_symbol) if term]
    elif claim.claim_type == "safety_concern":
        terms = [term for term in (claim.candidate_name,) if term]
    else:
        terms = [
            term
            for term in (claim.candidate_name, claim.target_symbol, claim.disease_name)
            if term
        ]
    if not terms:
        return ""
    if len(terms) == 1:
        return terms[0]
    if len(terms) == 2:
        return f"{terms[0]} and {terms[1]}"
    return f"{terms[0]}, {terms[1]}, and {terms[2]}"


def _first_relation_cue(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in (
        "negative_or_contradictory",
        "safety_concern",
        "clinical",
        "supportive_mechanism",
    ):
        cues = value.get(key)
        if isinstance(cues, list) and cues:
            return str(cues[0])
    return None


def _citation(paper: LiteraturePaper) -> Citation:
    citation_text = _citation_text(paper)
    return Citation(
        title=paper.title,
        authors=paper.authors,
        journal=paper.journal,
        publication_date=paper.publication_date,
        year=paper.year,
        doi=paper.doi,
        pmid=paper.pmid,
        pmcid=paper.pmcid,
        openalex_id=paper.openalex_id,
        url=_url(paper),
        citation_text=citation_text,
    )


def _citation_text(paper: LiteraturePaper) -> str:
    identifiers = []
    if paper.pmid:
        identifiers.append(f"PMID:{paper.pmid}")
    if paper.doi:
        identifiers.append(f"doi:{paper.doi}")
    id_text = f" {'; '.join(identifiers)}" if identifiers else ""
    year_text = f" ({paper.year})" if paper.year else ""
    return f"{paper.title}.{year_text}{id_text}".strip()
