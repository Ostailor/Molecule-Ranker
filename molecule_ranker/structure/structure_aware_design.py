from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.generation.schemas import GeneratedMolecule
from molecule_ranker.structure.schemas import StructureAwareAssessment


class StructureAwareDesignConfig(BaseModel):
    batch_size: int = Field(default=8, ge=1)
    max_per_diversity_cluster: int = Field(default=1, ge=1)
    poor_pose_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    docking_score_weight: float = Field(default=0.04, ge=0.0, le=0.08)


class StructureAwareCandidateSignal(BaseModel):
    molecule_id: str
    canonical_smiles: str
    diversity_cluster: str | None = None
    structure_aware_score: float = Field(ge=0.0, le=1.0)
    structure_consensus_score: float = Field(ge=0.0, le=1.0)
    docking_score: float = Field(ge=0.0, le=1.0)
    pose_qc_score: float = Field(ge=0.0, le=1.0)
    interaction_score: float = Field(ge=0.0, le=1.0)
    developability_score: float = Field(ge=0.0, le=1.0)
    novelty_score: float = Field(ge=0.0, le=1.0)
    diversity_score: float = Field(ge=0.0, le=1.0)
    uncertainty_manageability_score: float = Field(ge=0.0, le=1.0)
    applicability_domain: str
    selected: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    explanation: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructureAwareGenerationLoopResult(BaseModel):
    selected_candidates: list[StructureAwareCandidateSignal] = Field(default_factory=list)
    candidate_signals: list[StructureAwareCandidateSignal] = Field(default_factory=list)
    report: dict[str, Any] = Field(default_factory=dict)
    report_markdown: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def candidate_by_id(self, molecule_id: str) -> StructureAwareCandidateSignal:
        for signal in self.candidate_signals:
            if signal.molecule_id == molecule_id:
                return signal
        raise KeyError(molecule_id)


