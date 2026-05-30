from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.structure.schemas import (
    ApplicabilityDomain,
    DockingPose,
    ProteinLigandInteractionProfile,
    StructureAwareAssessment,
    StructureRecommendation,
)

LigandOriginForRescoring = Literal["existing", "generated"]


class RescoringConfig(BaseModel):
    docking_weight: float = Field(default=0.2, ge=0.0, le=0.35)
    pose_qc_weight: float = Field(default=0.18, ge=0.0, le=1.0)
    interaction_weight: float = Field(default=0.16, ge=0.0, le=1.0)
    reference_similarity_weight: float = Field(default=0.08, ge=0.0, le=1.0)
    structure_confidence_weight: float = Field(default=0.14, ge=0.0, le=1.0)
    receptor_confidence_weight: float = Field(default=0.08, ge=0.0, le=1.0)
    ligand_confidence_weight: float = Field(default=0.08, ge=0.0, le=1.0)
    surrogate_weight: float = Field(default=0.04, ge=0.0, le=1.0)
    developability_weight: float = Field(default=0.04, ge=0.0, le=1.0)
    docking_only_max_consensus: float = Field(default=0.4, ge=0.0, le=1.0)
    retain_threshold: float = Field(default=0.62, ge=0.0, le=1.0)
    deprioritize_threshold: float = Field(default=0.35, ge=0.0, le=1.0)


def score_structure_aware_assessment(
    *,
    molecule_id: str,
    molecule_name: str,
    target_symbol: str,
    ligand_origin: LigandOriginForRescoring,
    structure_id: str | None,
    applicability_domain: ApplicabilityDomain,
    structure_selection_confidence: float = 0.7,
    receptor_preparation_confidence: float = 0.6,
    ligand_preparation_confidence: float = 0.7,
    poses: list[DockingPose] | None = None,
    interaction_profiles: list[ProteinLigandInteractionProfile] | None = None,
    calibrated_surrogate_score: float | None = None,
    developability_score: float | None = None,
    config: RescoringConfig | dict[str, Any] | None = None,
) -> StructureAwareAssessment:
    scoring_config = _config(config)
    pose_list = poses or []
    profile_list = interaction_profiles or []

    docking_score = _best_docking_score(pose_list)
    pose_qc_score = _pose_qc_score(pose_list)
    interaction_score = _interaction_score(profile_list)
    reference_similarity = _best_reference_similarity(profile_list)
    structure_score = _bounded(structure_selection_confidence) * _domain_multiplier(
        applicability_domain
    )
    receptor_score = _bounded(receptor_preparation_confidence)
    ligand_score = _bounded(ligand_preparation_confidence)
    surrogate_score = _bounded_optional(calibrated_surrogate_score)
    developability = _bounded_optional(developability_score)

    component_scores = {
        "docking_score": docking_score,
        "pose_qc_score": pose_qc_score,
        "interaction_profile_score": interaction_score,
        "reference_ligand_similarity": reference_similarity,
        "structure_selection_confidence": structure_score,
        "receptor_preparation_confidence": receptor_score,
        "ligand_preparation_confidence": ligand_score,
        "calibrated_surrogate_score": surrogate_score,
        "developability_score": developability,
    }
    weighted = _weighted_consensus(component_scores, scoring_config)
    consensus = weighted * _domain_multiplier(applicability_domain)
    if ligand_origin == "generated":
        consensus *= 0.92
    if _is_docking_only_signal(
        docking_score=docking_score,
        pose_qc_score=pose_qc_score,
        interaction_score=interaction_score,
        structure_score=structure_score,
        receptor_score=receptor_score,
        ligand_score=ligand_score,
        surrogate_score=surrogate_score,
        developability_score=developability,
    ):
        consensus = min(consensus, scoring_config.docking_only_max_consensus)
    if _has_rejected_pose(pose_list):
        consensus *= 0.65
    consensus = _bounded(consensus)

    warnings = _warnings(
        ligand_origin=ligand_origin,
        applicability_domain=applicability_domain,
        docking_only=_is_docking_only_signal(
            docking_score=docking_score,
            pose_qc_score=pose_qc_score,
            interaction_score=interaction_score,
            structure_score=structure_score,
            receptor_score=receptor_score,
            ligand_score=ligand_score,
            surrogate_score=surrogate_score,
            developability_score=developability,
        ),
        rejected_pose=_has_rejected_pose(pose_list),
    )
    recommendation = _recommendation(consensus, applicability_domain, scoring_config, pose_list)
    pose_confidence = _bounded(_mean([pose.confidence for pose in pose_list], default=0.0))
    return StructureAwareAssessment(
        assessment_id=f"structure-aware-assessment-{_safe_id(molecule_id)}-{_safe_id(target_symbol)}",
        molecule_id=molecule_id,
        molecule_name=molecule_name,
        target_symbol=target_symbol,
        structure_id=structure_id,
        docking_pose_ids=[pose.pose_id for pose in pose_list],
        structure_score=structure_score,
        pose_confidence=pose_confidence,
        interaction_score=interaction_score,
        consensus_score=consensus,
        applicability_domain=applicability_domain,
        recommendation=recommendation,
        warnings=warnings,
        explanation=(
            "Conservative computational structure-aware review score. It is not a "
            "predicted binding affinity, not activity evidence, and not proof of binding."
        ),
        metadata={
            "component_scores": component_scores,
            "component_weights": _weights(scoring_config),
            "score_is_not_predicted_binding_affinity": True,
            "docking_score_capped_as_weak_signal": True,
            "docking_scores_not_proof_of_binding": True,
            "structure_scores_not_activity_evidence": True,
            "generated_molecule_caution": ligand_origin == "generated",
            "calibrated_surrogate_included": calibrated_surrogate_score is not None,
            "developability_score_included": developability_score is not None,
        },
    )


