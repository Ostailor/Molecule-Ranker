from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.structure.schemas import BindingSiteDefinition, StructureRecord

BindingSiteSelectionMethod = Literal[
    "auto",
    "co_crystal_ligand",
    "known_residues",
    "pocket_detection",
    "user_supplied_box",
    "full_protein_blind",
]
PocketDetector = Callable[[StructureRecord, "BindingSiteConfig"], dict[str, Any] | None]


class BindingSiteConfig(BaseModel):
    method: BindingSiteSelectionMethod = "auto"
    user_center: list[float] | None = None
    user_box_size: list[float] | None = None
    user_box_source: str | None = None
    known_residues: list[str] = Field(default_factory=list)
    known_residue_source: str | None = None
    enable_pocket_detection: bool = False
    allow_full_protein_blind: bool = False
    ligand_box_padding_angstrom: float = Field(default=5.0, gt=0.0)
    minimum_box_size_angstrom: float = Field(default=12.0, gt=0.0)
    strict_binding_site: bool = False


def define_binding_site(
    structure: StructureRecord,
    *,
    config: BindingSiteConfig | dict[str, Any] | None = None,
    pocket_detector: PocketDetector | None = None,
) -> BindingSiteDefinition:
    site_config = _config(config)
    if site_config.method == "auto":
        co_crystal = _co_crystal_site(structure, site_config)
        if co_crystal is not None:
            return co_crystal
        if site_config.known_residues:
            return _known_residue_site(structure, site_config)
        return _unavailable_site(
            structure,
            "Binding-site definition unavailable; docking should be skipped.",
        )
    if site_config.method == "co_crystal_ligand":
        site = _co_crystal_site(structure, site_config)
        if site is not None:
            return site
        return _unavailable_site(
            structure,
            "No co-crystal ligand coordinates were available; docking should be skipped.",
        )
    if site_config.method == "known_residues":
        return _known_residue_site(structure, site_config)
    if site_config.method == "user_supplied_box":
        return _user_supplied_box_site(structure, site_config)
    if site_config.method == "pocket_detection":
        return _pocket_detection_site(structure, site_config, pocket_detector)
    if site_config.method == "full_protein_blind":
        return _full_protein_blind_site(structure, site_config)
    return _unavailable_site(
        structure,
        "Unsupported binding-site method; docking should be skipped.",
    )


def _co_crystal_site(
    structure: StructureRecord,
    config: BindingSiteConfig,
) -> BindingSiteDefinition | None:
    if structure.structure_type != "experimental":
        return None
    for ligand in structure.ligands:
        coordinates = _coordinates_from_ligand(ligand)
        ligand_id = str(ligand.get("ligand_id") or ligand.get("id") or "").strip()
        if not ligand_id or not coordinates:
            continue
        center = _center(coordinates)
        box_size = _box_size(coordinates, config)
        return BindingSiteDefinition(
            binding_site_id=_site_id(structure, "co-crystal-ligand", ligand_id),
            target_symbol=structure.target_symbol,
            structure_id=structure.structure_id,
            method="co_crystal_ligand",
            center=center,
            box_size=box_size,
            residues=_residues_from_ligand(ligand),
            reference_ligand_id=ligand_id,
            confidence=0.8,
            warnings=[
                "Binding-site definition is computational workflow metadata, not proof of binding.",
                "Co-crystal ligand coordinates require target relevance review.",
            ],
            metadata={
                "provenance": "structure_ligand_coordinates",
                "source_structure_id": structure.structure_id,
                "source_ligand_id": ligand_id,
                "coordinate_count": len(coordinates),
                "docking_skipped": False,
                "codex_generated_site": False,
            },
        )
    return None


