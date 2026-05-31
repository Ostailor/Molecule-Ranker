from __future__ import annotations

from molecule_ranker.portfolio.diversity import (
    compute_pairwise_similarity_matrix,
    compute_portfolio_diversity,
    suggest_diversity_improvements,
    target_coverage,
)
from molecule_ranker.portfolio.risk import (
    compute_risk_concentration,
    identify_correlated_risks,
)
from molecule_ranker.portfolio.schemas import PortfolioCandidate


def _candidate(
    candidate_id: str,
    *,
    origin: str = "existing",
    target: str = "T1",
    scaffold: str = "scaffold-a",
    smiles: str | None = None,
    fingerprint: list[str] | None = None,
    risk_flags: list[str] | None = None,
    generated_without_direct_evidence: bool | None = None,
) -> PortfolioCandidate:
    generated = origin == "generated"
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate_id,
        origin=origin,  # type: ignore[arg-type]
        canonical_smiles=smiles,
        target_symbols=[target],
        mechanism_label=f"{target} modulation",
        chemical_series_id=scaffold,
        scaffold_id=scaffold,
        evidence_score=None if generated else 0.7,
        experiment_readiness_score=0.6,
        uncertainty_score=0.8 if generated else 0.4,
        diversity_features={"fingerprint": fingerprint or []},
        risk_flags=list(risk_flags or []),
        blocking_risks=[],
        direct_experimental_evidence=False,
        generated_without_direct_evidence=(
            generated
            if generated_without_direct_evidence is None
            else generated_without_direct_evidence
        ),
        metadata={},
    )


def test_near_duplicate_molecules_reduce_diversity() -> None:
    near_duplicate = [
        _candidate(
            "a",
            target="T1",
            scaffold="scaffold-a",
            smiles="CCO",
            fingerprint=["1", "2", "3"],
        ),
        _candidate(
            "b",
            target="T1",
            scaffold="scaffold-a",
            smiles="CCO",
            fingerprint=["1", "2", "3"],
        ),
    ]
    diverse = [
        _candidate("a", target="T1", scaffold="scaffold-a", fingerprint=["1", "2", "3"]),
        _candidate("c", target="T2", scaffold="scaffold-c", fingerprint=["8", "9", "10"]),
    ]

    near_duplicate_summary = compute_portfolio_diversity(near_duplicate)
    diverse_summary = compute_portfolio_diversity(diverse)
    matrix = compute_pairwise_similarity_matrix(near_duplicate)

    assert matrix["a"]["b"] == 1.0
    assert (
        near_duplicate_summary["overall_diversity_score"]
        < diverse_summary["overall_diversity_score"]
    )
    assert near_duplicate_summary["near_duplicate_pairs"]
    assert "near_duplicate_candidates_reduce_diversity" in near_duplicate_summary["warnings"]


def test_target_coverage_computed() -> None:
    candidates = [
        _candidate("a", target="T1"),
        _candidate("b", target="T2", scaffold="scaffold-b"),
        _candidate("c", target="T2", scaffold="scaffold-c"),
    ]

    coverage = target_coverage(candidates)
    diversity = compute_portfolio_diversity(candidates)

    assert coverage == {"covered_targets": ["T1", "T2"], "covered_target_count": 2}
    assert diversity["target_coverage"]["covered_target_count"] == 2
    assert diversity["category_counts"]["targets"] == {"T1": 1, "T2": 2}


def test_correlated_safety_flags_identified() -> None:
    candidates = [
        _candidate("a", risk_flags=["toxicity_alert"], scaffold="scaffold-a"),
        _candidate("b", risk_flags=["toxicity_alert"], scaffold="scaffold-b", target="T2"),
        _candidate("c", risk_flags=["reactive_group"], scaffold="scaffold-c", target="T3"),
    ]

    correlated = identify_correlated_risks(candidates)

    assert "correlated_safety_or_alert_flags" in correlated["warnings"]
    assert any(
        cluster["risk_dimension"] == "shared_alert_or_toxicophore"
        and cluster["mode"] == "toxicity_alert"
        and cluster["candidate_ids"] == ["a", "b"]
        for cluster in correlated["clusters"]
    )


def test_generated_only_concentration_flagged() -> None:
    candidates = [
        _candidate("g1", origin="generated", target="T1", scaffold="scaffold-a"),
        _candidate("g2", origin="generated", target="T2", scaffold="scaffold-b"),
        _candidate("existing", origin="existing", target="T3", scaffold="scaffold-c"),
    ]

    concentration = compute_risk_concentration(candidates)
    correlated = identify_correlated_risks(candidates)

    assert concentration["dimension_fractions"]["generated_only_fraction"] > 0.5
    assert "generated_only_concentration" in concentration["warnings"]
    assert any(
        cluster["risk_dimension"] == "shared_generated_only_status"
        for cluster in correlated["clusters"]
    )


def test_suggest_diversity_improvements_prioritizes_new_targets_and_scaffolds() -> None:
    selection = [_candidate("a", target="T1", scaffold="scaffold-a", fingerprint=["1", "2"])]
    pool = [
        *selection,
        _candidate("b", target="T1", scaffold="scaffold-a", fingerprint=["1", "2"]),
        _candidate("c", target="T2", scaffold="scaffold-c", fingerprint=["8", "9"]),
    ]

    suggestions = suggest_diversity_improvements(selection, pool)

    assert suggestions[0]["candidate_id"] == "c"
    assert "adds_underrepresented_target" in suggestions[0]["reasons"]
    assert "adds_scaffold_or_series" in suggestions[0]["reasons"]
