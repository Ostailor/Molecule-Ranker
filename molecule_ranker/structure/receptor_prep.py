from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker.structure.schemas import ReceptorPreparation, StructureRecord

ReceptorPrepMethod = Literal[
    "metadata_only",
    "rdkit_basic",
    "pdbfixer_optional",
    "external_tool_placeholder",
]
PDBFixerRunner = Callable[[StructureRecord, Path, Path, "ReceptorPrepConfig"], dict[str, Any]]


class ReceptorPrepConfig(BaseModel):
    receptor_prep_method: ReceptorPrepMethod = "metadata_only"
    keep_reference_ligand: bool = False
    remove_waters: bool = True
    remove_ions: bool = True
    allow_pdbfixer: bool = False
    strict_receptor_prep: bool = False
    receptor_artifact_dir: Path | None = None
    protonation_policy: str = "metadata_only_no_protonation_change"
    reference_ligand_ids: list[str] = Field(default_factory=list)


def prepare_receptor(
    structure: StructureRecord,
    *,
    selected_chain_ids: list[str] | None = None,
    config: ReceptorPrepConfig | dict[str, Any] | None = None,
    pdbfixer_runner: PDBFixerRunner | None = None,
) -> ReceptorPreparation:
    prep_config = _config(config)
    input_path = _input_path(structure)
    kept_chains = _kept_chains(structure, selected_chain_ids)
    removed_chains = [chain for chain in structure.chains if chain not in set(kept_chains)]
    kept_heterogens, removed_heterogens = _heterogen_policy(structure, prep_config)
    warnings = _base_warnings(structure, prep_config)

    try:
        if prep_config.receptor_prep_method == "metadata_only":
            return _build_preparation(
                structure=structure,
                config=prep_config,
                input_path=input_path,
                output_path=None,
                kept_chains=kept_chains,
                removed_chains=removed_chains,
                kept_heterogens=kept_heterogens,
                removed_heterogens=removed_heterogens,
                warnings=warnings,
                confidence=0.45,
                missing_atoms_fixed=False,
                missing_hydrogens_added=False,
                missing_loops_modeled=False,
                alternate_locations_resolved=False,
                docking_ready=False,
            )
        if prep_config.receptor_prep_method == "rdkit_basic":
            output_path = _artifact_path(structure, prep_config, "rdkit-basic.pdb")
            _write_filtered_receptor(
                input_path=input_path,
                output_path=output_path,
                kept_chains=kept_chains,
                kept_heterogens=kept_heterogens,
                config=prep_config,
            )
            return _build_preparation(
                structure=structure,
                config=prep_config,
                input_path=input_path,
                output_path=output_path,
                kept_chains=kept_chains,
                removed_chains=removed_chains,
                kept_heterogens=kept_heterogens,
                removed_heterogens=removed_heterogens,
                warnings=[
                    *warnings,
                    "RDKit/basic receptor preparation is approximate and computational only.",
                ],
                confidence=0.6,
                missing_atoms_fixed=False,
                missing_hydrogens_added=False,
                missing_loops_modeled=False,
                alternate_locations_resolved=bool(structure.metadata.get("alternate_locations_present")),
                docking_ready=True,
            )
        if prep_config.receptor_prep_method == "pdbfixer_optional":
            return _prepare_with_pdbfixer(
                structure=structure,
                config=prep_config,
                input_path=input_path,
                kept_chains=kept_chains,
                removed_chains=removed_chains,
                kept_heterogens=kept_heterogens,
                removed_heterogens=removed_heterogens,
                warnings=warnings,
                runner=pdbfixer_runner,
            )
        if prep_config.receptor_prep_method == "external_tool_placeholder":
            output_path = _artifact_path(structure, prep_config, "external-placeholder.pdb")
            shutil.copyfile(input_path, output_path)
            return _build_preparation(
                structure=structure,
                config=prep_config,
                input_path=input_path,
                output_path=output_path,
                kept_chains=kept_chains,
                removed_chains=removed_chains,
                kept_heterogens=kept_heterogens,
                removed_heterogens=removed_heterogens,
                warnings=[
                    *warnings,
                    "External receptor preparation placeholder copied input only.",
                ],
                confidence=0.5,
                missing_atoms_fixed=False,
                missing_hydrogens_added=False,
                missing_loops_modeled=False,
                alternate_locations_resolved=False,
                docking_ready=False,
            )
    except Exception as exc:
        if prep_config.strict_receptor_prep:
            raise
        return _failure_preparation(
            structure=structure,
            config=prep_config,
            input_path=input_path,
            kept_chains=kept_chains,
            removed_chains=removed_chains,
            kept_heterogens=kept_heterogens,
            removed_heterogens=removed_heterogens,
            warnings=[*warnings, f"Receptor preparation failed: {exc}"],
        )
    raise ValueError(f"Unsupported receptor_prep_method: {prep_config.receptor_prep_method}")


