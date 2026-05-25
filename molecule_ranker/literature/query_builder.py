from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from molecule_ranker.literature.schemas import LiteratureQuery, QueryType
from molecule_ranker.schemas import Disease, MoleculeCandidate, Target

DEFAULT_MAX_LITERATURE_QUERIES = 50
DEFAULT_MAX_PAPERS_PER_QUERY = 5
DEFAULT_MAX_TARGETS_FOR_LITERATURE = 10
DEFAULT_MAX_CANDIDATES_FOR_LITERATURE = 10


def build_literature_query(
    *,
    query_id: str,
    disease_name: str,
    query_type: QueryType,
    terms: Iterable[str],
    target_symbol: str | None = None,
    target_name: str | None = None,
    molecule_name: str | None = None,
    molecule_identifiers: dict[str, str] | None = None,
    max_results: int = 10,
) -> LiteratureQuery:
    """Build a traceable Boolean query from explicit source terms."""

    cleaned_terms = [term.strip() for term in terms if term and term.strip()]
    query_text = " AND ".join(_quote(term) for term in cleaned_terms)
    return LiteratureQuery(
        query_id=query_id,
        disease_name=disease_name,
        target_symbol=target_symbol,
        target_name=target_name,
        molecule_name=molecule_name,
        molecule_identifiers=molecule_identifiers or {},
        query_text=query_text,
        query_type=query_type,
        max_results=max_results,
        metadata={"term_count": len(cleaned_terms)},
    )


def build_literature_queries(
    *,
    disease: Disease,
    targets: list[Target],
    candidates: list[MoleculeCandidate],
    config: Mapping[str, Any] | object | None = None,
) -> list[LiteratureQuery]:
    """Generate conservative, auditable literature queries from retrieved entities."""

    limits = _query_limits(config)
    selected_targets = sorted(
        targets,
        key=lambda target: target.disease_relevance_score,
        reverse=True,
    )[: limits["max_targets_for_literature"]]
    selected_candidates = candidates[: limits["max_candidates_for_literature"]]
    queries: list[LiteratureQuery] = []
    seen_query_text: set[str] = set()

    def add_query(
        *,
        query_type: QueryType,
        query_text: str,
        target: Target | None = None,
        candidate: MoleculeCandidate | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if len(queries) >= limits["max_literature_queries"]:
            return
        if query_text in seen_query_text:
            return
        seen_query_text.add(query_text)
        query = LiteratureQuery(
            query_id=f"lit-{len(queries) + 1:04d}",
            disease_name=disease.canonical_name,
            target_symbol=target.symbol if target is not None else None,
            target_name=target.name if target is not None else None,
            molecule_name=candidate.name if candidate is not None else None,
            molecule_identifiers=dict(candidate.identifiers) if candidate is not None else {},
            query_text=query_text,
            query_type=query_type,
            max_results=limits["max_papers_per_query"],
            metadata={
                "generated_by": "literature.query_builder",
                "query_type": query_type,
                "disease_identifiers": dict(disease.identifiers),
                "target_identifiers": dict(target.identifiers) if target is not None else {},
                "molecule_identifiers": (
                    dict(candidate.identifiers) if candidate is not None else {}
                ),
                **(metadata or {}),
            },
        )
        queries.append(query)

    for target in selected_targets:
        add_query(
            query_type="disease_target",
            query_text=f"({_term(disease.canonical_name)}) AND ({_target_clause(target)})",
            target=target,
            metadata={"source_terms": [disease.canonical_name, target.symbol, target.name]},
        )

    for candidate in selected_candidates:
        matched_targets = _matched_targets(candidate, selected_targets)
        for target in matched_targets:
            add_query(
                query_type="molecule_target",
                query_text=f"({_molecule_clause(candidate)}) AND ({_target_clause(target)})",
                target=target,
                candidate=candidate,
                metadata={
                    "source_terms": [
                        candidate.name,
                        *_candidate_synonyms(candidate),
                        target.symbol,
                        target.name,
                    ]
                },
            )

        add_query(
            query_type="molecule_disease",
            query_text=f"({_term(candidate.name)}) AND ({_term(disease.canonical_name)})",
            candidate=candidate,
            metadata={"source_terms": [candidate.name, disease.canonical_name]},
        )

        if candidate.mechanism_of_action:
            for target in matched_targets:
                add_query(
                    query_type="mechanism",
                    query_text=(
                        f"({_term(candidate.name)}) AND ({_term(target.symbol)}) AND "
                        "(mechanism OR inhibition OR activation OR agonist OR antagonist)"
                    ),
                    target=target,
                    candidate=candidate,
                    metadata={
                        "source_terms": [
                            candidate.name,
                            target.symbol,
                            candidate.mechanism_of_action,
                        ]
                    },
                )

        add_query(
            query_type="clinical",
            query_text=(
                f"({_term(candidate.name)}) AND ({_term(disease.canonical_name)}) AND "
                "(trial OR clinical OR patient OR phase)"
            ),
            candidate=candidate,
            metadata={"source_terms": [candidate.name, disease.canonical_name]},
        )
        add_query(
            query_type="safety",
            query_text=f"({_term(candidate.name)}) AND (toxicity OR adverse OR safety OR warning)",
            candidate=candidate,
            metadata={"source_terms": [candidate.name]},
        )

    return queries


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value else value


def _query_limits(config: Mapping[str, Any] | object | None) -> dict[str, int]:
    return {
        "max_literature_queries": _config_int(
            config,
            "max_literature_queries",
            _config_int(
                config,
                "max_literature_queries_per_candidate",
                DEFAULT_MAX_LITERATURE_QUERIES,
            ),
        ),
        "max_papers_per_query": _config_int(
            config,
            "max_papers_per_query",
            _config_int(config, "max_literature_results_per_query", DEFAULT_MAX_PAPERS_PER_QUERY),
        ),
        "max_targets_for_literature": _config_int(
            config,
            "max_targets_for_literature",
            DEFAULT_MAX_TARGETS_FOR_LITERATURE,
        ),
        "max_candidates_for_literature": _config_int(
            config,
            "max_candidates_for_literature",
            DEFAULT_MAX_CANDIDATES_FOR_LITERATURE,
        ),
    }


def _config_int(config: Mapping[str, Any] | object | None, key: str, default: int) -> int:
    if config is None:
        return default
    value: Any
    if isinstance(config, Mapping):
        value = config.get(key, default)
    else:
        value = getattr(config, key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, 1)


def _target_clause(target: Target) -> str:
    terms = [target.symbol]
    if target.name and target.name != target.symbol:
        terms.append(target.name)
    return " OR ".join(_term(term) for term in _dedupe_terms(terms))


def _molecule_clause(candidate: MoleculeCandidate) -> str:
    terms = [candidate.name, *_candidate_synonyms(candidate)]
    return " OR ".join(_term(term) for term in _dedupe_terms(terms))


def _term(value: str | None) -> str:
    cleaned = " ".join(str(value or "").split())
    return _quote(cleaned)


def _candidate_synonyms(candidate: MoleculeCandidate) -> list[str]:
    synonyms = candidate.chemical_metadata.get("synonyms")
    if isinstance(synonyms, list):
        return [str(value) for value in synonyms if value not in (None, "")]
    if isinstance(synonyms, str) and synonyms:
        return [synonyms]
    return []


def _matched_targets(
    candidate: MoleculeCandidate,
    targets: list[Target],
) -> list[Target]:
    known = {target.upper() for target in candidate.known_targets}
    return [target for target in targets if target.symbol.upper() in known]


def _dedupe_terms(terms: Iterable[str | None]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = " ".join(str(term or "").split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped
