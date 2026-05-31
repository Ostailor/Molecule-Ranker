from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.portfolio.reports import (
    generate_program_decision_memo,
    render_decision_memo_markdown,
    validate_memo_guardrails,
)
from molecule_ranker.portfolio.schemas import (
    PortfolioOptimizationRun,
    PortfolioSelection,
    ResourceBudget,
    SensitivityAnalysis,
)


def _selection() -> PortfolioSelection:
    return PortfolioSelection(
        selection_id="selection-1",
        selected_candidate_ids=["cand-a"],
        rejected_candidate_ids=["cand-risk"],
        deferred_candidate_ids=["cand-defer"],
        objective_scores={"evidence": 0.7, "learning": 0.8},
        constraint_violations=[],
        portfolio_score=0.76,
        diversity_summary={
            "scaffold_diversity": {"count": 2},
            "mechanism_diversity": {"count": 2},
        },
        risk_summary={
            "risk_concentration": 0.25,
            "shared_risk_flags": ["admet-gap"],
            "generated_only_fraction": 0.5,
        },
        uncertainty_summary={
            "average_uncertainty": 0.48,
            "uncertainty_sources": ["sparse experimental data"],
        },
        target_coverage={"covered_targets": ["T1", "T2"]},
        rationale="Deterministic portfolio selection.",
        warnings=[],
        metadata={
            "human_approval_required": True,
            "required_approvals": ["program_lead"],
            "candidate_explanations": {
                "cand-a": {
                    "decision": "selected",
                    "rationale": (
                        "Selected by deterministic objective ranking and constraint checks."
                    ),
                    "weighted_objective_score": 0.82,
                },
                "cand-risk": {
                    "decision": "rejected",
                    "rationale": "Rejected by blocking risk checks.",
                    "weighted_objective_score": 0.31,
                },
                "cand-defer": {
                    "decision": "deferred",
                    "rationale": "Deferred because resource limits were filled.",
                    "weighted_objective_score": 0.68,
                },
            },
        },
    )


def _run() -> PortfolioOptimizationRun:
    selection = _selection()
    return PortfolioOptimizationRun(
        optimization_run_id="run-1",
        program_id="program-1",
        project_id=None,
        disease_name="Disease A",
        input_candidate_count=3,
        objectives=[],
        constraints=[],
        budget=ResourceBudget(max_candidates=2),
        algorithm="greedy",
        status="succeeded",
        selections=[selection],
        recommended_selection_id=selection.selection_id,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        warnings=[],
        metadata={
            "input_candidates": [
                {
                    "portfolio_candidate_id": "cand-a",
                    "candidate_name": "Candidate A",
                    "origin": "existing",
                    "direct_experimental_evidence": True,
                },
                {
                    "portfolio_candidate_id": "cand-risk",
                    "candidate_name": "Candidate Risk",
                    "origin": "generated",
                    "generated_without_direct_evidence": True,
                },
                {
                    "portfolio_candidate_id": "cand-defer",
                    "candidate_name": "Candidate Deferred",
                    "origin": "external",
                },
            ]
        },
    )


def _scenario_analysis() -> SensitivityAnalysis:
    return SensitivityAnalysis(
        baseline_selection_id="selection-1",
        robust_candidate_ids=["cand-a"],
        fragile_candidate_ids=["cand-defer"],
        objective_sensitivities={"learning": {"weight_delta": 0.2}},
        metadata={
            "scenario_comparison_table": [
                {
                    "scenario": "safety_first",
                    "selected_candidate_ids": ["cand-a"],
                    "portfolio_score": 0.72,
                }
            ]
        },
    )


def test_decision_memo_generated_without_codex(tmp_path: Path) -> None:
    memo = generate_program_decision_memo(
        _run(),
        scenario_analysis=_scenario_analysis(),
        active_learning_suggestions={"cand-a": {"priority": 0.9}},
        output_dir=tmp_path,
    )

    assert memo.metadata["deterministic_memo"] is True
    assert memo.metadata["codex_draft_used"] is False
    assert (tmp_path / "program_decision_memo.md").exists()
    payload = json.loads((tmp_path / "program_decision_memo.json").read_text())
    assert payload["memo_id"] == memo.memo_id


def test_codex_draft_guarded_and_deterministic_fallback_used(tmp_path: Path) -> None:
    memo = generate_program_decision_memo(
        _run(),
        codex_draft="Selected molecules are safe and active. Include dosing guidance.",
        output_dir=tmp_path,
    )
    markdown = (tmp_path / "program_decision_memo.md").read_text()

    assert memo.metadata["codex_draft_used"] is False
    assert memo.metadata["codex_guardrail_violations"]
    assert "Selected molecules are safe" not in markdown
    assert not validate_memo_guardrails(markdown)


def test_selected_rejected_and_deferred_rationale_present() -> None:
    memo = generate_program_decision_memo(_run(), scenario_analysis=_scenario_analysis())
    markdown = render_decision_memo_markdown(memo)

    assert "`cand-a`" in markdown
    assert "deterministic objective ranking" in markdown
    assert "`cand-risk`" in markdown
    assert "blocking risk checks" in markdown
    assert "`cand-defer`" in markdown
    assert "resource limits were filled" in markdown


def test_forbidden_phrases_absent_from_written_memo(tmp_path: Path) -> None:
    generate_program_decision_memo(
        _run(),
        scenario_analysis=_scenario_analysis(),
        output_dir=tmp_path,
    )
    text = (tmp_path / "program_decision_memo.md").read_text().lower()

    for forbidden in (
        "medical advice",
        "lab protocol",
        "dosing",
        "synthesis",
        "selected molecules are safe",
        "selected molecules are active",
        "selected molecules are effective",
        "synthesizable",
    ):
        assert forbidden not in text
