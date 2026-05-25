from __future__ import annotations

from dataclasses import dataclass

from rdkit import Chem

from molecule_ranker.generation.chemistry import (
    canonicalize_smiles,
    inchi_key_from_mol,
    mol_from_smiles,
    tanimoto_similarity,
)
from molecule_ranker.generation.schemas import (
    GeneratedMolecule,
    GenerationConfig,
    NoveltyAssessment,
    NoveltyClass,
    SeedMolecule,
)
from molecule_ranker.schemas import MoleculeCandidate


@dataclass(frozen=True)
class ReferenceMolecule:
    name: str
    canonical_smiles: str
    inchi_key: str | None
    mol: Chem.Mol


class NoveltyFilter:
    """Remove duplicates and near-duplicates while annotating novelty class."""

    def filter(
        self,
        generated: list[GeneratedMolecule],
        *,
        existing_candidates: list[MoleculeCandidate],
        seeds: list[SeedMolecule],
        config: GenerationConfig,
    ) -> tuple[list[GeneratedMolecule], list[GeneratedMolecule]]:
        existing_refs = self._existing_references(existing_candidates)
        seed_refs = self._seed_references(seeds)
        seen_generated: dict[str, GeneratedMolecule] = {}
        retained: list[GeneratedMolecule] = []
        rejected: list[GeneratedMolecule] = []

        for candidate in sorted(
            generated,
            key=lambda item: item.generation_score or 0.0,
            reverse=True,
        ):
            updated = self._annotate_candidate(
                candidate=candidate,
                existing_refs=existing_refs,
                seed_refs=seed_refs,
                seen_generated=seen_generated,
                config=config,
            )
            novelty = updated.novelty
            should_reject = bool(
                novelty
                and (
                    novelty.novelty_class in {"duplicate", "near_duplicate"}
                    or (
                        novelty.novelty_class == "distant"
                        and config.reject_distant_generated_molecules
                    )
                )
            )
            if should_reject:
                rejected.append(updated)
                continue
            retained.append(updated)
            seen_generated[updated.canonical_smiles] = updated
            if updated.inchi_key:
                seen_generated[updated.inchi_key] = updated
        return retained, rejected

    def _annotate_candidate(
        self,
        *,
        candidate: GeneratedMolecule,
        existing_refs: list[ReferenceMolecule],
        seed_refs: list[ReferenceMolecule],
        seen_generated: dict[str, GeneratedMolecule],
        config: GenerationConfig,
    ) -> GeneratedMolecule:
        canonical = canonicalize_smiles(candidate.canonical_smiles or candidate.smiles)
        mol = mol_from_smiles(canonical or candidate.smiles)
        if canonical is None or mol is None:
            return candidate
        inchi_key = inchi_key_from_mol(mol)
        duplicate_existing = any(
            canonical == ref.canonical_smiles
            or (inchi_key is not None and inchi_key == ref.inchi_key)
            for ref in existing_refs
        )
        duplicate_generated = canonical in seen_generated or (
            inchi_key is not None and inchi_key in seen_generated
        )
        existing_similarity, nearest_existing = self._nearest_similarity(mol, existing_refs)
        seed_similarity, nearest_seed = self._nearest_similarity(mol, seed_refs)
        max_similarity = max(existing_similarity, seed_similarity)
        novelty_class = self._novelty_class(
            duplicate_existing=duplicate_existing,
            duplicate_generated=duplicate_generated,
            max_similarity=max_similarity,
            has_reference_context=bool(existing_refs or seed_refs),
            config=config,
        )
        warnings = list(candidate.warnings)
        if novelty_class == "distant":
            warnings.append("distant_from_seed_context")
        novelty = NoveltyAssessment(
            duplicate_of_existing=duplicate_existing,
            duplicate_of_generated=duplicate_generated,
            max_similarity_to_existing=round(existing_similarity, 3),
            nearest_existing_name=nearest_existing,
            max_similarity_to_seed=round(seed_similarity, 3),
            nearest_seed_name=nearest_seed,
            novelty_class=novelty_class,
            metadata={
                "duplicate_similarity_threshold": config.duplicate_similarity_threshold,
                "near_duplicate_similarity_threshold": (
                    config.near_duplicate_similarity_threshold
                ),
                "distant_similarity_threshold": config.distant_similarity_threshold,
            },
        )
        return candidate.model_copy(
            update={
                "canonical_smiles": canonical,
                "inchi_key": inchi_key or candidate.inchi_key,
                "novelty": novelty,
                "warnings": sorted(set(warnings)),
            }
        )

    def _novelty_class(
        self,
        *,
        duplicate_existing: bool,
        duplicate_generated: bool,
        max_similarity: float,
        has_reference_context: bool,
        config: GenerationConfig,
    ) -> NoveltyClass:
        if (
            duplicate_existing
            or duplicate_generated
            or max_similarity >= config.duplicate_similarity_threshold
        ):
            return "duplicate"
        if max_similarity >= config.near_duplicate_similarity_threshold:
            return "near_duplicate"
        if not has_reference_context:
            return "novel_analog"
        if max_similarity < config.distant_similarity_threshold:
            return "distant"
        if max_similarity >= 0.5:
            return "close_analog"
        return "novel_analog"

    def _nearest_similarity(
        self,
        mol: Chem.Mol,
        refs: list[ReferenceMolecule],
    ) -> tuple[float, str | None]:
        best = 0.0
        best_name: str | None = None
        for ref in refs:
            similarity = tanimoto_similarity(mol, ref.mol)
            if similarity > best:
                best = similarity
                best_name = ref.name
        return best, best_name

    def _existing_references(
        self,
        candidates: list[MoleculeCandidate],
    ) -> list[ReferenceMolecule]:
        refs: list[ReferenceMolecule] = []
        for candidate in candidates:
            smiles = self._candidate_smiles(candidate)
            if smiles is None:
                continue
            ref = self._reference(candidate.name, smiles)
            if ref is not None:
                refs.append(ref)
        return refs

    def _seed_references(self, seeds: list[SeedMolecule]) -> list[ReferenceMolecule]:
        refs: list[ReferenceMolecule] = []
        for seed in seeds:
            ref = self._reference(seed.name, seed.canonical_smiles)
            if ref is not None:
                refs.append(ref)
        return refs

    def _candidate_smiles(self, candidate: MoleculeCandidate) -> str | None:
        for field in ("canonical_smiles", "isomeric_smiles", "smiles"):
            value = candidate.chemical_metadata.get(field) or candidate.identifiers.get(field)
            if value not in (None, ""):
                return str(value)
        return None

    def _reference(self, name: str, smiles: str) -> ReferenceMolecule | None:
        canonical = canonicalize_smiles(smiles)
        mol = mol_from_smiles(canonical or smiles)
        if canonical is None or mol is None:
            return None
        return ReferenceMolecule(
            name=name,
            canonical_smiles=canonical,
            inchi_key=inchi_key_from_mol(mol),
            mol=mol,
        )
