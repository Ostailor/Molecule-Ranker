from __future__ import annotations

import importlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from molecule_ranker.structure.schemas import (
    BindingSiteDefinition,
    DockingRun,
    Ligand3DPreparation,
    ReceptorPreparation,
)

DockingEngineName = Literal["null", "vina", "external"]
VinaDependencyLoader = Callable[[], Any | None]


class DockingEngine(Protocol):
    engine_name: str
    engine_version: str | None

    def dock(
        self,
        receptor: ReceptorPreparation,
        ligands: list[Ligand3DPreparation],
        binding_site: BindingSiteDefinition,
        config: dict[str, Any],
    ) -> DockingRun: ...


class DockingConfig(BaseModel):
    enable_structure_docking: bool = False
    docking_engine: DockingEngineName = "null"
    docking_exhaustiveness: int = Field(default=8, ge=1)
    docking_num_poses: int = Field(default=1, ge=1)
    docking_timeout_seconds: int = Field(default=300, ge=1)
    max_docked_ligands: int = Field(default=100, ge=1)
    write_pose_files: bool = False
    strict_docking: bool = False
    docking_random_seed: int | None = None
    docking_artifact_dir: Path | None = None


class NullDockingEngine:
    engine_name = "null"
    engine_version: str | None = None

    def dock(
        self,
        receptor: ReceptorPreparation,
        ligands: list[Ligand3DPreparation],
        binding_site: BindingSiteDefinition,
        config: dict[str, Any],
    ) -> DockingRun:
        run_config = _config(config)
        now = _now()
        return DockingRun(
            docking_run_id=_run_id(receptor, binding_site, self.engine_name),
            target_symbol=receptor.target_symbol,
            structure_id=receptor.structure_id,
            receptor_prep_id=receptor.receptor_prep_id,
            binding_site_id=binding_site.binding_site_id,
            docking_engine=self.engine_name,
            docking_engine_version=self.engine_version,
            config=_config_payload(run_config),
            started_at=now,
            completed_at=now,
            status="skipped",
            ligand_count=0,
            pose_count=0,
            artifacts={},
            warnings=[
                "Structure docking disabled or routed to NullDockingEngine.",
                "Docking scores are weak computational signals, not proof of binding.",
            ],
            metadata={
                "docking_performed": False,
                "requested_ligand_count": len(ligands),
                "no_evidence_item_created": True,
                "non_structure_evidence_unaffected": True,
            },
        )