def _known_residue_site(
    structure: StructureRecord,
    config: BindingSiteConfig,
) -> BindingSiteDefinition:
    if not config.known_residues:
        return _unavailable_site(
            structure,
            "Known-residue binding-site definition unavailable; no residues were provided.",
        )
    if not _has_allowed_residue_provenance(config.known_residue_source):
        raise ValueError(
            "Known-residue binding-site definitions must have curated or user provenance."
        )
    return BindingSiteDefinition(
        binding_site_id=_site_id(structure, "known-residues", config.known_residue_source),
        target_symbol=structure.target_symbol,
        structure_id=structure.structure_id,
        method="known_residues",
        center=None,
        box_size=None,
        residues=sorted(set(config.known_residues)),
        reference_ligand_id=None,
        confidence=0.58,
        warnings=[
            "Known-residue binding-site definition is not experimental evidence.",
            "No binding-site box was inferred from residues; docking may need explicit setup.",
        ],
        metadata={
            "provenance": config.known_residue_source,
            "docking_skipped": True,
            "codex_generated_site": False,
        },
    )


def _user_supplied_box_site(
    structure: StructureRecord,
    config: BindingSiteConfig,
) -> BindingSiteDefinition:
    center = _validated_vector(
        config.user_center,
        name="User-supplied binding-site center",
        require_positive=False,
    )
    box_size = _validated_vector(
        config.user_box_size,
        name="User-supplied binding-site box size",
        require_positive=True,
    )
    if not config.user_box_source:
        raise ValueError("User-supplied binding-site box must include provenance.")
    return BindingSiteDefinition(
        binding_site_id=_site_id(structure, "user-box", config.user_box_source),
        target_symbol=structure.target_symbol,
        structure_id=structure.structure_id,
        method="user_supplied_box",
        center=center,
        box_size=box_size,
        residues=[],
        reference_ligand_id=None,
        confidence=0.62,
        warnings=[
            "User-supplied binding-site box is computational setup, not experimental evidence.",
        ],
        metadata={
            "provenance": config.user_box_source,
            "docking_skipped": False,
            "codex_generated_site": False,
        },
    )


def _pocket_detection_site(
    structure: StructureRecord,
    config: BindingSiteConfig,
    pocket_detector: PocketDetector | None,
) -> BindingSiteDefinition:
    if not config.enable_pocket_detection or pocket_detector is None:
        return _unavailable_site(
            structure,
            "Pocket detection is disabled or unavailable; docking should be skipped.",
        )
    result = pocket_detector(structure, config)
    if result is None:
        return _unavailable_site(
            structure,
            "Pocket detection did not return a usable binding-site definition.",
        )
    provenance = str(result.get("provenance") or "").strip()
    if not provenance:
        raise ValueError("Pocket-detection binding-site definitions must include provenance.")
    return BindingSiteDefinition(
        binding_site_id=_site_id(structure, "pocket-detection", provenance),
        target_symbol=structure.target_symbol,
        structure_id=structure.structure_id,
        method="pocket_detection",
        center=_validated_vector(
            result.get("center"),
            name="Pocket center",
            require_positive=False,
        ),
        box_size=_validated_vector(
            result.get("box_size"),
            name="Pocket box size",
            require_positive=True,
        ),
        residues=[str(item) for item in result.get("residues", [])],
        reference_ligand_id=None,
        confidence=_bounded_float(result.get("confidence"), default=0.45),
        warnings=[
            "Pocket detection is computational and lower-confidence than co-crystal evidence.",
            *[str(item) for item in result.get("warnings", [])],
        ],
        metadata={
            "provenance": provenance,
            "docking_skipped": False,
            "codex_generated_site": False,
        },
    )


