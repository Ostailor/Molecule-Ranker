from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.generation.chemistry import mol_from_smiles, tanimoto_similarity
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationObjective,
    SeedMolecule,
)


class OracleResult(BaseModel):
    oracle_name: str
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    explanation: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class OracleStackResult(BaseModel):
    generated_id: str
    experiment_worthiness_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    component_scores: dict[str, float] = Field(default_factory=dict)
    risk_flags: list[str] = Field(default_factory=list)
    oracles: list[OracleResult] = Field(default_factory=list)
    explanation: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def oracle_by_name(self, name: str) -> OracleResult:
        for oracle in self.oracles:
            if oracle.oracle_name == name:
                return oracle
        raise KeyError(name)


class MultiObjectiveOracleStack:
    """Deterministic, inspectable oracle stack for experiment-worthiness triage."""

    def score(
        self,
        *,
        candidate: GeneratedMolecule,
        objective: GenerationObjective | None,
        seeds: list[SeedMolecule],
        retained_generated: list[GeneratedMolecule],
        enable_docking: bool = False,
        enable_surrogate: bool = False,
    ) -> OracleStackResult:
        oracles = [
            self._validity_oracle(candidate),
            self._novelty_oracle(candidate),
            self._diversity_oracle(candidate, retained_generated),
            self._seed_similarity_oracle(candidate),
            self._scaffold_novelty_oracle(candidate, seeds),
            self._developability_oracle(candidate),
            self._alert_toxicity_risk_oracle(candidate),
            self._experimental_gap_oracle(candidate),
            self._literature_context_oracle(objective, seeds),
            self._docking_oracle(candidate, enable_docking),
            self._surrogate_activity_oracle(candidate, enable_surrogate),
            self._synthetic_accessibility_heuristic_oracle(candidate),
        ]
        by_name = {oracle.oracle_name: oracle for oracle in oracles}
        target_context_score = self._target_context_score(objective, seeds)
        seed_evidence_score = self._seed_evidence_score(seeds)
        novelty_score = self._avg(
            by_name["novelty_oracle"].score,
            by_name["scaffold_novelty_oracle"].score,
        )
        diversity_score = by_name["diversity_oracle"].score
        developability_score = self._avg(
            by_name["developability_oracle"].score,
            by_name["synthetic_accessibility_heuristic_oracle"].score,
        )
        risk_penalty = self._clamp(1.0 - by_name["alert_toxicity_risk_oracle"].score)
        uncertainty_value = self._uncertainty_value(oracles)
        experimental_gap_value = by_name["experimental_gap_oracle"].score
        structure_score = by_name["docking_oracle"].score if enable_docking else None
        surrogate_score = (
            by_name["surrogate_activity_oracle"].score if enable_surrogate else None
        )
        composite = self._composite(
            target_context_score=target_context_score,
            novelty_score=novelty_score,
            diversity_score=diversity_score,
            developability_score=developability_score,
            risk_penalty=risk_penalty,
            uncertainty_value=uncertainty_value,
            experimental_gap_value=experimental_gap_value,
            seed_evidence_score=seed_evidence_score,
            structure_score=structure_score,
            surrogate_score=surrogate_score,
        )
        risk_flags = sorted({flag for oracle in oracles for flag in oracle.risk_flags})
        if "critical_developability_risk" in risk_flags:
            composite = min(composite, 0.30)
        if "invalid_structure" in risk_flags:
            composite = min(composite, 0.20)
        confidence = self._clamp(sum(oracle.confidence for oracle in oracles) / len(oracles))
        component_scores = {
            "target_context_score": round(target_context_score, 3),
            "novelty_score": round(novelty_score, 3),
            "diversity_score": round(diversity_score, 3),
            "developability_score": round(developability_score, 3),
            "risk_penalty": round(risk_penalty, 3),
            "uncertainty_value": round(uncertainty_value, 3),
            "experimental_gap_value": round(experimental_gap_value, 3),
            "seed_evidence_score": round(seed_evidence_score, 3),
        }
        if structure_score is not None:
            component_scores["structure_score"] = round(structure_score, 3)
        if surrogate_score is not None:
            component_scores["surrogate_score"] = round(surrogate_score, 3)
        return OracleStackResult(
            generated_id=candidate.generated_id,
            experiment_worthiness_score=round(self._clamp(composite), 3),
            confidence=round(confidence, 3),
            component_scores=component_scores,
            risk_flags=risk_flags,
            oracles=oracles,
            explanation=self._explanation(enable_docking, enable_surrogate),
            metadata={
                "score_name": "experiment_worthiness_score",
                "claim_boundary": "computational triage only",
                "docking_enabled": enable_docking,
                "surrogate_enabled": enable_surrogate,
            },
        )

    def _validity_oracle(self, candidate: GeneratedMolecule) -> OracleResult:
        validation = candidate.validation
        checks = [
            validation.valid_rdkit_mol,
            validation.sanitization_ok,
            validation.canonicalization_ok,
            validation.allowed_elements_ok,
            validation.descriptor_bounds_ok,
        ]
        score = sum(1.0 for check in checks if check) / len(checks)
        risk_flags: list[str] = []
        if not validation.valid_rdkit_mol or not validation.sanitization_ok:
            risk_flags.append("invalid_structure")
        if validation.rejection_reasons:
            score *= 0.45
            risk_flags.extend(validation.rejection_reasons)
        return self._oracle(
            "validity_oracle",
            score,
            0.9,
            risk_flags,
            "RDKit validity and deterministic generation checks only.",
            {"validation": validation.model_dump(mode="json")},
        )

    def _novelty_oracle(self, candidate: GeneratedMolecule) -> OracleResult:
        novelty = candidate.novelty
        if novelty is None:
            return self._oracle(
                "novelty_oracle",
                0.4,
                0.35,
                ["novelty_unassessed"],
                "Novelty was not assessed; neutral-low score used.",
                {},
            )
        score = {
            "duplicate": 0.0,
            "near_duplicate": 0.15,
            "close_analog": 0.55,
            "novel_analog": 0.9,
            "distant": 0.45,
        }[novelty.novelty_class]
        risk_flags = [f"novelty_class_{novelty.novelty_class}"]
        if novelty.novelty_class in {"duplicate", "near_duplicate"}:
            risk_flags.append("low_novelty")
        return self._oracle(
            "novelty_oracle",
            score,
            0.75,
            risk_flags,
            "Novelty reflects duplicate and similarity triage, not biological value.",
            novelty.model_dump(mode="json"),
        )

    def _diversity_oracle(
        self,
        candidate: GeneratedMolecule,
        retained_generated: list[GeneratedMolecule],
    ) -> OracleResult:
        if candidate.diversity_cluster:
            same_cluster = sum(
                1
                for retained in retained_generated
                if retained.diversity_cluster == candidate.diversity_cluster
            )
            score = 1.0 / (same_cluster + 1.0)
            return self._oracle(
                "diversity_oracle",
                score,
                0.75,
                ["same_diversity_cluster"] if same_cluster else [],
                "Diversity rewards coverage away from already retained generated clusters.",
                {"diversity_cluster": candidate.diversity_cluster, "same_cluster": same_cluster},
            )
        molecule = mol_from_smiles(candidate.canonical_smiles)
        if molecule is None or not retained_generated:
            return self._oracle(
                "diversity_oracle",
                1.0,
                0.45,
                [],
                (
                    "No retained comparator was available; diversity defaults high with "
                    "low confidence."
                ),
                {},
            )
        similarities = []
        for retained in retained_generated:
            retained_mol = mol_from_smiles(retained.canonical_smiles)
            if retained_mol is not None:
                similarities.append(tanimoto_similarity(molecule, retained_mol))
        return self._oracle(
            "diversity_oracle",
            1.0 - max(similarities, default=0.0),
            0.65,
            [],
            "Diversity uses fingerprint distance from retained generated hypotheses.",
            {"max_similarity_to_retained": max(similarities, default=0.0)},
        )

    def _seed_similarity_oracle(self, candidate: GeneratedMolecule) -> OracleResult:
        similarity = (
            candidate.novelty.max_similarity_to_seed if candidate.novelty is not None else None
        )
        if similarity is None:
            return self._oracle(
                "seed_similarity_oracle",
                0.45,
                0.3,
                ["seed_similarity_unassessed"],
                "Seed similarity missing; neutral-low score used.",
                {},
            )
        score = 1.0 - min(abs(similarity - 0.65) / 0.65, 1.0)
        return self._oracle(
            "seed_similarity_oracle",
            score,
            0.65,
            ["distant_from_seed"] if similarity < 0.25 else [],
            "Seed similarity is a design-context signal, not predicted binding.",
            {"max_similarity_to_seed": similarity, "preferred_range_center": 0.65},
        )

    def _scaffold_novelty_oracle(
        self,
        candidate: GeneratedMolecule,
        seeds: list[SeedMolecule],
    ) -> OracleResult:
        scaffold_id = candidate.metadata.get("scaffold_id") or candidate.metadata.get("scaffold")
        seed_scaffolds = {
            seed.metadata.get("scaffold_id") or seed.metadata.get("scaffold")
            for seed in seeds
            if seed.metadata.get("scaffold_id") or seed.metadata.get("scaffold")
        }
        if not scaffold_id:
            return self._oracle(
                "scaffold_novelty_oracle",
                0.5,
                0.25,
                ["scaffold_not_annotated"],
                "No scaffold annotation was available for this generated hypothesis.",
                {"seed_scaffold_count": len(seed_scaffolds)},
            )
        is_new = scaffold_id not in seed_scaffolds
        return self._oracle(
            "scaffold_novelty_oracle",
            0.85 if is_new else 0.35,
            0.6,
            [] if is_new else ["seed_scaffold_reused"],
            "Scaffold novelty is a structural diversity signal only.",
            {"scaffold_id": scaffold_id, "seed_scaffolds": sorted(map(str, seed_scaffolds))},
        )

    def _developability_oracle(self, candidate: GeneratedMolecule) -> OracleResult:
        assessment = candidate.developability_assessment
        if assessment is None:
            raw = candidate.metadata.get("developability_score")
            score = self._clamp(float(raw)) if isinstance(raw, (int, float)) else 0.5
            return self._oracle(
                "developability_oracle",
                score,
                0.35,
                ["developability_assessment_absent"],
                "Developability assessment absent; neutral score used.",
                {},
            )
        risk_level = str(assessment.metadata.get("risk_level") or "").lower()
        risk_flags = [f"{risk_level}_developability_risk"] if risk_level else []
        if assessment.triage_recommendation == "high_risk_flags":
            risk_flags.append("critical_developability_risk")
        score = assessment.developability_score
        if "critical_developability_risk" in risk_flags:
            score = min(score, 0.15)
        return self._oracle(
            "developability_oracle",
            score,
            0.7,
            sorted(set(risk_flags)),
            "Developability is heuristic triage, not a safety claim.",
            assessment.model_dump(mode="json"),
        )

    def _alert_toxicity_risk_oracle(self, candidate: GeneratedMolecule) -> OracleResult:
        alert_count = len(candidate.validation.pains_or_alerts)
        rejection_count = len(candidate.validation.rejection_reasons)
        risk_level = ""
        if candidate.developability_assessment is not None:
            risk_level = str(
                candidate.developability_assessment.metadata.get("risk_level") or ""
            ).lower()
        score = 1.0 - min(0.85, 0.18 * alert_count + 0.22 * rejection_count)
        risk_flags = [f"alert_{alert}" for alert in candidate.validation.pains_or_alerts]
        if risk_level == "critical":
            score = min(score, 0.1)
            risk_flags.append("critical_developability_risk")
        return self._oracle(
            "alert_toxicity_risk_oracle",
            score,
            0.7,
            risk_flags,
            "Alert/toxicity risk is a conservative structural warning heuristic only.",
            {"alert_count": alert_count, "rejection_count": rejection_count},
        )

    def _experimental_gap_oracle(self, candidate: GeneratedMolecule) -> OracleResult:
        imported_results = candidate.metadata.get("experimental_results")
        if imported_results:
            return self._oracle(
                "experimental_gap_oracle",
                0.35,
                0.6,
                [],
                "Imported experimental context reduces gap value; no result is inferred.",
                {"imported_results_present": True},
            )
        return self._oracle(
            "experimental_gap_oracle",
            0.75,
            0.45,
            ["generated_candidate_has_no_direct_experimental_evidence"],
            "Experimental gap can make a hypothesis useful for triage, not validated.",
            {"imported_results_present": False},
        )

    def _literature_context_oracle(
        self,
        objective: GenerationObjective | None,
        seeds: list[SeedMolecule],
    ) -> OracleResult:
        values: list[float] = []
        if objective is not None:
            raw = objective.metadata.get("literature_context_score")
            if isinstance(raw, (int, float)):
                values.append(float(raw))
        for seed in seeds:
            raw = seed.metadata.get("literature_support_score")
            if isinstance(raw, (int, float)):
                values.append(float(raw))
        score = self._clamp(max(values, default=0.0))
        return self._oracle(
            "literature_context_oracle",
            score,
            0.55 if values else 0.25,
            [] if values else ["literature_context_absent"],
            (
                "Literature context is inherited from target/seed evidence, not the "
                "generated molecule."
            ),
            {"source_values": values},
        )

    def _docking_oracle(self, candidate: GeneratedMolecule, enabled: bool) -> OracleResult:
        raw = candidate.metadata.get("docking_score")
        if not enabled:
            return self._oracle(
                "docking_oracle",
                0.5,
                0.1,
                ["docking_disabled"],
                "Docking is disabled by default and contributes no binding claim.",
                {"enabled": False},
            )
        if not isinstance(raw, (int, float)):
            return self._oracle(
                "docking_oracle",
                0.5,
                0.15,
                ["docking_score_absent"],
                "Docking enabled but no score was imported; neutral weak signal used.",
                {"enabled": True, "available": False},
            )
        score = self._clamp(float(raw))
        return self._oracle(
            "docking_oracle",
            score,
            0.25,
            ["weak_structure_signal"],
            "Optional docking is a weak structural signal, not predicted binding.",
            {"enabled": True, "available": True, "raw_score": raw},
        )

    def _surrogate_activity_oracle(
        self,
        candidate: GeneratedMolecule,
        enabled: bool,
    ) -> OracleResult:
        raw = candidate.metadata.get("surrogate_activity_score")
        if not enabled:
            return self._oracle(
                "surrogate_activity_oracle",
                0.5,
                0.1,
                ["surrogate_absent"],
                "Surrogate activity model is absent or disabled; neutral weak signal used.",
                {"enabled": False, "available": False},
            )
        if not isinstance(raw, (int, float)):
            return self._oracle(
                "surrogate_activity_oracle",
                0.5,
                0.1,
                ["surrogate_absent"],
                "Surrogate enabled but no imported experimental model score exists.",
                {"enabled": True, "available": False},
            )
        return self._oracle(
            "surrogate_activity_oracle",
            self._clamp(float(raw)),
            0.25,
            ["weak_surrogate_signal"],
            "Surrogate score is a weak prioritization signal, not predicted activity.",
            {"enabled": True, "available": True, "raw_score": raw},
        )

    def _synthetic_accessibility_heuristic_oracle(
        self,
        candidate: GeneratedMolecule,
    ) -> OracleResult:
        raw = candidate.metadata.get("synthetic_accessibility_score")
        if isinstance(raw, (int, float)):
            score = self._clamp(float(raw))
            confidence = 0.45
        else:
            heavy_atoms = float(candidate.descriptors.get("heavy_atom_count", 25.0))
            rotatable = float(candidate.descriptors.get("rotatable_bonds", 6.0))
            score = self._clamp(1.0 - max(0.0, heavy_atoms - 35.0) / 50.0 - rotatable / 40.0)
            confidence = 0.3
        return self._oracle(
            "synthetic_accessibility_heuristic_oracle",
            score,
            confidence,
            ["synthetic_accessibility_is_heuristic"],
            "Synthetic-accessibility score is a heuristic only and provides no route.",
            {"route_or_protocol_provided": False},
        )

    def _target_context_score(
        self,
        objective: GenerationObjective | None,
        seeds: list[SeedMolecule],
    ) -> float:
        values: list[float] = []
        if objective is not None:
            raw = objective.metadata.get("target_relevance_score")
            if isinstance(raw, (int, float)):
                values.append(float(raw))
        values.extend(seed.target_relevance_score for seed in seeds)
        return self._clamp(sum(values) / len(values)) if values else 0.0

    def _seed_evidence_score(self, seeds: list[SeedMolecule]) -> float:
        values: list[float] = []
        for seed in seeds:
            raw = seed.metadata.get("seed_score")
            if isinstance(raw, (int, float)):
                values.append(float(raw))
            else:
                values.append(
                    0.55 * seed.best_evidence_confidence
                    + 0.45 * seed.target_relevance_score
                )
        return self._clamp(sum(values) / len(values)) if values else 0.0

    def _uncertainty_value(self, oracles: list[OracleResult]) -> float:
        average_confidence = sum(oracle.confidence for oracle in oracles) / len(oracles)
        # Moderate uncertainty can be valuable for active-learning triage.
        return self._clamp(1.0 - abs((1.0 - average_confidence) - 0.45) / 0.55)

    def _composite(
        self,
        *,
        target_context_score: float,
        novelty_score: float,
        diversity_score: float,
        developability_score: float,
        risk_penalty: float,
        uncertainty_value: float,
        experimental_gap_value: float,
        seed_evidence_score: float,
        structure_score: float | None,
        surrogate_score: float | None,
    ) -> float:
        score = (
            0.16 * target_context_score
            + 0.16 * novelty_score
            + 0.14 * diversity_score
            + 0.16 * developability_score
            + 0.10 * uncertainty_value
            + 0.10 * experimental_gap_value
            + 0.13 * seed_evidence_score
            - 0.20 * risk_penalty
        )
        if structure_score is not None:
            score += 0.03 * structure_score
        if surrogate_score is not None:
            score += 0.05 * surrogate_score
        return self._clamp(score)

    def _explanation(self, enable_docking: bool, enable_surrogate: bool) -> str:
        optional = []
        if enable_docking:
            optional.append("optional docking was treated as a weak structural signal")
        if enable_surrogate:
            optional.append("optional surrogate scoring was treated as a weak model signal")
        optional_text = (
            "; ".join(optional)
            if optional
            else "optional docking and surrogate signals were not used"
        )
        return (
            "Experiment worthiness score is a bounded computational triage score, "
            "not predicted efficacy and not predicted binding. "
            f"{optional_text}. Individual oracles remain inspectable."
        )

    def _oracle(
        self,
        name: str,
        score: float,
        confidence: float,
        risk_flags: list[str],
        explanation: str,
        metadata: dict[str, Any],
    ) -> OracleResult:
        return OracleResult(
            oracle_name=name,
            score=round(self._clamp(score), 3),
            confidence=round(self._clamp(confidence), 3),
            risk_flags=sorted(set(risk_flags)),
            explanation=explanation,
            metadata=metadata,
        )

    def _avg(self, *values: float) -> float:
        return self._clamp(sum(values) / len(values))

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
