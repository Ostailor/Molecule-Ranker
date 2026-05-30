from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.structure.schemas import (
    DockingPose,
    ProteinLigandInteractionProfile,
)

ExternalInteractionProfiler = Callable[
    [DockingPose, "InteractionProfileConfig"],
    ProteinLigandInteractionProfile | None,
]


class InteractionProfileConfig(BaseModel):
    hydrogen_bond_distance_angstrom: float = Field(default=3.5, gt=0.0)
    hydrophobic_distance_angstrom: float = Field(default=4.5, gt=0.0)
    salt_bridge_distance_angstrom: float = Field(default=4.0, gt=0.0)
    pi_contact_distance_angstrom: float = Field(default=5.0, gt=0.0)
    metal_coordination_distance_angstrom: float = Field(default=2.8, gt=0.0)
    enable_external_profiler: bool = False
    confidence_floor_with_contacts: float = Field(default=0.35, ge=0.0, le=1.0)


def profile_interactions(
    pose: DockingPose,
    *,
    config: InteractionProfileConfig | dict[str, Any] | None = None,
    external_profiler: ExternalInteractionProfiler | None = None,
) -> ProteinLigandInteractionProfile:
    profile_config = _config(config)
    if profile_config.enable_external_profiler and external_profiler is not None:
        external = external_profiler(pose, profile_config)
        if external is not None:
            return external

    ligand_atoms = _atoms(pose.metadata.get("ligand_atoms"))
    receptor_atoms = _atoms(pose.metadata.get("receptor_atoms"))
    if not ligand_atoms or not receptor_atoms:
        return _empty_profile(
            pose,
            [
                "Interaction profiling skipped: ligand or receptor coordinates are missing.",
                "Interactions are computational pose annotations, not experimental evidence.",
            ],
        )

    interactions: list[dict[str, Any]] = []
    warnings = ["Interactions are computational pose annotations, not experimental evidence."]
    for ligand_atom in ligand_atoms:
        for receptor_atom in receptor_atoms:
            distance = _distance(ligand_atom.coord, receptor_atom.coord)
            interactions.extend(
                _classify_interactions(
                    ligand_atom=ligand_atom,
                    receptor_atom=receptor_atom,
                    distance=distance,
                    config=profile_config,
                )
            )
    if any(item["interaction_type"] == "metal_coordination_like" for item in interactions):
        warnings.append(
            "Metal coordination-like contact requires expert review; heuristic only."
        )

    counts = Counter(str(item["interaction_type"]) for item in interactions)
    residues = sorted({str(item["residue"]) for item in interactions if item.get("residue")})
    confidence = _confidence(len(interactions), profile_config)
    return ProteinLigandInteractionProfile(
        profile_id=_profile_id(pose),
        pose_id=pose.pose_id,
        target_symbol=pose.target_symbol,
        molecule_id=pose.molecule_id,
        interactions=interactions,
        interaction_counts=dict(sorted(counts.items())),
        key_residue_contacts=residues,
        reference_similarity=None,
        warnings=sorted(set(warnings)),
        confidence=confidence,
        metadata={
            "method": "simple_geometric_heuristics",
            "provenance": "pose_metadata_coordinates",
            "not_experimental_evidence": True,
            "no_contacts_invented": True,
            "external_profiler_enabled": profile_config.enable_external_profiler,
        },
    )


def annotate_pose_interactions(
    pose: DockingPose,
    *,
    config: InteractionProfileConfig | dict[str, Any] | None = None,
    external_profiler: ExternalInteractionProfiler | None = None,
) -> DockingPose:
    profile = profile_interactions(
        pose,
        config=config,
        external_profiler=external_profiler,
    )
    payload = pose.model_dump()
    payload["interaction_summary"] = {
        "profile_id": profile.profile_id,
        "interaction_counts": profile.interaction_counts,
        "key_residue_contacts": profile.key_residue_contacts,
        "method": profile.metadata.get("method"),
        "not_experimental_evidence": True,
    }
    payload["warnings"] = sorted(set([*pose.warnings, *profile.warnings]))
    payload["metadata"] = {
        **pose.metadata,
        "interaction_profile": profile.model_dump(mode="json"),
        "interaction_profile_not_experimental_evidence": True,
    }
    return DockingPose(**payload)


class _Atom(BaseModel):
    atom_id: str
    element: str
    coord: list[float]
    residue: str | None = None
    atom_name: str | None = None
    donor: bool = False
    acceptor: bool = False
    hydrophobic: bool = False
    aromatic: bool = False
    charge: int = 0
    metal: bool = False


def _config(
    config: InteractionProfileConfig | dict[str, Any] | None,
) -> InteractionProfileConfig:
    if isinstance(config, InteractionProfileConfig):
        return config
    if isinstance(config, dict):
        return InteractionProfileConfig(**config)
    return InteractionProfileConfig()


def _atoms(value: Any) -> list[_Atom]:
    if not isinstance(value, list):
        return []
    atoms: list[_Atom] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            return []
        coord = _coordinate(item.get("coord") or item.get("coordinate"))
        if coord is None:
            return []
        atoms.append(
            _Atom(
                atom_id=str(item.get("atom_id") or item.get("atom_name") or index),
                atom_name=_optional_str(item.get("atom_name")),
                residue=_optional_str(item.get("residue")),
                element=str(item.get("element") or "").upper(),
                coord=coord,
                donor=bool(item.get("donor")),
                acceptor=bool(item.get("acceptor")),
                hydrophobic=bool(item.get("hydrophobic")),
                aromatic=bool(item.get("aromatic")),
                charge=_charge(item.get("charge")),
                metal=bool(item.get("metal")),
            )
        )
    return atoms


