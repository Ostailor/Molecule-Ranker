from __future__ import annotations

import re
from typing import Literal

from molecule_ranker.literature.schemas import EvidenceClaim, LiteraturePaper

StudyType = Literal[
    "review",
    "clinical_trial",
    "observational_human",
    "animal_preclinical",
    "in_vitro",
    "computational",
    "case_report",
    "unknown",
]
EvidenceLevel = Literal[
    "high",
    "medium",
    "low",
    "mention_only",
    "contradictory",
    "safety_concern",
]
ClinicalRelevance = Literal[
    "direct_disease",
    "related_disease",
    "target_only",
    "molecule_only",
    "unclear",
]

CLINICAL_TRIAL_TERMS = (
    "randomized",
    "randomised",
    "clinical trial",
    "placebo",
    "phase 1",
    "phase i",
    "phase 2",
    "phase ii",
    "phase 3",
    "phase iii",
    "double-blind",
    "controlled trial",
)
OBSERVATIONAL_TERMS = (
    "observational",
    "cohort",
    "case-control",
    "case control",
    "retrospective",
    "prospective",
    "registry",
    "patients",
    "human",
)
ANIMAL_TERMS = (
    "mouse",
    "mice",
    "rat",
    "rats",
    "murine",
    "animal",
    "zebrafish",
    "preclinical",
    "model",
)
IN_VITRO_TERMS = (
    "in vitro",
    "cell line",
    "cultured cells",
    "cell culture",
    "assay",
    "cellular",
)
COMPUTATIONAL_TERMS = (
    "in silico",
    "computational",
    "docking",
    "molecular dynamics",
    "bioinformatics",
    "database",
    "prediction",
)
CASE_REPORT_TERMS = ("case report", "case reports", "case series")
REVIEW_TERMS = ("review", "systematic review", "meta-analysis", "meta analysis")

CLASSIFICATION_METHOD = "rule_based_literature_evidence_classifier"


def classify_evidence(paper: LiteraturePaper, claim: EvidenceClaim) -> EvidenceClaim:
    """Classify a retrieved paper/claim pair without inferring beyond retrieved text."""

    study_type = _study_type(paper)
    clinical_relevance = _clinical_relevance(paper, claim)
    evidence_level = _evidence_level(
        paper=paper,
        claim=claim,
        study_type=study_type,
        clinical_relevance=clinical_relevance,
    )
    metadata = {
        **claim.metadata,
        "study_type": study_type,
        "evidence_level": evidence_level,
        "clinical_relevance": clinical_relevance,
        "classification_method": CLASSIFICATION_METHOD,
    }
    if paper.is_retracted:
        warnings = list(metadata.get("warnings", []))
        warnings.append("Paper is marked as retracted; evidence level should be treated as low.")
        metadata["warnings"] = warnings
    return claim.model_copy(update={"metadata": metadata})


def classify_paper(paper: LiteraturePaper) -> str:
    """Return the broad evidence class already encoded on a literature paper."""

    if paper.is_retracted:
        return "retracted"
    study_type = _study_type(paper)
    if study_type in {"clinical_trial", "observational_human", "case_report"}:
        return "clinical"
    if study_type == "review":
        return "review"
    if study_type in {"animal_preclinical", "in_vitro", "computational"}:
        return "preclinical"
    return "database_or_other"


def _study_type(paper: LiteraturePaper) -> StudyType:
    text = _paper_text(paper)
    if paper.is_review or _contains_any(text, REVIEW_TERMS):
        return "review"
    if _contains_any(text, CLINICAL_TRIAL_TERMS):
        return "clinical_trial"
    if _contains_any(text, CASE_REPORT_TERMS):
        return "case_report"
    if _contains_any(text, IN_VITRO_TERMS):
        return "in_vitro"
    if _contains_any(text, COMPUTATIONAL_TERMS):
        return "computational"
    if paper.is_clinical or _contains_any(text, OBSERVATIONAL_TERMS):
        return "observational_human"
    if paper.is_preclinical or _contains_any(text, ANIMAL_TERMS):
        return "animal_preclinical"
    return "unknown"


def _evidence_level(
    *,
    paper: LiteraturePaper,
    claim: EvidenceClaim,
    study_type: StudyType,
    clinical_relevance: ClinicalRelevance,
) -> EvidenceLevel:
    if claim.direction == "contradictory" or claim.claim_type == "negative_or_contradictory":
        return "contradictory"
    if claim.direction == "safety_concern" or claim.claim_type == "safety_concern":
        return "safety_concern"
    if claim.claim_type == "mention_only":
        return "mention_only"
    if paper.is_retracted:
        return "low"
    if study_type == "clinical_trial" and clinical_relevance == "direct_disease":
        return "high"
    if study_type in {"review", "observational_human", "animal_preclinical"}:
        return "medium"
    if study_type in {"in_vitro", "computational", "case_report"}:
        return "low"
    return "low"


def _clinical_relevance(
    paper: LiteraturePaper,
    claim: EvidenceClaim,
) -> ClinicalRelevance:
    text = _paper_text(paper)
    matched_entities = claim.metadata.get("matched_entities", {})
    disease_match = _metadata_entity_match(matched_entities, "disease") or _contains_term(
        text, claim.disease_name
    )
    target_match = _metadata_entity_match(matched_entities, "target") or _contains_term(
        text, claim.target_symbol
    )
    molecule_match = _metadata_entity_match(matched_entities, "molecule") or _contains_term(
        text, claim.candidate_name
    )

    if disease_match:
        return "direct_disease"
    if target_match:
        return "target_only"
    if molecule_match:
        return "molecule_only"
    return "unclear"


def _metadata_entity_match(value: object, key: str) -> bool:
    if not isinstance(value, dict):
        return False
    matches = value.get(key)
    return isinstance(matches, list) and bool(matches)


def _paper_text(paper: LiteraturePaper) -> str:
    publication_type = paper.publication_type or ""
    metadata_types = paper.metadata.get("publication_types", [])
    if isinstance(metadata_types, list):
        metadata_text = " ".join(str(value) for value in metadata_types)
    else:
        metadata_text = str(metadata_types or "")
    return " ".join(
        part
        for part in (paper.title, paper.abstract or "", publication_type, metadata_text)
        if part
    ).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_phrase(text, term) for term in terms)


def _contains_term(text: str, term: str | None) -> bool:
    return bool(term) and _contains_phrase(text, str(term).lower())


def _contains_phrase(text: str, term: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None
