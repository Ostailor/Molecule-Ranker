from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.portfolio.batch_builder import build_portfolio_batch
from molecule_ranker.portfolio.candidate_builder import build_portfolio_candidates
from molecule_ranker.portfolio.objectives import default_objectives
from molecule_ranker.portfolio.optimizer import PortfolioOptimizer
from molecule_ranker.portfolio.reports import (
    generate_program_decision_memo,
    render_decision_memo_markdown,
    render_portfolio_report_markdown,
)
from molecule_ranker.portfolio.scenarios import compare_decision_scenarios, default_scenarios
from molecule_ranker.portfolio.schemas import (
    DecisionScenario,
    Portfolio,
    PortfolioBatch,
    PortfolioCandidate,
    PortfolioConstraint,
    PortfolioSelection,
    Program,
    ResourceBudget,
    SensitivityAnalysis,
    StageGate,
)
from molecule_ranker.portfolio.stage_gates import build_stage_gate
from molecule_ranker.schemas import GeneratedMoleculeHypothesis, MoleculeCandidate
from molecule_ranker.utils import slugify


class PortfolioOptimizationAgent(BaseAgent):
    """Run deterministic V1.4 portfolio optimization before review/report writing."""

    name = "PortfolioOptimizationAgent"

    def __init__(self) -> None:
        super().__init__()
        self._last_metadata: dict[str, Any] = self._disabled_metadata()

    def process(self, context: PipelineContext) -> PipelineContext:
        if not bool(context.config.get("enable_portfolio_optimization", False)):
            self._last_metadata = self._disabled_metadata()
            return context

        portfolio = self._build_portfolio(context)
        algorithm = _portfolio_algorithm(context.config)
        random_seed = _random_seed(context.config)
        run = PortfolioOptimizer(algorithm=algorithm, random_seed=random_seed).optimize(portfolio)
        selection = run.selections[0] if run.selections else None
        scenario_analysis = self._run_scenario_analysis(
            portfolio,
            context.config,
            algorithm=algorithm,
            random_seed=random_seed,
        )
        memo = generate_program_decision_memo(
            run,
            selection,
            scenario_analysis=scenario_analysis,
            candidate_summaries=run.metadata.get("input_candidates"),
            risks=selection.risk_summary if selection is not None else {},
            review_decisions=context.config.get("review_queue"),
            experimental_evidence=context.config.get("experimental_evidence"),
            active_learning_suggestions=context.config.get("active_learning_batch"),
        )

        output_dir = _portfolio_output_dir(context)
        output_dir.mkdir(parents=True, exist_ok=True)
        stage_gates = _build_stage_gate_decisions(portfolio.candidates, selection, context.config)
        batch = _build_default_portfolio_batch(portfolio.candidates, selection)
        portfolio_report = render_portfolio_report_markdown(
            run,
            selection,
            candidates=portfolio.candidates,
            scenario_analysis=scenario_analysis,
            stage_gates=stage_gates,
            batches=[batch],
        )
        artifact_paths = _write_portfolio_artifacts(
            output_dir=output_dir,
            candidates=portfolio.candidates,
            run=run,
            scenario_analysis=scenario_analysis,
            batch=batch,
            stage_gates=stage_gates,
            memo_markdown=render_decision_memo_markdown(memo),
            portfolio_report=portfolio_report,
        )
        payload = {
            "success": True,
            "enabled": True,
            "created_at": datetime.now(UTC).isoformat(),
            "portfolio_candidates": [
                candidate.model_dump(mode="json") for candidate in portfolio.candidates
            ],
            "optimization_run": run.model_dump(mode="json"),
            "scenario_analysis": (
                scenario_analysis.model_dump(mode="json") if scenario_analysis is not None else None
            ),
            "program_decision_memo": memo.model_dump(mode="json"),
            "warnings": list(run.warnings),
            "claim_boundaries": [
                "research_prioritization_only",
                "no_protocols_or_treatment_guidance",
                "deterministic_outputs_only",
            ],
        }
        optimization_path = artifact_paths["portfolio_optimization"]
        optimization_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        memo_path = artifact_paths["program_decision_memo"]

        context.output_dir = output_dir
        context.config["portfolio_optimization"] = {
            "enabled": True,
            "artifact_path": str(optimization_path),
            "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
            "decision_memo_path": str(memo_path),
            "candidate_count": len(portfolio.candidates),
            "selected_candidate_ids": (
                list(selection.selected_candidate_ids) if selection is not None else []
            ),
            "rejected_candidate_ids": (
                list(selection.rejected_candidate_ids) if selection is not None else []
            ),
            "deferred_candidate_ids": (
                list(selection.deferred_candidate_ids) if selection is not None else []
            ),
            "optimization_run": run.model_dump(mode="json"),
            "scenario_analysis": (
                scenario_analysis.model_dump(mode="json") if scenario_analysis is not None else None
            ),
            "program_decision_memo": memo.model_dump(mode="json"),
            "portfolio_batch": batch.model_dump(mode="json"),
            "stage_gate_decisions": [gate.model_dump(mode="json") for gate in stage_gates],
        }
        self._last_metadata = {
            "enabled": True,
            "candidate_count": len(portfolio.candidates),
            "selected_count": len(selection.selected_candidate_ids) if selection else 0,
            "rejected_count": len(selection.rejected_candidate_ids) if selection else 0,
            "scenario_analysis_enabled": scenario_analysis is not None,
            "artifact_path": str(optimization_path),
            "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
            "decision_memo_path": str(memo_path),
            "algorithm": run.algorithm,
            "random_seed": random_seed,
        }
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        metadata = self._last_metadata
        if not metadata.get("enabled", False):
            return "Portfolio optimization disabled."
        return (
            f"Optimized portfolio over {metadata.get('candidate_count', 0)} candidate(s); "
            f"selected {metadata.get('selected_count', 0)}."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        del context
        return dict(self._last_metadata)

    def _build_portfolio(self, context: PipelineContext) -> Portfolio:
        candidates = build_portfolio_candidates(
            existing_candidates=_existing_candidates(context.candidates),
            generated_molecules=_generated_candidates(context),
            experimental_results=_experimental_results(context),
            disease_name=_disease_name(context),
        )
        budget = ResourceBudget(
            budget_id="portfolio-agent-budget",
            name="Portfolio optimization budget",
            max_candidates=_max_candidates(context.config),
        )
        return Portfolio(
            portfolio_id=f"portfolio-agent-{slugify(_disease_name(context) or 'run')}",
            program=_program_from_context(context),
            candidates=candidates,
            objectives=default_objectives(),
            constraints=_constraints_from_config(context.config),
            budget=budget,
            metadata={
                "algorithm": _portfolio_algorithm(context.config),
                "random_seed": _random_seed(context.config),
                "project_id": context.config.get("project_id"),
                "deterministic_validation": True,
                "codex_generated_selection": False,
            },
        )

    def _run_scenario_analysis(
        self,
        portfolio: Portfolio,
        config: dict[str, Any],
        *,
        algorithm: str,
        random_seed: int,
    ) -> SensitivityAnalysis | None:
        scenarios = _configured_scenarios(config)
        if not scenarios:
            return None
        return compare_decision_scenarios(
            portfolio,
            scenarios,
            algorithm=algorithm,
            random_seed=random_seed,
        )

    def _disabled_metadata(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "candidate_count": 0,
            "selected_count": 0,
            "scenario_analysis_enabled": False,
        }


def _existing_candidates(candidates: Sequence[MoleculeCandidate]) -> list[MoleculeCandidate]:
    return [candidate for candidate in candidates if candidate.origin != "generated"]


def _generated_candidates(
    context: PipelineContext,
) -> list[GeneratedMoleculeHypothesis]:
    return list(context.generated_candidates)


def _experimental_results(context: PipelineContext) -> list[AssayResult]:
    payload = context.config.get("experimental_evidence")
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    parsed: list[AssayResult] = []
    for item in results:
        if isinstance(item, AssayResult):
            parsed.append(item)
        elif isinstance(item, dict):
            parsed.append(AssayResult.model_validate(item))
    return parsed


def _program_from_context(context: PipelineContext) -> Program:
    disease_name = _disease_name(context)
    target_symbols = [target.symbol for target in context.targets]
    program_id = str(
        context.config.get("program_id")
        or context.config.get("project_id")
        or f"program-{slugify(disease_name or context.disease_input)}"
    )
    return Program(
        program_id=program_id,
        name=str(context.config.get("program_name") or disease_name or context.disease_input),
        disease_focus=[disease_name] if disease_name else [],
        target_focus=target_symbols,
        description="Deterministic portfolio optimization program context.",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={
            "source": "PipelineContext",
            "target_count": len(target_symbols),
            "disease_input": context.disease_input,
        },
    )


def _constraints_from_config(config: dict[str, Any]) -> list[PortfolioConstraint]:
    constraints = [
        PortfolioConstraint(
            constraint_id="portfolio-agent-max-candidates",
            name="Portfolio max candidates",
            constraint_type="max_candidates",
            value=_max_candidates(config),
            hard=True,
            violation_action="reject",
            description="Limit deterministic portfolio selection size.",
        ),
        PortfolioConstraint(
            constraint_id="portfolio-agent-max-generated-fraction",
            name="Generated hypothesis fraction",
            constraint_type="max_generated_fraction",
            value=float(config.get("portfolio_max_generated_fraction", 0.4)),
            hard=True,
            violation_action="reject",
            description="Limit generated-only concentration in portfolio selections.",
        ),
        PortfolioConstraint(
            constraint_id="portfolio-agent-max-generated-count",
            name="Generated hypothesis count",
            constraint_type="max_generated_candidates",
            value=max(
                0,
                int(
                    _max_candidates(config)
                    * float(config.get("portfolio_max_generated_fraction", 0.4))
                ),
            ),
            hard=True,
            violation_action="reject",
            description="Limit generated-only candidate count in portfolio selections.",
        ),
    ]
    if config.get("portfolio_min_target_coverage") is not None:
        constraints.append(
            PortfolioConstraint(
                constraint_id="portfolio-agent-min-target-coverage",
                name="Minimum target coverage",
                constraint_type="min_target_coverage",
                value=int(config["portfolio_min_target_coverage"]),
                hard=False,
                violation_action="warn",
                description="Warn when target coverage is below the configured minimum.",
            )
        )
    if bool(config.get("portfolio_require_review_for_generated", True)):
        constraints.append(
            PortfolioConstraint(
                constraint_id="portfolio-agent-generated-review",
                name="Generated hypothesis review approval",
                constraint_type="require_review_approval_for_generated",
                value=True,
                hard=True,
                violation_action="reject",
                description="Generated hypotheses require review approval by default.",
            )
        )
    if bool(config.get("portfolio_exclude_critical_risk", True)):
        constraints.append(
            PortfolioConstraint(
                constraint_id="portfolio-agent-exclude-critical-risk",
                name="Exclude critical risk",
                constraint_type="exclude_critical_developability_risk",
                value=True,
                hard=True,
                violation_action="reject",
                description="Exclude candidates with critical developability risk annotations.",
            )
        )
    return constraints


def _configured_scenarios(config: dict[str, Any]) -> list[DecisionScenario]:
    raw = config.get("portfolio_scenarios") or []
    scenario_ids = [str(item) for item in raw] if isinstance(raw, list) else [str(raw)]
    if not scenario_ids:
        return []
    all_scenarios = default_scenarios()
    if "all" in {scenario_id.lower() for scenario_id in scenario_ids}:
        return all_scenarios
    by_id = {scenario.scenario_id: scenario for scenario in all_scenarios}
    return [by_id[scenario_id] for scenario_id in scenario_ids if scenario_id in by_id]


def _build_default_portfolio_batch(
    candidates: list[PortfolioCandidate],
    selection: PortfolioSelection | None,
) -> PortfolioBatch:
    return build_portfolio_batch(
        candidates,
        batch_type="expert_review_batch",
        selection=selection,
        exclude_high_risk=True,
    )


def _build_stage_gate_decisions(
    candidates: list[PortfolioCandidate],
    selection: PortfolioSelection | None,
    config: dict[str, Any],
) -> list[StageGate]:
    selected_ids = set(selection.selected_candidate_ids if selection is not None else [])
    rejected_ids = set(selection.rejected_candidate_ids if selection is not None else [])
    deferred_ids = set(selection.deferred_candidate_ids if selection is not None else [])
    to_stage = str(config.get("portfolio_stage_gate_to_stage") or "expert_review")
    decisions: list[StageGate] = []
    for candidate in candidates:
        status = "selected" if candidate.portfolio_candidate_id in selected_ids else None
        if candidate.portfolio_candidate_id in rejected_ids:
            status = "rejected"
        elif candidate.portfolio_candidate_id in deferred_ids:
            status = "deferred"
        annotated = candidate.model_copy(
            update={
                "metadata": {
                    **candidate.metadata,
                    "portfolio_selection_status": status or "not_selected",
                }
            },
            deep=True,
        )
        decisions.append(
            build_stage_gate(
                annotated,
                from_stage=str(candidate.metadata.get("stage") or "computational_triage"),
                to_stage=to_stage,
                require_human_approval=False,
            )
        )
    return decisions


def _write_portfolio_artifacts(
    *,
    output_dir: Path,
    candidates: list[PortfolioCandidate],
    run: Any,
    scenario_analysis: SensitivityAnalysis | None,
    batch: PortfolioBatch,
    stage_gates: list[StageGate],
    memo_markdown: str,
    portfolio_report: str,
) -> dict[str, Path]:
    artifact_paths = {
        "portfolio_candidates": output_dir / "portfolio_candidates.json",
        "portfolio_optimization": output_dir / "portfolio_optimization.json",
        "scenario_analysis": output_dir / "scenario_analysis.json",
        "portfolio_batch": output_dir / "portfolio_batch.json",
        "stage_gate_decisions": output_dir / "stage_gate_decisions.json",
        "program_decision_memo": output_dir / "program_decision_memo.md",
        "portfolio_report": output_dir / "portfolio_report.md",
    }
    _write_json(
        artifact_paths["portfolio_candidates"],
        {
            "portfolio_candidates": [
                candidate.model_dump(mode="json") for candidate in candidates
            ],
            "candidate_count": len(candidates),
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    _write_json(artifact_paths["portfolio_optimization"], run.model_dump(mode="json"))
    _write_json(
        artifact_paths["scenario_analysis"],
        {
            "scenario_analysis": (
                scenario_analysis.model_dump(mode="json")
                if scenario_analysis is not None
                else None
            ),
            "scenario_analysis_included": scenario_analysis is not None,
        },
    )
    _write_json(artifact_paths["portfolio_batch"], batch.model_dump(mode="json"))
    _write_json(
        artifact_paths["stage_gate_decisions"],
        {
            "stage_gate_decisions": [gate.model_dump(mode="json") for gate in stage_gates],
            "decision_count": len(stage_gates),
        },
    )
    artifact_paths["program_decision_memo"].write_text(memo_markdown.rstrip() + "\n")
    artifact_paths["portfolio_report"].write_text(portfolio_report.rstrip() + "\n")
    return artifact_paths


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _portfolio_algorithm(config: dict[str, Any]) -> str:
    requested = str(config.get("portfolio_algorithm") or "greedy")
    if requested == "scenario_comparison":
        return "greedy"
    return requested


def _random_seed(config: dict[str, Any]) -> int:
    value = config.get("portfolio_random_seed")
    return int(value) if value is not None else 0


def _max_candidates(config: dict[str, Any]) -> int:
    return max(0, int(config.get("portfolio_max_candidates", 10)))


def _disease_name(context: PipelineContext) -> str | None:
    return context.disease.canonical_name if context.disease is not None else None


def _portfolio_output_dir(context: PipelineContext) -> Path:
    if context.output_dir is not None:
        return context.output_dir
    results_dir = Path(str(context.config.get("results_dir") or "results"))
    disease_name = _disease_name(context)
    if disease_name:
        return results_dir / slugify(disease_name)
    return results_dir


__all__ = ["PortfolioOptimizationAgent"]