class VinaDockingEngine:
    engine_name = "AutoDock Vina"

    def __init__(
        self,
        *,
        dependency_loader: VinaDependencyLoader | None = None,
        engine_version: str | None = None,
    ) -> None:
        self._dependency_loader = dependency_loader or _load_vina_class
        self.engine_version = engine_version

    def dock(
        self,
        receptor: ReceptorPreparation,
        ligands: list[Ligand3DPreparation],
        binding_site: BindingSiteDefinition,
        config: dict[str, Any],
    ) -> DockingRun:
        run_config = _config(config)
        started_at = _now()
        warnings = _base_warnings()
        if not run_config.enable_structure_docking:
            return _skipped_run(
                receptor=receptor,
                binding_site=binding_site,
                engine_name=self.engine_name,
                engine_version=self.engine_version,
                config=run_config,
                started_at=started_at,
                warnings=[*warnings, "Structure docking disabled by configuration."],
                requested_ligand_count=len(ligands),
            )

        vina_class = self._dependency_loader()
        if vina_class is None:
            return self._unavailable(
                receptor=receptor,
                ligands=ligands,
                binding_site=binding_site,
                config=run_config,
                started_at=started_at,
                warnings=[
                    *warnings,
                    "AutoDock Vina Python package is unavailable; docking skipped.",
                ],
            )

        input_warnings = _input_warnings(receptor, ligands, binding_site)
        if input_warnings:
            return self._unavailable(
                receptor=receptor,
                ligands=ligands,
                binding_site=binding_site,
                config=run_config,
                started_at=started_at,
                warnings=[*warnings, *input_warnings],
            )

        selected_ligands = ligands[: run_config.max_docked_ligands]
        if len(selected_ligands) < len(ligands):
            warnings.append("Docked ligand count was limited by max_docked_ligands.")

        artifact_root = _artifact_root(run_config)
        artifacts: dict[str, str] = {}
        raw_scores: dict[str, list[float]] = {}
        pose_count = 0
        try:
            for ligand in selected_ligands:
                ligand_path = Path(ligand.prepared_ligand_paths[0]).resolve()
                vina = vina_class(sf_name="vina", seed=run_config.docking_random_seed)
                vina.set_receptor(str(Path(str(receptor.prepared_receptor_path)).resolve()))
                vina.set_ligand_from_file(str(ligand_path))
                vina.compute_vina_maps(
                    center=list(binding_site.center or []),
                    box_size=list(binding_site.box_size or []),
                )
                vina.dock(
                    exhaustiveness=run_config.docking_exhaustiveness,
                    n_poses=run_config.docking_num_poses,
                )
                scores = _extract_vina_scores(
                    vina.energies(n_poses=run_config.docking_num_poses)
                )
                raw_scores[ligand.molecule_id] = scores
                pose_count += min(run_config.docking_num_poses, max(1, len(scores)))
                if run_config.write_pose_files:
                    pose_path = _artifact_path(
                        artifact_root,
                        f"{ligand.molecule_id}-poses.pdbqt",
                    )
                    vina.write_poses(
                        str(pose_path),
                        n_poses=run_config.docking_num_poses,
                        overwrite=True,
                    )
                    artifacts[f"pose_{ligand.molecule_id}"] = str(pose_path)
        except Exception as exc:
            if run_config.strict_docking:
                raise RuntimeError(f"AutoDock Vina docking failed: {exc}") from exc
            return _failed_run(
                receptor=receptor,
                binding_site=binding_site,
                engine_name=self.engine_name,
                engine_version=self.engine_version,
                config=run_config,
                started_at=started_at,
                warnings=[*warnings, f"AutoDock Vina docking failed: {exc}"],
                requested_ligand_count=len(ligands),
            )

        return DockingRun(
            docking_run_id=_run_id(receptor, binding_site, "vina"),
            target_symbol=receptor.target_symbol,
            structure_id=receptor.structure_id,
            receptor_prep_id=receptor.receptor_prep_id,
            binding_site_id=binding_site.binding_site_id,
            docking_engine=self.engine_name,
            docking_engine_version=self.engine_version,
            config=_config_payload(run_config),
            started_at=started_at,
            completed_at=_now(),
            status="succeeded",
            ligand_count=len(selected_ligands),
            pose_count=pose_count,
            artifacts=artifacts,
            warnings=sorted(set(warnings)),
            metadata={
                "docking_performed": True,
                "raw_scores": raw_scores,
                "raw_score_units": "kcal/mol",
                "score_interpretation": (
                    "weak computational signal; not proof of binding and not activity evidence"
                ),
                "no_evidence_item_created": True,
                "non_structure_evidence_unaffected": True,
            },
        )

    def _unavailable(
        self,
        *,
        receptor: ReceptorPreparation,
        ligands: list[Ligand3DPreparation],
        binding_site: BindingSiteDefinition,
        config: DockingConfig,
        started_at: datetime,
        warnings: list[str],
    ) -> DockingRun:
        if config.strict_docking:
            raise RuntimeError("; ".join(warnings))
        return _skipped_run(
            receptor=receptor,
            binding_site=binding_site,
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            config=config,
            started_at=started_at,
            warnings=warnings,
            requested_ligand_count=len(ligands),
        )


class ExternalDockingEnginePlaceholder:
    engine_name = "external_placeholder"
    engine_version: str | None = None

    def dock(
        self,
        receptor: ReceptorPreparation,
        ligands: list[Ligand3DPreparation],
        binding_site: BindingSiteDefinition,
        config: dict[str, Any],
    ) -> DockingRun:
        run_config = _config(config)
        warnings = [
            "External docking engine placeholder is disabled by default.",
            "No docking score or pose was generated.",
        ]
        if run_config.strict_docking:
            raise RuntimeError("; ".join(warnings))
        return _skipped_run(
            receptor=receptor,
            binding_site=binding_site,
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            config=run_config,
            started_at=_now(),
            warnings=warnings,
            requested_ligand_count=len(ligands),
        )


def run_docking(
    receptor: ReceptorPreparation,
    ligands: list[Ligand3DPreparation],
    binding_site: BindingSiteDefinition,
    config: DockingConfig | dict[str, Any] | None = None,
    *,
    engine: DockingEngine | None = None,
) -> DockingRun:
    run_config = _config(config)
    if not run_config.enable_structure_docking:
        return NullDockingEngine().dock(
            receptor,
            ligands,
            binding_site,
            _config_payload(run_config),
        )
    selected_engine = engine or _engine_for_config(run_config)
    return selected_engine.dock(receptor, ligands, binding_site, _config_payload(run_config))


def _engine_for_config(config: DockingConfig) -> DockingEngine:
    if config.docking_engine == "vina":
        return VinaDockingEngine()
    if config.docking_engine == "external":
        return ExternalDockingEnginePlaceholder()
    return NullDockingEngine()


