from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.structure.schemas import BindingSiteDefinition, DockingPose


class PoseQCConfig(BaseModel):
    expect_pose_file: bool = True
    severe_clash_distance_angstrom: float = Field(default=1.2, gt=0.0)
    reject_on_failed_required_checks: bool = True
    protein_coordinates: list[list[float]] = Field(default_factory=list)
    reference_ligand_coordinates: list[list[float]] | None = None
    repeated_pose_coordinates: list[list[list[float]]] = Field(default_factory=list)
    reproducibility_rmsd_threshold: float = Field(default=2.0, gt=0.0)
    fragment_heavy_atom_threshold: int = Field(default=8, ge=1)
    large_ligand_heavy_atom_threshold: int = Field(default=70, ge=1)
    minimum_reasonable_raw_score: float = -40.0
    maximum_reasonable_raw_score: float = 25.0


def evaluate_pose_quality(
    pose: DockingPose,
    binding_site: BindingSiteDefinition,
    *,
    config: PoseQCConfig | dict[str, Any] | None = None,
) -> DockingPose:
    qc_config = _config(config)
    warnings = list(pose.warnings)
    checks: dict[str, bool] = {}
    quality: dict[str, Any] = {
        "qc_does_not_prove_binding": True,
        "not_experimental_evidence": True,
    }

    checks["docking_score_present"] = pose.docking_score is not None
    if not checks["docking_score_present"]:
        warnings.append("Docking score is missing for this pose.")

    checks["pose_file_exists"] = _pose_file_exists(pose, qc_config)
    if qc_config.expect_pose_file and not checks["pose_file_exists"]:
        warnings.append("Expected pose file is missing or unavailable.")

    input_heavy_atoms = _optional_int(pose.metadata.get("input_heavy_atom_count"))
    pose_heavy_atoms = _optional_int(pose.metadata.get("pose_heavy_atom_count"))
    checks["ligand_heavy_atoms_preserved"] = (
        input_heavy_atoms is not None
        and pose_heavy_atoms is not None
        and input_heavy_atoms == pose_heavy_atoms
    )
    quality["input_heavy_atom_count"] = input_heavy_atoms
    quality["pose_heavy_atom_count"] = pose_heavy_atoms
    if not checks["ligand_heavy_atoms_preserved"]:
        warnings.append("Ligand heavy atoms were not confirmed as preserved in the pose.")

    _add_size_warnings(input_heavy_atoms, warnings, qc_config)

    ligand_coordinates = _coordinates(pose.metadata.get("ligand_coordinates"))
    quality["ligand_coordinate_count"] = len(ligand_coordinates)
    checks["ligand_coordinates_available"] = bool(ligand_coordinates)
    if not ligand_coordinates:
        warnings.append("Ligand pose coordinates are unavailable for pose QC.")

    checks["ligand_within_binding_site_box"] = _within_binding_site_box(
        ligand_coordinates,
        binding_site,
    )
    if not checks["ligand_within_binding_site_box"]:
        warnings.append("Ligand pose coordinates are outside the binding-site box.")

    severe_clashes = _severe_clash_count(
        ligand_coordinates,
        _coordinates(qc_config.protein_coordinates),
        qc_config.severe_clash_distance_angstrom,
    )
    quality["severe_clash_count"] = severe_clashes
    checks["no_severe_clashes"] = severe_clashes == 0
    if severe_clashes:
        warnings.append("Severe pose clash detected by simple distance heuristic.")

    raw_score = pose.metadata.get("raw_docking_score")
    checks["pose_energy_sane"] = _energy_sane(raw_score, qc_config)
    quality["raw_docking_score"] = raw_score
    if not checks["pose_energy_sane"]:
        warnings.append("Pose energy sanity check failed.")

    _add_reference_rmsd(
        ligand_coordinates=ligand_coordinates,
        reference_coordinates=qc_config.reference_ligand_coordinates,
        quality=quality,
        checks=checks,
        warnings=warnings,
    )
    _add_reproducibility(
        ligand_coordinates=ligand_coordinates,
        repeated_pose_coordinates=qc_config.repeated_pose_coordinates,
        quality=quality,
        checks=checks,
        warnings=warnings,
        threshold=qc_config.reproducibility_rmsd_threshold,
    )

    failed_required_checks = _failed_required_checks(checks, qc_config)
    quality["checks"] = checks
    quality["failed_required_checks"] = failed_required_checks
    quality["status"] = _status(failed_required_checks, checks, qc_config)
    quality["pose_qc_not_binding_evidence"] = True

    if failed_required_checks:
        warnings.append(
            "Poor pose quality can reject or deprioritize structure-aware assessment."
        )
    warnings.append("Pose QC does not prove binding.")

    adjusted_confidence = _adjusted_confidence(pose.confidence, quality["status"], checks)
    payload = pose.model_dump()
    payload.update(
        {
            "pose_quality": quality,
            "warnings": sorted(set(warnings)),
            "confidence": adjusted_confidence,
            "metadata": {
                **pose.metadata,
                "pose_qc_performed": True,
                "pose_qc_not_experimental_evidence": True,
                "no_binding_claim_from_pose_qc": True,
            },
        }
    )
    return DockingPose(**payload)


