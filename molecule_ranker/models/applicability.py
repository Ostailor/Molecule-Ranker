"""Applicability-domain helpers for assay-specific surrogate models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold

from molecule_ranker.generation.chemistry import (
    descriptors_from_mol,
    mol_from_smiles,
    tanimoto_similarity,
)
from molecule_ranker.models.schemas import ApplicabilityDomain


@dataclass(frozen=True)
class ApplicabilityAssessment:
    applicability_domain: ApplicabilityDomain
    confidence: float
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def assess_applicability_domain(
    *,
    candidate: Mapping[str, Any],
    training_rows: Sequence[Mapping[str, Any]],
    endpoint_context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> ApplicabilityAssessment:
    config = dict(config or {})
    endpoint_context = dict(endpoint_context or {})
    candidate_smiles = _optional_string(
        candidate.get("canonical_smiles") or candidate.get("smiles")
    )
    candidate_mol = mol_from_smiles(candidate_smiles) if candidate_smiles else None
    if candidate_mol is None:
        return ApplicabilityAssessment(
            applicability_domain="unknown",
            confidence=0.0,
            metrics={"structure_present": False},
            warnings=["missing_or_invalid_structure"],
            metadata={"candidate_origin": str(candidate.get("candidate_origin") or "unknown")},
        )

    endpoint_match = _endpoint_matches(candidate, endpoint_context)
    training_mols = _training_molecules(training_rows)
    if not training_mols:
        return ApplicabilityAssessment(
            applicability_domain="unknown",
            confidence=0.0,
            metrics={"structure_present": True, "training_structure_count": 0},
            warnings=["missing_training_structures"],
        )

    nearest_similarity = max(
        tanimoto_similarity(candidate_mol, training_mol) for training_mol in training_mols
    )
    tanimoto_distance = 1.0 - nearest_similarity
    descriptor_distance = _descriptor_z_distance(candidate_mol, training_mols)
    scaffold = _scaffold_for_mol(candidate_mol)
    training_scaffolds = {_scaffold_for_mol(training_mol) for training_mol in training_mols}
    scaffold_seen = scaffold in training_scaffolds
    generated = str(candidate.get("candidate_origin") or "") == "generated"

    metrics = {
        "nearest_neighbor_tanimoto": nearest_similarity,
        "nearest_neighbor_tanimoto_distance": tanimoto_distance,
        "descriptor_z_score_distance": descriptor_distance,
        "candidate_scaffold": scaffold,
        "training_scaffold_seen": scaffold_seen,
        "endpoint_match": endpoint_match,
        "generated_candidate": generated,
        "training_structure_count": len(training_mols),
    }
    warnings: list[str] = []
    if not endpoint_match:
        warnings.append("endpoint_mismatch")
        return ApplicabilityAssessment(
            applicability_domain="out_of_domain",
            confidence=0.1,
            metrics=metrics,
            warnings=warnings,
            metadata={"candidate_origin": str(candidate.get("candidate_origin") or "unknown")},
        )

    in_tanimoto = float(config.get("in_domain_tanimoto", 0.55) or 0.55)
    near_tanimoto = float(config.get("near_domain_tanimoto", 0.3) or 0.3)
    max_in_descriptor_z = float(config.get("in_domain_descriptor_z", 3.0) or 3.0)
    max_near_descriptor_z = float(config.get("near_domain_descriptor_z", 5.0) or 5.0)
    domain = _domain_from_components(
        nearest_similarity=nearest_similarity,
        descriptor_distance=descriptor_distance,
        scaffold_seen=scaffold_seen,
        in_tanimoto=in_tanimoto,
        near_tanimoto=near_tanimoto,
        max_in_descriptor_z=max_in_descriptor_z,
        max_near_descriptor_z=max_near_descriptor_z,
    )
    if generated and domain != "in_domain":
        warnings.append("generated_candidate_requires_domain_review")
    if not scaffold_seen:
        warnings.append("unseen_scaffold")
    if domain == "out_of_domain":
        warnings.append("out_of_applicability_domain")

    return ApplicabilityAssessment(
        applicability_domain=domain,
        confidence=_confidence_for_domain(domain, generated),
        metrics=metrics,
        warnings=warnings,
        metadata={"candidate_origin": str(candidate.get("candidate_origin") or "unknown")},
    )


def _domain_from_components(
    *,
    nearest_similarity: float,
    descriptor_distance: float,
    scaffold_seen: bool,
    in_tanimoto: float,
    near_tanimoto: float,
    max_in_descriptor_z: float,
    max_near_descriptor_z: float,
) -> ApplicabilityDomain:
    if (
        nearest_similarity >= in_tanimoto
        and descriptor_distance <= max_in_descriptor_z
        and scaffold_seen
    ):
        return "in_domain"
    if nearest_similarity >= near_tanimoto and descriptor_distance <= max_near_descriptor_z:
        return "near_domain"
    return "out_of_domain"


def _endpoint_matches(
    candidate: Mapping[str, Any],
    endpoint_context: Mapping[str, Any],
) -> bool:
    expected_target = _normalized(endpoint_context.get("target_symbol"))
    expected_disease = _normalized(endpoint_context.get("disease_name"))
    candidate_target = _normalized(candidate.get("target_symbol"))
    candidate_disease = _normalized(candidate.get("disease_name"))
    if expected_target and candidate_target and expected_target != candidate_target:
        return False
    if expected_disease and candidate_disease and expected_disease != candidate_disease:
        return False
    return True


def _training_molecules(training_rows: Sequence[Mapping[str, Any]]) -> list[Any]:
    molecules = []
    for row in training_rows:
        smiles = _optional_string(row.get("canonical_smiles") or row.get("smiles"))
        mol = mol_from_smiles(smiles) if smiles else None
        if mol is not None:
            molecules.append(mol)
    return molecules


def _descriptor_z_distance(candidate_mol: Any, training_mols: Sequence[Any]) -> float:
    candidate_descriptors = descriptors_from_mol(candidate_mol)
    training_descriptors = [descriptors_from_mol(mol) for mol in training_mols]
    z_scores = []
    for name, candidate_value in candidate_descriptors.items():
        values = [descriptor[name] for descriptor in training_descriptors if name in descriptor]
        if not values:
            continue
        center = mean(values)
        spread = pstdev(values) if len(values) > 1 else 0.0
        if spread == 0.0:
            z_scores.append(0.0 if candidate_value == center else abs(candidate_value - center))
        else:
            z_scores.append(abs(candidate_value - center) / spread)
    return max(z_scores) if z_scores else 0.0


def _scaffold_for_mol(mol: Any) -> str:
    scaffold_mol = MurckoScaffold.GetScaffoldForMol(mol)
    scaffold = Chem.MolToSmiles(scaffold_mol, canonical=True, isomericSmiles=True)
    if scaffold:
        return scaffold
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)


def _confidence_for_domain(domain: ApplicabilityDomain, generated: bool) -> float:
    base = {
        "in_domain": 0.85,
        "near_domain": 0.55,
        "out_of_domain": 0.25,
        "unknown": 0.0,
    }[domain]
    if generated and domain != "in_domain":
        base -= 0.1
    return max(0.0, min(base, 1.0))


def _normalized(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value).strip().lower()


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


__all__ = [
    "ApplicabilityAssessment",
    "assess_applicability_domain",
]
