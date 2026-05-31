from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.portfolio_optimization import PortfolioOptimizationAgent
from molecule_ranker.portfolio.reports import validate_memo_guardrails
from molecule_ranker.schemas import Disease, GeneratedMoleculeHypothesis, MoleculeCandidate, Target


def _disease() -> Disease:
    return Disease(input_name="Disease A", canonical_name="Disease A")


def _target(symbol: str) -> Target:
    return Target(symbol=symbol, disease_relevance_score=0.8)


def _candidate(
    name: str,
    *,
    target: str = "T1",
    score: float = 0.7,
    warnings: list[str] | None = None,
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        origin="existing",
        identifiers={"chembl": name},
        known_targets=[target],
        mechanism_of_action=f"{target} modulation",
        chemical_metadata={
            "canonical_smiles": "CCO",
            "chemical_series": f"series-{name}",
            "scaffold_id": f"scaffold-{name}",
        },
        score=score,
        warnings=warnings or [],
    )


def _generated(
    name: str,
    *,
    target: str = "T2",
    score: float = 0.8,
) -> GeneratedMoleculeHypothesis:
    return GeneratedMoleculeHypothesis(
        name=name,
        canonical_smiles="CCN",
        target_symbol=target,
        generation_score=score,
        min_seed_similarity=0.2,
        max_seed_similarity=0.5,
        mean_seed_similarity=0.35,
        descriptors={"novelty_score": 0.7},
        trace={"diversity_cluster": f"cluster-{name}", "uncertainty_score": 0.6},
        warnings=[],
    )


def _context(tmp_path: Path, *, enabled: bool = True) -> PipelineContext:
    return PipelineContext(
        disease_input="Disease A",
        disease=_disease(),
        targets=[_target("T1"), _target("T2")],
        candidates=[
            _candidate("existing-a", target="T1", score=0.85),
            _candidate("existing-b", target="T2", score=0.78),
        ],
        generated_candidates=[],
        config={
            "results_dir": str(tmp_path),
            "enable_portfolio_optimization": enabled,
            "portfolio_max_candidates": 3,
            "portfolio_max_generated_fraction": 0.5,
        },
        output_dir=tmp_path,
    )


def test_portfolio_optimization_agent_disabled_noop(tmp_path: Path) -> None:
    context = _context(tmp_path, enabled=False)
    updated = PortfolioOptimizationAgent().run(context)

    assert "portfolio_optimization" not in updated.config
    assert updated.traces[-1].agent_name == "PortfolioOptimizationAgent"
    assert updated.traces[-1].metadata["enabled"] is False
    assert not (tmp_path / "portfolio_optimization.json").exists()


def test_portfolio_optimization_agent_enabled_writes_artifacts(tmp_path: Path) -> None:
    updated = PortfolioOptimizationAgent().run(_context(tmp_path))

    assert updated.config["portfolio_optimization"]["enabled"] is True
    for artifact_name in (
        "portfolio_candidates.json",
        "portfolio_optimization.json",
        "scenario_analysis.json",
        "portfolio_batch.json",
        "stage_gate_decisions.json",
        "program_decision_memo.md",
        "portfolio_report.md",
    ):
        assert (tmp_path / artifact_name).exists(), artifact_name
    payload = json.loads((tmp_path / "portfolio_optimization.json").read_text())
    assert payload["optimization_run"]["status"] == "succeeded"
    assert payload["program_decision_memo"]["optimization_run_id"]
    assert json.loads((tmp_path / "portfolio_candidates.json").read_text())["candidate_count"] == 2
    assert json.loads((tmp_path / "portfolio_batch.json").read_text())["batch_type"]
    assert json.loads((tmp_path / "stage_gate_decisions.json").read_text())["decision_count"] == 2


def test_portfolio_report_includes_rationale_and_safe_sections(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.candidates.append(
        _candidate(
            "critical-risk",
            target="T1",
            score=0.99,
            warnings=["critical developability risk"],
        )
    )
    context.config["portfolio_scenarios"] = ["conservative", "exploration"]

    PortfolioOptimizationAgent().run(context)
    report = (tmp_path / "portfolio_report.md").read_text()

    for section in (
        "## Portfolio Summary",
        "## Objectives and Constraints",
        "## Selected Candidates",
        "## Rejected and Deferred Candidates",
        "## Target Coverage",
        "## Scaffold/Chemical Series Diversity",
        "## Risk Concentration",
        "## Uncertainty and Scenario Analysis",
        "## Stage-Gate Decisions",
        "## Recommended High-Level Next Actions",
        "## Limitations",
    ):
        assert section in report
    assert "Selected by deterministic" in report
    assert "Rejected by deterministic risk or blocking-risk checks" in report
    assert "Scenario analysis included" in report
    assert validate_memo_guardrails(report) == []


def test_portfolio_optimization_agent_enforces_generated_fraction(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.generated_candidates = [
        _generated("gen-a", score=0.95),
        _generated("gen-b", score=0.9),
    ]
    context.config["portfolio_require_review_for_generated"] = False
    context.config["portfolio_max_candidates"] = 4
    context.config["portfolio_max_generated_fraction"] = 0.5

    updated = PortfolioOptimizationAgent().run(context)
    selected = updated.config["portfolio_optimization"]["selected_candidate_ids"]
    generated_selected = [
        candidate_id for candidate_id in selected if candidate_id.startswith("gen-")
    ]

    assert len(generated_selected) / max(1, len(selected)) <= 0.5


def test_portfolio_optimization_agent_excludes_critical_risk(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.candidates.append(
        _candidate(
            "critical-risk",
            target="T1",
            score=0.99,
            warnings=["critical developability risk"],
        )
    )

    updated = PortfolioOptimizationAgent().run(context)

    assert "critical-risk" not in updated.config["portfolio_optimization"]["selected_candidate_ids"]
    assert "critical-risk" in updated.config["portfolio_optimization"]["rejected_candidate_ids"]


def test_portfolio_optimization_agent_stores_scenario_analysis(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.config["portfolio_scenarios"] = ["conservative", "exploration"]

    updated = PortfolioOptimizationAgent().run(context)
    scenario_analysis = updated.config["portfolio_optimization"]["scenario_analysis"]

    assert scenario_analysis is not None
    assert scenario_analysis["metadata"]["deterministic_scenario_analysis"] is True
    assert set(scenario_analysis["metadata"]["scenario_specific_selected_candidates"]) == {
        "conservative",
        "exploration",
    }
