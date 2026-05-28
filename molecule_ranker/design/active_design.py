from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.generation.schemas import GeneratedMolecule, GenerationObjective

ActiveDesignStrategy = Literal[
    "exploit",
    "explore",
    "risk_reduction",
    "diversity",
    "uncertainty",
    "balanced",
]
NextGenerationFocus = Literal[
    "exploit_scaffold",
    "explore_new_scaffold",
    "reduce_toxicity_risk",
    "improve_solubility",
    "improve_diversity",
    "close_experimental_gap",
]

_ALLOWED_STRATEGIES: set[str] = {
    "exploit",
    "explore",
    "risk_reduction",
    "diversity",
    "uncertainty",
    "balanced",
}


class DesignPlan(BaseModel):
    """Deterministic next-round design plan produced from active-design signals."""

    design_plan_id: str
    disease_name: str
    target_priorities: list[dict[str, Any]] = Field(default_factory=list)
    design_objectives: list[dict[str, Any]] = Field(default_factory=list)
    seed_strategy: dict[str, Any] = Field(default_factory=dict)
    generator_strategy: dict[str, Any] = Field(default_factory=dict)
    oracle_strategy: dict[str, Any] = Field(default_factory=dict)
    diversity_strategy: dict[str, Any] = Field(default_factory=dict)
    uncertainty_strategy: dict[str, Any] = Field(default_factory=dict)
    experiment_readiness_strategy: dict[str, Any] = Field(default_factory=dict)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    required_followups: list[dict[str, Any]] = Field(default_factory=list)
    codex_task_result_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActiveDesignCandidateSignal(BaseModel):
    molecule_id: str
    canonical_smiles: str
    objective_id: str | None = None
    scaffold_id: str | None = None
    oracle_score: float = Field(ge=0.0, le=1.0)
    exploit_score: float = Field(ge=0.0, le=1.0)
    explore_score: float = Field(ge=0.0, le=1.0)
    risk_reduction_score: float = Field(ge=0.0, le=1.0)
    diversity_score: float = Field(ge=0.0, le=1.0)
    uncertainty_score: float = Field(ge=0.0, le=1.0)
    balanced_score: float = Field(ge=0.0, le=1.0)
    exact_feedback_result_ids: list[str] = Field(default_factory=list)
    positive_feedback_count: int = Field(ge=0)
    negative_feedback_count: int = Field(ge=0)
    safety_feedback_count: int = Field(ge=0)
    experimental_gap_score: float = Field(ge=0.0, le=1.0)
    recommended_focus: NextGenerationFocus
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActiveDesignResult(BaseModel):
    selected_strategy: ActiveDesignStrategy
    suggested_focus: NextGenerationFocus
    selected_candidates: list[ActiveDesignCandidateSignal] = Field(default_factory=list)
    candidate_signals: list[ActiveDesignCandidateSignal] = Field(default_factory=list)
    next_design_plan: DesignPlan
    surrogate_metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActiveLearningDesignPlanner:
    """Plan the next generation round from oracle scores and imported feedback."""

    def plan_next_round(
        self,
        *,
        objectives: Sequence[GenerationObjective | Mapping[str, Any] | Any],
        generated_candidates: Sequence[GeneratedMolecule],
        experimental_results: Sequence[AssayResult] = (),
        strategy: ActiveDesignStrategy | str = "balanced",
        batch_size: int = 8,
        metadata: Mapping[str, Any] | None = None,
    ) -> ActiveDesignResult:
        selected_strategy = self._normalize_strategy(strategy)
        signals = [
            self.score_candidate(candidate, experimental_results=experimental_results)
            for candidate in generated_candidates
        ]
        feedback_used = any(signal.exact_feedback_result_ids for signal in signals)
        safety_feedback = any(signal.safety_feedback_count > 0 for signal in signals)
        if selected_strategy == "balanced" and safety_feedback:
            selected_strategy = "risk_reduction"

        ranked = sorted(
            signals,
            key=lambda signal: self._strategy_score(signal, selected_strategy),
            reverse=True,
        )
        selected = ranked[: max(1, int(batch_size))]
        suggested_focus = self._suggested_focus(
            selected_strategy=selected_strategy,
            selected=selected,
            signals=signals,
            feedback_used=feedback_used,
        )
        surrogate_metadata = self._surrogate_metadata(experimental_results, signals)
        plan = self._next_design_plan(
            objectives=objectives,
            selected=selected,
            selected_strategy=selected_strategy,
            suggested_focus=suggested_focus,
            feedback_used=feedback_used,
            surrogate_metadata=surrogate_metadata,
            metadata=metadata or {},
        )
        return ActiveDesignResult(
            selected_strategy=selected_strategy,
            suggested_focus=suggested_focus,
            selected_candidates=selected,
            candidate_signals=signals,
            next_design_plan=plan,
            surrogate_metadata=surrogate_metadata,
            warnings=self._result_warnings(feedback_used, surrogate_metadata),
            metadata={
                "experimental_feedback_used": feedback_used,
                "selection_basis": "exact_feedback_plus_oracles"
                if feedback_used
                else "oracle_only",
                "experimental_results_apply_only_to_exact_tested_structures": True,
                "no_lab_protocols": True,
                "candidate_count": len(signals),
                "selected_count": len(selected),
            },
        )

    def score_candidate(
        self,
        candidate: GeneratedMolecule,
        *,
        experimental_results: Sequence[AssayResult],
    ) -> ActiveDesignCandidateSignal:
        exact_results = self._exact_results(candidate, experimental_results)
        positive_count = sum(1 for result in exact_results if self._positive_feedback(result))
        negative_count = sum(1 for result in exact_results if self._negative_feedback(result))
        safety_count = sum(1 for result in exact_results if self._safety_feedback(result))
        oracle_score = self._oracle_score(candidate)
        novelty_score = self._novelty_score(candidate)
        diversity_score = self._diversity_score(candidate)
        uncertainty_score = self._uncertainty_score(candidate)
        risk_score = self._risk_score(candidate, safety_count=safety_count)
        experimental_gap_score = 0.0 if exact_results else 1.0

        exploit_score = self._clamp(
            0.62 * oracle_score
            + 0.18 * (positive_count > 0)
            - 0.22 * min(negative_count, 2)
            - 0.28 * min(safety_count, 1)
        )
        explore_score = self._clamp(
            0.42 * novelty_score
            + 0.28 * diversity_score
            + 0.20 * experimental_gap_score
            + 0.10 * oracle_score
            - 0.18 * risk_score
        )
        risk_reduction_score = self._clamp(
            0.45 * risk_score
            + 0.35 * min(safety_count, 1)
            + 0.12 * (1.0 - oracle_score)
            + 0.08 * negative_count
        )
        uncertainty_focus_score = self._clamp(
            0.62 * uncertainty_score
            + 0.25 * experimental_gap_score
            + 0.13 * diversity_score
            - 0.20 * risk_score
        )
        balanced_score = self._clamp(
            0.24 * exploit_score
            + 0.20 * explore_score
            + 0.18 * diversity_score
            + 0.18 * uncertainty_focus_score
            + 0.12 * experimental_gap_score
            - 0.08 * risk_score
        )
        return ActiveDesignCandidateSignal(
            molecule_id=candidate.generated_id,
            canonical_smiles=candidate.canonical_smiles,
            objective_id=candidate.objective_id,
            scaffold_id=self._scaffold_id(candidate),
            oracle_score=round(oracle_score, 3),
            exploit_score=round(exploit_score, 3),
            explore_score=round(explore_score, 3),
            risk_reduction_score=round(risk_reduction_score, 3),
            diversity_score=round(diversity_score, 3),
            uncertainty_score=round(uncertainty_focus_score, 3),
            balanced_score=round(balanced_score, 3),
            exact_feedback_result_ids=[result.result_id for result in exact_results],
            positive_feedback_count=positive_count,
            negative_feedback_count=negative_count,
            safety_feedback_count=safety_count,
            experimental_gap_score=round(experimental_gap_score, 3),
            recommended_focus=self._candidate_focus(
                positive_count=positive_count,
                negative_count=negative_count,
                safety_count=safety_count,
                novelty_score=novelty_score,
                diversity_score=diversity_score,
                uncertainty_score=uncertainty_score,
                experimental_gap_score=experimental_gap_score,
            ),
            warnings=self._candidate_warnings(candidate, exact_results, safety_count),
            metadata={
                "exact_feedback_only": True,
                "surrogate_estimates_are_not_evidence": True,
                "oracle_scores_are_not_experimental_feedback": True,
                "risk_score": round(risk_score, 3),
                "feedback_summary": {
                    "positive": positive_count,
                    "negative": negative_count,
                    "safety": safety_count,
                    "result_ids": [result.result_id for result in exact_results],
                },
            },
        )

    def _next_design_plan(
        self,
        *,
        objectives: Sequence[GenerationObjective | Mapping[str, Any] | Any],
        selected: Sequence[ActiveDesignCandidateSignal],
        selected_strategy: ActiveDesignStrategy,
        suggested_focus: NextGenerationFocus,
        feedback_used: bool,
        surrogate_metadata: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> DesignPlan:
        disease_name = self._disease_name(objectives)
        target_priorities = self._target_priorities(objectives, selected)
        selected_ids = [candidate.molecule_id for candidate in selected]
        preferred_scaffolds = sorted(
            {
                candidate.scaffold_id
                for candidate in selected
                if candidate.scaffold_id and suggested_focus == "exploit_scaffold"
            }
        )
        explore_scaffolds = sorted(
            {
                candidate.scaffold_id
                for candidate in selected
                if candidate.scaffold_id
                and suggested_focus in {"explore_new_scaffold", "improve_diversity"}
            }
        )
        return DesignPlan(
            design_plan_id=self._plan_id(
                disease_name=disease_name,
                strategy=selected_strategy,
                focus=suggested_focus,
                selected_ids=selected_ids,
            ),
            disease_name=disease_name,
            target_priorities=target_priorities,
            design_objectives=[
                self._objective_payload(objective, suggested_focus, selected_strategy)
                for objective in objectives
            ],
            seed_strategy={
                "selected_parent_generated_ids": selected_ids,
                "preferred_scaffolds": preferred_scaffolds,
                "explore_scaffolds": explore_scaffolds,
                "exact_feedback_only": True,
            },
            generator_strategy={
                "strategy": selected_strategy,
                "next_focus": suggested_focus,
                "allowed_focuses": [
                    "exploit scaffold",
                    "explore new scaffold",
                    "reduce toxicity risk",
                    "improve solubility",
                    "improve diversity",
                    "close experimental gap",
                ],
                "no_synthesis_routes": True,
            },
            oracle_strategy={
                "use_oracle_stack": True,
                "emphasize": self._oracle_emphasis(suggested_focus),
                "surrogate_signal_scope": "weak_prioritization_only",
            },
            diversity_strategy={
                "maintain_diverse_batch": suggested_focus
                in {"explore_new_scaffold", "improve_diversity", "balanced"},
                "avoid_duplicate_generated_structures": True,
            },
            uncertainty_strategy={
                "use_uncertainty_for_active_learning": True,
                "uncertainty_is_not_efficacy": True,
            },
            experiment_readiness_strategy={
                "human_review_required": True,
                "review_scope": "expert_review_before_possible_assay_triage",
                "no_lab_protocols": True,
            },
            risks=self._plan_risks(suggested_focus, feedback_used),
            constraints={
                "no_fabricated_experimental_feedback": True,
                "experimental_results_apply_only_to_exact_tested_structures": True,
                "surrogate_estimates_are_not_evidence": True,
                "no_lab_protocols": True,
            },
            required_followups=self._required_followups(suggested_focus),
            codex_task_result_id="deterministic-active-design-v1-1",
            metadata={
                **dict(metadata),
                "generated_at": datetime.now(UTC).isoformat(),
                "active_design_strategy": selected_strategy,
                "next_focus": suggested_focus,
                "experimental_feedback_used": feedback_used,
                "selected_generated_ids": selected_ids,
                "surrogate": dict(surrogate_metadata),
                "surrogate_estimates_are_not_evidence": True,
                "not_biomedical_truth": True,
            },
        )

    def _suggested_focus(
        self,
        *,
        selected_strategy: ActiveDesignStrategy,
        selected: Sequence[ActiveDesignCandidateSignal],
        signals: Sequence[ActiveDesignCandidateSignal],
        feedback_used: bool,
    ) -> NextGenerationFocus:
        if selected_strategy == "risk_reduction":
            if any(signal.safety_feedback_count > 0 for signal in signals):
                return "reduce_toxicity_risk"
            return "improve_solubility"
        if selected_strategy == "exploit":
            return "exploit_scaffold"
        if selected_strategy == "explore":
            return "explore_new_scaffold"
        if selected_strategy == "diversity":
            return "improve_diversity"
        if selected_strategy == "uncertainty":
            return "close_experimental_gap"
        if feedback_used and any(signal.positive_feedback_count > 0 for signal in selected):
            return "exploit_scaffold"
        if (
            selected
            and sum(signal.experimental_gap_score for signal in selected) / len(selected)
            > 0.65
        ):
            return "close_experimental_gap"
        if selected and sum(signal.diversity_score for signal in selected) / len(selected) > 0.75:
            return "improve_diversity"
        return "explore_new_scaffold"

    def _candidate_focus(
        self,
        *,
        positive_count: int,
        negative_count: int,
        safety_count: int,
        novelty_score: float,
        diversity_score: float,
        uncertainty_score: float,
        experimental_gap_score: float,
    ) -> NextGenerationFocus:
        if safety_count:
            return "reduce_toxicity_risk"
        if positive_count and not negative_count:
            return "exploit_scaffold"
        if diversity_score >= 0.78:
            return "improve_diversity"
        if novelty_score >= 0.74:
            return "explore_new_scaffold"
        if uncertainty_score >= 0.65 or experimental_gap_score >= 0.8:
            return "close_experimental_gap"
        return "improve_solubility"

    def _surrogate_metadata(
        self,
        experimental_results: Sequence[AssayResult],
        signals: Sequence[ActiveDesignCandidateSignal],
    ) -> dict[str, Any]:
        exact_result_ids = sorted(
            {result_id for signal in signals for result_id in signal.exact_feedback_result_ids}
        )
        qc_passed = [
            result
            for result in experimental_results
            if result.qc_status == "passed" and result.result_id in set(exact_result_ids)
        ]
        trained = len(qc_passed) >= 8
        return {
            "trained": trained,
            "training_result_count": len(qc_passed),
            "exact_feedback_result_ids": exact_result_ids,
            "surrogate_estimates_are_not_evidence": True,
            "experimental_results_apply_only_to_exact_tested_structures": True,
            "limitations": []
            if trained
            else ["Insufficient exact QC-passed feedback for local surrogate update."],
        }

    def _exact_results(
        self,
        candidate: GeneratedMolecule,
        experimental_results: Sequence[AssayResult],
    ) -> list[AssayResult]:
        matches: list[AssayResult] = []
        for result in experimental_results:
            if result.qc_status == "failed":
                continue
            if result.canonical_smiles and result.canonical_smiles == candidate.canonical_smiles:
                matches.append(result)
                continue
            if result.candidate_id and result.candidate_id == candidate.generated_id:
                matches.append(result)
        return matches

    def _positive_feedback(self, result: AssayResult) -> bool:
        if self._safety_feedback(result):
            return False
        return result.outcome_label == "positive" or result.activity_direction in {
            "active",
            "improved",
        }

    def _negative_feedback(self, result: AssayResult) -> bool:
        return result.outcome_label == "negative" or result.activity_direction in {
            "inactive",
            "worsened",
            "no_effect",
        }

    def _safety_feedback(self, result: AssayResult) -> bool:
        endpoint = result.assay_context.endpoint
        return (
            endpoint.endpoint_category == "safety"
            or result.assay_context.assay_type == "safety"
            or result.activity_direction == "toxic"
        )

    def _oracle_score(self, candidate: GeneratedMolecule) -> float:
        oracle = self._mapping(candidate.metadata.get("oracle_scoring"))
        return self._first_score(
            oracle.get("experiment_worthiness_score"),
            candidate.generation_score,
            candidate.score_breakdown.final_generation_score
            if candidate.score_breakdown
            else None,
            default=0.5,
        )

    def _novelty_score(self, candidate: GeneratedMolecule) -> float:
        breakdown = candidate.score_breakdown
        oracle = self._mapping(candidate.metadata.get("oracle_scoring"))
        components = self._mapping(oracle.get("component_scores"))
        novelty_map = {
            "duplicate": 0.0,
            "near_duplicate": 0.25,
            "close_analog": 0.55,
            "novel_analog": 0.78,
            "distant": 0.88,
        }
        novelty = candidate.novelty.novelty_class if candidate.novelty else "near_duplicate"
        return self._first_score(
            components.get("novelty_score"),
            breakdown.novelty_score if breakdown else None,
            novelty_map.get(str(novelty)),
            default=0.45,
        )

    def _diversity_score(self, candidate: GeneratedMolecule) -> float:
        breakdown = candidate.score_breakdown
        oracle = self._mapping(candidate.metadata.get("oracle_scoring"))
        components = self._mapping(oracle.get("component_scores"))
        return self._first_score(
            components.get("diversity_score"),
            breakdown.diversity_score if breakdown else None,
            default=0.5,
        )

    def _uncertainty_score(self, candidate: GeneratedMolecule) -> float:
        uncertainty = self._mapping(candidate.metadata.get("uncertainty"))
        breakdown = candidate.score_breakdown
        oracle = self._mapping(candidate.metadata.get("oracle_scoring"))
        components = self._mapping(oracle.get("component_scores"))
        return self._first_score(
            uncertainty.get("active_learning_value"),
            components.get("uncertainty_value"),
            breakdown.active_learning_priority_score if breakdown else None,
            default=0.5,
        )

    def _risk_score(self, candidate: GeneratedMolecule, *, safety_count: int) -> float:
        oracle = self._mapping(candidate.metadata.get("oracle_scoring"))
        raw_risk_flags = oracle.get("risk_flags")
        risk_flags = raw_risk_flags if isinstance(raw_risk_flags, list) else []
        risk = 0.0
        risk += min(0.35, 0.08 * len(candidate.validation.pains_or_alerts))
        risk += 0.45 if candidate.validation.rejection_reasons else 0.0
        risk += 0.35 if safety_count else 0.0
        if any("critical" in str(flag).lower() for flag in risk_flags):
            risk += 0.4
        return self._clamp(risk)

    def _strategy_score(
        self,
        signal: ActiveDesignCandidateSignal,
        strategy: ActiveDesignStrategy,
    ) -> float:
        if strategy == "exploit":
            return signal.exploit_score
        if strategy == "explore":
            return signal.explore_score
        if strategy == "risk_reduction":
            return signal.risk_reduction_score
        if strategy == "diversity":
            return signal.diversity_score
        if strategy == "uncertainty":
            return signal.uncertainty_score
        return signal.balanced_score

    def _normalize_strategy(self, strategy: ActiveDesignStrategy | str) -> ActiveDesignStrategy:
        value = str(strategy).lower()
        if value not in _ALLOWED_STRATEGIES:
            value = "balanced"
        return value  # type: ignore[return-value]

    def _target_priorities(
        self,
        objectives: Sequence[GenerationObjective | Mapping[str, Any] | Any],
        selected: Sequence[ActiveDesignCandidateSignal],
    ) -> list[dict[str, Any]]:
        selected_by_objective = {
            signal.objective_id for signal in selected if signal.objective_id is not None
        }
        priorities: list[dict[str, Any]] = []
        for objective in objectives:
            target = self._objective_value(objective, "target_symbol", "unknown")
            objective_id = self._objective_value(objective, "objective_id", None)
            priorities.append(
                {
                    "target_symbol": target,
                    "objective_id": objective_id,
                    "priority": "high" if objective_id in selected_by_objective else "medium",
                    "basis": "selected generated hypotheses and active-design strategy",
                }
            )
        return priorities

    def _objective_payload(
        self,
        objective: GenerationObjective | Mapping[str, Any] | Any,
        focus: NextGenerationFocus,
        strategy: ActiveDesignStrategy,
    ) -> dict[str, Any]:
        return {
            "objective_id": self._objective_value(objective, "objective_id", "unknown"),
            "target_symbol": self._objective_value(objective, "target_symbol", "unknown"),
            "next_focus": focus,
            "active_design_strategy": strategy,
            "machine_readable": True,
        }

    def _oracle_emphasis(self, focus: NextGenerationFocus) -> list[str]:
        if focus == "reduce_toxicity_risk":
            return ["toxicity_risk"]
        if focus == "improve_solubility":
            return ["developability", "solubility_context"]
        if focus == "improve_diversity":
            return ["diversity", "scaffold_novelty"]
        if focus == "close_experimental_gap":
            return ["experimental_gap", "uncertainty"]
        if focus == "exploit_scaffold":
            return ["seed_evidence", "objective_alignment"]
        return ["novelty", "diversity"]

    def _plan_risks(self, focus: NextGenerationFocus, feedback_used: bool) -> list[dict[str, Any]]:
        risks = [
            {
                "risk": "surrogate_estimates_are_not_evidence",
                "mitigation": "Keep surrogate signals separate from imported exact results.",
            },
            {
                "risk": "generated_hypotheses_are_unvalidated",
                "mitigation": "Require expert review before any assay-triage decision.",
            },
        ]
        if focus == "reduce_toxicity_risk":
            risks.append(
                {
                    "risk": "safety_feedback_flag",
                    "mitigation": "Prioritize computational risk reduction and expert review.",
                }
            )
        if not feedback_used:
            risks.append(
                {
                    "risk": "no_imported_feedback",
                    "mitigation": (
                        "Use oracle and uncertainty signals only until exact results exist."
                    ),
                }
            )
        return risks

    def _required_followups(self, focus: NextGenerationFocus) -> list[dict[str, Any]]:
        followups = [{"action": "expert medchem review", "scope": "high_level_review"}]
        if focus == "reduce_toxicity_risk":
            followups.append(
                {"action": "computational toxicity-risk review", "scope": "risk_triage"}
            )
        elif focus == "close_experimental_gap":
            followups.append(
                {
                    "action": "orthogonal target-engagement assay class discussion",
                    "scope": "high_level_assay_class",
                }
            )
        elif focus == "improve_diversity":
            followups.append(
                {"action": "diversity review across generated scaffolds", "scope": "portfolio"}
            )
        return followups

    def _result_warnings(
        self,
        feedback_used: bool,
        surrogate_metadata: Mapping[str, Any],
    ) -> list[str]:
        warnings = [
            "active_design_is_computational_triage_only",
            "surrogate_estimates_are_not_evidence",
            "no_lab_protocols",
        ]
        if not feedback_used:
            warnings.append("no_exact_experimental_feedback_used")
        if not bool(surrogate_metadata.get("trained")):
            warnings.append("surrogate_not_trained_or_not_updated")
        return warnings

    def _candidate_warnings(
        self,
        candidate: GeneratedMolecule,
        exact_results: Sequence[AssayResult],
        safety_count: int,
    ) -> list[str]:
        warnings = ["surrogate_estimates_are_not_evidence"]
        if not exact_results:
            warnings.append("no_exact_imported_experimental_feedback")
        if safety_count:
            warnings.append("exact_safety_feedback_requires_risk_reduction_focus")
        if candidate.validation.rejection_reasons:
            warnings.append("deterministic_validation_rejection_present")
        return sorted(set(warnings))

    def _disease_name(
        self,
        objectives: Sequence[GenerationObjective | Mapping[str, Any] | Any],
    ) -> str:
        for objective in objectives:
            disease = self._objective_value(objective, "disease_name", None)
            if disease:
                return str(disease)
        return "unspecified disease"

    def _objective_value(
        self,
        objective: GenerationObjective | Mapping[str, Any] | Any,
        key: str,
        default: Any,
    ) -> Any:
        if isinstance(objective, Mapping):
            return objective.get(key, default)
        return getattr(objective, key, default)

    def _scaffold_id(self, candidate: GeneratedMolecule) -> str | None:
        value = candidate.metadata.get("scaffold_id")
        if value:
            return str(value)
        return candidate.diversity_cluster

    def _plan_id(
        self,
        *,
        disease_name: str,
        strategy: ActiveDesignStrategy,
        focus: NextGenerationFocus,
        selected_ids: Sequence[str],
    ) -> str:
        payload = "|".join([disease_name, strategy, focus, *selected_ids])
        return f"active-design-{uuid5(NAMESPACE_URL, payload)}"

    def _mapping(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    def _first_score(self, *values: Any, default: float) -> float:
        for value in values:
            if isinstance(value, (int, float)):
                return self._clamp(float(value))
        return self._clamp(default)

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
