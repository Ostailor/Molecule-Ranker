from __future__ import annotations

from statistics import pstdev
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.design.oracles import OracleStackResult
from molecule_ranker.evidence import is_molecule_target_evidence
from molecule_ranker.generation.chemistry import mol_from_smiles, tanimoto_similarity
from molecule_ranker.generation.schemas import GeneratedMolecule, SeedMolecule
from molecule_ranker.schemas import MoleculeCandidate

ApplicabilityDomain = Literal["in_domain", "near_domain", "out_of_domain", "unknown"]
UncertaintyClass = Literal[
    "low_uncertainty",
    "interesting_uncertainty",
    "uncontrolled_risk",
]


class UncertaintyEstimate(BaseModel):
    generated_id: str
    evidence_uncertainty: float = Field(ge=0.0, le=1.0)
    chemical_space_uncertainty: float = Field(ge=0.0, le=1.0)
    model_uncertainty: float = Field(ge=0.0, le=1.0)
    oracle_disagreement: float = Field(ge=0.0, le=1.0)
    applicability_domain: ApplicabilityDomain
    novelty_uncertainty: float = Field(ge=0.0, le=1.0)
    experimental_gap_uncertainty: float = Field(ge=0.0, le=1.0)
    overall_uncertainty: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    active_learning_value: float = Field(ge=0.0, le=1.0)
    uncertainty_class: UncertaintyClass
    risk_flags: list[str] = Field(default_factory=list)
    explanation: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class UncertaintyEstimator:
    """Estimate uncertainty for generated hypotheses without creating efficacy claims."""

    def estimate(
        self,
        *,
        candidate: GeneratedMolecule,
        seeds: list[SeedMolecule],
        known_candidates: list[MoleculeCandidate],
        oracle_result: OracleStackResult | None = None,
    ) -> UncertaintyEstimate:
        seed_distance = self._distance_to_seed_set(candidate, seeds)
        known_distance = self._distance_to_known_candidate_set(candidate, known_candidates)
        generator_disagreement = self._generator_disagreement(candidate)
        oracle_disagreement = self._oracle_disagreement(oracle_result)
        surrogate_variance = self._surrogate_variance(candidate)
        evidence_uncertainty = self._evidence_uncertainty(candidate, seeds)
        experimental_gap_uncertainty = self._experimental_gap_uncertainty(candidate)
        novelty_uncertainty = self._novelty_uncertainty(candidate, seed_distance, known_distance)
        chemical_space_uncertainty = self._clamp(0.55 * seed_distance + 0.45 * known_distance)
        model_uncertainty = self._clamp(
            0.45 * oracle_disagreement
            + 0.30 * generator_disagreement
            + 0.25 * surrogate_variance
        )
        applicability_domain = self._applicability_domain(
            seed_distance,
            known_distance,
            candidate,
        )
        risk_flags = self._risk_flags(
            candidate=candidate,
            applicability_domain=applicability_domain,
            chemical_space_uncertainty=chemical_space_uncertainty,
            model_uncertainty=model_uncertainty,
        )
        overall_uncertainty = self._clamp(
            0.20 * evidence_uncertainty
            + 0.22 * chemical_space_uncertainty
            + 0.18 * model_uncertainty
            + 0.15 * oracle_disagreement
            + 0.12 * novelty_uncertainty
            + 0.13 * experimental_gap_uncertainty
        )
        if applicability_domain == "out_of_domain":
            overall_uncertainty = max(overall_uncertainty, 0.75)
        uncontrolled_risk = self._uncontrolled_risk(
            applicability_domain=applicability_domain,
            risk_flags=risk_flags,
            candidate=candidate,
        )
        uncertainty_class = self._uncertainty_class(
            overall_uncertainty=overall_uncertainty,
            uncontrolled_risk=uncontrolled_risk,
        )
        confidence = self._confidence(
            overall_uncertainty=overall_uncertainty,
            evidence_uncertainty=evidence_uncertainty,
            uncontrolled_risk=uncontrolled_risk,
        )
        active_learning_value = self._active_learning_value(
            uncertainty=overall_uncertainty,
            novelty_uncertainty=novelty_uncertainty,
            experimental_gap_uncertainty=experimental_gap_uncertainty,
            uncontrolled_risk=uncontrolled_risk,
        )
        return UncertaintyEstimate(
            generated_id=candidate.generated_id,
            evidence_uncertainty=round(evidence_uncertainty, 3),
            chemical_space_uncertainty=round(chemical_space_uncertainty, 3),
            model_uncertainty=round(model_uncertainty, 3),
            oracle_disagreement=round(oracle_disagreement, 3),
            applicability_domain=applicability_domain,
            novelty_uncertainty=round(novelty_uncertainty, 3),
            experimental_gap_uncertainty=round(experimental_gap_uncertainty, 3),
            overall_uncertainty=round(overall_uncertainty, 3),
            confidence=round(confidence, 3),
            active_learning_value=round(active_learning_value, 3),
            uncertainty_class=uncertainty_class,
            risk_flags=sorted(set(risk_flags)),
            explanation=self._explanation(uncertainty_class),
            metadata={
                "distance_to_seed_set": round(seed_distance, 3),
                "distance_to_known_evidence_backed_candidate_set": round(known_distance, 3),
                "generator_disagreement": round(generator_disagreement, 3),
                "surrogate_ensemble_variance": round(surrogate_variance, 3),
                "claim_boundary": "uncertainty triage only",
                "out_of_domain_not_ranked_by_novelty_alone": True,
            },
        )

    def _distance_to_seed_set(
        self,
        candidate: GeneratedMolecule,
        seeds: list[SeedMolecule],
    ) -> float:
        candidate_mol = mol_from_smiles(candidate.canonical_smiles)
        if candidate_mol is None or not seeds:
            return 1.0
        similarities = []
        for seed in seeds:
            seed_mol = mol_from_smiles(seed.canonical_smiles)
            if seed_mol is not None:
                similarities.append(tanimoto_similarity(candidate_mol, seed_mol))
        return self._clamp(1.0 - max(similarities, default=0.0))

    def _distance_to_known_candidate_set(
        self,
        candidate: GeneratedMolecule,
        known_candidates: list[MoleculeCandidate],
    ) -> float:
        candidate_mol = mol_from_smiles(candidate.canonical_smiles)
        if candidate_mol is None:
            return 1.0
        similarities = []
        for known in known_candidates:
            if not self._has_direct_molecule_target_evidence(known):
                continue
            known_smiles = self._known_smiles(known)
            if known_smiles is None:
                continue
            known_mol = mol_from_smiles(known_smiles)
            if known_mol is not None:
                similarities.append(tanimoto_similarity(candidate_mol, known_mol))
        if not similarities:
            return 1.0
        return self._clamp(1.0 - max(similarities))

    def _known_smiles(self, candidate: MoleculeCandidate) -> str | None:
        for field in ("canonical_smiles", "isomeric_smiles", "smiles", "canonical_smile"):
            value = candidate.chemical_metadata.get(field) or candidate.identifiers.get(field)
            if value not in (None, ""):
                return str(value)
        return None

    def _has_direct_molecule_target_evidence(self, candidate: MoleculeCandidate) -> bool:
        return any(
            item.source
            and item.source_record_id
            and is_molecule_target_evidence(item)
            for item in candidate.evidence
        )

    def _generator_disagreement(self, candidate: GeneratedMolecule) -> float:
        provenance = candidate.metadata.get("generator_provenance")
        if not isinstance(provenance, list) or len(provenance) <= 1:
            return 0.0
        generator_names = {
            str(item.get("generator_name"))
            for item in provenance
            if isinstance(item, dict) and item.get("generator_name")
        }
        if len(generator_names) <= 1:
            return 0.1
        scores: list[float] = []
        for item in provenance:
            if not isinstance(item, dict):
                continue
            score = item.get("score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        if scores:
            return self._clamp(pstdev(scores))
        return self._clamp(min(0.8, 0.15 * len(generator_names)))

    def _oracle_disagreement(self, oracle_result: OracleStackResult | None) -> float:
        if oracle_result is None or len(oracle_result.oracles) <= 1:
            return 0.0
        scores = [oracle.score for oracle in oracle_result.oracles]
        return self._clamp(pstdev(scores) * 2.0)

    def _surrogate_variance(self, candidate: GeneratedMolecule) -> float:
        raw = candidate.metadata.get("surrogate_ensemble_scores")
        if not isinstance(raw, list) or len(raw) <= 1:
            return 0.0
        values = [float(value) for value in raw if isinstance(value, (int, float))]
        if len(values) <= 1:
            return 0.0
        return self._clamp(pstdev(values) * 2.0)

    def _evidence_uncertainty(
        self,
        candidate: GeneratedMolecule,
        seeds: list[SeedMolecule],
    ) -> float:
        direct_experimental = bool(candidate.metadata.get("direct_experimental_evidence"))
        literature_values: list[float] = []
        for seed in seeds:
            literature_score = seed.metadata.get("literature_support_score")
            if isinstance(literature_score, (int, float)):
                literature_values.append(float(literature_score))
        candidate_literature = candidate.metadata.get("literature_context_score")
        if isinstance(candidate_literature, (int, float)):
            literature_values.append(float(candidate_literature))
        evidence_uncertainty = 0.8 if not direct_experimental else 0.35
        if literature_values:
            evidence_uncertainty -= 0.25 * max(literature_values)
        else:
            evidence_uncertainty += 0.10
        return self._clamp(evidence_uncertainty)

    def _experimental_gap_uncertainty(self, candidate: GeneratedMolecule) -> float:
        if candidate.metadata.get("direct_experimental_evidence"):
            return 0.25
        if candidate.metadata.get("experimental_results"):
            return 0.4
        return 0.85

    def _novelty_uncertainty(
        self,
        candidate: GeneratedMolecule,
        seed_distance: float,
        known_distance: float,
    ) -> float:
        novelty = candidate.novelty.novelty_class if candidate.novelty is not None else "unknown"
        novelty_base = {
            "duplicate": 0.15,
            "near_duplicate": 0.25,
            "close_analog": 0.45,
            "novel_analog": 0.65,
            "distant": 0.9,
            "unknown": 0.55,
        }[novelty]
        return self._clamp(0.50 * novelty_base + 0.25 * seed_distance + 0.25 * known_distance)

    def _applicability_domain(
        self,
        seed_distance: float,
        known_distance: float,
        candidate: GeneratedMolecule,
    ) -> ApplicabilityDomain:
        if not candidate.validation.valid_rdkit_mol:
            return "unknown"
        nearest_distance = min(seed_distance, known_distance)
        if nearest_distance <= 0.45:
            return "in_domain"
        if nearest_distance <= 0.72:
            return "near_domain"
        return "out_of_domain"

    def _risk_flags(
        self,
        *,
        candidate: GeneratedMolecule,
        applicability_domain: ApplicabilityDomain,
        chemical_space_uncertainty: float,
        model_uncertainty: float,
    ) -> list[str]:
        flags: list[str] = []
        if applicability_domain == "out_of_domain":
            flags.append("out_of_domain")
        if applicability_domain == "unknown":
            flags.append("unknown_applicability_domain")
        if chemical_space_uncertainty > 0.75:
            flags.append("high_chemical_space_uncertainty")
        if model_uncertainty > 0.65:
            flags.append("high_model_uncertainty")
        if candidate.validation.rejection_reasons:
            flags.extend(candidate.validation.rejection_reasons)
        if candidate.validation.pains_or_alerts:
            flags.extend([f"alert_{alert}" for alert in candidate.validation.pains_or_alerts])
        return flags

    def _uncontrolled_risk(
        self,
        *,
        applicability_domain: ApplicabilityDomain,
        risk_flags: list[str],
        candidate: GeneratedMolecule,
    ) -> bool:
        if applicability_domain == "out_of_domain":
            return True
        if candidate.validation.rejection_reasons or candidate.validation.pains_or_alerts:
            return True
        return any(
            flag in {"high_chemical_space_uncertainty", "high_model_uncertainty"}
            for flag in risk_flags
        )

    def _uncertainty_class(
        self,
        *,
        overall_uncertainty: float,
        uncontrolled_risk: bool,
    ) -> UncertaintyClass:
        if uncontrolled_risk:
            return "uncontrolled_risk"
        if overall_uncertainty >= 0.45:
            return "interesting_uncertainty"
        return "low_uncertainty"

    def _confidence(
        self,
        *,
        overall_uncertainty: float,
        evidence_uncertainty: float,
        uncontrolled_risk: bool,
    ) -> float:
        confidence = 1.0 - 0.55 * overall_uncertainty - 0.25 * evidence_uncertainty
        if uncontrolled_risk:
            confidence -= 0.20
        return self._clamp(confidence)

    def _active_learning_value(
        self,
        *,
        uncertainty: float,
        novelty_uncertainty: float,
        experimental_gap_uncertainty: float,
        uncontrolled_risk: bool,
    ) -> float:
        value = (
            0.45 * self._interesting_uncertainty_value(uncertainty)
            + 0.30 * novelty_uncertainty
            + 0.25 * experimental_gap_uncertainty
        )
        if uncontrolled_risk:
            value *= 0.45
        return self._clamp(value)

    def _interesting_uncertainty_value(self, uncertainty: float) -> float:
        return self._clamp(1.0 - abs(uncertainty - 0.6) / 0.6)

    def _explanation(self, uncertainty_class: UncertaintyClass) -> str:
        if uncertainty_class == "uncontrolled_risk":
            prefix = "Uncontrolled risk should lower confidence and ranking priority."
        elif uncertainty_class == "interesting_uncertainty":
            prefix = "Interesting uncertainty can increase active-learning value."
        else:
            prefix = "Low uncertainty supports confidence in computational triage inputs."
        return (
            f"{prefix} This is active-learning uncertainty triage only, "
            "not predicted efficacy and not predicted binding."
        )

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