def _full_protein_blind_site(
    structure: StructureRecord,
    config: BindingSiteConfig,
) -> BindingSiteDefinition:
    if not config.allow_full_protein_blind:
        return _unavailable_site(
            structure,
            "Full-protein blind docking is disabled by default; docking should be skipped.",
        )
    return BindingSiteDefinition(
        binding_site_id=_site_id(structure, "full-protein-blind", "explicitly-enabled"),
        target_symbol=structure.target_symbol,
        structure_id=structure.structure_id,
        method="full_protein_blind",
        center=None,
        box_size=None,
        residues=[],
        reference_ligand_id=None,
        confidence=0.25,
        warnings=[
            "Full-protein blind docking is expensive, noisy, and lower-confidence.",
            "Blind docking setup is not proof of binding or activity.",
        ],
        metadata={
            "provenance": "explicit_configuration",
            "docking_skipped": False,
            "codex_generated_site": False,
        },
    )


def _unavailable_site(structure: StructureRecord, warning: str) -> BindingSiteDefinition:
    return BindingSiteDefinition(
        binding_site_id=_site_id(structure, "unavailable", "none"),
        target_symbol=structure.target_symbol,
        structure_id=structure.structure_id,
        method="unavailable",
        center=None,
        box_size=None,
        residues=[],
        reference_ligand_id=None,
        confidence=0.0,
        warnings=[warning, "Codex may not invent binding-site boxes or residues."],
        metadata={
            "provenance": "unavailable",
            "docking_skipped": True,
            "codex_generated_site": False,
        },
    )


def _config(config: BindingSiteConfig | dict[str, Any] | None) -> BindingSiteConfig:
    if isinstance(config, BindingSiteConfig):
        return config
    if isinstance(config, dict):
        return BindingSiteConfig(**config)
    return BindingSiteConfig()


def _coordinates_from_ligand(ligand: dict[str, Any]) -> list[list[float]]:
    raw = ligand.get("coordinates") or ligand.get("atom_coordinates") or ligand.get("coords")
    if not isinstance(raw, list):
        return []
    coordinates: list[list[float]] = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 3:
            return []
        try:
            coordinates.append([float(item[0]), float(item[1]), float(item[2])])
        except (TypeError, ValueError):
            return []
    return coordinates


def _residues_from_ligand(ligand: dict[str, Any]) -> list[str]:
    residues = ligand.get("binding_site_residues") or ligand.get("contacts") or []
    if not isinstance(residues, list):
        return []
    return sorted({str(residue) for residue in residues if str(residue).strip()})


def _center(coordinates: list[list[float]]) -> list[float]:
    return [
        round(sum(coordinate[axis] for coordinate in coordinates) / len(coordinates), 3)
        for axis in range(3)
    ]


def _box_size(coordinates: list[list[float]], config: BindingSiteConfig) -> list[float]:
    box: list[float] = []
    for axis in range(3):
        values = [coordinate[axis] for coordinate in coordinates]
        span = max(values) - min(values)
        size = max(
            config.minimum_box_size_angstrom,
            span + 2.0 * config.ligand_box_padding_angstrom,
        )
        box.append(round(size, 3))
    return box


def _validated_vector(
    value: Any,
    *,
    name: str,
    require_positive: bool,
) -> list[float]:
    if not isinstance(value, list | tuple) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three numeric values.")
    try:
        parsed = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain exactly three numeric values.") from exc
    if require_positive and any(item <= 0.0 for item in parsed):
        raise ValueError(f"{name} values must be positive.")
    return parsed


def _has_allowed_residue_provenance(source: str | None) -> bool:
    if not source:
        return False
    lowered = source.lower()
    if any(forbidden in lowered for forbidden in ("codex", "invented", "generated")):
        return False
    return any(
        allowed in lowered
        for allowed in ("user", "curated", "imported", "operator", "artifact")
    )


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _site_id(structure: StructureRecord, method: str, source: str | None) -> str:
    safe_structure = _safe_id(structure.structure_id)
    safe_source = _safe_id(source or "none")
    return f"binding-site-{safe_structure}-{method}-{safe_source}"


def _safe_id(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value).strip(
        "-"
    )


__all__ = [
    "BindingSiteConfig",
    "BindingSiteSelectionMethod",
    "PocketDetector",
    "define_binding_site",
]
