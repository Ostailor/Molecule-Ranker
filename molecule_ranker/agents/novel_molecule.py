from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.generation.errors import GenerationError
from molecule_ranker.generation.filters import (
    DiversityFilter,
    NoveltyFilter,
    ValidationFilter,
)
from molecule_ranker.generation.generators import SelfiesMutationGenerator
from molecule_ranker.generation.objective_builder import GenerationObjectiveBuilder
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationConfig,
    GenerationObjective,
    GenerationRun,
    SeedMolecule,
)
from molecule_ranker.generation.scoring import GeneratedMoleculeScorer
from molecule_ranker.generation.seed_selector import SeedSelector
from molecule_ranker.schemas import GeneratedMoleculeHypothesis, MoleculeCandidate

GENERATOR_REPORT_SOURCE = "SELFIES_MUTATION_CROSSOVER"
HYPOTHESIS_WARNINGS = [
    "in_silico_hypothesis_only",
    (
        "Generated structure has no direct experimental, disease activity, safety, "
        "or synthesis evidence."
    ),
]


class NovelMoleculeAgent(BaseAgent):
    """Run the opt-in V0.3 target-conditioned generated molecule pipeline."""

    name = "NovelMoleculeAgent"

    def __init__(
        self,
        *,
        seed_selector: SeedSelector | None = None,
        objective_builder: GenerationObjectiveBuilder | None = None,
        generator: SelfiesMutationGenerator | None = None,
        validation_filter: ValidationFilter | None = None,
        novelty_filter: NoveltyFilter | None = None,
        diversity_filter: DiversityFilter | None = None,
        scorer: GeneratedMoleculeScorer | None = None,
    ) -> None:
        super().__init__()
        self._seed_selector = seed_selector or SeedSelector()
        self._objective_builder = objective_builder or GenerationObjectiveBuilder()
        self._generator = generator or SelfiesMutationGenerator()
        self._validation_filter = validation_filter or ValidationFilter()
        self._novelty_filter = novelty_filter or NoveltyFilter()
        self._diversity_filter = diversity_filter or DiversityFilter()
        self._scorer = scorer or GeneratedMoleculeScorer()
        self._last_metadata: dict[str, Any] = {}

    def process(self, context: PipelineContext) -> PipelineContext:
        enabled = bool(
            context.config.get("enable_generation")
            or context.config.get("enable_novel_generation", False)
        )
        self._last_metadata = self._base_metadata(enabled=enabled, context=context)
        context.config.setdefault("generated_molecules", [])
        if not enabled:
            context.generated_candidates = []
            context.config["generated_molecules"] = []
            return context

        config = self._generation_config(context.config)
        warnings: list[str] = []
        if config.generation_method != self._generator.name:
            return self._handle_generation_stop(
                context=context,
                config=config,
                warning=(
                    f"Generation method {config.generation_method!r} is not available "
                    f"in V0.3; available method is {self._generator.name!r}."
                ),
                seeds=[],
                objectives=[],
                generated=[],
                rejected=[],
            )
        if context.disease is None:
            return self._handle_generation_stop(
                context=context,
                config=config,
                warning="Generation requires a resolved disease before seed selection.",
                seeds=[],
                objectives=[],
                generated=[],
                rejected=[],
            )

        literature_evidence = self._literature_evidence(context.config)
        seeds = self._seed_selector.select(
            disease=context.disease,
            targets=context.targets,
            candidates=context.candidates,
            literature_evidence=literature_evidence,
            config=config,
        )
        if not seeds:
            return self._handle_generation_stop(
                context=context,
                config=config,
                warning="No seed molecules available for generated molecule hypotheses.",
                seeds=[],
                objectives=[],
                generated=[],
                rejected=[],
            )

        objectives = self._objective_builder.build(
            disease=context.disease,
            targets=context.targets,
            seeds=seeds,
            existing_candidates=context.candidates,
            literature_evidence=literature_evidence,
            config=config,
        )
        if not objectives:
            return self._handle_generation_stop(
                context=context,
                config=config,
                warning="No generation objectives could be built from selected seeds.",
                seeds=seeds,
                objectives=[],
                generated=[],
                rejected=[],
            )

        generated = self._generate_for_objectives(objectives, seeds, config)
        validated, validation_rejected = self._validation_filter.filter(
            generated,
            config=config,
        )
        novel, novelty_rejected = self._novelty_filter.filter(
            validated,
            existing_candidates=context.candidates,
            seeds=seeds,
            config=config,
        )
        diverse, diversity_rejected = self._diversity_filter.filter(
            novel,
            config=config,
        )
        scored = self._scorer.score(
            diverse,
            objectives=objectives,
            seeds=seeds,
            retained_generated=[],
        )
        limit = int(context.config.get("max_retained_generated", config.max_retained_generated))
        retained = scored[:limit]
        rejected = [*validation_rejected, *novelty_rejected, *diversity_rejected, *scored[limit:]]

        if not retained:
            warnings.append("Generator produced no valid retained generated molecule hypotheses.")
            if bool(context.config.get("strict_generation", False)):
                run = self._generation_run(
                    objectives=objectives,
                    seeds=seeds,
                    generated=generated,
                    retained=[],
                    rejected=rejected,
                    warnings=warnings,
                )
                self._store_run(context, run)
                self._last_metadata = self._metadata_for_run(
                    enabled=True,
                    context=context,
                    config=config,
                    run=run,
                )
                raise GenerationError(warnings[-1])

        run = self._generation_run(
            objectives=objectives,
            seeds=seeds,
            generated=generated,
            retained=retained,
            rejected=rejected,
            warnings=warnings,
        )
        self._store_run(context, run)
        context.generated_candidates = self._report_hypotheses(retained, objectives, seeds)
        main_ranking_generated_count = 0
        if bool(context.config.get("include_generated_in_main_ranking", False)):
            main_ranking_generated = self._main_ranking_candidates(retained)
            main_ranking_generated_count = len(main_ranking_generated)
            context.candidates = sorted(
                [*context.candidates, *main_ranking_generated],
                key=lambda candidate: candidate.score or 0.0,
                reverse=True,
            )
        context.config["generated_molecules_in_main_ranking"] = main_ranking_generated_count
        self._last_metadata = self._metadata_for_run(
            enabled=True,
            context=context,
            config=config,
            run=run,
            main_ranking_generated_count=main_ranking_generated_count,
        )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if not self._last_metadata.get("generation_enabled"):
            return "Novel molecule generation disabled; no generated hypotheses retained."
        return (
            f"Generated {len(context.generated_candidates)} target-conditioned "
            "molecule hypotheses."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        return dict(self._last_metadata)

    def _generate_for_objectives(
        self,
        objectives: list[GenerationObjective],
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ) -> list[GeneratedMolecule]:
        seeds_by_id = {self._seed_id(seed): seed for seed in seeds}
        generated: list[GeneratedMolecule] = []
        for objective in objectives:
            objective_seeds = [
                seeds_by_id[seed_id]
                for seed_id in objective.seed_molecule_ids
                if seed_id in seeds_by_id
            ]
            if not objective_seeds:
                objective_seed_names = set(objective.seed_molecule_names)
                objective_seeds = [
                    seed
                    for seed in seeds
                    if seed.name in objective_seed_names
                    or seed.source_candidate_name in objective_seed_names
                ]
            generated.extend(self._generator.generate(objective, objective_seeds, config))
        return generated

    def _handle_generation_stop(
        self,
        *,
        context: PipelineContext,
        config: GenerationConfig,
        warning: str,
        seeds: list[SeedMolecule],
        objectives: list[GenerationObjective],
        generated: list[GeneratedMolecule],
        rejected: list[GeneratedMolecule],
    ) -> PipelineContext:
        run = self._generation_run(
            objectives=objectives,
            seeds=seeds,
            generated=generated,
            retained=[],
            rejected=rejected,
            warnings=[warning],
        )
        self._store_run(context, run)
        context.generated_candidates = []
        self._last_metadata = self._metadata_for_run(
            enabled=True,
            context=context,
            config=config,
            run=run,
            main_ranking_generated_count=0,
        )
        if bool(context.config.get("strict_generation", False)):
            raise GenerationError(warning)
        return context

    def _generation_config(self, runtime_config: Mapping[str, Any]) -> GenerationConfig:
        payload: dict[str, Any] = {}
        for field_name in GenerationConfig.model_fields:
            if field_name in runtime_config:
                payload[field_name] = runtime_config[field_name]

        if "generated_per_objective" not in payload:
            payload["generated_per_objective"] = runtime_config.get(
                "generated_candidate_limit",
                GenerationConfig.model_fields["generated_per_objective"].default,
            )
        if "max_generated_before_filtering" not in payload:
            payload["max_generated_before_filtering"] = runtime_config.get(
                "generation_attempt_budget",
                GenerationConfig.model_fields["max_generated_before_filtering"].default,
            )
        if "near_duplicate_similarity_threshold" not in payload:
            payload["near_duplicate_similarity_threshold"] = runtime_config.get(
                "near_identical_similarity_threshold",
                GenerationConfig.model_fields["near_duplicate_similarity_threshold"].default,
            )
        if "duplicate_similarity_threshold" not in payload and (
            "near_identical_similarity_threshold" in runtime_config
        ):
            payload["duplicate_similarity_threshold"] = max(
                float(runtime_config["near_identical_similarity_threshold"]),
                float(GenerationConfig.model_fields["duplicate_similarity_threshold"].default),
            )
        if "reject_distant_generated" in runtime_config:
            payload["reject_distant_generated_molecules"] = runtime_config[
                "reject_distant_generated"
            ]
        if "reject_basic_alerts" in runtime_config:
            payload["basic_alerts_warning_only"] = not bool(
                runtime_config["reject_basic_alerts"]
            )
        return GenerationConfig(**payload)

    def _generation_run(
        self,
        *,
        objectives: list[GenerationObjective],
        seeds: list[SeedMolecule],
        generated: list[GeneratedMolecule],
        retained: list[GeneratedMolecule],
        rejected: list[GeneratedMolecule],
        warnings: list[str],
    ) -> GenerationRun:
        return GenerationRun(
            objectives=objectives,
            seeds=seeds,
            generated=generated,
            retained=retained,
            rejected=rejected,
            warnings=warnings,
            metadata={
                "generator": self._generator.name,
                "generation_method": self._generator.name,
                "generator_version": "v0.3",
                "run_timestamp": datetime.now(UTC).isoformat(),
                "validation_filter": self._validation_filter.__class__.__name__,
                "novelty_filter": self._novelty_filter.__class__.__name__,
                "diversity_filter": self._diversity_filter.__class__.__name__,
                "hypothesis_only": True,
                "no_invented_evidence": True,
            },
        )

    def _store_run(self, context: PipelineContext, run: GenerationRun) -> None:
        context.config["generation_run"] = run
        context.config["generated_molecules"] = run.retained

    def _report_hypotheses(
        self,
        generated: list[GeneratedMolecule],
        objectives: list[GenerationObjective],
        seeds: list[SeedMolecule],
    ) -> list[GeneratedMoleculeHypothesis]:
        objectives_by_id = {objective.objective_id: objective for objective in objectives}
        seeds_by_id = {self._seed_id(seed): seed for seed in seeds}
        hypotheses: list[GeneratedMoleculeHypothesis] = []
        for rank, candidate in enumerate(generated, start=1):
            objective = objectives_by_id.get(candidate.objective_id)
            parent_seeds = [
                seeds_by_id[seed_id]
                for seed_id in candidate.parent_seed_ids
                if seed_id in seeds_by_id
            ]
            max_seed_similarity = (
                candidate.novelty.max_similarity_to_seed if candidate.novelty else 0.0
            )
            explanation = (
                candidate.score_breakdown.explanation if candidate.score_breakdown else ""
            )
            hypotheses.append(
                GeneratedMoleculeHypothesis(
                    name=candidate.generated_id,
                    canonical_smiles=candidate.canonical_smiles,
                    source=GENERATOR_REPORT_SOURCE,
                    target_symbol=(
                        candidate.conditioned_targets[0]
                        if candidate.conditioned_targets
                        else objective.target_symbol
                        if objective is not None
                        else "unknown"
                    ),
                    target_name=objective.target_name if objective is not None else None,
                    seed_molecule_names=[seed.name for seed in parent_seeds],
                    seed_identifiers=[
                        {str(key): str(value) for key, value in seed.identifiers.items()}
                        for seed in parent_seeds
                        if seed.identifiers
                    ],
                    generation_score=candidate.generation_score or 0.0,
                    rank=rank,
                    min_seed_similarity=max_seed_similarity,
                    max_seed_similarity=max_seed_similarity,
                    mean_seed_similarity=max_seed_similarity,
                    descriptors=dict(candidate.descriptors),
                    trace={
                        "origin": candidate.origin,
                        "generator": candidate.generation_method,
                        "generated_id": candidate.generated_id,
                        "objective_id": candidate.objective_id,
                        "parent_seed_ids": candidate.parent_seed_ids,
                        "novelty_class": (
                            candidate.novelty.novelty_class if candidate.novelty else None
                        ),
                        "diversity_cluster": candidate.diversity_cluster,
                        "mutation_operations": candidate.metadata.get(
                            "mutation_operations",
                            [],
                        ),
                        "score_explanation": explanation,
                    },
                    warnings=sorted(set([*candidate.warnings, *HYPOTHESIS_WARNINGS])),
                    evidence=[],
                )
            )
        hypotheses.sort(key=lambda item: item.generation_score, reverse=True)
        return [item.model_copy(update={"rank": index}) for index, item in enumerate(hypotheses, 1)]

    def _main_ranking_candidates(
        self,
        generated: list[GeneratedMolecule],
    ) -> list[MoleculeCandidate]:
        candidates: list[MoleculeCandidate] = []
        for molecule in generated:
            explanation = (
                molecule.score_breakdown.explanation if molecule.score_breakdown else ""
            )
            candidates.append(
                MoleculeCandidate(
                    name=molecule.generated_id,
                    molecule_type="generated",
                    origin="generated",
                    identifiers={
                        key: value
                        for key, value in {
                            "generated_id": molecule.generated_id,
                            "inchikey": molecule.inchi_key,
                        }.items()
                        if value
                    },
                    known_targets=list(molecule.conditioned_targets),
                    development_status=None,
                    mechanism_of_action=None,
                    chemical_metadata={
                        "origin": molecule.origin,
                        "generation_method": molecule.generation_method,
                        "canonical_smiles": molecule.canonical_smiles,
                        "objective_id": molecule.objective_id,
                        "parent_seed_ids": list(molecule.parent_seed_ids),
                        "generation_score_explanation": explanation,
                        "direct_experimental_evidence": False,
                    },
                    generation_metadata={
                        "generated_id": molecule.generated_id,
                        "objective_id": molecule.objective_id,
                        "generation_method": molecule.generation_method,
                        "parent_seed_ids": list(molecule.parent_seed_ids),
                        "conditioned_targets": list(molecule.conditioned_targets),
                        "generation_score_explanation": explanation,
                        "validation": molecule.validation.model_dump(mode="json"),
                        "novelty": (
                            molecule.novelty.model_dump(mode="json")
                            if molecule.novelty is not None
                            else None
                        ),
                    },
                    direct_evidence_available=False,
                    evidence=[],
                    score=molecule.generation_score,
                    score_breakdown=None,
                    warnings=sorted(set([*molecule.warnings, *HYPOTHESIS_WARNINGS])),
                )
            )
        return candidates

    def _metadata_for_run(
        self,
        *,
        enabled: bool,
        context: PipelineContext,
        config: GenerationConfig,
        run: GenerationRun,
        main_ranking_generated_count: int = 0,
    ) -> dict[str, Any]:
        metadata = self._base_metadata(enabled=enabled, context=context)
        metadata.update(
            {
                "seed_count": len(run.seeds),
                "objective_count": len(run.objectives),
                "generated_count": len(run.retained),
                "raw_generated_count": len(run.generated),
                "rejected_count": len(run.rejected),
                "warnings": list(run.warnings),
                "include_generated_in_main_ranking": bool(
                    context.config.get("include_generated_in_main_ranking", False)
                ),
                "main_ranking_generated_count": main_ranking_generated_count,
                "generator": self._generator.name,
                "generation_run": {
                    "objective_count": len(run.objectives),
                    "seed_count": len(run.seeds),
                    "raw_generated_count": len(run.generated),
                    "retained_count": len(run.retained),
                    "rejected_count": len(run.rejected),
                    "warning_count": len(run.warnings),
                },
                "seed_selection": self._seed_selector.trace_metadata,
                "objective_building": self._objective_builder.trace_metadata,
                "generator_trace": {
                    "method": self._generator.name,
                    "random_seed": config.generation_random_seed,
                    "raw_generated_count": len(run.generated),
                    "generated_ids": [candidate.generated_id for candidate in run.generated],
                    "operations": [
                        candidate.metadata.get("operation")
                        for candidate in run.generated
                        if candidate.metadata.get("operation")
                    ],
                },
                "validation_filtering_trace": {
                    "validated_count": len(run.retained) + len(run.rejected),
                    "retained_count": len(run.retained),
                    "rejected_count": len(run.rejected),
                    "rejection_reasons": self._rejection_reason_counts(run.rejected),
                },
                "scoring_trace": {
                    "scored_count": len(run.retained),
                    "scores": [
                        {
                            "generated_id": candidate.generated_id,
                            "generation_score": candidate.generation_score,
                            "confidence": (
                                candidate.score_breakdown.confidence
                                if candidate.score_breakdown
                                else None
                            ),
                        }
                        for candidate in run.retained
                    ],
                },
                "filters": {
                    "descriptor_bounds_warning_only": config.descriptor_bounds_warning_only,
                    "basic_alerts_warning_only": config.basic_alerts_warning_only,
                    "duplicate_similarity_threshold": config.duplicate_similarity_threshold,
                    "near_duplicate_similarity_threshold": (
                        config.near_duplicate_similarity_threshold
                    ),
                    "distant_similarity_threshold": config.distant_similarity_threshold,
                    "reject_distant_generated_molecules": (
                        config.reject_distant_generated_molecules
                    ),
                    "diversity_similarity_threshold": config.diversity_similarity_threshold,
                    "max_generated_per_diversity_cluster": (
                        config.max_generated_per_diversity_cluster
                    ),
                },
                "ranked_generated_candidates": [
                    {
                        "name": candidate.generated_id,
                        "canonical_smiles": candidate.canonical_smiles,
                        "generation_score": candidate.generation_score,
                        "target_symbol": (
                            candidate.conditioned_targets[0]
                            if candidate.conditioned_targets
                            else None
                        ),
                        "origin": candidate.origin,
                    }
                    for candidate in run.retained
                ],
            }
        )
        return metadata

    def _rejection_reason_counts(
        self,
        rejected: list[GeneratedMolecule],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in rejected:
            reasons = list(candidate.validation.rejection_reasons)
            if candidate.novelty is not None and candidate.novelty.novelty_class in {
                "duplicate",
                "near_duplicate",
                "distant",
            }:
                reasons.append(candidate.novelty.novelty_class)
            if not reasons:
                reasons.append("diversity_or_retention_limit")
            for reason in reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return counts

    def _base_metadata(self, *, enabled: bool, context: PipelineContext) -> dict[str, Any]:
        return {
            "implemented": True,
            "generation_enabled": enabled,
            "generated_count": len(context.generated_candidates),
            "seed_count": 0,
            "mode": "opt_in" if enabled else "disabled_by_default",
            "policy": {
                "hypothesis_only": True,
                "no_disease_activity_claims": True,
                "no_target_binding_or_modulation_claims": True,
                "no_synthesis_protocols": True,
                "no_invented_evidence": True,
            },
        }

    def _literature_evidence(self, runtime_config: Mapping[str, Any]) -> Mapping[str, Any] | None:
        value = runtime_config.get("literature_evidence")
        return value if isinstance(value, Mapping) else None

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey", "name"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name