def _config(config: PoseQCConfig | dict[str, Any] | None) -> PoseQCConfig:
    if isinstance(config, PoseQCConfig):
        return config
    if isinstance(config, dict):
        return PoseQCConfig(**config)
    return PoseQCConfig()


def _pose_file_exists(pose: DockingPose, config: PoseQCConfig) -> bool:
    if not config.expect_pose_file:
        return True
    if not pose.pose_path:
        return False
    return Path(pose.pose_path).exists()


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coordinates(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    coordinates: list[list[float]] = []
    for item in value:
        if not isinstance(item, list | tuple) or len(item) != 3:
            return []
        try:
            coordinates.append([float(item[0]), float(item[1]), float(item[2])])
        except (TypeError, ValueError):
            return []
    return coordinates


def _within_binding_site_box(
    ligand_coordinates: list[list[float]],
    binding_site: BindingSiteDefinition,
) -> bool:
    if not ligand_coordinates or binding_site.center is None or binding_site.box_size is None:
        return False
    lower = [
        float(center) - float(size) / 2.0
        for center, size in zip(binding_site.center, binding_site.box_size, strict=True)
    ]
    upper = [
        float(center) + float(size) / 2.0
        for center, size in zip(binding_site.center, binding_site.box_size, strict=True)
    ]
    return all(
        lower[axis] <= coordinate[axis] <= upper[axis]
        for coordinate in ligand_coordinates
        for axis in range(3)
    )


def _severe_clash_count(
    ligand_coordinates: list[list[float]],
    protein_coordinates: list[list[float]],
    threshold: float,
) -> int:
    if not ligand_coordinates or not protein_coordinates:
        return 0
    threshold_squared = threshold * threshold
    count = 0
    for ligand_coordinate in ligand_coordinates:
        if any(
            _distance_squared(ligand_coordinate, protein_coordinate) < threshold_squared
            for protein_coordinate in protein_coordinates
        ):
            count += 1
    return count


def _distance_squared(first: list[float], second: list[float]) -> float:
    return sum((first[axis] - second[axis]) ** 2 for axis in range(3))


def _energy_sane(raw_score: Any, config: PoseQCConfig) -> bool:
    if raw_score is None:
        return True
    try:
        parsed = float(raw_score)
    except (TypeError, ValueError):
        return False
    return config.minimum_reasonable_raw_score <= parsed <= config.maximum_reasonable_raw_score


def _add_reference_rmsd(
    *,
    ligand_coordinates: list[list[float]],
    reference_coordinates: list[list[float]] | None,
    quality: dict[str, Any],
    checks: dict[str, bool],
    warnings: list[str],
) -> None:
    reference = _coordinates(reference_coordinates)
    if not reference:
        checks["reference_rmsd_computed"] = False
        quality["reference_rmsd_note"] = (
            "Reference RMSD only meaningful when comparable reference ligand exists."
        )
        return
    if len(reference) != len(ligand_coordinates) or not ligand_coordinates:
        checks["reference_rmsd_computed"] = False
        quality["reference_rmsd_note"] = (
            "Reference RMSD only meaningful for comparable ligand coordinate sets."
        )
        warnings.append("Reference RMSD was not computed because ligands are not comparable.")
        return
    rmsd = _rmsd(ligand_coordinates, reference)
    quality["reference_rmsd"] = rmsd
    quality["reference_rmsd_note"] = (
        "Reference RMSD only meaningful when comparable reference ligand exists."
    )
    checks["reference_rmsd_computed"] = True


def _add_reproducibility(
    *,
    ligand_coordinates: list[list[float]],
    repeated_pose_coordinates: list[list[list[float]]],
    quality: dict[str, Any],
    checks: dict[str, bool],
    warnings: list[str],
    threshold: float,
) -> None:
    if not repeated_pose_coordinates:
        checks["pose_reproducibility_checked"] = False
        return
    rmsds: list[float] = []
    for repeated in repeated_pose_coordinates:
        coordinates = _coordinates(repeated)
        if len(coordinates) != len(ligand_coordinates) or not ligand_coordinates:
            warnings.append("Pose reproducibility was not computed for non-comparable poses.")
            checks["pose_reproducibility_checked"] = False
            return
        rmsds.append(_rmsd(ligand_coordinates, coordinates))
    quality["reproducibility_rmsd_values"] = rmsds
    checks["pose_reproducibility_checked"] = True
    checks["pose_reproducible"] = all(rmsd <= threshold for rmsd in rmsds)
    if not checks["pose_reproducible"]:
        warnings.append("Pose reproducibility check exceeded configured RMSD threshold.")


def _rmsd(first: list[list[float]], second: list[list[float]]) -> float:
    squared_sum = sum(_distance_squared(a, b) for a, b in zip(first, second, strict=True))
    return round(math.sqrt(squared_sum / len(first)), 3)


def _add_size_warnings(
    heavy_atom_count: int | None,
    warnings: list[str],
    config: PoseQCConfig,
) -> None:
    if heavy_atom_count is None:
        return
    if heavy_atom_count <= config.fragment_heavy_atom_threshold:
        warnings.append("Fragment-sized ligand may yield noisy pose quality interpretation.")
    if heavy_atom_count >= config.large_ligand_heavy_atom_threshold:
        warnings.append("Large ligand may yield noisy pose quality interpretation.")


def _failed_required_checks(checks: dict[str, bool], config: PoseQCConfig) -> list[str]:
    required = [
        "docking_score_present",
        "ligand_heavy_atoms_preserved",
        "ligand_coordinates_available",
        "ligand_within_binding_site_box",
        "no_severe_clashes",
        "pose_energy_sane",
    ]
    if config.expect_pose_file:
        required.append("pose_file_exists")
    if checks.get("pose_reproducibility_checked"):
        required.append("pose_reproducible")
    return [name for name in required if checks.get(name) is False]


def _status(
    failed_required_checks: list[str],
    checks: dict[str, bool],
    config: PoseQCConfig,
) -> str:
    severe_failures = {
        "ligand_within_binding_site_box",
        "no_severe_clashes",
        "ligand_heavy_atoms_preserved",
    }
    if config.reject_on_failed_required_checks and any(
        item in severe_failures for item in failed_required_checks
    ):
        return "reject"
    if failed_required_checks:
        return "deprioritize"
    if checks.get("reference_rmsd_computed") and checks.get("pose_reproducible") is False:
        return "deprioritize"
    return "pass"


def _adjusted_confidence(
    original_confidence: float,
    status: str,
    checks: dict[str, bool],
) -> float:
    failed_count = sum(1 for passed in checks.values() if passed is False)
    adjusted = original_confidence - 0.08 * failed_count
    if status == "reject":
        adjusted = min(adjusted, 0.1)
    elif status == "deprioritize":
        adjusted = min(adjusted, 0.3)
    return max(0.0, min(1.0, round(adjusted, 3)))


__all__ = ["PoseQCConfig", "evaluate_pose_quality"]
