from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator
from rdkit import Chem
from rdkit.Chem import AllChem

from molecule_ranker.generation.chemistry import mol_from_smiles
from molecule_ranker.structure.schemas import Ligand3DPreparation

LigandForceField = Literal["MMFF", "UFF", "none"]


class LigandPrepConfig(BaseModel):
    ligand_conformer_count: int = Field(default=10, ge=1)
    ligand_max_attempts: int = Field(default=50, ge=1)
    ligand_forcefield: LigandForceField = "MMFF"
    enumerate_tautomers: bool = False
    enumerate_protonation_states: bool = False
    strict_stereochemistry: bool = False
    max_ligands_for_docking: int = Field(default=100, ge=1)
    ligand_artifact_dir: Path | None = None
    protonation_policy: str = "input_smiles_only"

    @field_validator("ligand_forcefield", mode="before")
    @classmethod
    def normalize_forcefield(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.lower() == "none":
                return "none"
            return normalized.upper()
        return value


def prepare_ligand_3d(
    *,
    molecule_id: str,
    molecule_name: str,
    origin: Literal["existing", "generated"],
    canonical_smiles: str,
    config: LigandPrepConfig | dict[str, Any] | None = None,
) -> Ligand3DPreparation:
    prep_config = _config(config)
    mol = mol_from_smiles(canonical_smiles)
    if mol is None or mol.GetNumAtoms() == 0:
        raise ValueError(f"Invalid canonical SMILES for ligand preparation: {canonical_smiles}")

    warnings = _base_warnings(origin)
    stereochemistry_status = _stereochemistry_status(mol)
    if stereochemistry_status in {"ambiguous", "unspecified"}:
        message = (
            "Ligand stereochemistry is unspecified or ambiguous; prepared conformers "
            "require expert review."
        )
        if prep_config.strict_stereochemistry:
            raise ValueError(message)
        warnings.append(message)

    requested_conformers = prep_config.ligand_conformer_count
    effective_conformers = min(requested_conformers, prep_config.ligand_max_attempts)
    if effective_conformers < requested_conformers:
        warnings.append(
            "Requested conformer count was limited to avoid excessive ligand preparation."
        )

    metadata: dict[str, Any] = {
        "requested_conformer_count": requested_conformers,
        "effective_conformer_count": effective_conformers,
        "ligand_max_attempts": prep_config.ligand_max_attempts,
        "ligand_forcefield": prep_config.ligand_forcefield,
        "enumerate_tautomers": prep_config.enumerate_tautomers,
        "enumerate_protonation_states": prep_config.enumerate_protonation_states,
        "max_ligands_for_docking": prep_config.max_ligands_for_docking,
        "not_experimental_evidence": True,
        "no_activity_inference_from_conformation": True,
        "no_evidence_item_created": True,
    }
    if prep_config.enumerate_tautomers:
        tautomer_smiles = _enumerate_tautomers(mol)
        metadata["enumerated_tautomer_smiles"] = tautomer_smiles
        if len(tautomer_smiles) > 1:
            warnings.append(
                "Limited tautomer enumeration was recorded for review; docking inputs "
                "use the canonical input form."
            )
    if prep_config.enumerate_protonation_states:
        warnings.append(
            "Protonation-state enumeration requires a configured chemistry backend; "
            "input protonation was preserved."
        )

    mol_h = Chem.AddHs(mol)
    conformer_ids = _embed_conformers(mol_h, effective_conformers)
    if not conformer_ids:
        raise ValueError("RDKit failed to generate ligand conformers.")

    optimization_warnings = _optimize_conformers(mol_h, conformer_ids, prep_config)
    warnings.extend(optimization_warnings)
    artifact_paths = _write_conformer_artifacts(
        mol=mol_h,
        molecule_id=molecule_id,
        conformer_ids=conformer_ids,
        config=prep_config,
    )
    metadata["rdkit_conformer_ids"] = [int(conf_id) for conf_id in conformer_ids]

    confidence = _confidence(
        conformer_count=len(artifact_paths),
        stereochemistry_status=stereochemistry_status,
        warnings=warnings,
    )
    return Ligand3DPreparation(
        ligand_prep_id=_prep_id(molecule_id),
        molecule_id=molecule_id,
        molecule_name=molecule_name,
        origin=origin,
        canonical_smiles=Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True),
        conformer_count=len(artifact_paths),
        prepared_ligand_paths=[str(path) for path in artifact_paths],
        charge_method=_charge_method(prep_config.ligand_forcefield),
        protonation_policy=prep_config.protonation_policy,
        stereochemistry_status=stereochemistry_status,
        warnings=sorted(set(warnings)),
        confidence=confidence,
        metadata=metadata,
    )


def _config(config: LigandPrepConfig | dict[str, Any] | None) -> LigandPrepConfig:
    if isinstance(config, LigandPrepConfig):
        return config
    if isinstance(config, dict):
        return LigandPrepConfig(**config)
    return LigandPrepConfig()


def _base_warnings(origin: str) -> list[str]:
    warnings = [
        "Ligand 3D preparation is computational and not experimental evidence.",
        "Ligand conformations do not establish binding, activity, safety, or efficacy.",
    ]
    if origin == "generated":
        warnings.append("Generated molecule remains a computational hypothesis.")
    return warnings


