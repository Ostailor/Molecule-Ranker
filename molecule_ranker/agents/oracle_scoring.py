from __future__ import annotations

from typing import Any

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.design.oracles import MultiObjectiveOracleStack, OracleStackResult
from molecule_ranker.generation.schemas import GeneratedMolecule, GenerationRun, SeedMolecule


class OracleScoringAgent(BaseAgent):
    """Attach inspectable V1.1 oracle scores to generated molecule hypotheses."""

    name = "OracleScoringAgent"

    def __init__(self, oracle_stack: MultiObjectiveOracleStack | None = None) -> None:
        super().__init__()
        self._oracle_stack = oracle_stack or MultiObjectiveOracleStack()
        self._last_results: list[OracleStackResult] = []
        self._last_warning: str | None = None

    def process(self, context: PipelineContext) -> PipelineContext:
        self._last_results = []
        self._last_warning = None
        run = context.config.get("generation_run")
        if not isinstance(run, GenerationRun):
            self._last_warning = "No GenerationRun available for oracle scoring."
            return context

        retained = self._score_candidates(
            candidates=run.retained,
            run=run,
            config=context.config,
        )
        generated_by_id = {candidate.generated_id: candidate for candidate in retained}
        generated = [
            generated_by_id.get(candidate.generated_id, candidate) for candidate in run.generated
        ]
        updated_run = run.model_copy(
            update={
                "generated": generated,
                "retained": retained,
                "metadata": {
                    **run.metadata,
                    "oracle_scoring_agent": {
                        "scored_count": len(retained),
                        "score_name": "experiment_worthiness_score",
                        "claim_boundary": "computational experiment-worthiness triage only",
                    },
                },
            }
        )
        context.config["generation_run"] = updated_run
        context.config["generated_molecules"] = updated_run.retained
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if self._last_warning:
            return self._last_warning
        return f"Oracle-scored {len(self._last_results)} generated molecule hypotheses."

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        return {
            "scored_count": len(self._last_results),
            "oracle_names": [
                oracle.oracle_name
                for result in self._last_results[:1]
                for oracle in result.oracles
            ],
            "experiment_worthiness_scores": {
                result.generated_id: result.experiment_worthiness_score
                for result in self._last_results
            },
            "claim_boundary": "not predicted efficacy; not predicted binding",
            **({"warning": self._last_warning} if self._last_warning else {}),
        }

    def _score_candidates(
        self,
        *,
        candidates: list[GeneratedMolecule],
        run: GenerationRun,
        config: dict[str, Any],
    ) -> list[GeneratedMolecule]:
        retained_so_far: list[GeneratedMolecule] = []
        scored: list[GeneratedMolecule] = []
        for candidate in candidates:
            objective = next(
                (
                    item
                    for item in run.objectives
                    if item.objective_id == candidate.objective_id
                ),
                None,
            )
            parent_seeds = self._parent_seeds(candidate, run.seeds)
            result = self._oracle_stack.score(
                candidate=candidate,
                objective=objective,
                seeds=parent_seeds,
                retained_generated=retained_so_far,
                enable_docking=bool(config.get("enable_docking_oracle", False)),
                enable_surrogate=bool(config.get("enable_surrogate_activity_oracle", False)),
            )
            self._last_results.append(result)
            updated = candidate.model_copy(
                update={
                    "generation_score": result.experiment_worthiness_score,
                    "metadata": {
                        **candidate.metadata,
                        "oracle_scoring": result.model_dump(mode="json"),
                        "oracle_scores": {
                            oracle.oracle_name: oracle.score for oracle in result.oracles
                        },
                        "experiment_worthiness_score": result.experiment_worthiness_score,
                    },
                    "warnings": sorted(
                        {
                            *candidate.warnings,
                            "experiment_worthiness_score_is_not_activity_or_binding",
                            *result.risk_flags,
                        }
                    ),
                }
            )
            scored.append(updated)
            retained_so_far.append(updated)
        scored.sort(key=lambda item: item.generation_score or 0.0, reverse=True)
        return scored

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

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name