def _config(config: RescoringConfig | dict[str, Any] | None) -> RescoringConfig:
    if isinstance(config, RescoringConfig):
        return config
    if isinstance(config, dict):
        return RescoringConfig(**config)
    return RescoringConfig()


def _best_docking_score(poses: list[DockingPose]) -> float:
    scores = [_bounded(pose.docking_score) for pose in poses if pose.docking_score is not None]
    return max(scores, default=0.0)


def _pose_qc_score(poses: list[DockingPose]) -> float:
    if not poses:
        return 0.0
    scores = [_pose_quality_score(pose.pose_quality) for pose in poses]
    return _bounded(_mean(scores, default=0.0))


def _pose_quality_score(pose_quality: dict[str, Any]) -> float:
    status = str(pose_quality.get("status") or "").lower()
    if status == "pass":
        return 0.8
    if status == "deprioritize":
        return 0.35
    if status == "reject":
        return 0.05
    checks = pose_quality.get("checks")
    if isinstance(checks, dict) and checks:
        passed = sum(1 for value in checks.values() if value is True)
        return passed / len(checks)
    return 0.0


def _interaction_score(profiles: list[ProteinLigandInteractionProfile]) -> float:
    if not profiles:
        return 0.0
    scores: list[float] = []
    for profile in profiles:
        contact_count = sum(profile.interaction_counts.values())
        contact_score = min(1.0, contact_count / 6.0)
        scores.append(_bounded(0.55 * profile.confidence + 0.45 * contact_score))
    return _bounded(_mean(scores, default=0.0))


def _best_reference_similarity(profiles: list[ProteinLigandInteractionProfile]) -> float:
    similarities = [
        _bounded(profile.reference_similarity)
        for profile in profiles
        if profile.reference_similarity is not None
    ]
    return max(similarities, default=0.0)


def _weighted_consensus(
    component_scores: dict[str, float],
    config: RescoringConfig,
) -> float:
    weights = _weights(config)
    weighted_sum = sum(component_scores[name] * weight for name, weight in weights.items())
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return 0.0
    return _bounded(weighted_sum / total_weight)


def _weights(config: RescoringConfig) -> dict[str, float]:
    return {
        "docking_score": min(config.docking_weight, 0.25),
        "pose_qc_score": config.pose_qc_weight,
        "interaction_profile_score": config.interaction_weight,
        "reference_ligand_similarity": config.reference_similarity_weight,
        "structure_selection_confidence": config.structure_confidence_weight,
        "receptor_preparation_confidence": config.receptor_confidence_weight,
        "ligand_preparation_confidence": config.ligand_confidence_weight,
        "calibrated_surrogate_score": config.surrogate_weight,
        "developability_score": config.developability_weight,
    }


def _domain_multiplier(applicability_domain: str) -> float:
    if applicability_domain == "suitable_experimental_structure":
        return 1.0
    if applicability_domain == "lower_confidence_predicted_structure":
        return 0.72
    if applicability_domain == "weak_or_unknown_structure":
        return 0.5
    return 0.0


def _is_docking_only_signal(
    *,
    docking_score: float,
    pose_qc_score: float,
    interaction_score: float,
    structure_score: float,
    receptor_score: float,
    ligand_score: float,
    surrogate_score: float,
    developability_score: float,
) -> bool:
    supporting = [
        pose_qc_score,
        interaction_score,
        structure_score,
        receptor_score,
        ligand_score,
        surrogate_score,
        developability_score,
    ]
    return docking_score > 0.0 and max(supporting, default=0.0) <= 0.2


def _has_rejected_pose(poses: list[DockingPose]) -> bool:
    return any(str(pose.pose_quality.get("status") or "").lower() == "reject" for pose in poses)


def _warnings(
    *,
    ligand_origin: str,
    applicability_domain: str,
    docking_only: bool,
    rejected_pose: bool,
) -> list[str]:
    warnings = [
        "Consensus score is a computational review signal, not predicted binding affinity.",
        "Docking scores are weak computational signals and not proof of binding.",
        "Structure-aware scores are not activity evidence.",
    ]
    if applicability_domain == "lower_confidence_predicted_structure":
        warnings.append(
            "Predicted structures are lower-confidence than suitable experimental structures."
        )
    elif applicability_domain in {"weak_or_unknown_structure", "unavailable"}:
        warnings.append("Weak or unavailable structure lowers applicability.")
    if rejected_pose:
        warnings.append("Poor pose QC lowers the structure-aware consensus score.")
    if docking_only:
        warnings.append("Docking score alone cannot support a high-confidence recommendation.")
    if ligand_origin == "generated":
        warnings.append("Generated molecule remains a computational hypothesis requiring caution.")
    return sorted(set(warnings))


def _recommendation(
    consensus: float,
    applicability_domain: str,
    config: RescoringConfig,
    poses: list[DockingPose],
) -> StructureRecommendation:
    if applicability_domain == "unavailable":
        return "needs_structure_review"
    if _has_rejected_pose(poses):
        return "deprioritize" if consensus >= config.deprioritize_threshold else "reject"
    if consensus >= config.retain_threshold:
        return "retain_for_review"
    if consensus >= config.deprioritize_threshold:
        return "deprioritize"
    return "needs_structure_review"


def _bounded_optional(value: float | None) -> float:
    if value is None:
        return 0.0
    return _bounded(value)


def _bounded(value: float | None) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, parsed)), 3)


def _mean(values: list[float], *, default: float) -> float:
    if not values:
        return default
    return sum(values) / len(values)


def _safe_id(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value).strip(
        "-"
    )


__all__ = ["RescoringConfig", "score_structure_aware_assessment"]
