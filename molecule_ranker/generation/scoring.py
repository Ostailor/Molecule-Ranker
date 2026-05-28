from __future__ import annotations

from molecule_ranker.generation.chemistry import mol_from_smiles, tanimoto_similarity
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GeneratedMoleculeScoreBreakdown,
    GenerationObjective,
    SeedMolecule,
)
from molecule_ranker.schemas import DevelopabilityAssessment

CONFIDENCE_CAP_V0_3 = 0.45
DEVELOPABILITY_ADJUSTMENT_METADATA_KEY = "developability_adjusted_generation_score"


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
                        "scoring_policy": "v1.1_generated_hypothesis_only",
                        "oracle_scores": self._oracle_scores_payload(breakdown),
                        "medicinal_chemistry_critique": self._medchem_critique_payload(
                            candidate,
                            breakdown,
                        ),
                        "uncertainty": self._uncertainty_payload(breakdown),
                        "experiment_readiness": self._experiment_readiness_payload(
                            breakdown,
                        ),
                        "active_learning": self._active_learning_payload(breakdown),
                    },
                }
            )
            updated = self.apply_developability_modifier(updated)
            scored.append(updated)
            retained.append(updated)
        scored.sort(key=lambda item: item.generation_score or 0.0, reverse=True)
        return scored

    def apply_developability_modifier(self, candidate: GeneratedMolecule) -> GeneratedMolecule:
        """Apply post-assessment developability scoring to an already scored molecule."""

        assessment = candidate.developability_assessment
        breakdown = candidate.score_breakdown
        if assessment is None or breakdown is None:
            return candidate
        if candidate.metadata.get(DEVELOPABILITY_ADJUSTMENT_METADATA_KEY) is True:
            return candidate

        original_score = self._clamp(breakdown.final_generation_score)
        developability_score = self._developability_score(assessment)
        adjusted_score = self._clamp(0.70 * original_score + 0.30 * developability_score)
        risk_level = self._developability_risk_level(assessment)
        warnings = list(candidate.warnings)
        validation = candidate.validation

        if risk_level == "critical":
            adjusted_score = min(adjusted_score, 0.20)
            validation = validation.model_copy(
                update={
                    "rejection_reasons": sorted(
                        set([*validation.rejection_reasons, "developability_critical_risk"])
                    )
                }
            )
            warnings.append("Generated molecule rejected by critical developability risk flag.")
        elif risk_level == "high":
            adjusted_score = min(adjusted_score, 0.45)
            warnings.append("Generated molecule deprioritized by high developability risk flag.")

        confidence = breakdown.confidence
        if risk_level == "critical":
            confidence = max(0.0, confidence - 0.25)
        elif risk_level == "high":
            confidence = max(0.0, confidence - 0.15)

        explanation = (
            f"{breakdown.explanation} Developability modifier applied as "
            "0.70 * original generation score + 0.30 * developability score. "
            "Critical developability risk flags reject generated molecules by default; "
            "high risk caps generated score at 0.45."
        )
        updated_breakdown = breakdown.model_copy(
            update={
                "developability_score": round(developability_score, 3),
                "final_generation_score": round(adjusted_score, 3),
                "confidence": round(self._clamp(confidence), 3),
                "explanation": explanation,
            }
        )
        return candidate.model_copy(
            update={
                "generation_score": updated_breakdown.final_generation_score,
                "score_breakdown": updated_breakdown,
                "validation": validation,
                "warnings": sorted(set(warnings)),
                "metadata": {
                    **candidate.metadata,
                    DEVELOPABILITY_ADJUSTMENT_METADATA_KEY: True,
                    "original_generation_score": round(original_score, 3),
                    "developability_score": round(developability_score, 3),
                    "developability_risk_level": risk_level,
                },
            }
        )

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
        objective_alignment_score = self._objective_alignment_score(
            target_conditioning_score,
            property_profile_score,
            literature_context_score,
        )
        generator_ensemble_score = self._generator_ensemble_score(candidate)
        medchem_critique_score = self._medchem_critique_score(
            candidate,
            chemical_validity_score,
            property_profile_score,
        )
        base_signal = self._clamp(
            0.30 * chemical_validity_score
            + 0.25 * seed_evidence_score
            + 0.20 * target_conditioning_score
            + 0.15 * novelty_score
            + 0.10 * property_profile_score
        )
        uncertainty_score = self._uncertainty_score(
            base_signal=base_signal,
            candidate=candidate,
            parent_seeds=parent_seeds,
        )
        experiment_readiness_score = self._experiment_readiness_score(
            chemical_validity_score=chemical_validity_score,
            property_profile_score=property_profile_score,
            medchem_critique_score=medchem_critique_score,
            diversity_score=diversity_score,
            uncertainty_score=uncertainty_score,
        )
        active_learning_priority_score = self._active_learning_priority_score(
            novelty_score=novelty_score,
            uncertainty_score=uncertainty_score,
            target_conditioning_score=target_conditioning_score,
            diversity_score=diversity_score,
        )
        final_score = self._clamp(
            0.18 * target_conditioning_score
            + 0.16 * seed_evidence_score
            + 0.15 * novelty_score
            + 0.12 * diversity_score
            + 0.10 * chemical_validity_score
            + 0.09 * property_profile_score
            + 0.08 * objective_alignment_score
            + 0.07 * medchem_critique_score
            + 0.03 * experiment_readiness_score
            + 0.02 * active_learning_priority_score
        )
        if candidate.novelty is not None and candidate.novelty.novelty_class in {
            "duplicate",
            "near_duplicate",
        }:
            final_score *= 0.45
        confidence = min(
            CONFIDENCE_CAP_V0_3,
            base_signal,
        )
        return GeneratedMoleculeScoreBreakdown(
            target_conditioning_score=round(target_conditioning_score, 3),
            seed_evidence_score=round(seed_evidence_score, 3),
            novelty_score=round(novelty_score, 3),
            diversity_score=round(diversity_score, 3),
            chemical_validity_score=round(chemical_validity_score, 3),
            property_profile_score=round(property_profile_score, 3),
            literature_context_score=round(literature_context_score, 3),
            developability_score=0.0,
            objective_alignment_score=round(objective_alignment_score, 3),
            generator_ensemble_score=round(generator_ensemble_score, 3),
            uncertainty_score=round(uncertainty_score, 3),
            medchem_critique_score=round(medchem_critique_score, 3),
            experiment_readiness_score=round(experiment_readiness_score, 3),
            active_learning_priority_score=round(active_learning_priority_score, 3),
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

    def _objective_alignment_score(
        self,
        target_conditioning_score: float,
        property_profile_score: float,
        literature_context_score: float,
    ) -> float:
        return self._clamp(
            0.55 * target_conditioning_score
            + 0.30 * property_profile_score
            + 0.15 * literature_context_score
        )

    def _generator_ensemble_score(self, candidate: GeneratedMolecule) -> float:
        raw = candidate.metadata.get("generator_ensemble_weight")
        if isinstance(raw, (int, float)):
            return self._clamp(float(raw))
        return 0.75 if candidate.generation_method else 0.4

    def _medchem_critique_score(
        self,
        candidate: GeneratedMolecule,
        chemical_validity_score: float,
        property_profile_score: float,
    ) -> float:
        alert_penalty = min(0.35, 0.12 * len(candidate.validation.pains_or_alerts))
        rejection_penalty = 0.30 if candidate.validation.rejection_reasons else 0.0
        score = 0.55 * chemical_validity_score + 0.45 * property_profile_score
        return self._clamp(score - alert_penalty - rejection_penalty)

    def _uncertainty_score(
        self,
        *,
        base_signal: float,
        candidate: GeneratedMolecule,
        parent_seeds: list[SeedMolecule],
    ) -> float:
        missing_seed_penalty = 0.20 if not parent_seeds else 0.0
        novelty_penalty = 0.10 if (
            candidate.novelty is not None and candidate.novelty.novelty_class == "distant"
        ) else 0.0
        validation_penalty = 0.20 if candidate.validation.rejection_reasons else 0.0
        return self._clamp(
            1.0
            - base_signal
            + missing_seed_penalty
            + novelty_penalty
            + validation_penalty
        )

    def _experiment_readiness_score(
        self,
        *,
        chemical_validity_score: float,
        property_profile_score: float,
        medchem_critique_score: float,
        diversity_score: float,
        uncertainty_score: float,
    ) -> float:
        return self._clamp(
            0.35 * chemical_validity_score
            + 0.25 * property_profile_score
            + 0.20 * medchem_critique_score
            + 0.10 * diversity_score
            + 0.10 * (1.0 - uncertainty_score)
        )

    def _active_learning_priority_score(
        self,
        *,
        novelty_score: float,
        uncertainty_score: float,
        target_conditioning_score: float,
        diversity_score: float,
    ) -> float:
        return self._clamp(
            0.40 * novelty_score
            + 0.25 * uncertainty_score
            + 0.20 * target_conditioning_score
            + 0.15 * diversity_score
        )

    def _oracle_scores_payload(
        self,
        breakdown: GeneratedMoleculeScoreBreakdown,
    ) -> dict[str, float]:
        return {
            "target_conditioning_score": breakdown.target_conditioning_score,
            "objective_alignment_score": breakdown.objective_alignment_score,
            "novelty_score": breakdown.novelty_score,
            "diversity_score": breakdown.diversity_score,
            "chemical_validity_score": breakdown.chemical_validity_score,
            "property_profile_score": breakdown.property_profile_score,
            "medchem_critique_score": breakdown.medchem_critique_score,
            "experiment_readiness_score": breakdown.experiment_readiness_score,
            "active_learning_priority_score": breakdown.active_learning_priority_score,
        }

    def _medchem_critique_payload(
        self,
        candidate: GeneratedMolecule,
        breakdown: GeneratedMoleculeScoreBreakdown,
    ) -> dict[str, object]:
        notes = [
            "Check deterministic structure validation, alert flags, "
            "and descriptor fit before review."
        ]
        if candidate.validation.pains_or_alerts:
            notes.append("Structural alert flags lowered the critique score.")
        if candidate.validation.rejection_reasons:
            notes.append("Validation rejection reasons lowered the critique score.")
        return {
            "score": breakdown.medchem_critique_score,
            "scope": "non-protocol computational critique",
            "notes": notes,
        }

    def _uncertainty_payload(self, breakdown: GeneratedMoleculeScoreBreakdown) -> dict[str, object]:
        return {
            "score": breakdown.uncertainty_score,
            "confidence": breakdown.confidence,
            "drivers": [
                "limited to deterministic generation, seed evidence, novelty, "
                "and validation signals"
            ],
            "claim_boundary": "uncertainty describes computational triage only",
        }

    def _experiment_readiness_payload(
        self,
        breakdown: GeneratedMoleculeScoreBreakdown,
    ) -> dict[str, object]:
        score = breakdown.experiment_readiness_score
        if score >= 0.70:
            label = "review_ready"
        elif score >= 0.45:
            label = "needs_review"
        else:
            label = "deprioritized"
        return {
            "score": score,
            "label": label,
            "basis": [
                "chemical validation",
                "descriptor fit",
                "non-protocol critique",
                "diversity",
                "uncertainty estimate",
            ],
            "claim_boundary": "readiness is for review queue triage only",
        }

    def _active_learning_payload(
        self,
        breakdown: GeneratedMoleculeScoreBreakdown,
    ) -> dict[str, object]:
        return {
            "priority_score": breakdown.active_learning_priority_score,
            "basis": ["novelty", "uncertainty", "target conditioning", "diversity"],
            "loop": "review-prioritized computational design loop",
            "fabricated_assay_results": False,
        }

    def _explanation(self, candidate: GeneratedMolecule) -> str:
        novelty = candidate.novelty.novelty_class if candidate.novelty else "unknown novelty"
        return (
            "Generated molecule is an in-silico research hypothesis scored separately "
            "from existing evidence-backed molecules. Target conditioning reflects seed "
            "similarity and target disease relevance, not predicted binding affinity. "
            f"Novelty class is {novelty}. Property profile uses coarse descriptor sanity "
            "relative to seed constraints and is not ADMET. Literature context, if present, "
            "comes from parent seed or target context only; the generated molecule has no "
            "direct experimental evidence. V1.1 adds deterministic objective alignment, "
            "uncertainty, diversity, medicinal chemistry critique, experiment-readiness, "
            "and learning-loop triage scores without creating activity or safety claims."
        )

    def _developability_score(self, assessment: DevelopabilityAssessment) -> float:
        return self._clamp(assessment.developability_score)

    def _developability_risk_level(self, assessment: DevelopabilityAssessment) -> str:
        risk_level = str(assessment.metadata.get("risk_level") or "").lower()
        if risk_level in {"critical", "high", "medium", "low", "unknown"}:
            return risk_level
        if assessment.triage_recommendation == "high_risk_flags":
            return "high"
        if assessment.triage_recommendation in {"review_flags", "insufficient_structure"}:
            return "medium"
        return "low"

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
