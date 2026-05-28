from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationObjective,
    GenerationRun,
    SeedMolecule,
)

ReadinessBucket = Literal[
    "ready_for_expert_review",
    "promising_but_needs_more_computation",
    "active_learning_candidate",
    "deprioritize",
    "reject",
]


class ExperimentReadinessScore(BaseModel):
    objective_alignment: float = Field(ge=0.0, le=1.0)
    seed_evidence_support: float = Field(ge=0.0, le=1.0)
    chemical_validity: float = Field(ge=0.0, le=1.0)
    developability: float = Field(ge=0.0, le=1.0)
    novelty_without_uncontrolled_risk: float = Field(ge=0.0, le=1.0)
    diversity: float = Field(ge=0.0, le=1.0)
    uncertainty_value: float = Field(ge=0.0, le=1.0)
    experimental_gap_value: float = Field(ge=0.0, le=1.0)
    medchem_critique: float = Field(ge=0.0, le=1.0)
    review_priority_context: float = Field(ge=0.0, le=1.0)
    assay_feasibility_context: float = Field(ge=0.0, le=1.0)
    readiness_score: float = Field(ge=0.0, le=1.0)


class ExperimentReadyCandidate(BaseModel):
    molecule_id: str
    canonical_smiles: str
    target_symbols: list[str] = Field(default_factory=list)
    readiness_score: float = Field(ge=0.0, le=1.0)
    readiness_bucket: ReadinessBucket
    top_reasons: list[str] = Field(default_factory=list)
    blocking_risks: list[str] = Field(default_factory=list)
    suggested_high_level_followup: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentReadinessAgent(BaseAgent):
    """Select generated hypotheses for expert-review triage without protocol claims."""

    name = "ExperimentReadinessAgent"

    def __init__(self) -> None:
        super().__init__()
        self._last_candidates: list[ExperimentReadyCandidate] = []
        self._last_warning: str | None = None

    def process(self, context: PipelineContext) -> PipelineContext:
        self._last_candidates = []
        self._last_warning = None
        run = context.config.get("generation_run")
        if not isinstance(run, GenerationRun):
            self._last_warning = "No GenerationRun available for experiment readiness scoring."
            return context

        retained, ready_candidates = self.score_run(run, config=context.config)
        self._last_candidates = ready_candidates
        retained_by_id = {candidate.generated_id: candidate for candidate in retained}
        generated = [
            retained_by_id.get(candidate.generated_id, candidate) for candidate in run.generated
        ]
        bucket_counts: dict[str, int] = {}
        for candidate in ready_candidates:
            bucket_counts[candidate.readiness_bucket] = (
                bucket_counts.get(candidate.readiness_bucket, 0) + 1
            )
        updated_run = run.model_copy(
            update={
                "generated": generated,
                "retained": retained,
                "metadata": {
                    **run.metadata,
                    "experiment_readiness_agent": {
                        "scored_count": len(ready_candidates),
                        "bucket_counts": bucket_counts,
                        "human_review_required": True,
                        "claim_boundary": (
                            "expert-review triage only; not an experiment, activity, "
                            "safety, or synthesis recommendation"
                        ),
                    },
                },
            }
        )
        context.config["generation_run"] = updated_run
        context.config["generated_molecules"] = updated_run.retained
        context.config["experiment_ready_candidates"] = ready_candidates
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if self._last_warning:
            return self._last_warning
        return f"Experiment-readiness scored {len(self._last_candidates)} generated hypotheses."

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        bucket_counts: dict[str, int] = {}
        for candidate in self._last_candidates:
            bucket_counts[candidate.readiness_bucket] = (
                bucket_counts.get(candidate.readiness_bucket, 0) + 1
            )
        return {
            "scored_count": len(self._last_candidates),
            "bucket_counts": bucket_counts,
            "human_review_required": True,
            "no_lab_protocols": True,
            "claim_boundary": "expert-review triage only",
            **({"warning": self._last_warning} if self._last_warning else {}),
        }

    def score_run(
        self,
        run: GenerationRun,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> tuple[list[GeneratedMolecule], list[ExperimentReadyCandidate]]:
        retained: list[GeneratedMolecule] = []
        ready_candidates: list[ExperimentReadyCandidate] = []
        cluster_counts = self._cluster_counts(run.retained)
        for candidate in run.retained:
            objective = self._objective_for(candidate, run.objectives)
            parent_seeds = self._parent_seeds(candidate, run.seeds)
            readiness = self.score_candidate(
                candidate=candidate,
                objective=objective,
                parent_seeds=parent_seeds,
                cluster_counts=cluster_counts,
                config=config or {},
            )
            ready_candidates.append(readiness)
            retained.append(self._with_readiness_metadata(candidate, readiness))
        order = {candidate.molecule_id: index for index, candidate in enumerate(ready_candidates)}
        ready_candidates.sort(key=lambda item: item.readiness_score, reverse=True)
        retained.sort(
            key=lambda item: (
                next(
                    (
                        ready.readiness_score
                        for ready in ready_candidates
                        if ready.molecule_id == item.generated_id
                    ),
                    0.0,
                ),
                -order.get(item.generated_id, 0),
            ),
            reverse=True,
        )
        return retained, ready_candidates

    def score_state(
        self,
        *,
        generated: list[GeneratedMolecule],
        objectives: list[Any],
        seeds: list[SeedMolecule],
        config: Mapping[str, Any] | None = None,
    ) -> list[ExperimentReadyCandidate]:
        valid_objectives = [
            objective for objective in objectives if isinstance(objective, GenerationObjective)
        ]
        run = GenerationRun(
            objectives=valid_objectives,
            seeds=seeds,
            generated=generated,
            retained=generated,
        )
        _, ready_candidates = self.score_run(run, config=config)
        return ready_candidates

    def score_candidate(
        self,
        *,
        candidate: GeneratedMolecule,
        objective: GenerationObjective | None,
        parent_seeds: list[SeedMolecule],
        cluster_counts: Mapping[str, int],
        config: Mapping[str, Any],
    ) -> ExperimentReadyCandidate:
        components = self._score_components(
            candidate=candidate,
            objective=objective,
            parent_seeds=parent_seeds,
            cluster_counts=cluster_counts,
        )
        blocking_risks = self._blocking_risks(candidate)
        score = self._composite_score(components)
        if blocking_risks:
            score = min(score, 0.20)
        if self._uncontrolled_risk(candidate):
            score = min(score, 0.42)
        bucket = self._bucket(candidate, score, blocking_risks)
        reasons = self._top_reasons(components, candidate, bucket)
        followup = self._safe_followup(bucket, blocking_risks)
        warnings = self._warnings(candidate, bucket)
        score_model = ExperimentReadinessScore(
            **components,
            readiness_score=round(score, 3),
        )
        return ExperimentReadyCandidate(
            molecule_id=candidate.generated_id,
            canonical_smiles=candidate.canonical_smiles,
            target_symbols=self._target_symbols(candidate, objective),
            readiness_score=round(score, 3),
            readiness_bucket=bucket,
            top_reasons=reasons,
            blocking_risks=blocking_risks,
            suggested_high_level_followup=followup,
            warnings=warnings,
            metadata={
                "score_components": score_model.model_dump(mode="json"),
                "objective_id": candidate.objective_id,
                "parent_seed_ids": list(candidate.parent_seed_ids),
                "human_review_required": True,
                "experiment_label_used": False,
                "experiment_label_allowed_by_config": bool(
                    config.get("allow_experiment_label", False)
                ),
                "claim_boundary": (
                    "readiness is expert-review triage only, not predicted activity, "
                    "safety, synthesizability, or an assay recommendation"
                ),
            },
        )

    def _score_components(
        self,
        *,
        candidate: GeneratedMolecule,
        objective: GenerationObjective | None,
        parent_seeds: list[SeedMolecule],
        cluster_counts: Mapping[str, int],
    ) -> dict[str, float]:
        breakdown = candidate.score_breakdown
        oracle_components = self._oracle_components(candidate)
        uncertainty = self._mapping(candidate.metadata.get("uncertainty"))
        return {
            "objective_alignment": self._first_score(
                breakdown.objective_alignment_score if breakdown else None,
                breakdown.target_conditioning_score if breakdown else None,
                oracle_components.get("target_context_score"),
                self._objective_metadata_score(objective, "target_relevance_score"),
                default=0.35,
            ),
            "seed_evidence_support": self._first_score(
                breakdown.seed_evidence_score if breakdown else None,
                oracle_components.get("seed_evidence_score"),
                self._seed_evidence_score(parent_seeds),
                default=0.0,
            ),
            "chemical_validity": self._chemical_validity_score(candidate),
            "developability": self._developability_score(candidate, oracle_components),
            "novelty_without_uncontrolled_risk": self._novelty_without_uncontrolled_risk(
                candidate,
                oracle_components,
            ),
            "diversity": self._diversity_score(candidate, cluster_counts, oracle_components),
            "uncertainty_value": self._uncertainty_value(candidate, uncertainty),
            "experimental_gap_value": self._first_score(
                uncertainty.get("experimental_gap_uncertainty"),
                oracle_components.get("experimental_gap_value"),
                default=0.35,
            ),
            "medchem_critique": self._medchem_critique_score(candidate),
            "review_priority_context": self._review_priority_context(candidate, objective),
            "assay_feasibility_context": self._assay_feasibility_context(candidate),
        }

    def _composite_score(self, components: Mapping[str, float]) -> float:
        weighted = (
            0.14 * components["objective_alignment"]
            + 0.10 * components["seed_evidence_support"]
            + 0.12 * components["chemical_validity"]
            + 0.13 * components["developability"]
            + 0.10 * components["novelty_without_uncontrolled_risk"]
            + 0.08 * components["diversity"]
            + 0.09 * components["uncertainty_value"]
            + 0.07 * components["experimental_gap_value"]
            + 0.10 * components["medchem_critique"]
            + 0.04 * components["review_priority_context"]
            + 0.03 * components["assay_feasibility_context"]
        )
        return self._clamp(weighted)

    def _bucket(
        self,
        candidate: GeneratedMolecule,
        score: float,
        blocking_risks: list[str],
    ) -> ReadinessBucket:
        if blocking_risks:
            return "reject"
        medchem = self._medchem_payload(candidate)
        action = str(medchem.get("recommended_action") or "")
        if action == "deprioritize":
            return "deprioritize"
        uncertainty = self._mapping(candidate.metadata.get("uncertainty"))
        if (
            uncertainty.get("uncertainty_class") == "interesting_uncertainty"
            and self._float(uncertainty.get("active_learning_value"), 0.0) >= 0.65
            and not self._uncontrolled_risk(candidate)
        ):
            return "active_learning_candidate"
        if score >= 0.72:
            return "ready_for_expert_review"
        if score >= 0.45:
            return "promising_but_needs_more_computation"
        return "deprioritize"

    def _blocking_risks(self, candidate: GeneratedMolecule) -> list[str]:
        risks: list[str] = []
        if not candidate.validation.valid_rdkit_mol:
            risks.append("invalid_structure")
        if not candidate.validation.sanitization_ok:
            risks.append("sanitization_failed")
        if not candidate.validation.canonicalization_ok:
            risks.append("canonicalization_failed")
        if not candidate.validation.allowed_elements_ok:
            risks.append("disallowed_elements")
        risks.extend(candidate.validation.rejection_reasons)
        assessment = candidate.developability_assessment
        if assessment is not None:
            risk_level = str(assessment.metadata.get("risk_level") or "").lower()
            if risk_level == "critical":
                risks.append("critical_developability_risk")
            if assessment.triage_recommendation == "high_risk_flags":
                risks.append("high_risk_developability_triage")
        medchem = self._medchem_payload(candidate)
        if medchem.get("recommended_action") == "reject":
            risks.append("medchem_critic_recommends_reject")
        return sorted(set(str(risk) for risk in risks if str(risk).strip()))

    def _with_readiness_metadata(
        self,
        candidate: GeneratedMolecule,
        readiness: ExperimentReadyCandidate,
    ) -> GeneratedMolecule:
        payload = readiness.model_dump(mode="json")
        score_breakdown = candidate.score_breakdown
        if score_breakdown is not None:
            score_breakdown = score_breakdown.model_copy(
                update={"experiment_readiness_score": readiness.readiness_score}
            )
        warnings = sorted({*candidate.warnings, *readiness.warnings})
        return candidate.model_copy(
            update={
                "score_breakdown": score_breakdown,
                "metadata": {
                    **candidate.metadata,
                    "experiment_readiness_v1_1": payload,
                    "experiment_readiness": {
                        "score": readiness.readiness_score,
                        "label": readiness.readiness_bucket,
                        "bucket": readiness.readiness_bucket,
                        "basis": readiness.top_reasons,
                        "blocking_risks": readiness.blocking_risks,
                        "suggested_high_level_followup": (
                            readiness.suggested_high_level_followup
                        ),
                        "human_review_required": True,
                        "claim_boundary": "review queue triage only",
                    },
                },
                "warnings": warnings,
            }
        )

    def _chemical_validity_score(self, candidate: GeneratedMolecule) -> float:
        validation = candidate.validation
        checks = [
            validation.valid_rdkit_mol,
            validation.sanitization_ok,
            validation.canonicalization_ok,
            validation.allowed_elements_ok,
            validation.descriptor_bounds_ok,
        ]
        score = sum(1.0 for item in checks if item) / len(checks)
        score -= min(0.25, 0.08 * len(validation.pains_or_alerts))
        if validation.rejection_reasons:
            score = min(score, 0.2)
        return self._clamp(score)

    def _developability_score(
        self,
        candidate: GeneratedMolecule,
        oracle_components: Mapping[str, Any],
    ) -> float:
        breakdown = candidate.score_breakdown
        assessment = candidate.developability_assessment
        score = self._first_score(
            assessment.developability_score if assessment else None,
            breakdown.developability_score if breakdown else None,
            breakdown.property_profile_score if breakdown else None,
            oracle_components.get("developability_score"),
            default=0.45,
        )
        if assessment is not None:
            risk_level = str(assessment.metadata.get("risk_level") or "").lower()
            if risk_level == "critical" or assessment.triage_recommendation == "high_risk_flags":
                score = min(score, 0.2)
            elif risk_level == "high":
                score = min(score, 0.45)
        return self._clamp(score)

    def _novelty_without_uncontrolled_risk(
        self,
        candidate: GeneratedMolecule,
        oracle_components: Mapping[str, Any],
    ) -> float:
        novelty_scores = {
            "duplicate": 0.0,
            "near_duplicate": 0.25,
            "close_analog": 0.58,
            "novel_analog": 0.82,
            "distant": 0.66,
        }
        novelty = candidate.novelty.novelty_class if candidate.novelty else "near_duplicate"
        score = self._first_score(
            oracle_components.get("novelty_score"),
            novelty_scores.get(str(novelty), 0.35),
            default=0.35,
        )
        uncertainty = self._mapping(candidate.metadata.get("uncertainty"))
        if uncertainty.get("applicability_domain") == "out_of_domain":
            score = min(score, 0.45)
        if uncertainty.get("uncertainty_class") == "uncontrolled_risk":
            score = min(score, 0.25)
        return self._clamp(score)

    def _diversity_score(
        self,
        candidate: GeneratedMolecule,
        cluster_counts: Mapping[str, int],
        oracle_components: Mapping[str, Any],
    ) -> float:
        breakdown = candidate.score_breakdown
        base = self._first_score(
            breakdown.diversity_score if breakdown else None,
            oracle_components.get("diversity_score"),
            default=0.5,
        )
        if candidate.diversity_cluster and cluster_counts.get(candidate.diversity_cluster, 0) <= 1:
            base = max(base, 0.78)
        elif candidate.diversity_cluster:
            base = min(base, 0.62)
        return self._clamp(base)

    def _uncertainty_value(
        self,
        candidate: GeneratedMolecule,
        uncertainty: Mapping[str, Any],
    ) -> float:
        if uncertainty.get("uncertainty_class") == "uncontrolled_risk":
            return 0.15
        active_learning_value = self._float(uncertainty.get("active_learning_value"), -1.0)
        if uncertainty.get("uncertainty_class") == "interesting_uncertainty":
            return self._clamp(max(active_learning_value, 0.0))
        overall_uncertainty = self._float(uncertainty.get("overall_uncertainty"), -1.0)
        if overall_uncertainty >= 0.0:
            return self._clamp(1.0 - overall_uncertainty)
        breakdown = candidate.score_breakdown
        if breakdown is not None:
            return self._clamp(1.0 - breakdown.uncertainty_score)
        return 0.5

    def _medchem_critique_score(self, candidate: GeneratedMolecule) -> float:
        medchem = self._medchem_payload(candidate)
        action_scores = {
            "retain_for_review": 0.85,
            "needs_expert_review": 0.65,
            "deprioritize": 0.30,
            "reject": 0.0,
        }
        if medchem:
            score = action_scores.get(str(medchem.get("recommended_action")), 0.5)
            concerns = medchem.get("concerns")
            if isinstance(concerns, list):
                score -= min(0.20, 0.04 * len(concerns))
            return self._clamp(score)
        breakdown = candidate.score_breakdown
        return self._first_score(
            breakdown.medchem_critique_score if breakdown else None,
            default=0.5,
        )

    def _review_priority_context(
        self,
        candidate: GeneratedMolecule,
        objective: GenerationObjective | None,
    ) -> float:
        review_context = self._mapping(candidate.metadata.get("review_priority_context"))
        return self._first_score(
            review_context.get("score"),
            candidate.generation_score,
            self._objective_metadata_score(objective, "review_priority_context"),
            self._objective_metadata_score(objective, "target_relevance_score"),
            default=0.5,
        )

    def _assay_feasibility_context(self, candidate: GeneratedMolecule) -> float:
        feasibility = self._mapping(candidate.metadata.get("assay_feasibility_context"))
        score = self._first_score(feasibility.get("score"), default=0.5)
        if candidate.validation.rejection_reasons or not candidate.validation.valid_rdkit_mol:
            score = min(score, 0.15)
        return self._clamp(score)

    def _top_reasons(
        self,
        components: Mapping[str, float],
        candidate: GeneratedMolecule,
        bucket: ReadinessBucket,
    ) -> list[str]:
        names = {
            "objective_alignment": "aligned with the target-conditioned design objective",
            "seed_evidence_support": "retains traceable seed evidence support",
            "chemical_validity": "passed deterministic chemical validation checks",
            "developability": "has favorable computational developability triage",
            "novelty_without_uncontrolled_risk": "adds novelty without uncontrolled risk flags",
            "diversity": "contributes diversity to the generated set",
            "uncertainty_value": "has useful uncertainty context for review",
            "experimental_gap_value": "addresses an experimental evidence gap",
            "medchem_critique": "medicinal chemistry critique supports review",
            "review_priority_context": "matches review-priority context",
            "assay_feasibility_context": "has high-level assay triage context",
        }
        if bucket == "reject":
            return ["Blocking computational risk flags prevent expert-review prioritization."]
        ordered = sorted(components.items(), key=lambda item: item[1], reverse=True)
        reasons = [names[key] for key, value in ordered if value >= 0.65 and key in names]
        if self._uncontrolled_risk(candidate):
            reasons.append("Uncontrolled uncertainty prevents novelty-only prioritization.")
        return reasons[:4] or ["Generated hypothesis remains traceable for expert review."]

    def _safe_followup(
        self,
        bucket: ReadinessBucket,
        blocking_risks: list[str],
    ) -> list[str]:
        if bucket == "reject":
            followup = ["expert review of blocking computational risk flags"]
        elif bucket == "active_learning_candidate":
            followup = [
                "additional computational uncertainty review",
                "expert medchem review",
            ]
        elif bucket == "promising_but_needs_more_computation":
            followup = [
                "additional computational scoring review",
                "expert medchem review",
            ]
        elif blocking_risks:
            followup = ["expert review of blocking computational risk flags"]
        else:
            followup = [
                "expert medchem review",
                "orthogonal target-engagement assay class discussion",
                "selectivity triage planning",
            ]
        return [item for item in followup if self._safe_followup_text(item)]

    def _warnings(
        self,
        candidate: GeneratedMolecule,
        bucket: ReadinessBucket,
    ) -> list[str]:
        warnings = {
            "human_review_required",
            "generated_hypothesis_only",
            "not_activity_or_safety_evidence",
            "no_lab_protocol_recommendations",
        }
        if bucket != "ready_for_expert_review":
            warnings.add(f"experiment_readiness_bucket_{bucket}")
        if candidate.validation.pains_or_alerts:
            warnings.add("structural_alerts_require_review")
        if self._uncontrolled_risk(candidate):
            warnings.add("uncontrolled_uncertainty_risk")
        return sorted(warnings)

    def _oracle_components(self, candidate: GeneratedMolecule) -> dict[str, Any]:
        oracle = self._mapping(candidate.metadata.get("oracle_scoring"))
        components = self._mapping(oracle.get("component_scores"))
        return dict(components)

    def _medchem_payload(self, candidate: GeneratedMolecule) -> dict[str, Any]:
        return self._mapping(candidate.metadata.get("medicinal_chemistry_critique"))

    def _uncontrolled_risk(self, candidate: GeneratedMolecule) -> bool:
        uncertainty = self._mapping(candidate.metadata.get("uncertainty"))
        return (
            uncertainty.get("uncertainty_class") == "uncontrolled_risk"
            or uncertainty.get("applicability_domain") == "out_of_domain"
        )

    def _target_symbols(
        self,
        candidate: GeneratedMolecule,
        objective: GenerationObjective | None,
    ) -> list[str]:
        symbols = [str(symbol) for symbol in candidate.conditioned_targets if symbol]
        if objective is not None and objective.target_symbol not in symbols:
            symbols.append(objective.target_symbol)
        return sorted(set(symbols))

    def _parent_seeds(
        self,
        candidate: GeneratedMolecule,
        seeds: list[SeedMolecule],
    ) -> list[SeedMolecule]:
        seeds_by_id = {self._seed_id(seed): seed for seed in seeds}
        return [
            seeds_by_id[seed_id]
            for seed_id in candidate.parent_seed_ids
            if seed_id in seeds_by_id
        ]

    def _seed_evidence_score(self, seeds: list[SeedMolecule]) -> float:
        if not seeds:
            return 0.0
        scores = [
            self._clamp(
                0.55 * seed.best_evidence_confidence
                + 0.45 * seed.target_relevance_score
            )
            for seed in seeds
        ]
        return self._clamp(sum(scores) / len(scores))

    def _objective_for(
        self,
        candidate: GeneratedMolecule,
        objectives: list[GenerationObjective],
    ) -> GenerationObjective | None:
        return next(
            (
                objective
                for objective in objectives
                if objective.objective_id == candidate.objective_id
            ),
            None,
        )

    def _objective_metadata_score(
        self,
        objective: GenerationObjective | None,
        key: str,
    ) -> float | None:
        if objective is None:
            return None
        value = objective.metadata.get(key)
        if isinstance(value, Mapping):
            value = value.get("score")
        if isinstance(value, (int, float)):
            return self._clamp(float(value))
        return None

    def _cluster_counts(self, candidates: list[GeneratedMolecule]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in candidates:
            if candidate.diversity_cluster:
                counts[candidate.diversity_cluster] = (
                    counts.get(candidate.diversity_cluster, 0) + 1
                )
        return counts

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name

    def _first_score(self, *values: Any, default: float) -> float:
        for value in values:
            if isinstance(value, (int, float)):
                return self._clamp(float(value))
        return self._clamp(default)

    def _float(self, value: Any, default: float) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return default

    def _mapping(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    def _safe_followup_text(self, text: str) -> bool:
        lowered = text.lower()
        forbidden = (
            "protocol",
            "reagent",
            "reaction condition",
            "synthesis route",
            "dosing",
            "animal",
            "patient",
        )
        return not any(term in lowered for term in forbidden)

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
