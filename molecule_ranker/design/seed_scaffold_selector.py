from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field
from rdkit import Chem, DataStructs
from rdkit.Chem.Scaffolds import MurckoScaffold

from molecule_ranker.evidence import is_molecule_target_evidence, is_safety_warning
from molecule_ranker.generation.chemistry import (
    canonicalize_inchi,
    canonicalize_smiles,
    inchi_key_from_mol,
    mol_from_smiles,
    morgan_fingerprint,
)
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate, Target

ScaffoldType = Literal["murcko", "ring_system", "pharmacophore_like", "fragment"]


class DesignSeed(BaseModel):
    seed_id: str
    candidate_id: str
    name: str
    canonical_smiles: str
    inchi_key: str = ""
    target_symbols: list[str] = Field(default_factory=list)
    evidence_score: float = Field(ge=0.0, le=1.0)
    developability_score: float = Field(ge=0.0, le=1.0)
    experimental_result_summary: dict[str, Any] = Field(default_factory=dict)
    literature_support_summary: dict[str, Any] = Field(default_factory=dict)
    safety_risk_summary: dict[str, Any] = Field(default_factory=dict)
    reason_selected: str
    rejection_risks: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DesignScaffold(BaseModel):
    scaffold_id: str
    scaffold_smiles: str
    source_seed_ids: list[str] = Field(default_factory=list)
    scaffold_type: ScaffoldType
    target_symbols: list[str] = Field(default_factory=list)
    novelty_context: dict[str, Any] = Field(default_factory=dict)
    reason_selected: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SeedScaffoldSet(BaseModel):
    seeds: list[DesignSeed] = Field(default_factory=list)
    scaffolds: list[DesignScaffold] = Field(default_factory=list)
    target_coverage: dict[str, list[str]] = Field(default_factory=dict)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DesignSeedScaffoldSelector:
    """Build deterministic seed and scaffold artifacts for V1.1 design objectives."""

    def select(
        self,
        *,
        targets: list[Target],
        candidates: list[MoleculeCandidate],
        max_seeds_per_target: int = 4,
        max_total_seeds: int = 20,
        allow_serious_safety_warnings: bool = False,
        exclude_critical_developability: bool = True,
        diversity_similarity_threshold: float = 0.72,
    ) -> SeedScaffoldSet:
        evidence_backed_targets = self._evidence_backed_targets(targets)
        target_symbols = set(evidence_backed_targets)
        eligible_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
        rejected: list[dict[str, Any]] = []

        for candidate in candidates:
            canonical_smiles = self._canonical_structure(candidate)
            matched_targets = sorted(
                {
                    target.upper()
                    for target in candidate.known_targets
                    if target.upper() in target_symbols
                }
            )
            target_evidence = self._direct_molecule_target_evidence(candidate, matched_targets)
            serious_warnings = self._serious_warning_items(candidate)
            rejection_reasons = self._rejection_reasons(
                candidate=candidate,
                canonical_smiles=canonical_smiles,
                matched_targets=matched_targets,
                target_evidence=target_evidence,
                serious_warnings=serious_warnings,
                allow_serious_safety_warnings=allow_serious_safety_warnings,
                exclude_critical_developability=exclude_critical_developability,
            )
            if rejection_reasons:
                rejected.append(
                    {
                        "name": candidate.name,
                        "candidate_id": self._candidate_id(candidate),
                        "rejection_reasons": sorted(set(rejection_reasons)),
                        "matched_targets": matched_targets,
                    }
                )
                continue

            assert canonical_smiles is not None
            scaffold = self.extract_murcko_scaffold(canonical_smiles)
            seed = self._design_seed(
                candidate=candidate,
                canonical_smiles=canonical_smiles,
                matched_targets=matched_targets,
                target_evidence=target_evidence,
                serious_warnings=serious_warnings,
                scaffold=scaffold,
            )
            for target_symbol in matched_targets:
                eligible_by_target[target_symbol].append(
                    {
                        "seed": seed,
                        "scaffold": scaffold,
                        "score": self._selection_score(
                            seed=seed,
                            target=evidence_backed_targets[target_symbol],
                        ),
                    }
                )

        selected: dict[str, tuple[DesignSeed, DesignScaffold]] = {}
        for target_symbol in sorted(evidence_backed_targets):
            target_items = sorted(
                eligible_by_target.get(target_symbol, []),
                key=lambda item: item["score"],
                reverse=True,
            )
            for item in self._diverse_target_selection(
                target_items,
                max_seeds=max_seeds_per_target,
                similarity_threshold=diversity_similarity_threshold,
            ):
                seed = item["seed"]
                selected.setdefault(seed.seed_id, (seed, item["scaffold"]))
                if len(selected) >= max_total_seeds:
                    break
            if len(selected) >= max_total_seeds:
                break

        seeds = [seed for seed, _ in selected.values()]
        scaffolds = self._merge_scaffolds([pair[1] for pair in selected.values()], seeds)
        return SeedScaffoldSet(
            seeds=seeds,
            scaffolds=scaffolds,
            target_coverage=self._target_coverage(seeds),
            rejected_candidates=rejected,
            metadata={
                "selection_strategy": "direct_molecule_target_evidence_diverse_scaffold",
                "max_seeds_per_target": max_seeds_per_target,
                "max_total_seeds": max_total_seeds,
                "allow_serious_safety_warnings": allow_serious_safety_warnings,
                "exclude_critical_developability": exclude_critical_developability,
                "diversity_similarity_threshold": diversity_similarity_threshold,
                "eligible_target_count": len(evidence_backed_targets),
            },
        )

    def extract_murcko_scaffold(self, canonical_smiles: str) -> DesignScaffold:
        mol = mol_from_smiles(canonical_smiles)
        if mol is None:
            return DesignScaffold(
                scaffold_id=self._stable_id("scaffold", canonical_smiles),
                scaffold_smiles=canonical_smiles,
                scaffold_type="fragment",
                reason_selected=(
                    "Fallback fragment scaffold used because RDKit could not parse the "
                    "structure."
                ),
                metadata={"source": "fallback_fragment"},
            )

        scaffold_mol = MurckoScaffold.GetScaffoldForMol(mol)
        scaffold_smiles = Chem.MolToSmiles(scaffold_mol, canonical=True, isomericSmiles=True)
        scaffold_type: ScaffoldType = "murcko"
        reason = "Bemis-Murcko scaffold extracted with RDKit."
        if not scaffold_smiles:
            scaffold_smiles = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
            scaffold_type = "fragment"
            reason = (
                "Structure has no ring/linker Murcko scaffold; canonical structure retained "
                "as fragment."
            )

        return DesignScaffold(
            scaffold_id=self._stable_id("scaffold", scaffold_smiles),
            scaffold_smiles=scaffold_smiles,
            scaffold_type=scaffold_type,
            reason_selected=reason,
            metadata={"rdkit_scaffold_method": "Bemis-Murcko"},
        )

    def _evidence_backed_targets(self, targets: list[Target]) -> dict[str, Target]:
        return {
            target.symbol.upper(): target
            for target in targets
            if any(item.source and item.source_record_id for item in target.evidence)
        }

    def _canonical_structure(self, candidate: MoleculeCandidate) -> str | None:
        for field in ("canonical_smiles", "isomeric_smiles", "smiles", "canonical_smile"):
            value = candidate.chemical_metadata.get(field) or candidate.identifiers.get(field)
            if value not in (None, ""):
                canonical = canonicalize_smiles(str(value))
                if canonical is not None:
                    return canonical
        for field in ("inchi", "standard_inchi"):
            value = candidate.chemical_metadata.get(field) or candidate.identifiers.get(field)
            if value not in (None, ""):
                canonical = canonicalize_inchi(str(value))
                if canonical is not None:
                    return canonical
        return None

    def _direct_molecule_target_evidence(
        self,
        candidate: MoleculeCandidate,
        matched_targets: list[str],
    ) -> list[EvidenceItem]:
        target_set = {target.upper() for target in matched_targets}
        direct = [
            item
            for item in candidate.evidence
            if item.source
            and item.source_record_id
            and is_molecule_target_evidence(item)
            and self._evidence_matches_target(item, target_set)
        ]
        if direct:
            return direct
        return [
            item
            for item in candidate.evidence
            if item.source and item.source_record_id and is_molecule_target_evidence(item)
        ]

    def _evidence_matches_target(self, item: EvidenceItem, target_symbols: set[str]) -> bool:
        if not target_symbols:
            return False
        text = " ".join(
            [
                item.title,
                item.summary,
                str(item.metadata.get("target_symbol") or ""),
                str(item.metadata.get("target") or ""),
            ]
        ).upper()
        return any(target in text for target in target_symbols)

    def _rejection_reasons(
        self,
        *,
        candidate: MoleculeCandidate,
        canonical_smiles: str | None,
        matched_targets: list[str],
        target_evidence: list[EvidenceItem],
        serious_warnings: list[EvidenceItem],
        allow_serious_safety_warnings: bool,
        exclude_critical_developability: bool,
    ) -> list[str]:
        reasons: list[str] = []
        if canonical_smiles is None:
            reasons.append("missing_valid_structure")
        if not matched_targets:
            reasons.append("no_evidence_backed_target_overlap")
        if not target_evidence:
            reasons.append("no_direct_molecule_target_evidence")
        if exclude_critical_developability and self._has_critical_developability_risk(candidate):
            reasons.append("critical_developability_risk")
        if serious_warnings and not allow_serious_safety_warnings:
            reasons.append("serious_safety_warning")
        return reasons

    def _has_critical_developability_risk(self, candidate: MoleculeCandidate) -> bool:
        assessment = candidate.developability_assessment
        if assessment is None:
            return False
        if assessment.triage_recommendation == "high_risk_flags":
            return True
        metadata_risk = str(assessment.metadata.get("risk_level") or "").lower()
        return metadata_risk == "critical"

    def _serious_warning_items(self, candidate: MoleculeCandidate) -> list[EvidenceItem]:
        return [
            item
            for item in candidate.evidence
            if is_safety_warning(item) and self._is_serious_warning(item)
        ]

    def _is_serious_warning(self, item: EvidenceItem) -> bool:
        text = " ".join(
            [
                str(item.metadata.get("warning_type") or ""),
                str(item.metadata.get("warning_class") or ""),
                item.summary,
                item.title,
            ]
        ).lower()
        return any(
            term in text
            for term in (
                "black box",
                "boxed",
                "contraindication",
                "withdrawn",
                "fatal",
                "serious",
                "severe",
            )
        )

    def _design_seed(
        self,
        *,
        candidate: MoleculeCandidate,
        canonical_smiles: str,
        matched_targets: list[str],
        target_evidence: list[EvidenceItem],
        serious_warnings: list[EvidenceItem],
        scaffold: DesignScaffold,
    ) -> DesignSeed:
        seed_id = self._stable_id("seed", self._candidate_id(candidate), canonical_smiles)
        scaffold_id = scaffold.scaffold_id
        return DesignSeed(
            seed_id=seed_id,
            candidate_id=self._candidate_id(candidate),
            name=candidate.name,
            canonical_smiles=canonical_smiles,
            inchi_key=self._inchi_key(candidate, canonical_smiles),
            target_symbols=matched_targets,
            evidence_score=round(self._evidence_score(candidate, target_evidence), 3),
            developability_score=round(self._developability_score(candidate), 3),
            experimental_result_summary=self._experimental_result_summary(candidate),
            literature_support_summary=self._literature_support_summary(candidate),
            safety_risk_summary=self._safety_risk_summary(candidate, serious_warnings),
            reason_selected=self._selection_reason(
                matched_targets=matched_targets,
                target_evidence=target_evidence,
                exact_structure=True,
                scaffold=scaffold,
            ),
            rejection_risks=self._selected_rejection_risks(candidate, serious_warnings),
            metadata={
                "source": "existing_candidate",
                "scaffold_id": scaffold_id,
                "scaffold_smiles": scaffold.scaffold_smiles,
                "direct_molecule_target_evidence_count": len(target_evidence),
                "known_targets": list(candidate.known_targets),
            },
        )

    def _candidate_id(self, candidate: MoleculeCandidate) -> str:
        for key in ("chembl", "chembl_id", "pubchem_cid", "cid", "id"):
            value = candidate.identifiers.get(key)
            if value not in (None, ""):
                return str(value)
        return self._stable_id("candidate", candidate.name)

    def _inchi_key(self, candidate: MoleculeCandidate, canonical_smiles: str) -> str:
        for field in ("inchi_key", "inchikey", "standard_inchi_key"):
            value = candidate.identifiers.get(field) or candidate.chemical_metadata.get(field)
            if value not in (None, ""):
                return str(value)
        mol = mol_from_smiles(canonical_smiles)
        if mol is None:
            return ""
        return inchi_key_from_mol(mol) or ""

    def _evidence_score(
        self,
        candidate: MoleculeCandidate,
        target_evidence: list[EvidenceItem],
    ) -> float:
        if candidate.score_breakdown is not None:
            return candidate.score_breakdown.molecule_target_evidence
        if target_evidence:
            return max(item.confidence for item in target_evidence)
        return float(candidate.score or 0.0)

    def _developability_score(self, candidate: MoleculeCandidate) -> float:
        assessment = candidate.developability_assessment
        if assessment is None:
            return 0.5
        return assessment.developability_score

    def _experimental_result_summary(self, candidate: MoleculeCandidate) -> dict[str, Any]:
        results = candidate.generation_metadata.get("experimental_results")
        if isinstance(results, dict):
            return {"available": True, **results}
        return {"available": False, "summary": "No imported experimental result summary."}

    def _literature_support_summary(self, candidate: MoleculeCandidate) -> dict[str, Any]:
        bundle = candidate.literature_evidence
        if bundle is None:
            return {"available": False, "summary": "No imported literature evidence bundle."}
        return {
            "available": bool(bundle.items),
            "query_count": bundle.query_count,
            "quality_score": bundle.quality_score,
            "absent_reason": bundle.absent_reason,
        }

    def _safety_risk_summary(
        self,
        candidate: MoleculeCandidate,
        serious_warnings: list[EvidenceItem],
    ) -> dict[str, Any]:
        warnings = [item for item in candidate.evidence if is_safety_warning(item)]
        return {
            "warning_count": len(warnings),
            "serious_warning_count": len(serious_warnings),
            "warnings": [item.title for item in warnings],
        }

    def _selection_reason(
        self,
        *,
        matched_targets: list[str],
        target_evidence: list[EvidenceItem],
        exact_structure: bool,
        scaffold: DesignScaffold,
    ) -> str:
        sources = sorted({item.source for item in target_evidence})
        reason = (
            "Selected for direct molecule-target evidence"
            f" from {', '.join(sources)} and overlap with target(s) {', '.join(matched_targets)}"
        )
        if exact_structure:
            reason += "; exact structure available"
        reason += f"; {scaffold.scaffold_type} scaffold retained for diversity."
        return reason

    def _selected_rejection_risks(
        self,
        candidate: MoleculeCandidate,
        serious_warnings: list[EvidenceItem],
    ) -> list[str]:
        risks: list[str] = []
        if candidate.developability_assessment is None:
            risks.append("missing_developability_assessment")
        if serious_warnings:
            risks.append("serious_safety_warning_allowed")
        return risks

    def _selection_score(self, *, seed: DesignSeed, target: Target) -> float:
        return max(
            0.0,
            min(
                1.0,
                0.45 * seed.evidence_score
                + 0.25 * seed.developability_score
                + 0.20 * target.disease_relevance_score
                + 0.10,
            ),
        )

    def _diverse_target_selection(
        self,
        items: list[dict[str, Any]],
        *,
        max_seeds: int,
        similarity_threshold: float,
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        used_scaffolds: set[str] = set()
        for item in items:
            scaffold_id = item["scaffold"].scaffold_id
            if scaffold_id in used_scaffolds:
                continue
            selected.append(item)
            used_scaffolds.add(scaffold_id)
            if len(selected) >= max_seeds:
                return selected

        for item in items:
            if item in selected:
                continue
            if self._is_fingerprint_diverse(item, selected, similarity_threshold):
                selected.append(item)
            if len(selected) >= max_seeds:
                break
        return selected

    def _is_fingerprint_diverse(
        self,
        item: dict[str, Any],
        selected: list[dict[str, Any]],
        similarity_threshold: float,
    ) -> bool:
        item_mol = mol_from_smiles(item["seed"].canonical_smiles)
        if item_mol is None:
            return False
        item_fp = morgan_fingerprint(item_mol)
        for selected_item in selected:
            selected_mol = mol_from_smiles(selected_item["seed"].canonical_smiles)
            if selected_mol is None:
                continue
            similarity = float(
                DataStructs.TanimotoSimilarity(item_fp, morgan_fingerprint(selected_mol))
            )
            if similarity >= similarity_threshold:
                return False
        return True

    def _merge_scaffolds(
        self,
        scaffolds: list[DesignScaffold],
        seeds: list[DesignSeed],
    ) -> list[DesignScaffold]:
        source_seed_ids_by_scaffold: dict[str, list[str]] = defaultdict(list)
        target_symbols_by_scaffold: dict[str, set[str]] = defaultdict(set)
        seed_by_id = {seed.seed_id: seed for seed in seeds}
        for seed in seeds:
            scaffold_id = str(seed.metadata.get("scaffold_id") or "")
            if scaffold_id:
                source_seed_ids_by_scaffold[scaffold_id].append(seed.seed_id)
                target_symbols_by_scaffold[scaffold_id].update(seed.target_symbols)

        merged: dict[str, DesignScaffold] = {}
        for scaffold in scaffolds:
            source_seed_ids = sorted(set(source_seed_ids_by_scaffold[scaffold.scaffold_id]))
            target_symbols = sorted(target_symbols_by_scaffold[scaffold.scaffold_id])
            source_seeds = [
                seed_by_id[seed_id]
                for seed_id in source_seed_ids
                if seed_id in seed_by_id
            ]
            merged[scaffold.scaffold_id] = scaffold.model_copy(
                update={
                    "source_seed_ids": source_seed_ids,
                    "target_symbols": target_symbols,
                    "novelty_context": {
                        "source_seed_count": len(source_seed_ids),
                        "underexplored_scaffold": len(source_seed_ids) == 1,
                        "source_seed_names": [seed.name for seed in source_seeds],
                    },
                    "metadata": {
                        **scaffold.metadata,
                        "cluster_size": len(source_seed_ids),
                    },
                }
            )
        return sorted(merged.values(), key=lambda scaffold: scaffold.scaffold_id)

    def _target_coverage(self, seeds: list[DesignSeed]) -> dict[str, list[str]]:
        coverage: dict[str, list[str]] = defaultdict(list)
        for seed in seeds:
            for target_symbol in seed.target_symbols:
                coverage[target_symbol].append(seed.name)
        return {target: sorted(names) for target, names in sorted(coverage.items())}

    def _stable_id(self, prefix: str, *parts: str) -> str:
        digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{digest}"
