from __future__ import annotations

from molecule_ranker.generation.chemistry import mol_from_smiles, tanimoto_similarity
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    GenerationObjective,
    SeedMolecule,
)

CONFIDENCE_CAP_V0_3 = 0.45


class GeneratedMoleculeScorer:
    """Score generated molecule hypotheses separately from evidence-backed molecules."""

    def score(
        self,
        generated: list[GeneratedMolecule],
        *,
        objectives: list[GenerationObjective],
        seeds: list[SeedMolecule],
        retained_generated: list[GeneratedMolecule],
    ) -> list[GeneratedMolecule]:
        objectives_by_id = {objective.objective_id: objective for objective in objectives}
        seeds_by_id = {self._seed_id(seed): seed for seed in seeds}
        retained: list[GeneratedMolecule] = list(retained_generated)
        scored: list[GeneratedMolecule] = []
        for candidate in generated:
            objective = objectives_by_id.get(candidate.objective_id)
            parent_seeds = [
                seeds_by_id[seed_id]
                for seed_id in candidate.parent_seed_ids
                if seed_id in seeds_by_id
            ]
            breakdown = self._score_candidate(
                candidate=candidate,
                objective=objective,
                parent_seeds=parent_seeds,
                retained_generated=retained,
            )
            updated = candidate.model_copy(
                update={
                    "generation_score": breakdown.final_generation_score,
                    "score_breakdown": breakdown,
                    "metadata": {
                        **candidate.metadata,
                        "direct_generated_molecule_literature_evidence": False,
                        "scoring_policy": "v0.3_generated_hypothesis_only",
                    },
                }
            )
            scored.append(updated)
            retained.append(updated)
        scored.sort(key=lambda item: item.generation_score or 0.0, reverse=True)
        return scored

    def _score_candidate(
        self,
        *,
        candidate: GeneratedMolecule,
        objective: GenerationObjective | None,
        parent_seeds: list[SeedMolecule],
        retained_generated: list[GeneratedMolecule],
    ) -> GeneratedMoleculeScoreBreakdown:
        target_conditioning_score = self._target_conditioning_score(
            candidate,
            objective,
            parent_seeds,
        )
        seed_evidence_score = self._seed_evidence_score(parent_seeds)
        novelty_score = self._novelty_score(candidate)
        diversity_score = self._diversity_score(candidate, retained_generated)
        chemical_validity_score = self._chemical_validity_score(candidate)
        property_profile_score = self._property_profile_score(candidate, objective)
        literature_context_score = self._literature_context_score(objective, parent_seeds)
        final_score = self._clamp(
            0.25 * target_conditioning_score
            + 0.20 * seed_evidence_score
            + 0.15 * novelty_score
            + 0.15 * diversity_score
            + 0.10 * chemical_validity_score
            + 0.10 * property_profile_score
            + 0.05 * literature_context_score
        )
        if candidate.novelty is not None and candidate.novelty.novelty_class in {
            "duplicate",
            "near_duplicate",
        }:
            final_score *= 0.45
        confidence = min(
            CONFIDENCE_CAP_V0_3,
            self._clamp(
                0.30 * chemical_validity_score
                + 0.25 * seed_evidence_score
                + 0.20 * target_conditioning_score
                + 0.15 * novelty_score
                + 0.10 * property_profile_score
            ),
        )
        return GeneratedMoleculeScoreBreakdown(
            target_conditioning_score=round(target_conditioning_score, 3),
            seed_evidence_score=round(seed_evidence_score, 3),
            novelty_score=round(novelty_score, 3),
            diversity_score=round(diversity_score, 3),
            chemical_validity_score=round(chemical_validity_score, 3),
            property_profile_score=round(property_profile_score, 3),
            literature_context_score=round(literature_context_score, 3),
            final_generation_score=round(final_score, 3),
            confidence=round(confidence, 3),
            explanation=self._explanation(candidate),
        )

    def _target_conditioning_score(
        self,
        candidate: GeneratedMolecule,
        objective: GenerationObjective | None,
        parent_seeds: list[SeedMolecule],
    ) -> float:
        target_relevance = self._objective_target_relevance(objective, parent_seeds)
        similarity = 0.0
        if candidate.novelty is not None:
            similarity = candidate.novelty.max_similarity_to_seed
        conditioning_similarity = 1.0 - min(abs(similarity - 0.65) / 0.65, 1.0)
        return self._clamp(0.55 * conditioning_similarity + 0.45 * target_relevance)

    def _objective_target_relevance(
        self,
        objective: GenerationObjective | None,
        parent_seeds: list[SeedMolecule],
    ) -> float:
        if objective is not None:
            raw = objective.metadata.get("target_relevance_score")
            if isinstance(raw, (int, float)):
                return self._clamp(float(raw))
        if parent_seeds:
            return self._clamp(
                sum(seed.target_relevance_score for seed in parent_seeds) / len(parent_seeds)
            )
        return 0.0

    def _seed_evidence_score(self, parent_seeds: list[SeedMolecule]) -> float:
        if not parent_seeds:
            return 0.0
        values = []
        for seed in parent_seeds:
            seed_score = seed.metadata.get("seed_score")
            if isinstance(seed_score, (int, float)):
                values.append(float(seed_score))
            else:
                values.append(
                    0.55 * seed.best_evidence_confidence
                    + 0.45 * seed.target_relevance_score
                )
        return self._clamp(sum(values) / len(values))

    def _novelty_score(self, candidate: GeneratedMolecule) -> float:
        if candidate.novelty is None:
            return 0.4
        return {
            "duplicate": 0.0,
            "near_duplicate": 0.1,
            "close_analog": 0.55,
            "novel_analog": 0.9,
            "distant": 0.35,
        }[candidate.novelty.novelty_class]

    def _diversity_score(
        self,
        candidate: GeneratedMolecule,
        retained_generated: list[GeneratedMolecule],
    ) -> float:
        if candidate.diversity_cluster:
            cluster_size = sum(
                1
                for retained in retained_generated
                if retained.diversity_cluster == candidate.diversity_cluster
            )
            return self._clamp(1.0 / (cluster_size + 1.0))
        candidate_mol = mol_from_smiles(candidate.canonical_smiles)
        if candidate_mol is None or not retained_generated:
            return 1.0
        similarities = []
        for retained in retained_generated:
            retained_mol = mol_from_smiles(retained.canonical_smiles)
            if retained_mol is not None:
                similarities.append(tanimoto_similarity(candidate_mol, retained_mol))
        return self._clamp(1.0 - max(similarities, default=0.0))

    def _chemical_validity_score(self, candidate: GeneratedMolecule) -> float:
        validation = candidate.validation
        checks = [
            validation.valid_rdkit_mol,
            validation.sanitization_ok,
            validation.canonicalization_ok,
            validation.allowed_elements_ok,
            validation.descriptor_bounds_ok,
        ]
        score = sum(1.0 for check in checks if check) / len(checks)
        if validation.rejection_reasons:
            score *= 0.4
        if validation.pains_or_alerts:
            score *= 0.8
        return self._clamp(score)

    def _property_profile_score(
        self,
        candidate: GeneratedMolecule,
        objective: GenerationObjective | None,
    ) -> float:
        if objective is None or not objective.constraints:
            return 0.6 if candidate.validation.descriptor_bounds_ok else 0.25
        fields = ["molecular_weight", "logp", "tpsa"]
        scores = []
        for field in fields:
            value = candidate.descriptors.get(field)
            constraint = objective.constraints.get(field)
            if not isinstance(value, (int, float)) or not isinstance(constraint, dict):
                continue
            minimum = constraint.get("min")
            maximum = constraint.get("max")
            if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
                continue
            if minimum <= value <= maximum:
                scores.append(1.0)
            else:
                span = max(float(maximum) - float(minimum), 1.0)
                distance = min(
                    abs(float(value) - float(minimum)),
                    abs(float(value) - float(maximum)),
                )
                scores.append(max(0.0, 1.0 - distance / span))
        if not scores:
            return 0.6 if candidate.validation.descriptor_bounds_ok else 0.25
        return self._clamp(sum(scores) / len(scores))

    def _literature_context_score(
        self,
        objective: GenerationObjective | None,
        parent_seeds: list[SeedMolecule],
    ) -> float:
        values: list[float] = []
        if objective is not None:
            raw = objective.metadata.get("literature_context_score")
            if isinstance(raw, (int, float)):
                values.append(float(raw))
        for seed in parent_seeds:
            raw = seed.metadata.get("literature_support_score")
            if isinstance(raw, (int, float)):
                values.append(float(raw))
        return self._clamp(max(values, default=0.0))

    def _explanation(self, candidate: GeneratedMolecule) -> str:
        novelty = candidate.novelty.novelty_class if candidate.novelty else "unknown novelty"
        return (
            "Generated molecule is an in-silico research hypothesis scored separately "
            "from existing evidence-backed molecules. Target conditioning reflects seed "
            "similarity and target disease relevance, not predicted binding affinity. "
            f"Novelty class is {novelty}. Property profile uses coarse descriptor sanity "
            "relative to seed constraints and is not ADMET. Literature context, if present, "
            "comes from parent seed or target context only; the generated molecule has no "
            "direct experimental evidence."
        )

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
