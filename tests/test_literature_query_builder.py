from __future__ import annotations

from molecule_ranker.literature.query_builder import build_literature_queries
from molecule_ranker.schemas import Disease, MoleculeCandidate, Target


def _disease() -> Disease:
    return Disease(
        input_name="PD",
        canonical_name="Parkinson disease",
        synonyms=["Parkinson's disease"],
        identifiers={"mondo": "MONDO:0005180"},
    )


def _target(symbol: str, name: str, score: float) -> Target:
    return Target(
        symbol=symbol,
        name=name,
        identifiers={"ensembl": f"ENSG_{symbol}"},
        disease_relevance_score=score,
        evidence=[],
    )


def _candidate(
    name: str,
    known_targets: list[str],
    *,
    mechanism: str | None = None,
    synonyms: list[str] | None = None,
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": f"CHEMBL_{name.upper()}"},
        known_targets=known_targets,
        mechanism_of_action=mechanism,
        chemical_metadata={"synonyms": synonyms or []},
        evidence=[],
    )


def test_build_literature_queries_generates_all_conservative_query_types() -> None:
    targets = [
        _target("SNCA", "alpha-synuclein", 0.7),
        _target("MAOB", "Monoamine oxidase B", 0.9),
    ]
    candidates = [
        _candidate(
            "Rasagiline",
            ["MAOB"],
            mechanism="MAOB inhibitor",
            synonyms=["Azilect", "Rasagiline mesylate"],
        )
    ]

    queries = build_literature_queries(
        disease=_disease(),
        targets=targets,
        candidates=candidates,
        config={
            "max_literature_queries": 20,
            "max_papers_per_query": 7,
            "max_targets_for_literature": 1,
            "max_candidates_for_literature": 1,
        },
    )

    by_type = {query.query_type: query for query in queries}
    assert set(by_type) == {
        "disease_target",
        "molecule_target",
        "molecule_disease",
        "mechanism",
        "clinical",
        "safety",
    }
    assert by_type["disease_target"].query_text == (
        '("Parkinson disease") AND (MAOB OR "Monoamine oxidase B")'
    )
    assert by_type["molecule_target"].query_text == (
        '(Rasagiline OR Azilect OR "Rasagiline mesylate") AND '
        '(MAOB OR "Monoamine oxidase B")'
    )
    assert by_type["molecule_disease"].query_text == (
        '(Rasagiline) AND ("Parkinson disease")'
    )
    assert by_type["mechanism"].query_text == (
        "(Rasagiline) AND (MAOB) AND "
        "(mechanism OR inhibition OR activation OR agonist OR antagonist)"
    )
    assert by_type["clinical"].query_text == (
        '(Rasagiline) AND ("Parkinson disease") AND '
        "(trial OR clinical OR patient OR phase)"
    )
    assert by_type["safety"].query_text == (
        "(Rasagiline) AND (toxicity OR adverse OR safety OR warning)"
    )
    assert all(query.max_results == 7 for query in queries)
    assert all(query.metadata["generated_by"] == "literature.query_builder" for query in queries)
    assert by_type["molecule_target"].metadata["molecule_identifiers"]["chembl"].startswith(
        "CHEMBL_"
    )


def test_build_literature_queries_respects_limits_and_deduplicates_query_text() -> None:
    duplicate_targets = [
        _target("MAOB", "Monoamine oxidase B", 0.9),
        _target("MAOB", "Monoamine oxidase B", 0.8),
    ]
    duplicate_candidates = [
        _candidate("Rasagiline", ["MAOB"], mechanism="MAOB inhibitor"),
        _candidate("Rasagiline", ["MAOB"], mechanism="MAOB inhibitor"),
    ]

    queries = build_literature_queries(
        disease=_disease(),
        targets=duplicate_targets,
        candidates=duplicate_candidates,
        config={
            "max_literature_queries": 3,
            "max_papers_per_query": 4,
            "max_targets_for_literature": 2,
            "max_candidates_for_literature": 2,
        },
    )

    query_texts = [query.query_text for query in queries]
    assert len(queries) == 3
    assert len(query_texts) == len(set(query_texts))
    assert [query.query_id for query in queries] == ["lit-0001", "lit-0002", "lit-0003"]
    assert {query.max_results for query in queries} == {4}


def test_build_literature_queries_skips_mechanism_without_mechanism_text() -> None:
    queries = build_literature_queries(
        disease=_disease(),
        targets=[_target("MAOB", "Monoamine oxidase B", 0.9)],
        candidates=[_candidate("Rasagiline", ["MAOB"], mechanism=None)],
        config={
            "max_literature_queries": 20,
            "max_papers_per_query": 5,
            "max_targets_for_literature": 1,
            "max_candidates_for_literature": 1,
        },
    )

    assert "mechanism" not in {query.query_type for query in queries}