def _coordinate(value: Any) -> list[float] | None:
    if not isinstance(value, list | tuple) or len(value) != 3:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _charge(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    if parsed > 0:
        return 1
    if parsed < 0:
        return -1
    return 0


def _classify_interactions(
    *,
    ligand_atom: _Atom,
    receptor_atom: _Atom,
    distance: float,
    config: InteractionProfileConfig,
) -> list[dict[str, Any]]:
    interactions: list[dict[str, Any]] = []
    if _is_hydrogen_bond_like(ligand_atom, receptor_atom, distance, config):
        interactions.append(
            _interaction("hydrogen_bond_like", ligand_atom, receptor_atom, distance)
        )
    if _is_hydrophobic(ligand_atom, receptor_atom, distance, config):
        interactions.append(
            _interaction("hydrophobic_contact", ligand_atom, receptor_atom, distance)
        )
    if _is_pi_like(ligand_atom, receptor_atom, distance, config):
        interactions.append(_interaction("pi_contact_like", ligand_atom, receptor_atom, distance))
    if _is_salt_bridge_like(ligand_atom, receptor_atom, distance, config):
        interactions.append(_interaction("salt_bridge_like", ligand_atom, receptor_atom, distance))
    if _is_metal_coordination_like(ligand_atom, receptor_atom, distance, config):
        interactions.append(
            _interaction("metal_coordination_like", ligand_atom, receptor_atom, distance)
        )
    return interactions


def _is_hydrogen_bond_like(
    ligand_atom: _Atom,
    receptor_atom: _Atom,
    distance: float,
    config: InteractionProfileConfig,
) -> bool:
    if distance > config.hydrogen_bond_distance_angstrom:
        return False
    return (ligand_atom.acceptor and receptor_atom.donor) or (
        ligand_atom.donor and receptor_atom.acceptor
    )


def _is_hydrophobic(
    ligand_atom: _Atom,
    receptor_atom: _Atom,
    distance: float,
    config: InteractionProfileConfig,
) -> bool:
    return (
        distance <= config.hydrophobic_distance_angstrom
        and ligand_atom.hydrophobic
        and receptor_atom.hydrophobic
    )


def _is_pi_like(
    ligand_atom: _Atom,
    receptor_atom: _Atom,
    distance: float,
    config: InteractionProfileConfig,
) -> bool:
    return (
        distance <= config.pi_contact_distance_angstrom
        and ligand_atom.aromatic
        and receptor_atom.aromatic
    )


def _is_salt_bridge_like(
    ligand_atom: _Atom,
    receptor_atom: _Atom,
    distance: float,
    config: InteractionProfileConfig,
) -> bool:
    return (
        distance <= config.salt_bridge_distance_angstrom
        and ligand_atom.charge != 0
        and receptor_atom.charge != 0
        and ligand_atom.charge != receptor_atom.charge
    )


def _is_metal_coordination_like(
    ligand_atom: _Atom,
    receptor_atom: _Atom,
    distance: float,
    config: InteractionProfileConfig,
) -> bool:
    if distance > config.metal_coordination_distance_angstrom:
        return False
    metal_atom = (
        ligand_atom if ligand_atom.metal or ligand_atom.element in _METALS else receptor_atom
    )
    other_atom = receptor_atom if metal_atom is ligand_atom else ligand_atom
    return (metal_atom.metal or metal_atom.element in _METALS) and other_atom.element in {
        "N",
        "O",
        "S",
    }


def _interaction(
    interaction_type: str,
    ligand_atom: _Atom,
    receptor_atom: _Atom,
    distance: float,
) -> dict[str, Any]:
    return {
        "interaction_type": interaction_type,
        "residue": receptor_atom.residue,
        "receptor_atom": receptor_atom.atom_name or receptor_atom.atom_id,
        "ligand_atom": ligand_atom.atom_id,
        "distance_angstrom": round(distance, 3),
        "method": "simple_geometric_heuristic",
    }


def _empty_profile(
    pose: DockingPose,
    warnings: list[str],
) -> ProteinLigandInteractionProfile:
    return ProteinLigandInteractionProfile(
        profile_id=_profile_id(pose),
        pose_id=pose.pose_id,
        target_symbol=pose.target_symbol,
        molecule_id=pose.molecule_id,
        interactions=[],
        interaction_counts={},
        key_residue_contacts=[],
        reference_similarity=None,
        warnings=sorted(set(warnings)),
        confidence=0.0,
        metadata={
            "method": "simple_geometric_heuristics",
            "provenance": "unavailable_coordinates",
            "not_experimental_evidence": True,
            "no_contacts_invented": True,
        },
    )


def _distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((first[axis] - second[axis]) ** 2 for axis in range(3)))


def _confidence(interaction_count: int, config: InteractionProfileConfig) -> float:
    if interaction_count <= 0:
        return 0.0
    base = config.confidence_floor_with_contacts + min(0.35, 0.04 * interaction_count)
    return round(max(0.0, min(1.0, base)), 3)


def _profile_id(pose: DockingPose) -> str:
    return f"interaction-profile-{_safe_id(pose.pose_id)}"


def _safe_id(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value).strip(
        "-"
    )


_METALS = {"ZN", "MG", "MN", "FE", "CU", "CA", "CO", "NI"}


__all__ = [
    "ExternalInteractionProfiler",
    "InteractionProfileConfig",
    "annotate_pose_interactions",
    "profile_interactions",
]
