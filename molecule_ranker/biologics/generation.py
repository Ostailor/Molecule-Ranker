from __future__ import annotations

import random
import re
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.biologics.developability import assess_antibody_developability
from molecule_ranker.biologics.novelty import assess_antibody_novelty
from molecule_ranker.biologics.numbering import (
    annotate_cdrs,
    number_antibody_sequence,
    validate_cdr_regions,
)
from molecule_ranker.biologics.objectives import AntibodyDesignObjective
from molecule_ranker.biologics.schemas import (
    ALLOWED_AMINO_ACIDS,
    AntibodySequence,
    AntigenContext,
    GeneratedAntibodyHypothesis,
)
from molecule_ranker.biologics.validation import validate_antibody_sequence

NO_BINDING_CLAIM_WARNING = (
    "Generated antibody hypotheses make no binding, neutralization, treatment, "
    "safety, developability, or manufacturability claim."
)
EXTERNAL_PLUGIN_DISABLED_WARNING = (
    "External antibody generation plugins are disabled by default and require "
    "an approved tool package plus deterministic output validation."
)


class AntibodyGenerator(Protocol):
    generator_name: str
    generator_version: str
    supported_modes: list[str]

    def generate(
        self,
        objective: AntibodyDesignObjective,
        seeds: list[AntibodySequence],
        antigen_context: AntigenContext | None,
        config: dict[str, Any],
    ) -> list[GeneratedAntibodyHypothesis]: ...


class NullAntibodyGenerator:
    generator_name = "null_antibody_generator"
    generator_version = "1.0"
    supported_modes = ["none"]

    def generate(
        self,
        objective: AntibodyDesignObjective,
        seeds: list[AntibodySequence],
        antigen_context: AntigenContext | None,
        config: dict[str, Any],
    ) -> list[GeneratedAntibodyHypothesis]:
        _ = (objective, seeds, antigen_context, config)
        return []


class ConservativeCDRMutator:
    generator_name = "conservative_cdr_mutator"
    generator_version = "1.0"
    supported_modes = ["cdr_mutation", "broad_target_context"]

    def __init__(self, *, random_seed: int = 0, mutation_count: int = 1) -> None:
        self.random_seed = random_seed
        self.mutation_count = max(1, min(mutation_count, 2))
        self.last_rejections: list[dict[str, Any]] = []

    def generate(
        self,
        objective: AntibodyDesignObjective,
        seeds: list[AntibodySequence],
        antigen_context: AntigenContext | None,
        config: dict[str, Any],
    ) -> list[GeneratedAntibodyHypothesis]:
        mode = str(config.get("mode") or objective.mode)
        if mode not in self.supported_modes:
            raise ValueError(f"unsupported antibody generation mode: {mode}")

        self.last_rejections = []
        rng = random.Random(int(config.get("random_seed", self.random_seed)))
        mutation_count = max(
            1,
            min(int(config.get("mutation_count", self.mutation_count)), 2),
        )
        max_outputs = min(int(config.get("max_outputs", objective.max_outputs)), 25)
        hypotheses: list[GeneratedAntibodyHypothesis] = []

        for seed in seeds:
            if len(hypotheses) >= max_outputs:
                break
            regions = _cdr_regions(seed)
            if not regions:
                self.last_rejections.append(
                    {
                        "seed_sequence_id": seed.sequence_id,
                        "reason": "No source-backed or confident CDR annotation available.",
                    }
                )
                continue
            candidate_sequence = _candidate_sequence(
                seed,
                regions=regions,
                rng=rng,
                mutation_count=mutation_count,
                override=config.get("candidate_sequence_override"),
            )
            generated_sequence = _generated_sequence_record(
                seed,
                objective=objective,
                candidate_sequence=candidate_sequence,
            )
            validation = validate_antibody_sequence(generated_sequence)
            if not validation["valid"] or validation["rejected"]:
                self.last_rejections.append(
                    {
                        "seed_sequence_id": seed.sequence_id,
                        "generated_sequence_id": generated_sequence.sequence_id,
                        "reason": "Generated sequence failed deterministic validation.",
                        "validation": validation,
                    }
                )
                continue

            numbering = number_antibody_sequence(generated_sequence)
            cdr_annotation = annotate_cdrs(generated_sequence, numbering)
            cdr_findings = validate_cdr_regions(cdr_annotation)
            novelty = assess_antibody_novelty(
                novelty_id=_stable_id("nov", generated_sequence.sequence_id),
                biologic_id=objective.biologic_id,
                sequences=[generated_sequence],
                known_sequences=config.get("known_sequences") or {},
                internal_candidate_registry=config.get("internal_candidate_registry") or (),
                imported_external_registry=config.get("imported_external_registry") or (),
                generated_sequence_archive=config.get("generated_sequence_archive") or (),
                parent_sequences=[seed],
                sources_checked=["parent_sequences", "configured_generation_context"],
            )
            if novelty.metadata.get("generated_exact_duplicate_rejected"):
                self.last_rejections.append(
                    {
                        "seed_sequence_id": seed.sequence_id,
                        "generated_sequence_id": generated_sequence.sequence_id,
                        "reason": "Generated exact duplicate rejected by novelty triage.",
                        "novelty": novelty.model_dump(mode="json"),
                    }
                )
                continue
            developability = assess_antibody_developability(
                assessment_id=_stable_id("dev", generated_sequence.sequence_id),
                biologic_id=objective.biologic_id,
                sequences=[generated_sequence],
            )
            hypotheses.append(
                build_generation_hypothesis(
                    generated_antibody_id=_stable_id("gab", generated_sequence.sequence_id),
                    biologic_id=objective.biologic_id,
                    design_objective_id=objective.design_objective_id,
                    generation_method=f"{self.generator_name}:{self.generator_version}",
                    generated_sequence_ids=[generated_sequence.sequence_id],
                    parent_sequence_ids=[seed.sequence_id],
                    antigen_context_id=(
                        antigen_context.antigen_context_id if antigen_context else None
                    ),
                    target_symbols=objective.target_symbols
                    or ([antigen_context.target_symbol] if antigen_context else []),
                    score=None,
                    confidence=0.25,
                    metadata={
                        "generated_sequences": [
                            generated_sequence.model_dump(mode="json")
                        ],
                        "validation": validation,
                        "numbering": numbering.model_dump(mode="json"),
                        "cdr_annotation": cdr_annotation.model_dump(mode="json"),
                        "cdr_findings": cdr_findings,
                        "novelty": novelty.model_dump(mode="json"),
                        "developability": developability.model_dump(mode="json"),
                        "no_binding_activity_claim": True,
                        "epitope_specific_design": False,
                    },
                )
            )
        return hypotheses


