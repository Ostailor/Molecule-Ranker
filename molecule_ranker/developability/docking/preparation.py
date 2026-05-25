from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

BindingSiteMethod = Literal[
    "known_ligand_site",
    "user_supplied_box",
    "skipped",
    "unavailable",
]


class BindingSite(BaseModel):
    method: BindingSiteMethod
    center_x: float | None = None
    center_y: float | None = None
    center_z: float | None = None
    size_x: float | None = Field(default=None, gt=0.0)
    size_y: float | None = Field(default=None, gt=0.0)
    size_z: float | None = Field(default=None, gt=0.0)
    reference_ligand_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_explicit_box(self) -> bool:
        return all(
            value is not None
            for value in (
                self.center_x,
                self.center_y,
                self.center_z,
                self.size_x,
                self.size_y,
                self.size_z,
            )
        )


class DockingRunConfig(BaseModel):
    enable_docking: bool = False
    strict_structure_mode: bool = False
    write_docking_artifacts: bool = False
    vina_executable: str = "vina"
    prepared_receptor_path: str | None = None
    prepared_ligand_path: str | None = None
    output_pose_path: str | None = None
    exhaustiveness: int = Field(default=8, ge=1)
    num_modes: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


def binding_site_from_any(value: Any) -> BindingSite:
    if isinstance(value, BindingSite):
        return value
    if isinstance(value, Mapping):
        return BindingSite(**dict(value))
    return BindingSite(method="unavailable")


def config_from_any(value: Any) -> DockingRunConfig:
    if isinstance(value, DockingRunConfig):
        return value
    if isinstance(value, Mapping):
        return DockingRunConfig(**dict(value))
    return DockingRunConfig()


def preparation_metadata(config: DockingRunConfig, binding_site: BindingSite) -> dict[str, Any]:
    return {
        "receptor_preparation": _path_state(config.prepared_receptor_path),
        "ligand_preparation": _path_state(config.prepared_ligand_path),
        "binding_site_method": binding_site.method,
        "binding_site_confidence": binding_site.confidence,
        "binding_site_box_supplied": binding_site.has_explicit_box,
        "receptor_preparation_required_external_to_molecule_ranker": True,
        "ligand_preparation_required_external_to_molecule_ranker": True,
        "no_synthesis_or_lab_protocol_generated": True,
    }


def prepared_inputs_available(config: DockingRunConfig, binding_site: BindingSite) -> bool:
    return (
        bool(config.prepared_receptor_path)
        and bool(config.prepared_ligand_path)
        and Path(str(config.prepared_receptor_path)).exists()
        and Path(str(config.prepared_ligand_path)).exists()
        and binding_site.method in {"known_ligand_site", "user_supplied_box"}
        and binding_site.has_explicit_box
    )


def preparation_warnings(config: DockingRunConfig, binding_site: BindingSite) -> list[str]:
    warnings: list[str] = []
    if not config.prepared_receptor_path:
        warnings.append("Docking skipped: explicit prepared receptor file is required.")
    elif not Path(config.prepared_receptor_path).exists():
        warnings.append("Docking skipped: prepared receptor file was not found.")
    if not config.prepared_ligand_path:
        warnings.append("Docking skipped: explicit prepared ligand file is required.")
    elif not Path(config.prepared_ligand_path).exists():
        warnings.append("Docking skipped: prepared ligand file was not found.")
    if binding_site.method not in {"known_ligand_site", "user_supplied_box"}:
        warnings.append("Docking skipped: binding-site method is unavailable or skipped.")
    elif not binding_site.has_explicit_box:
        warnings.append("Docking skipped: explicit binding-site box is required.")
    return warnings


def _path_state(path: str | None) -> dict[str, Any]:
    if not path:
        return {"provided": False, "exists": False}
    return {"provided": True, "exists": Path(path).exists(), "path": path}


__all__ = [
    "BindingSite",
    "BindingSiteMethod",
    "DockingRunConfig",
    "binding_site_from_any",
    "config_from_any",
    "preparation_metadata",
    "preparation_warnings",
    "prepared_inputs_available",
]
