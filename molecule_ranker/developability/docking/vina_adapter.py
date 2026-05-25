from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from molecule_ranker.developability.docking.base import DockingUnavailableError
from molecule_ranker.developability.docking.preparation import (
    BindingSite,
    DockingRunConfig,
    binding_site_from_any,
    config_from_any,
    preparation_metadata,
    preparation_warnings,
    prepared_inputs_available,
)
from molecule_ranker.developability.schemas import DockingAssessment

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class VinaAdapter:
    """Optional AutoDock Vina adapter for cautious computational triage."""

    engine_name = "AutoDock Vina"

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        executable_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._runner = runner or self._run_command
        self._executable_resolver = executable_resolver or shutil.which

    def dock(
        self,
        ligand_smiles: str,
        structure: Any,
        binding_site: Any,
        config: Any,
    ) -> DockingAssessment:
        run_config = config_from_any(config)
        site = binding_site_from_any(binding_site)
        structure_metadata = _structure_metadata(structure)
        ligand_id = str(run_config.metadata.get("ligand_id") or ligand_smiles)
        preparation = preparation_metadata(run_config, site)

        if not run_config.enable_docking:
            return self._assessment(
                enabled=False,
                ligand_id=ligand_id,
                structure_metadata=structure_metadata,
                binding_site=site,
                warnings=["Docking disabled by configuration."],
                metadata={"preparation": preparation, "docking_performed": False},
            )

        executable = self._executable_resolver(run_config.vina_executable)
        if executable is None:
            return self._unavailable(
                message="AutoDock Vina executable is unavailable.",
                ligand_id=ligand_id,
                structure_metadata=structure_metadata,
                binding_site=site,
                config=run_config,
                preparation=preparation,
            )

        prep_warnings = preparation_warnings(run_config, site)
        if not prepared_inputs_available(run_config, site):
            return self._unavailable(
                message="Docking inputs are incomplete or uncertain.",
                ligand_id=ligand_id,
                structure_metadata=structure_metadata,
                binding_site=site,
                config=run_config,
                preparation=preparation,
                warnings=prep_warnings,
            )

        command = self._vina_command(executable, run_config, site)
        try:
            result = self._runner(command)
        except Exception as exc:
            return self._unavailable(
                message=f"AutoDock Vina execution failed: {exc}",
                ligand_id=ligand_id,
                structure_metadata=structure_metadata,
                binding_site=site,
                config=run_config,
                preparation=preparation,
            )
        if result.returncode != 0:
            return self._unavailable(
                message="AutoDock Vina returned a non-zero exit code.",
                ligand_id=ligand_id,
                structure_metadata=structure_metadata,
                binding_site=site,
                config=run_config,
                preparation=preparation,
                warnings=[result.stderr.strip()] if result.stderr.strip() else [],
            )

        raw_score = _parse_vina_affinity(result.stdout)
        normalized_score = _normalize_vina_affinity(raw_score)
        return self._assessment(
            enabled=True,
            ligand_id=ligand_id,
            structure_metadata=structure_metadata,
            binding_site=site,
            docking_score=normalized_score,
            score_units="normalized_vina_affinity_0_1",
            pose_file=run_config.output_pose_path if run_config.write_docking_artifacts else None,
            confidence=0.35,
            warnings=[
                "Docking score is a weak computational heuristic and does not prove binding.",
                "Docking used externally prepared receptor and ligand artifacts.",
            ],
            metadata={
                "preparation": preparation,
                "docking_performed": True,
                "vina_command": _redacted_command(command),
                "engine_version": self._engine_version(executable),
                "raw_docking_score": raw_score,
                "raw_score_units": "kcal/mol",
                "pose_file_written": bool(run_config.write_docking_artifacts),
                "no_evidence_claim_created": True,
            },
        )

    def _unavailable(
        self,
        *,
        message: str,
        ligand_id: str,
        structure_metadata: dict[str, Any],
        binding_site: BindingSite,
        config: DockingRunConfig,
        preparation: dict[str, Any],
        warnings: list[str] | None = None,
    ) -> DockingAssessment:
        all_warnings = [
            message,
            *(warnings or []),
            "Docking was not used because receptor, ligand, engine, or binding-site "
            "certainty was insufficient.",
            "No binding claim was made.",
        ]
        if config.strict_structure_mode:
            raise DockingUnavailableError("; ".join(all_warnings))
        return self._assessment(
            enabled=False,
            ligand_id=ligand_id,
            structure_metadata=structure_metadata,
            binding_site=binding_site,
            warnings=all_warnings,
            metadata={
                "preparation": preparation,
                "docking_performed": False,
                "strict_structure_mode": config.strict_structure_mode,
            },
        )

    def _assessment(
        self,
        *,
        enabled: bool,
        ligand_id: str,
        structure_metadata: dict[str, Any],
        binding_site: BindingSite,
        docking_score: float | None = None,
        score_units: str | None = None,
        pose_file: str | None = None,
        confidence: float = 0.0,
        warnings: list[str],
        metadata: dict[str, Any],
    ) -> DockingAssessment:
        return DockingAssessment(
            enabled=enabled,
            target_symbol=str(structure_metadata.get("target_symbol") or "unknown"),
            structure_source=structure_metadata.get("structure_source"),
            structure_id=structure_metadata.get("structure_id"),
            ligand_id=ligand_id,
            docking_engine=self.engine_name if enabled else None,
            docking_score=docking_score,
            score_units=score_units,
            binding_site_method=binding_site.method,
            pose_file=pose_file,
            confidence=confidence,
            warnings=warnings,
            metadata=metadata,
        )

    def _vina_command(
        self,
        executable: str,
        config: DockingRunConfig,
        binding_site: BindingSite,
    ) -> list[str]:
        command = [
            executable,
            "--receptor",
            str(config.prepared_receptor_path),
            "--ligand",
            str(config.prepared_ligand_path),
            "--center_x",
            str(binding_site.center_x),
            "--center_y",
            str(binding_site.center_y),
            "--center_z",
            str(binding_site.center_z),
            "--size_x",
            str(binding_site.size_x),
            "--size_y",
            str(binding_site.size_y),
            "--size_z",
            str(binding_site.size_z),
            "--exhaustiveness",
            str(config.exhaustiveness),
            "--num_modes",
            str(config.num_modes),
        ]
        if config.write_docking_artifacts:
            if config.output_pose_path:
                command.extend(["--out", config.output_pose_path])
        else:
            command.append("--score_only")
        return command

    def _engine_version(self, executable: str) -> str | None:
        try:
            result = self._runner([executable, "--version"])
        except Exception:
            return None
        text = (result.stdout or result.stderr or "").strip()
        return text or None

    def _run_command(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
        )


def _structure_metadata(structure: Any) -> dict[str, Any]:
    if structure is None:
        return {}
    if isinstance(structure, dict):
        return {
            "target_symbol": structure.get("target_symbol") or structure.get("target"),
            "structure_source": structure.get("source") or structure.get("structure_source"),
            "structure_id": structure.get("structure_id") or structure.get("id"),
        }
    return {
        "target_symbol": getattr(structure, "target_symbol", None),
        "structure_source": getattr(structure, "source", None),
        "structure_id": getattr(structure, "structure_id", None),
    }


def _parse_vina_affinity(stdout: str) -> float | None:
    patterns = [
        r"Affinity:\s*(-?\d+(?:\.\d+)?)",
        r"^\s*1\s+(-?\d+(?:\.\d+)?)\s+",
    ]
    for pattern in patterns:
        match = re.search(pattern, stdout, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return float(match.group(1))
    return None


def _normalize_vina_affinity(raw_score: float | None) -> float | None:
    if raw_score is None:
        return None
    return max(0.0, min(1.0, -raw_score / 12.0))


def _redacted_command(command: Sequence[str]) -> list[str]:
    return [str(part) for part in command]


__all__ = ["VinaAdapter"]