def _skipped_run(
    *,
    receptor: ReceptorPreparation,
    binding_site: BindingSiteDefinition,
    engine_name: str,
    engine_version: str | None,
    config: DockingConfig,
    started_at: datetime,
    warnings: list[str],
    requested_ligand_count: int,
) -> DockingRun:
    return DockingRun(
        docking_run_id=_run_id(receptor, binding_site, engine_name),
        target_symbol=receptor.target_symbol,
        structure_id=receptor.structure_id,
        receptor_prep_id=receptor.receptor_prep_id,
        binding_site_id=binding_site.binding_site_id,
        docking_engine=engine_name,
        docking_engine_version=engine_version,
        config=_config_payload(config),
        started_at=started_at,
        completed_at=_now(),
        status="skipped",
        ligand_count=0,
        pose_count=0,
        artifacts={},
        warnings=sorted(set(warnings)),
        metadata={
            "docking_performed": False,
            "requested_ligand_count": requested_ligand_count,
            "no_evidence_item_created": True,
            "non_structure_evidence_unaffected": True,
        },
    )


def _failed_run(
    *,
    receptor: ReceptorPreparation,
    binding_site: BindingSiteDefinition,
    engine_name: str,
    engine_version: str | None,
    config: DockingConfig,
    started_at: datetime,
    warnings: list[str],
    requested_ligand_count: int,
) -> DockingRun:
    return DockingRun(
        docking_run_id=_run_id(receptor, binding_site, engine_name),
        target_symbol=receptor.target_symbol,
        structure_id=receptor.structure_id,
        receptor_prep_id=receptor.receptor_prep_id,
        binding_site_id=binding_site.binding_site_id,
        docking_engine=engine_name,
        docking_engine_version=engine_version,
        config=_config_payload(config),
        started_at=started_at,
        completed_at=_now(),
        status="failed",
        ligand_count=0,
        pose_count=0,
        artifacts={},
        warnings=sorted(set(warnings)),
        metadata={
            "docking_performed": False,
            "requested_ligand_count": requested_ligand_count,
            "no_evidence_item_created": True,
            "non_structure_evidence_unaffected": True,
        },
    )


def _config(config: DockingConfig | dict[str, Any] | None) -> DockingConfig:
    if isinstance(config, DockingConfig):
        return config
    if isinstance(config, dict):
        return DockingConfig(**config)
    return DockingConfig()


def _config_payload(config: DockingConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def _base_warnings() -> list[str]:
    return [
        "Docking scores are weak computational signals, not proof of binding.",
        "Docking output is not activity evidence and must not create EvidenceItem records.",
    ]


def _input_warnings(
    receptor: ReceptorPreparation,
    ligands: list[Ligand3DPreparation],
    binding_site: BindingSiteDefinition,
) -> list[str]:
    warnings: list[str] = []
    if not receptor.prepared_receptor_path:
        warnings.append("Docking skipped: prepared receptor artifact is required.")
    elif not Path(receptor.prepared_receptor_path).exists():
        warnings.append("Docking skipped: prepared receptor artifact was not found.")
    if not ligands:
        warnings.append("Docking skipped: no prepared ligands were provided.")
    for ligand in ligands:
        if not ligand.prepared_ligand_paths:
            warnings.append(f"Docking skipped: ligand {ligand.molecule_id} has no artifact.")
        elif not Path(ligand.prepared_ligand_paths[0]).exists():
            warnings.append(
                f"Docking skipped: ligand artifact for {ligand.molecule_id} was not found."
            )
    if binding_site.method == "unavailable":
        warnings.append("Docking skipped: binding site is unavailable.")
    if binding_site.center is None or binding_site.box_size is None:
        warnings.append("Docking skipped: explicit binding-site center and box are required.")
    return warnings


def _artifact_root(config: DockingConfig) -> Path:
    root = (config.docking_artifact_dir or Path.cwd() / "docking_artifacts").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact_path(root: Path, name: str) -> Path:
    path = (root / _safe_filename(name)).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("Docking artifact path escaped configured artifact directory.")
    return path


def _extract_vina_scores(energies: Any) -> list[float]:
    scores: list[float] = []
    for row in energies:
        try:
            first = row[0]
        except (TypeError, IndexError):
            first = row
        try:
            scores.append(float(first))
        except (TypeError, ValueError):
            continue
    return scores


def _load_vina_class() -> Any | None:
    try:
        module = importlib.import_module("vina")
    except ImportError:
        return None
    return getattr(module, "Vina", None)


def _run_id(receptor: ReceptorPreparation, binding_site: BindingSiteDefinition, engine: str) -> str:
    safe_receptor = _safe_id(receptor.receptor_prep_id)
    safe_site = _safe_id(binding_site.binding_site_id)
    safe_engine = _safe_id(engine)
    return f"docking-run-{safe_receptor}-{safe_site}-{safe_engine}"


def _safe_id(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value).strip(
        "-"
    )


def _safe_filename(value: str) -> str:
    safe = _safe_id(value)
    return f"{safe or 'artifact'}.pdbqt"


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DockingConfig",
    "DockingEngine",
    "ExternalDockingEnginePlaceholder",
    "NullDockingEngine",
    "VinaDockingEngine",
    "run_docking",
]