def _stereochemistry_status(mol: Chem.Mol) -> Literal[
    "specified",
    "unspecified",
    "ambiguous",
    "corrected",
]:
    chiral_centers = Chem.FindMolChiralCenters(
        mol,
        includeUnassigned=True,
        useLegacyImplementation=False,
    )
    if any(status == "?" for _, status in chiral_centers):
        return "ambiguous"
    if _has_unassigned_double_bond_stereo(mol):
        return "ambiguous"
    return "specified"


def _has_unassigned_double_bond_stereo(mol: Chem.Mol) -> bool:
    for bond in mol.GetBonds():
        if bond.GetBondType() != Chem.BondType.DOUBLE:
            continue
        if bond.GetIsAromatic() or bond.IsInRing():
            continue
        begin_atom = bond.GetBeginAtom()
        end_atom = bond.GetEndAtom()
        if begin_atom.GetAtomicNum() == 1 or end_atom.GetAtomicNum() == 1:
            continue
        if begin_atom.GetDegree() <= 1 or end_atom.GetDegree() <= 1:
            continue
        if bond.GetStereo() == Chem.BondStereo.STEREONONE:
            return True
    return False


def _enumerate_tautomers(mol: Chem.Mol) -> list[str]:
    try:
        from rdkit.Chem.MolStandardize import rdMolStandardize
    except ImportError:
        return [Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)]

    enumerator = rdMolStandardize.TautomerEnumerator()
    enumerated = list(enumerator.Enumerate(mol))[:8]
    return sorted(
        {
            Chem.MolToSmiles(tautomer, canonical=True, isomericSmiles=True)
            for tautomer in enumerated
        }
    )


def _embed_conformers(mol: Chem.Mol, conformer_count: int) -> list[int]:
    all_chem = cast(Any, AllChem)
    params = all_chem.ETKDGv3()
    params.randomSeed = 0xC0D3
    params.pruneRmsThresh = 0.5
    conformer_ids = list(
        all_chem.EmbedMultipleConfs(mol, numConfs=conformer_count, params=params)
    )
    if conformer_ids:
        return [int(conf_id) for conf_id in conformer_ids]

    single_id = all_chem.EmbedMolecule(mol, params)
    if int(single_id) >= 0:
        return [int(single_id)]
    return []


def _optimize_conformers(
    mol: Chem.Mol,
    conformer_ids: list[int],
    config: LigandPrepConfig,
) -> list[str]:
    all_chem = cast(Any, AllChem)
    if config.ligand_forcefield == "none":
        return ["Force-field optimization was skipped by configuration."]
    if config.ligand_forcefield == "MMFF":
        try:
            if all_chem.MMFFHasAllMoleculeParams(mol):
                for conformer_id in conformer_ids:
                    all_chem.MMFFOptimizeMolecule(mol, confId=conformer_id)
                return []
        except Exception as exc:
            return [f"MMFF optimization failed: {exc}"]
        return ["MMFF parameters unavailable; conformers were not force-field optimized."]
    if config.ligand_forcefield == "UFF":
        warnings: list[str] = []
        for conformer_id in conformer_ids:
            try:
                all_chem.UFFOptimizeMolecule(mol, confId=conformer_id)
            except Exception as exc:
                warnings.append(f"UFF optimization failed for conformer {conformer_id}: {exc}")
        return warnings
    return [f"Unsupported ligand forcefield {config.ligand_forcefield}; optimization skipped."]


def _write_conformer_artifacts(
    *,
    mol: Chem.Mol,
    molecule_id: str,
    conformer_ids: list[int],
    config: LigandPrepConfig,
) -> list[Path]:
    root = config.ligand_artifact_dir or Path.cwd() / "ligand_artifacts"
    root.mkdir(parents=True, exist_ok=True)
    safe_id = _safe_id(molecule_id)
    paths: list[Path] = []
    for rank, conformer_id in enumerate(conformer_ids, start=1):
        path = root / f"{safe_id}-conf-{rank}.sdf"
        writer = Chem.SDWriter(str(path))
        try:
            mol.SetProp("molecule_id", molecule_id)
            mol.SetProp("conformer_rank", str(rank))
            mol.SetProp("computational_only", "true")
            writer.write(mol, confId=conformer_id)
        finally:
            writer.close()
        paths.append(path)
    return paths


def _confidence(
    *,
    conformer_count: int,
    stereochemistry_status: str,
    warnings: list[str],
) -> float:
    if conformer_count == 0:
        return 0.0
    confidence = 0.72
    if stereochemistry_status != "specified":
        confidence -= 0.18
    if any("optimization failed" in warning.lower() for warning in warnings):
        confidence -= 0.1
    if any("parameters unavailable" in warning.lower() for warning in warnings):
        confidence -= 0.05
    return max(0.0, min(1.0, round(confidence, 3)))


def _charge_method(forcefield: LigandForceField) -> str | None:
    if forcefield == "MMFF":
        return "MMFF94"
    if forcefield == "UFF":
        return "UFF"
    return None


def _prep_id(molecule_id: str) -> str:
    return f"ligand-prep-{_safe_id(molecule_id)}"


def _safe_id(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value).strip(
        "-"
    )


__all__ = ["LigandForceField", "LigandPrepConfig", "prepare_ligand_3d"]