def _prepare_with_pdbfixer(
    *,
    structure: StructureRecord,
    config: ReceptorPrepConfig,
    input_path: Path,
    kept_chains: list[str],
    removed_chains: list[str],
    kept_heterogens: list[str],
    removed_heterogens: list[str],
    warnings: list[str],
    runner: PDBFixerRunner | None,
) -> ReceptorPreparation:
    if not config.allow_pdbfixer:
        raise RuntimeError("PDBFixer preparation requested but allow_pdbfixer is false.")
    if runner is None and importlib.util.find_spec("pdbfixer") is None:
        raise RuntimeError("PDBFixer is not installed; receptor preparation skipped.")
    output_path = _artifact_path(structure, config, "pdbfixer.pdb")
    if runner is None:
        shutil.copyfile(input_path, output_path)
        result = {
            "missing_atoms_fixed": False,
            "missing_hydrogens_added": False,
            "missing_loops_modeled": False,
            "alternate_locations_resolved": False,
            "warnings": ["PDBFixer installed but no runner was configured; copied input only."],
        }
    else:
        result = runner(structure, input_path, output_path, config)
    if bool(result.get("missing_loops_modeled")):
        warnings.append(
            "Missing loops were modeled computationally; biologically important loops "
            "must be reviewed."
        )
    return _build_preparation(
        structure=structure,
        config=config,
        input_path=input_path,
        output_path=output_path,
        kept_chains=kept_chains,
        removed_chains=removed_chains,
        kept_heterogens=kept_heterogens,
        removed_heterogens=removed_heterogens,
        warnings=[*warnings, *[str(item) for item in result.get("warnings", [])]],
        confidence=0.7,
        missing_atoms_fixed=bool(result.get("missing_atoms_fixed")),
        missing_hydrogens_added=bool(result.get("missing_hydrogens_added")),
        missing_loops_modeled=bool(result.get("missing_loops_modeled")),
        alternate_locations_resolved=bool(result.get("alternate_locations_resolved")),
        docking_ready=output_path.exists(),
    )


def _build_preparation(
    *,
    structure: StructureRecord,
    config: ReceptorPrepConfig,
    input_path: Path,
    output_path: Path | None,
    kept_chains: list[str],
    removed_chains: list[str],
    kept_heterogens: list[str],
    removed_heterogens: list[str],
    warnings: list[str],
    confidence: float,
    missing_atoms_fixed: bool,
    missing_hydrogens_added: bool,
    missing_loops_modeled: bool,
    alternate_locations_resolved: bool,
    docking_ready: bool,
) -> ReceptorPreparation:
    return ReceptorPreparation(
        receptor_prep_id=_prep_id(structure, config.receptor_prep_method),
        structure_id=structure.structure_id,
        target_symbol=structure.target_symbol,
        input_structure_path=str(input_path),
        prepared_receptor_path=str(output_path) if output_path is not None else None,
        preparation_method=config.receptor_prep_method,
        protonation_policy=config.protonation_policy,
        kept_chains=kept_chains,
        removed_chains=removed_chains,
        kept_heterogens=kept_heterogens,
        removed_heterogens=removed_heterogens,
        missing_atoms_fixed=missing_atoms_fixed,
        missing_hydrogens_added=missing_hydrogens_added,
        missing_loops_modeled=missing_loops_modeled,
        alternate_locations_resolved=alternate_locations_resolved,
        warnings=sorted(set(warnings)),
        confidence=confidence,
        metadata={
            "docking_ready": docking_ready,
            "computational_workflow_not_experimental_evidence": True,
            "source_structure_sha256": structure.metadata.get("sha256"),
            "keep_reference_ligand": config.keep_reference_ligand,
            "remove_waters": config.remove_waters,
            "remove_ions": config.remove_ions,
            "allow_pdbfixer": config.allow_pdbfixer,
        },
    )


def _failure_preparation(
    *,
    structure: StructureRecord,
    config: ReceptorPrepConfig,
    input_path: Path,
    kept_chains: list[str],
    removed_chains: list[str],
    kept_heterogens: list[str],
    removed_heterogens: list[str],
    warnings: list[str],
) -> ReceptorPreparation:
    return _build_preparation(
        structure=structure,
        config=config,
        input_path=input_path,
        output_path=None,
        kept_chains=kept_chains,
        removed_chains=removed_chains,
        kept_heterogens=kept_heterogens,
        removed_heterogens=removed_heterogens,
        warnings=[*warnings, "Docking skipped for this structure."],
        confidence=0.0,
        missing_atoms_fixed=False,
        missing_hydrogens_added=False,
        missing_loops_modeled=False,
        alternate_locations_resolved=False,
        docking_ready=False,
    )


