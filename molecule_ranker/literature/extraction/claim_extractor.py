from __future__ import annotations

import re
from collections.abc import Iterable

from molecule_ranker.literature.schemas import (
    ClaimDirection,
    ClaimType,
    EvidenceClaim,
    LiteraturePaper,
    LiteratureQuery,
    QueryType,
)

SUPPORTIVE_CUES = (
    "inhibits",
    "activates",
    "agonist",
    "antagonist",
    "modulates",
    "binds",
    "reduces",
    "increases",
    "associated with",
    "protects against",
    "improves",
)
CLINICAL_CUES = (
    "clinical trial",
    "randomized",
    "patients",
    "phase",
    "efficacy",
    "safety",
)
SAFETY_CUES = (
    "toxicity",
    "adverse event",
    "warning",
    "hepatotoxicity",
    "cardiotoxicity",
    "mortality",
)
NEGATIVE_CUES = (
    "no significant",
    "failed to",
    "not associated",
    "did not improve",
    "lack of efficacy",
)

EXTRACTION_METHOD = "rule_based_title_abstract_cues"


def extract_claims(
    *,
    paper: LiteraturePaper,
    query: LiteratureQuery,
    disease_name: str,
    target_symbol: str | None = None,
    target_name: str | None = None,
    molecule_name: str | None = None,
    molecule_synonyms: list[str] | None = None,
) -> list[EvidenceClaim]:
    """Extract cautious claims from source-provided title and abstract text only."""

    snippets = _source_snippets(paper)
    if not snippets:
        return []

    text = " ".join(snippets)
    entities = _matched_entities(
        text=text,
        disease_name=disease_name,
        target_symbol=target_symbol,
        target_name=target_name,
        molecule_name=molecule_name,
        molecule_synonyms=molecule_synonyms or [],
    )
    if not _has_required_entity_overlap(query.query_type, entities):
        return []

    cue_groups = _matched_cues(text)
    claim_type, direction = _classify_claim(
        query_type=query.query_type,
        entities=entities,
        cue_groups=cue_groups,
        is_retracted=bool(paper.is_retracted),
    )
    supporting_snippet = _best_supporting_snippet(
        snippets=snippets,
        entities=entities,
        cue_groups=cue_groups,
    )
    if not supporting_snippet:
        return []

    warnings = []
    if paper.is_retracted:
        warnings.append("Paper is marked as retracted; extracted claim confidence set to 0.")

    confidence = _confidence(
        paper=paper,
        entities=entities,
        has_relation_cue=bool(_flatten_cues(cue_groups)),
        warnings=warnings,
    )

    return [
        EvidenceClaim(
            claim_id=f"{query.query_id}:{paper.paper_id}:{claim_type}",
            paper_id=paper.paper_id,
            candidate_name=molecule_name,
            target_symbol=target_symbol,
            disease_name=disease_name,
            claim_type=claim_type,
            claim_text=_claim_text(
                claim_type=claim_type,
                paper=paper,
                query=query,
                is_review=paper.is_review,
                is_retracted=bool(paper.is_retracted),
            ),
            supporting_snippet=supporting_snippet,
            confidence=confidence,
            direction=direction,
            extraction_method=EXTRACTION_METHOD,
            metadata={
                "query_id": query.query_id,
                "query_type": query.query_type,
                "matched_entities": entities,
                "relation_cues": cue_groups,
                "warnings": warnings,
                "source_text_scope": "title_and_abstract",
            },
        )
    ]


def extract_mention_claims(
    *,
    paper: LiteraturePaper,
    candidate_name: str | None,
    target_symbol: str | None,
    disease_name: str | None,
) -> list[EvidenceClaim]:
    """Extract conservative mention-only claims from source-provided title/abstract text."""

    query_type = _legacy_query_type(
        candidate_name=candidate_name,
        target_symbol=target_symbol,
        disease_name=disease_name,
    )
    query = LiteratureQuery(
        query_id=f"{paper.paper_id}:legacy",
        disease_name=disease_name or "",
        target_symbol=target_symbol,
        target_name=None,
        molecule_name=candidate_name,
        molecule_identifiers={},
        query_text=" ".join(
            term for term in (candidate_name, target_symbol, disease_name) if term
        ),
        query_type=query_type,
        max_results=1,
        metadata={"legacy_wrapper": True},
    )
    claims = extract_claims(
        paper=paper,
        query=query,
        disease_name=disease_name or "",
        target_symbol=target_symbol,
        target_name=None,
        molecule_name=candidate_name,
        molecule_synonyms=[],
    )
    return [
        claim.model_copy(
            update={
                "claim_type": "mention_only",
                "direction": "neutral",
                "extraction_method": "rule_based_title_abstract_mention",
            }
        )
        for claim in claims
    ]


def _source_snippets(paper: LiteraturePaper) -> list[str]:
    snippets = [paper.title.strip()] if paper.title.strip() else []
    if paper.abstract:
        snippets.extend(
            snippet.strip()
            for snippet in re.split(r"(?<=[.!?])\s+", paper.abstract)
            if snippet.strip()
        )
    return snippets


def _matched_entities(
    *,
    text: str,
    disease_name: str,
    target_symbol: str | None,
    target_name: str | None,
    molecule_name: str | None,
    molecule_synonyms: Iterable[str],
) -> dict[str, list[str]]:
    return {
        "disease": _matched_terms(text, [disease_name]),
        "target": _matched_terms(text, [target_symbol, target_name]),
        "molecule": _matched_terms(text, [molecule_name, *molecule_synonyms]),
    }


