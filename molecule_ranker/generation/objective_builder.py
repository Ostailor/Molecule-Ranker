from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from molecule_ranker.evidence import is_molecule_target_evidence
from molecule_ranker.generation.chemistry import descriptors_from_mol, mol_from_smiles
from molecule_ranker.generation.schemas import (
    GenerationConfig,
    GenerationObjective,
    SeedMolecule,
)
from molecule_ranker.schemas import Disease, MoleculeCandidate, Target
from molecule_ranker.utils import slugify

DESCRIPTOR_CONSTRAINT_FIELDS = ("molecular_weight", "logp", "tpsa")


class GenerationObjectiveBuilder:
    """Build target-conditioned generation objectives from selected seed molecules."""

    def __init__(self) -> None:
        self.trace_metadata: dict[str, Any] = {
            "created_objectives": [],
            "skipped_targets": [],
        }

    def build(
        self,
        *,
        disease: Disease,
        targets: list[Target],
        seeds: list[SeedMolecule],
        existing_candidates: list[MoleculeCandidate],
        literature_evidence: Mapping[str, Any] | None,
        config: GenerationConfig,
    ) -> list[GenerationObjective]:
        seeds_by_target = self._seeds_by_target(seeds)
        evidence_backed_targets = [
            target
            for target in targets
            if self._is_evidence_backed_target(target)
            and target.disease_relevance_score >= config.min_target_relevance_for_generation
        ]
        evidence_backed_targets.sort(
            key=lambda target: target.disease_relevance_score,
            reverse=True,
        )

        objectives: list[GenerationObjective] = []
        skipped: list[dict[str, str]] = []
        for target in evidence_backed_targets:
            target_seeds = seeds_by_target.get(target.symbol.upper(), [])
            if not target_seeds:
                skipped.append(
                    {
                        "target_symbol": target.symbol,
                        "reason": "no_selected_seed_molecules",
                    }
                )
                continue
            mechanism_hint, mechanism_source = self._mechanism_hint(
                target=target,
                target_seeds=target_seeds,
                existing_candidates=existing_candidates,
            )
            constraints, descriptor_summary = self._descriptor_constraints(
                target_seeds,
                margin_fraction=config.seed_property_margin_fraction,
            )
            objectives.append(
                GenerationObjective(
                    objective_id=f"{slugify(disease.canonical_name)}:{target.symbol}",
                    disease_name=disease.canonical_name,
                    target_symbol=target.symbol,
                    target_name=target.name,
                    target_identifiers={
                        str(key): str(value)
                        for key, value in target.identifiers.items()
                        if value not in (None, "")
                    },
                    mechanism_hint=mechanism_hint,
                    seed_molecule_names=[seed.name for seed in target_seeds],
                    seed_molecule_ids=[self._seed_id(seed) for seed in target_seeds],
                    objective_type="target_conditioned_analog_generation",
                    constraints=constraints,
                    metadata={
                        "target_relevance_score": target.disease_relevance_score,
                        "target_evidence_count": self._target_evidence_count(target),
                        "target_evidence_sources": sorted(
                            {
                                item.source
                                for item in target.evidence
                                if item.source and item.source_record_id
                            }
                        ),
                        "seed_count": len(target_seeds),
                        "seed_scores": {
                            seed.name: seed.metadata.get("seed_score")
                            for seed in target_seeds
                        },
                        "mechanism_hint_source": mechanism_source,
                        "seed_descriptor_summary": descriptor_summary,
                        "literature_context_available": bool(literature_evidence),
                    },
                )
            )
            if len(objectives) >= config.max_generation_objectives:
                break

        skipped.extend(
            {
                "target_symbol": target.symbol,
                "reason": "target_not_evidence_backed_or_below_relevance_threshold",
            }
            for target in targets
            if target.symbol.upper()
            not in {target.symbol.upper() for target in evidence_backed_targets}
        )
        self.trace_metadata = {
            "created_objectives": [
                {
                    "objective_id": objective.objective_id,
                    "target_symbol": objective.target_symbol,
                    "seed_count": len(objective.seed_molecule_names),
                }
                for objective in objectives
            ],
            "skipped_targets": skipped,
        }
        return objectives

    def _seeds_by_target(
        self,
        seeds: list[SeedMolecule],
    ) -> dict[str, list[SeedMolecule]]:
        grouped: dict[str, list[SeedMolecule]] = {}
        for seed in seeds:
            matched_targets = seed.metadata.get("matched_targets") or seed.known_targets
            for target in matched_targets:
                grouped.setdefault(str(target).upper(), []).append(seed)
        for target_seeds in grouped.values():
            target_seeds.sort(
                key=lambda seed: float(seed.metadata.get("seed_score") or 0.0),
                reverse=True,
            )
        return grouped

    def _is_evidence_backed_target(self, target: Target) -> bool:
        return any(item.source and item.source_record_id for item in target.evidence)

    def _mechanism_hint(
        self,
        *,
        target: Target,
        target_seeds: list[SeedMolecule],
        existing_candidates: list[MoleculeCandidate],
    ) -> tuple[str | None, str | None]:
        if target.mechanism:
            return target.mechanism, "target.mechanism"

        seed_names = {seed.source_candidate_name for seed in target_seeds} | {
            seed.name for seed in target_seeds
        }
        for candidate in existing_candidates:
            if candidate.name not in seed_names:
                continue
            if target.symbol.upper() not in {known.upper() for known in candidate.known_targets}:
                continue
            for item in candidate.evidence:
                if not (item.source and item.source_record_id):
                    continue
                if not is_molecule_target_evidence(item):
                    continue
                mechanism = item.metadata.get("mechanism") or item.summary
                if mechanism:
                    return str(mechanism), f"{item.source}:{item.source_record_id}"
        return None, None

    def _descriptor_constraints(
        self,
        seeds: list[SeedMolecule],
        *,
        margin_fraction: float,
    ) -> tuple[dict[str, dict[str, float | str]], dict[str, Any]]:
        descriptor_values: dict[str, list[float]] = {
            field: [] for field in DESCRIPTOR_CONSTRAINT_FIELDS
        }
        for seed in seeds:
            descriptors = self._seed_descriptors(seed)
            for field in DESCRIPTOR_CONSTRAINT_FIELDS:
                value = descriptors.get(field)
                if isinstance(value, (int, float)):
                    descriptor_values[field].append(float(value))

        constraints: dict[str, dict[str, float | str]] = {}
        for field, values in descriptor_values.items():
            if not values:
                continue
            seed_min = min(values)
            seed_max = max(values)
            span = max(seed_max - seed_min, abs(seed_max), 1.0)
            margin = span * margin_fraction
            constraints[field] = {
                "min": round(seed_min - margin, 3),
                "max": round(seed_max + margin, 3),
                "seed_min": round(seed_min, 3),
                "seed_max": round(seed_max, 3),
                "margin_fraction": margin_fraction,
                "source": "seed_descriptor_distribution",
            }

        return constraints, {
            "seed_count": len(seeds),
            "descriptor_fields": sorted(constraints),
        }

    def _seed_descriptors(self, seed: SeedMolecule) -> dict[str, float]:
        descriptors = seed.metadata.get("descriptors")
        if isinstance(descriptors, dict):
            return {
                str(key): float(value)
                for key, value in descriptors.items()
                if isinstance(value, (int, float))
            }
        mol = mol_from_smiles(seed.canonical_smiles)
        if mol is None:
            return {}
        return descriptors_from_mol(mol)

    def _target_evidence_count(self, target: Target) -> int:
        return sum(1 for item in target.evidence if item.source and item.source_record_id)

    def _seed_id(self, seed: SeedMolecule) -> str:
        for key in ("chembl", "pubchem_cid", "cid", "inchikey"):
            value = seed.identifiers.get(key)
            if value:
                return str(value)
        return seed.name
