from __future__ import annotations

from molecule_ranker.generation.chemistry import (
    BASIC_PROPERTY_BOUNDS,
    allowed_elements_check,
    basic_property_bounds_check,
    canonicalize_smiles,
    descriptors_from_mol,
    detect_basic_alerts,
    detect_structural_sanity_alerts,
    inchi_key_from_mol,
    mol_from_smiles,
)
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GenerationConfig,
)


class ValidationFilter:
    """Validate generated molecules with RDKit and coarse V0.3 sanity filters."""

    def filter(
        self,
        generated: list[GeneratedMolecule],
        *,
        config: GenerationConfig,
    ) -> tuple[list[GeneratedMolecule], list[GeneratedMolecule]]:
        retained: list[GeneratedMolecule] = []
        rejected: list[GeneratedMolecule] = []
        for candidate in generated:
            updated = self._validate_candidate(candidate, config)
            if updated.validation.rejection_reasons:
                rejected.append(updated)
            else:
                retained.append(updated)
        return retained, rejected

    def _validate_candidate(
        self,
        candidate: GeneratedMolecule,
        config: GenerationConfig,
    ) -> GeneratedMolecule:
        smiles = candidate.smiles or candidate.canonical_smiles
        mol = mol_from_smiles(smiles)
        if mol is None:
            validation = ChemicalValidationResult(
                valid_rdkit_mol=False,
                sanitization_ok=False,
                canonicalization_ok=False,
                allowed_elements_ok=False,
                descriptor_bounds_ok=False,
                pains_or_alerts=[],
                rejection_reasons=["rdkit_parse_failed"],
                metadata={"input_smiles": smiles},
            )
            return candidate.model_copy(update={"validation": validation})

        canonical_smiles = canonicalize_smiles(smiles)
        canonicalization_ok = bool(canonical_smiles)
        descriptors = descriptors_from_mol(mol)
        allowed_elements = set(config.allowed_generation_elements)
        allowed_elements_ok = allowed_elements_check(mol, allowed_elements)
        descriptor_reasons = basic_property_bounds_check(
            descriptors,
            BASIC_PROPERTY_BOUNDS,
        )
        alerts = detect_basic_alerts(mol)
        structural_sanity_alerts = detect_structural_sanity_alerts(mol)
        rejection_reasons: list[str] = []
        warnings = list(candidate.warnings)
        if not canonicalization_ok:
            rejection_reasons.append("canonicalization_failed")
        if not allowed_elements_ok:
            rejection_reasons.append("disallowed_elements")
        if descriptor_reasons:
            if config.descriptor_bounds_warning_only:
                warnings.append("descriptor_bounds_warning")
            else:
                rejection_reasons.extend(descriptor_reasons)
        if alerts:
            warnings.append("basic_alerts_present")
            if config.reject_basic_alerts or not config.basic_alerts_warning_only:
                rejection_reasons.append("basic_alerts_present")
        if structural_sanity_alerts:
            warnings.append("structural_sanity_alerts_present")
            rejection_reasons.extend(structural_sanity_alerts)

        validation = ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=canonicalization_ok,
            allowed_elements_ok=allowed_elements_ok,
            descriptor_bounds_ok=not descriptor_reasons,
            pains_or_alerts=alerts + structural_sanity_alerts,
            rejection_reasons=rejection_reasons,
            metadata={
                "descriptor_bounds": BASIC_PROPERTY_BOUNDS,
                "allowed_elements": sorted(allowed_elements),
                "structural_sanity_alerts": structural_sanity_alerts,
            },
        )
        return candidate.model_copy(
            update={
                "canonical_smiles": canonical_smiles or candidate.canonical_smiles,
                "inchi_key": inchi_key_from_mol(mol) or candidate.inchi_key,
                "descriptors": descriptors,
                "validation": validation,
                "warnings": sorted(set(warnings)),
            }
        )