class ExternalAntibodyGeneratorPlugin:
    generator_name = "external_antibody_generator_plugin"
    generator_version = "placeholder"
    supported_modes = ["approved_external_plugin"]

    def __init__(
        self,
        *,
        enabled: bool = False,
        approved_tool_package: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.approved_tool_package = approved_tool_package

    def generate(
        self,
        objective: AntibodyDesignObjective,
        seeds: list[AntibodySequence],
        antigen_context: AntigenContext | None,
        config: dict[str, Any],
    ) -> list[GeneratedAntibodyHypothesis]:
        _ = (objective, seeds, antigen_context, config)
        if not self.enabled:
            raise RuntimeError(EXTERNAL_PLUGIN_DISABLED_WARNING)
        if not self.approved_tool_package:
            raise RuntimeError("Approved antibody generation tool package is required.")
        raise NotImplementedError(
            "External antibody generator plugins must be wired through governed "
            "tool adapters with deterministic output validation."
        )


def build_generation_hypothesis(
    *,
    generated_antibody_id: str,
    biologic_id: str,
    design_objective_id: str,
    generation_method: str,
    generated_sequence_ids: list[str],
    parent_sequence_ids: list[str] | None = None,
    antigen_context_id: str | None = None,
    target_symbols: list[str] | None = None,
    score: float | None = None,
    confidence: float = 0.0,
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> GeneratedAntibodyHypothesis:
    merged_warnings = [
        "Generated antibodies are computational hypotheses only.",
        NO_BINDING_CLAIM_WARNING,
        *(warnings or []),
    ]
    merged_metadata = {
        "direct_experimental_evidence_required_before_evidence_claim": True,
        "binding_activity_claim": False,
        **(metadata or {}),
    }
    return GeneratedAntibodyHypothesis(
        generated_antibody_id=generated_antibody_id,
        biologic_id=biologic_id,
        design_objective_id=design_objective_id,
        generated_sequence_ids=generated_sequence_ids,
        parent_sequence_ids=parent_sequence_ids or [],
        generation_method=generation_method,
        antigen_context_id=antigen_context_id,
        target_symbols=target_symbols or [],
        score=score,
        confidence=confidence,
        direct_experimental_evidence=False,
        warnings=merged_warnings,
        metadata=merged_metadata,
    )


def _cdr_regions(seed: AntibodySequence) -> dict[str, tuple[int, int]]:
    metadata_regions = _metadata_cdr_regions(seed)
    if metadata_regions:
        return metadata_regions
    inferred = _regions_from_cdr_sequences(seed)
    if inferred:
        return inferred
    numbering = number_antibody_sequence(seed)
    if numbering.confidence >= 0.6:
        return numbering.cdr_regions
    return {}


def _metadata_cdr_regions(seed: AntibodySequence) -> dict[str, tuple[int, int]]:
    raw = seed.metadata.get("cdr_regions")
    if not isinstance(raw, dict):
        return {}
    regions: dict[str, tuple[int, int]] = {}
    for label, value in raw.items():
        region = _region_tuple(value)
        if region is not None and _region_in_bounds(region, seed.sequence_length):
            regions[str(label)] = region
    return regions


def _regions_from_cdr_sequences(seed: AntibodySequence) -> dict[str, tuple[int, int]]:
    cdr_sequences = seed.metadata.get("cdr_sequences")
    if not isinstance(cdr_sequences, dict):
        return {}
    regions: dict[str, tuple[int, int]] = {}
    for label, cdr_sequence in cdr_sequences.items():
        if not isinstance(cdr_sequence, str) or not cdr_sequence:
            continue
        match = re.search(re.escape(cdr_sequence.upper()), seed.amino_acid_sequence)
        if match:
            regions[str(label)] = (match.start() + 1, match.end())
    return regions


def _candidate_sequence(
    seed: AntibodySequence,
    *,
    regions: dict[str, tuple[int, int]],
    rng: random.Random,
    mutation_count: int,
    override: Any,
) -> str:
    if isinstance(override, str):
        return re.sub(r"\s+", "", override).upper()
    mutable = list(seed.amino_acid_sequence)
    candidate_positions = [
        position
        for region in regions.values()
        for position in range(region[0] - 1, region[1])
        if 0 <= position < len(mutable)
    ]
    if not candidate_positions:
        return seed.amino_acid_sequence
    rng.shuffle(candidate_positions)
    for position in candidate_positions[:mutation_count]:
        mutable[position] = _alternate_residue(mutable[position], rng)
    return "".join(mutable)


def _generated_sequence_record(
    seed: AntibodySequence,
    *,
    objective: AntibodyDesignObjective,
    candidate_sequence: str,
) -> AntibodySequence:
    sequence_id = _stable_id(
        "seq-generated",
        seed.sequence_id,
        objective.design_objective_id,
        candidate_sequence,
    )
    return AntibodySequence(
        sequence_id=sequence_id,
        biologic_id=objective.biologic_id,
        chain_type=seed.chain_type,
        amino_acid_sequence=candidate_sequence,
        sequence_length=len(candidate_sequence),
        species_origin=seed.species_origin,
        is_generated=True,
        parent_sequence_ids=[seed.sequence_id],
        source="generated",
        source_record_id=None,
        created_at=datetime.now(UTC),
        metadata={
            "generation_method": "conservative_cdr_mutator",
            "parent_sequence_id": seed.sequence_id,
            "computational_hypothesis_only": True,
            "binding_activity_claim": False,
            "no_binding_activity_claim": True,
        },
    )


def _alternate_residue(current: str, rng: random.Random) -> str:
    allowed = sorted(ALLOWED_AMINO_ACIDS - {current})
    return rng.choice(allowed)


def _region_tuple(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        start = int(value[0])
        end = int(value[1])
    except (TypeError, ValueError):
        return None
    if start < 1 or end < start:
        return None
    return start, end


def _region_in_bounds(region: tuple[int, int], sequence_length: int) -> bool:
    start, end = region
    return 1 <= start <= end <= sequence_length


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{uuid5(NAMESPACE_URL, '|'.join(parts))}"


__all__ = [
    "EXTERNAL_PLUGIN_DISABLED_WARNING",
    "NO_BINDING_CLAIM_WARNING",
    "AntibodyDesignObjective",
    "AntibodyGenerator",
    "ConservativeCDRMutator",
    "ExternalAntibodyGeneratorPlugin",
    "NullAntibodyGenerator",
    "build_generation_hypothesis",
]