def _matched_terms(text: str, terms: Iterable[str | None]) -> list[str]:
    matched: list[str] = []
    for term in terms:
        if term and term not in matched and _contains_term(text, term):
            matched.append(term)
    return matched


def _contains_term(text: str, term: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _matched_cues(text: str) -> dict[str, list[str]]:
    return {
        "negative_or_contradictory": _matched_terms(text, NEGATIVE_CUES),
        "safety_concern": _matched_terms(text, SAFETY_CUES),
        "clinical": _matched_terms(text, CLINICAL_CUES),
        "supportive_mechanism": _matched_terms(text, SUPPORTIVE_CUES),
    }


def _has_required_entity_overlap(
    query_type: QueryType, entities: dict[str, list[str]]
) -> bool:
    has_disease = bool(entities["disease"])
    has_target = bool(entities["target"])
    has_molecule = bool(entities["molecule"])
    if query_type == "disease_target":
        return has_disease and has_target
    if query_type in {"molecule_target", "mechanism"}:
        return has_molecule and has_target
    if query_type in {"molecule_disease", "clinical"}:
        return has_molecule and has_disease
    if query_type == "safety":
        return has_molecule
    return sum([has_disease, has_target, has_molecule]) >= 2


def _classify_claim(
    *,
    query_type: QueryType,
    entities: dict[str, list[str]],
    cue_groups: dict[str, list[str]],
    is_retracted: bool,
) -> tuple[ClaimType, ClaimDirection]:
    if is_retracted:
        return "mention_only", "neutral"
    if cue_groups["negative_or_contradictory"]:
        return "negative_or_contradictory", "contradictory"
    if cue_groups["safety_concern"]:
        return "safety_concern", "safety_concern"
    if query_type == "clinical" and cue_groups["clinical"]:
        return "clinical_support", "supportive"
    if cue_groups["supportive_mechanism"]:
        if query_type == "mechanism":
            return "mechanism_support", "supportive"
        if entities["molecule"] and entities["target"]:
            return "molecule_target_interaction", "supportive"
        if entities["disease"] and entities["target"]:
            return "disease_target_association", "supportive"
        if entities["molecule"] and entities["disease"]:
            return "molecule_disease_association", "supportive"
    if query_type == "clinical" and cue_groups["clinical"]:
        return "clinical_support", "supportive"
    return "mention_only", "neutral"


def _best_supporting_snippet(
    *,
    snippets: list[str],
    entities: dict[str, list[str]],
    cue_groups: dict[str, list[str]],
) -> str:
    all_entities = [term for terms in entities.values() for term in terms]
    def score(snippet: str) -> tuple[int, int, int]:
        cue_score = (
            4
            * sum(
                1
                for cue in cue_groups["negative_or_contradictory"]
                if _contains_term(snippet, cue)
            )
            + 3
            * sum(
                1 for cue in cue_groups["safety_concern"] if _contains_term(snippet, cue)
            )
            + 2
            * sum(1 for cue in cue_groups["clinical"] if _contains_term(snippet, cue))
            + 2
            * sum(
                1
                for cue in cue_groups["supportive_mechanism"]
                if _contains_term(snippet, cue)
            )
        )
        entity_score = sum(1 for term in all_entities if _contains_term(snippet, term))
        length_penalty = -len(snippet)
        return cue_score, entity_score, length_penalty

    return max(snippets, key=score)


def _flatten_cues(cue_groups: dict[str, list[str]]) -> list[str]:
    return [cue for cues in cue_groups.values() for cue in cues]


def _confidence(
    *,
    paper: LiteraturePaper,
    entities: dict[str, list[str]],
    has_relation_cue: bool,
    warnings: list[str],
) -> float:
    if paper.is_retracted:
        return 0.0

    entity_bucket_count = sum(1 for matches in entities.values() if matches)
    if has_relation_cue and entity_bucket_count == 3:
        confidence = 0.85
    elif has_relation_cue and entity_bucket_count >= 2:
        confidence = 0.65
    else:
        confidence = 0.35

    if paper.is_review:
        warnings.append("Review article; useful context but not direct experimental evidence.")
        confidence = max(0.0, confidence - 0.1)
    return confidence


def _claim_text(
    *,
    claim_type: ClaimType,
    paper: LiteraturePaper,
    query: LiteratureQuery,
    is_review: bool,
    is_retracted: bool,
) -> str:
    if is_retracted:
        return (
            f"{paper.source} paper {paper.paper_id} is retracted; any mention of the "
            "requested entities should not be treated as supporting evidence."
        )
    if claim_type == "mention_only":
        return (
            f"{paper.source} paper {paper.paper_id} mentions requested literature terms; "
            "this requires validation."
        )
    review_note = " in a review article" if is_review else ""
    return (
        f"{paper.source} paper {paper.paper_id}{review_note} {claim_type.replace('_', ' ')} "
        f"for query type {query.query_type}; this suggests an association and requires validation."
    )


def _legacy_query_type(
    *,
    candidate_name: str | None,
    target_symbol: str | None,
    disease_name: str | None,
) -> QueryType:
    if candidate_name and target_symbol:
        return "molecule_target"
    if candidate_name and disease_name:
        return "molecule_disease"
    return "disease_target"