def _config(config: ReceptorPrepConfig | dict[str, Any] | None) -> ReceptorPrepConfig:
    if isinstance(config, ReceptorPrepConfig):
        return config
    if isinstance(config, dict):
        return ReceptorPrepConfig(**config)
    return ReceptorPrepConfig()


def _input_path(structure: StructureRecord) -> Path:
    value = structure.metadata.get("input_structure_path") or structure.metadata.get("path")
    if not value:
        raise ValueError("StructureRecord metadata must include input_structure_path.")
    path = Path(str(value)).resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Input structure path does not exist: {path}")
    return path


def _kept_chains(
    structure: StructureRecord,
    selected_chain_ids: list[str] | None,
) -> list[str]:
    requested = [chain for chain in selected_chain_ids or [] if chain]
    if requested:
        return requested
    return list(structure.chains)


def _heterogen_policy(
    structure: StructureRecord,
    config: ReceptorPrepConfig,
) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    removed: list[str] = []
    reference_ids = {item.upper() for item in config.reference_ligand_ids}
    if config.keep_reference_ligand:
        reference_ids.update(
            str(ligand.get("ligand_id") or ligand.get("id") or "").upper()
            for ligand in structure.ligands
            if str(ligand.get("relationship") or "").lower()
            in {"relevant", "related", "co_crystal", "known_ligand"}
        )
        if not reference_ids and structure.ligands:
            first = str(structure.ligands[0].get("ligand_id") or structure.ligands[0].get("id"))
            if first:
                reference_ids.add(first.upper())
    for ligand in structure.ligands:
        ligand_id = str(ligand.get("ligand_id") or ligand.get("id") or "").upper()
        if not ligand_id:
            continue
        if config.remove_waters and ligand_id in _WATER_IDS:
            removed.append(ligand_id)
        elif config.remove_ions and ligand_id in _ION_IDS:
            removed.append(ligand_id)
        elif ligand_id in reference_ids:
            kept.append(ligand_id)
        else:
            removed.append(ligand_id)
    return sorted(set(kept)), sorted(set(removed))


def _write_filtered_receptor(
    *,
    input_path: Path,
    output_path: Path,
    kept_chains: list[str],
    kept_heterogens: list[str],
    config: ReceptorPrepConfig,
) -> None:
    kept_chain_set = set(kept_chains)
    kept_heterogen_set = {item.upper() for item in kept_heterogens}
    lines: list[str] = []
    for line in input_path.read_text().splitlines():
        record = line[:6].strip()
        if record in {"ATOM", "ANISOU"}:
            chain_id = line[21].strip() if len(line) > 21 else ""
            if not kept_chain_set or chain_id in kept_chain_set:
                lines.append(line)
        elif record == "HETATM":
            residue = line[17:20].strip().upper()
            chain_id = line[21].strip() if len(line) > 21 else ""
            if kept_chain_set and chain_id not in kept_chain_set:
                continue
            if config.remove_waters and residue in _WATER_IDS:
                continue
            if config.remove_ions and residue in _ION_IDS:
                continue
            if residue in kept_heterogen_set:
                lines.append(line)
        elif record in {"TER", "END"}:
            lines.append(line)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n")


def _artifact_path(
    structure: StructureRecord,
    config: ReceptorPrepConfig,
    suffix: str,
) -> Path:
    root = config.receptor_artifact_dir or Path.cwd() / "receptor_artifacts"
    root.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(
        character.lower() if character.isalnum() else "-"
        for character in structure.structure_id
    ).strip("-")
    return root / f"{safe_id}-{suffix}"


def _prep_id(structure: StructureRecord, method: str) -> str:
    safe_structure = "".join(
        character.lower() if character.isalnum() else "-"
        for character in structure.structure_id
    ).strip("-")
    return f"receptor-prep-{safe_structure}-{method.replace('_', '-')}"


def _base_warnings(
    structure: StructureRecord,
    config: ReceptorPrepConfig,
) -> list[str]:
    warnings = [
        "Receptor preparation is a computational workflow, not experimental evidence."
    ]
    if structure.structure_type == "user_supplied":
        warnings.append("User-supplied receptor structure is not trusted without review.")
    if structure.metadata.get("alternate_locations_present"):
        warnings.append("Alternate locations require review; resolution may be approximate.")
    if config.receptor_prep_method == "metadata_only":
        warnings.append("Metadata-only receptor preparation is not docking-ready.")
    if config.receptor_prep_method == "rdkit_basic":
        warnings.append("Missing hydrogens are not added by rdkit_basic mode.")
    return warnings


_WATER_IDS = {"HOH", "WAT", "H2O"}
_ION_IDS = {
    "NA",
    "CL",
    "K",
    "MG",
    "CA",
    "ZN",
    "MN",
    "FE",
    "CU",
    "CO",
    "NI",
}


__all__ = ["PDBFixerRunner", "ReceptorPrepConfig", "ReceptorPrepMethod", "prepare_receptor"]