class StructureAwareGenerationLoop:
    """Plan next generated-parent selection using conservative structure-aware prioritization."""

    def plan_next_round(
        self,
        *,
        generated_candidates: Sequence[GeneratedMolecule],
        assessments: Sequence[StructureAwareAssessment],
        batch_size: int | None = None,
        config: StructureAwareDesignConfig | Mapping[str, Any] | None = None,
    ) -> StructureAwareGenerationLoopResult:
        loop_config = self._config(config)
        if batch_size is not None:
            loop_config = loop_config.model_copy(update={"batch_size": max(1, int(batch_size))})
        assessments_by_id = {assessment.molecule_id: assessment for assessment in assessments}
        signals = [
            self._candidate_signal(
                candidate,
                assessments_by_id.get(candidate.generated_id),
                loop_config,
            )
            for candidate in generated_candidates
        ]
        selected = self._select_diverse(signals, loop_config)
        selected_ids = {signal.molecule_id for signal in selected}
        finalized = [
            signal.model_copy(update={"selected": signal.molecule_id in selected_ids})
            for signal in signals
        ]
        selected_final = [
            signal for signal in finalized if signal.molecule_id in selected_ids
        ]
        report = self._report(selected_final, finalized)
        return StructureAwareGenerationLoopResult(
            selected_candidates=selected_final,
            candidate_signals=finalized,
            report=report,
            report_markdown=self._report_markdown(report, finalized),
            metadata={
                "selection_basis": "structure-aware prioritization",
                "human_review_required": True,
                "diversity_preserved": self._diversity_preserved(selected_final),
                "docking_score_not_sole_basis": True,
                "structure_scores_not_activity_evidence": True,
            },
        )

    def _candidate_signal(
        self,
        candidate: GeneratedMolecule,
        assessment: StructureAwareAssessment | None,
        config: StructureAwareDesignConfig,
    ) -> StructureAwareCandidateSignal:
        component_scores = self._assessment_component_scores(assessment)
        consensus = self._bounded(assessment.consensus_score if assessment else 0.5)
        docking = self._bounded(component_scores.get("docking_score", 0.0))
        pose_qc = self._pose_qc_score(assessment, component_scores)
        interaction = self._bounded(
            assessment.interaction_score
            if assessment
            else component_scores.get("interaction_profile_score", 0.0)
        )
        developability = self._developability_score(candidate)
        novelty = self._novelty_score(candidate)
        diversity = self._diversity_score(candidate)
        uncertainty = self._uncertainty_manageability_score(candidate)
        applicability_domain = (
            assessment.applicability_domain if assessment else "unavailable"
        )
        risk_flags = self._risk_flags(
            candidate=candidate,
            assessment=assessment,
            component_scores=component_scores,
            pose_qc=pose_qc,
            interaction=interaction,
            config=config,
        )
        raw_score = (
            0.22 * pose_qc
            + 0.18 * interaction
            + 0.18 * developability
            + 0.14 * novelty
            + 0.12 * diversity
            + 0.10 * uncertainty
            + 0.06 * consensus
            + config.docking_score_weight * docking
        )
        score = self._bounded(raw_score - self._risk_penalty(risk_flags, applicability_domain))
        if self._selection_blocked(risk_flags):
            score = min(score, 0.25)
        return StructureAwareCandidateSignal(
            molecule_id=candidate.generated_id,
            canonical_smiles=candidate.canonical_smiles,
            diversity_cluster=candidate.diversity_cluster,
            structure_aware_score=round(score, 3),
            structure_consensus_score=round(consensus, 3),
            docking_score=round(docking, 3),
            pose_qc_score=round(pose_qc, 3),
            interaction_score=round(interaction, 3),
            developability_score=round(developability, 3),
            novelty_score=round(novelty, 3),
            diversity_score=round(diversity, 3),
            uncertainty_manageability_score=round(uncertainty, 3),
            applicability_domain=str(applicability_domain),
            risk_flags=sorted(set(risk_flags)),
            warnings=self._warnings(assessment, risk_flags),
            explanation=(
                "Structure-aware prioritization combines pose QC, interaction annotations, "
                "developability, novelty, uncertainty, and diversity. It does not claim "
                "improved binding, activity, safety, or validation."
            ),
            metadata={
                "assessment_id": assessment.assessment_id if assessment else None,
                "human_review_required": True,
                "docking_score_weight": config.docking_score_weight,
                "structure_scores_not_activity_evidence": True,
                "generated_molecule_remains_computational_hypothesis": True,
            },
        )

    def _select_diverse(
        self,
        signals: Sequence[StructureAwareCandidateSignal],
        config: StructureAwareDesignConfig,
    ) -> list[StructureAwareCandidateSignal]:
        ranked = sorted(
            signals,
            key=lambda signal: (
                signal.structure_aware_score,
                signal.diversity_score,
                signal.novelty_score,
            ),
            reverse=True,
        )
        selected: list[StructureAwareCandidateSignal] = []
        cluster_counts: dict[str, int] = {}
        for signal in ranked:
            if self._selection_blocked(signal.risk_flags):
                continue
            cluster = signal.diversity_cluster or signal.molecule_id
            if cluster_counts.get(cluster, 0) >= config.max_per_diversity_cluster:
                continue
            selected.append(signal)
            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
            if len(selected) >= config.batch_size:
                return selected
        for signal in ranked:
            if len(selected) >= config.batch_size:
                break
            if signal in selected or self._selection_blocked(signal.risk_flags):
                continue
            selected.append(signal)
        return selected

    def _risk_flags(
        self,
        *,
        candidate: GeneratedMolecule,
        assessment: StructureAwareAssessment | None,
        component_scores: Mapping[str, float],
        pose_qc: float,
        interaction: float,
        config: StructureAwareDesignConfig,
    ) -> list[str]:
        flags: list[str] = []
        if assessment is None:
            flags.append("structure_context_unavailable")
        else:
            domain = assessment.applicability_domain
            if domain == "lower_confidence_predicted_structure":
                flags.append("predicted_only_weak_structure_context")
            if domain in {"weak_or_unknown_structure", "unavailable"}:
                flags.append("out_of_domain_structure_context")
            if assessment.recommendation == "reject" or pose_qc <= config.poor_pose_threshold:
                flags.append("poor_structure_pose_qc")
            if self._contains_text(assessment.warnings, "clash"):
                flags.append("severe_pose_clash")
        if (
            component_scores.get("docking_score", 0.0) >= 0.85
            and pose_qc < 0.3
            and interaction < 0.3
        ):
            flags.append("docking_score_alone_not_sufficient")
        if self._developability_risk(candidate) == "critical":
            flags.append("critical_developability_risk")
        if not candidate.validation.valid_rdkit_mol or candidate.validation.rejection_reasons:
            flags.append("invalid_generated_molecule")
        return flags

    def _risk_penalty(self, risk_flags: Sequence[str], applicability_domain: str) -> float:
        penalty = 0.0
        if "predicted_only_weak_structure_context" in risk_flags:
            penalty += 0.08
        if "out_of_domain_structure_context" in risk_flags:
            penalty += 0.14
        if "poor_structure_pose_qc" in risk_flags:
            penalty += 0.35
        if "severe_pose_clash" in risk_flags:
            penalty += 0.30
        if "docking_score_alone_not_sufficient" in risk_flags:
            penalty += 0.22
        if "critical_developability_risk" in risk_flags:
            penalty += 0.28
        if "invalid_generated_molecule" in risk_flags:
            penalty += 0.5
        if applicability_domain == "lower_confidence_predicted_structure":
            penalty += 0.04
        return penalty

    def _selection_blocked(self, risk_flags: Sequence[str]) -> bool:
        return any(
            flag in risk_flags
            for flag in {
                "poor_structure_pose_qc",
                "severe_pose_clash",
                "docking_score_alone_not_sufficient",
                "critical_developability_risk",
                "invalid_generated_molecule",
            }
        )

    def _report(
        self,
        selected: Sequence[StructureAwareCandidateSignal],
        signals: Sequence[StructureAwareCandidateSignal],
    ) -> dict[str, Any]:
        return {
            "title": "Structure-aware generation loop",
            "selection_basis": "structure-aware prioritization",
            "selected_molecule_ids": [signal.molecule_id for signal in selected],
            "candidate_count": len(signals),
            "selected_count": len(selected),
            "human_review_required": True,
            "limitations": [
                "Docking scores are not proof of binding.",
                "A pose is not experimental evidence.",
                "Structure-aware scores are not activity evidence.",
                "Generated molecules remain computational hypotheses.",
            ],
            "candidate_summaries": [
                {
                    "molecule_id": signal.molecule_id,
                    "selected": signal.selected,
                    "structure_aware_score": signal.structure_aware_score,
                    "risk_flags": signal.risk_flags,
                }
                for signal in signals
            ],
        }

    def _report_markdown(
        self,
        report: Mapping[str, Any],
        signals: Sequence[StructureAwareCandidateSignal],
    ) -> str:
        lines = [
            "# Structure-aware generation loop",
            "",
            "Selection basis: structure-aware prioritization.",
            "Human review remains required.",
            "Docking scores are not proof of binding.",
            "A pose is not experimental evidence.",
            "Structure-aware scores are not activity evidence.",
            "Generated molecules remain computational hypotheses.",
            "",
            "| Molecule | Selected | Score | Risk flags |",
            "| --- | --- | --- | --- |",
        ]
        for signal in signals:
            risks = ", ".join(signal.risk_flags) if signal.risk_flags else "none"
            lines.append(
                f"| {signal.molecule_id} | {signal.selected} | "
                f"{signal.structure_aware_score:.3f} | {risks} |"
            )
        lines.append("")
        lines.append(
            "This report supports structure-aware prioritization only; it does not "
            "claim improved binding, activity, safety, or validation."
        )
        return "\n".join(lines)

    def _warnings(
        self,
        assessment: StructureAwareAssessment | None,
        risk_flags: Sequence[str],
    ) -> list[str]:
        warnings = set(assessment.warnings if assessment else [])
        warnings.add("structure_aware_prioritization_requires_human_review")
        if risk_flags:
            warnings.add("structure_aware_risk_flags_present")
        return sorted(warnings)

    def _assessment_component_scores(
        self,
        assessment: StructureAwareAssessment | None,
    ) -> dict[str, float]:
        if assessment is None:
            return {}
        scores = assessment.metadata.get("component_scores")
        if not isinstance(scores, Mapping):
            return {}
        return {
            str(key): self._bounded(value)
            for key, value in scores.items()
            if isinstance(value, (int, float))
        }

    def _pose_qc_score(
        self,
        assessment: StructureAwareAssessment | None,
        component_scores: Mapping[str, float],
    ) -> float:
        if assessment is None:
            return 0.5
        if "pose_qc_score" in component_scores:
            return self._bounded(component_scores["pose_qc_score"])
        return self._bounded(assessment.pose_confidence)

    def _developability_score(self, candidate: GeneratedMolecule) -> float:
        assessment = candidate.developability_assessment
        if assessment is not None:
            return self._bounded(assessment.developability_score)
        breakdown = candidate.score_breakdown
        if breakdown is not None:
            return self._bounded(breakdown.developability_score)
        oracle = candidate.metadata.get("oracle_scoring")
        if isinstance(oracle, Mapping):
            components = oracle.get("component_scores")
            if isinstance(components, Mapping):
                value = components.get("developability_score")
                if isinstance(value, (int, float)):
                    return self._bounded(value)
        return 0.5

    def _developability_risk(self, candidate: GeneratedMolecule) -> str:
        assessment = candidate.developability_assessment
        if assessment is None:
            return "unknown"
        risk = str(assessment.metadata.get("risk_level") or "").lower()
        if risk:
            return risk
        if assessment.triage_recommendation == "high_risk_flags":
            return "critical"
        return "low"

    def _novelty_score(self, candidate: GeneratedMolecule) -> float:
        if candidate.score_breakdown is not None:
            return self._bounded(candidate.score_breakdown.novelty_score)
        if candidate.novelty is None:
            return 0.5
        novelty_scores = {
            "duplicate": 0.05,
            "near_duplicate": 0.25,
            "close_analog": 0.55,
            "novel_analog": 0.8,
            "distant": 0.65,
        }
        return novelty_scores.get(candidate.novelty.novelty_class, 0.5)

    def _diversity_score(self, candidate: GeneratedMolecule) -> float:
        if candidate.score_breakdown is not None:
            return self._bounded(candidate.score_breakdown.diversity_score)
        return 0.65 if candidate.diversity_cluster else 0.5

    def _uncertainty_manageability_score(self, candidate: GeneratedMolecule) -> float:
        uncertainty = candidate.metadata.get("uncertainty")
        if isinstance(uncertainty, Mapping):
            value = uncertainty.get("overall_uncertainty")
            if isinstance(value, (int, float)):
                return self._bounded(1.0 - float(value))
        if candidate.score_breakdown is not None:
            return self._bounded(1.0 - candidate.score_breakdown.uncertainty_score)
        return 0.5

    def _diversity_preserved(
        self,
        selected: Sequence[StructureAwareCandidateSignal],
    ) -> bool:
        clusters = [signal.diversity_cluster or signal.molecule_id for signal in selected]
        return len(clusters) == len(set(clusters))

    def _contains_text(self, values: Sequence[str], needle: str) -> bool:
        lowered = needle.lower()
        return any(lowered in str(value).lower() for value in values)

    def _config(
        self,
        config: StructureAwareDesignConfig | Mapping[str, Any] | None,
    ) -> StructureAwareDesignConfig:
        if isinstance(config, StructureAwareDesignConfig):
            return config
        if isinstance(config, Mapping):
            return StructureAwareDesignConfig(**dict(config))
        return StructureAwareDesignConfig()

    def _bounded(self, value: float | int | None) -> float:
        if value is None:
            return 0.0
        return max(0.0, min(float(value), 1.0))


__all__ = [
    "StructureAwareCandidateSignal",
    "StructureAwareDesignConfig",
    "StructureAwareGenerationLoop",
    "StructureAwareGenerationLoopResult",
]
