from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.agents.base import AgentExecutionError, BaseAgent, PipelineContext
from molecule_ranker.biologics.antigen import build_antigen_contexts
from molecule_ranker.biologics.developability import assess_antibody_developability
from molecule_ranker.biologics.generation import (
    AntibodyGenerator,
    ConservativeCDRMutator,
    NullAntibodyGenerator,
)
from molecule_ranker.biologics.novelty import assess_antibody_novelty
from molecule_ranker.biologics.numbering import (
    annotate_cdrs,
    number_antibody_sequence,
    validate_cdr_regions,
)
from molecule_ranker.biologics.objectives import build_antibody_design_objective
from molecule_ranker.biologics.retrieval import retrieve_existing_biologics
from molecule_ranker.biologics.schemas import (
    AntibodyDevelopabilityAssessment,
    AntibodyNoveltyAssessment,
    AntibodyNumbering,
    AntibodySequence,
    AntigenContext,
    BiologicCandidate,
    CDRAnnotation,
    GeneratedAntibodyHypothesis,
)
from molecule_ranker.biologics.scoring import (
    rank_biologic_candidates,
    rank_generated_antibody_hypotheses,
    score_biologic_candidate,
    score_biologic_candidate_components,
    score_generated_antibody_hypothesis,
)
from molecule_ranker.biologics.validation import validate_antibody_sequence


class BiologicsDiscoveryConfig(BaseModel):
    enable_biologics: bool = False
    enable_antibody_generation: bool = False
    antibody_generation_method: str = "null"
    max_existing_biologics: int = Field(default=25, ge=0)
    max_generated_antibodies: int = Field(default=0, ge=0)
    require_epitope_for_epitope_design: bool = True
    reject_generated_sequence_liabilities: bool = True
    strict_biologics_validation: bool = False


class BiologicsDiscoveryAgent(BaseAgent):
    """Run the governed V2.9 biologics and antibody discovery track."""

    name = "BiologicsDiscoveryAgent"

    def __init__(self, *, generator: AntibodyGenerator | None = None) -> None:
        super().__init__()
        self._generator = generator
        self._last_metadata: dict[str, Any] = {}

    def process(self, context: PipelineContext) -> PipelineContext:
        config = _config_from_context(context)
        self._last_metadata = self._base_metadata(config)
        context.config.setdefault("biologics", {})
        if not config.enable_biologics:
            context.config["biologics"] = {
                "enabled": False,
                "candidates": [],
                "generated_antibodies": [],
            }
            return context

        retrieval = retrieve_existing_biologics(
            target_symbols=_target_symbols(context),
            disease_name=_disease_name(context),
            chembl_records=_records(context, "chembl_biologic_records", "biologics_chembl_records"),
            literature_evidence=_records(context, "biologics_literature_evidence"),
            external_registry_records=_records(
                context,
                "external_registry_biologic_records",
                "biologics_external_registry_records",
            ),
            imported_records=_records(
                context,
                "imported_biologic_records",
                "biologics_imported_records",
            ),
            user_candidate_records=_records(
                context,
                "user_biologic_candidate_records",
                "biologics_user_candidate_records",
            ),
            antibody_database_adapters=context.config.get("antibody_database_adapters") or (),
        )
        sequences = retrieval.sequences
        self._apply_sequence_metadata(sequences, context)
        validation_results = self._validate_sequences(sequences, config)
        numbering, cdr_annotations, cdr_findings = self._number_sequences(
            sequences,
            validation_results=validation_results,
        )
        antigen_contexts = self._build_antigen_contexts(context)
        novelty = self._assess_novelty(retrieval.candidates, sequences, context)
        developability = self._assess_developability(retrieval.candidates, sequences)

        ranked_candidates = self._rank_candidates(
            retrieval.candidates,
            novelty=novelty,
            developability=developability,
            antigen_contexts=antigen_contexts,
            limit=config.max_existing_biologics,
        )
        generated = self._maybe_generate_antibodies(
            context=context,
            config=config,
            seeds=sequences,
            antigen_contexts=antigen_contexts,
        )
        ranked_generated = rank_generated_antibody_hypotheses(generated)[
            : config.max_generated_antibodies
        ]
        for hypothesis in ranked_generated:
            hypothesis.score = score_generated_antibody_hypothesis(hypothesis)[
                "total_score"
            ]

        artifacts = self._write_artifacts(
            context=context,
            candidates=ranked_candidates,
            sequences=sequences,
            validation_results=validation_results,
            numbering=numbering,
            cdr_annotations=cdr_annotations,
            cdr_findings=cdr_findings,
            antigen_contexts=antigen_contexts,
            novelty=novelty,
            developability=developability,
            generated=ranked_generated,
            retrieval_warnings=retrieval.warnings,
        )
        context.config["biologics"] = {
            "enabled": True,
            "candidates": ranked_candidates,
            "sequences": sequences,
            "validation": validation_results,
            "numbering": numbering,
            "cdr_annotations": cdr_annotations,
            "cdr_findings": cdr_findings,
            "antigen_contexts": antigen_contexts,
            "novelty": novelty,
            "developability": developability,
            "generated_antibodies": ranked_generated,
            "artifacts": artifacts,
        }
        self._last_metadata = {
            **self._base_metadata(config),
            "enabled": True,
            "retrieved_candidate_count": len(retrieval.candidates),
            "ranked_candidate_count": len(ranked_candidates),
            "sequence_count": len(sequences),
            "antigen_context_count": len(antigen_contexts),
            "novelty_assessment_count": len(novelty),
            "developability_assessment_count": len(developability),
            "generation_enabled": config.enable_antibody_generation,
            "generated_count": len(ranked_generated),
            "artifacts": artifacts,
            "warnings": retrieval.warnings,
        }
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        if not self._last_metadata.get("enabled"):
            return "Biologics discovery disabled; no biologics work performed."
        return (
            f"Ranked {self._last_metadata.get('ranked_candidate_count', 0)} "
            "existing biologics and "
            f"{self._last_metadata.get('generated_count', 0)} generated antibody hypotheses."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        _ = context
        return dict(self._last_metadata)

    def _validate_sequences(
        self,
        sequences: Sequence[AntibodySequence],
        config: BiologicsDiscoveryConfig,
    ) -> list[dict[str, Any]]:
        results = [validate_antibody_sequence(sequence) for sequence in sequences]
        invalid = [result for result in results if not result.get("valid")]
        if invalid and config.strict_biologics_validation:
            invalid_ids = ", ".join(str(result.get("sequence_id")) for result in invalid)
            raise AgentExecutionError(
                "Strict biologics validation failed for antibody sequences: "
                f"{invalid_ids}"
            )
        return results

    def _apply_sequence_metadata(
        self,
        sequences: Sequence[AntibodySequence],
        context: PipelineContext,
    ) -> None:
        metadata_by_id = _sequence_metadata_by_id(context)
        for sequence in sequences:
            for key in (
                sequence.sequence_id,
                sequence.source_record_id or "",
                sequence.biologic_id or "",
            ):
                metadata = metadata_by_id.get(key)
                if metadata:
                    sequence.metadata.update(metadata)

    def _number_sequences(
        self,
        sequences: Sequence[AntibodySequence],
        *,
        validation_results: Sequence[Mapping[str, Any]],
    ) -> tuple[list[AntibodyNumbering], list[CDRAnnotation], dict[str, list[str]]]:
        validation_by_id = {
            str(result.get("sequence_id")): bool(result.get("valid"))
            for result in validation_results
        }
        numbering: list[AntibodyNumbering] = []
        annotations: list[CDRAnnotation] = []
        findings: dict[str, list[str]] = {}
        for sequence in sequences:
            if validation_by_id.get(sequence.sequence_id) is False:
                findings[sequence.sequence_id] = [
                    "Numbering skipped because deterministic sequence validation failed."
                ]
                continue
            numbered = number_antibody_sequence(sequence)
            annotation = annotate_cdrs(sequence, numbered)
            numbering.append(numbered)
            annotations.append(annotation)
            findings[sequence.sequence_id] = validate_cdr_regions(annotation)
        return numbering, annotations, findings

    def _build_antigen_contexts(self, context: PipelineContext) -> list[AntigenContext]:
        target_records = [
            {
                "target_symbol": target.symbol,
                "antigen_name": target.name or target.symbol,
                "identifiers": target.identifiers,
                "evidence_item_ids": [
                    evidence.source_record_id
                    for evidence in target.evidence
                    if evidence.source_record_id
                ],
                "confidence": target.disease_relevance_score,
            }
            for target in context.targets
        ]
        return build_antigen_contexts(
            target_records=[
                *target_records,
                *_records(context, "biologics_target_records", "antigen_target_records"),
            ],
            structure_records=_records(context, "biologics_structure_records"),
            literature_claims=_records(context, "biologics_literature_claims"),
            external_registry_metadata=_records(
                context,
                "biologics_external_registry_metadata",
            ),
            user_supplied_antigen_annotations=_records(
                context,
                "biologics_user_antigen_annotations",
            ),
        )

    def _assess_novelty(
        self,
        candidates: Sequence[BiologicCandidate],
        sequences: Sequence[AntibodySequence],
        context: PipelineContext,
    ) -> list[AntibodyNoveltyAssessment]:
        sequence_by_id = {sequence.sequence_id: sequence for sequence in sequences}
        assessments: list[AntibodyNoveltyAssessment] = []
        for candidate in candidates:
            candidate_sequences = [
                sequence_by_id[sequence_id]
                for sequence_id in candidate.sequence_ids
                if sequence_id in sequence_by_id
            ]
            if not candidate_sequences:
                continue
            candidate_sequence_ids = {sequence.sequence_id for sequence in candidate_sequences}
            comparison_sequences = [
                sequence
                for sequence in sequences
                if sequence.sequence_id not in candidate_sequence_ids
            ]
            assessments.append(
                assess_antibody_novelty(
                    novelty_id=f"nov-{candidate.biologic_id}",
                    biologic_id=candidate.biologic_id,
                    sequences=candidate_sequences,
                    known_sequences=context.config.get("known_antibody_sequences") or {},
                    internal_candidate_registry=comparison_sequences,
                    imported_external_registry=context.config.get(
                        "imported_antibody_registry_sequences"
                    )
                    or (),
                    generated_sequence_archive=context.config.get(
                        "generated_antibody_sequence_archive"
                    )
                    or (),
                    public_antibody_database_adapters=context.config.get(
                        "public_antibody_database_adapters"
                    )
                    or (),
                    sources_checked=["internal_candidate_registry"],
                )
            )
        return assessments

    def _assess_developability(
        self,
        candidates: Sequence[BiologicCandidate],
        sequences: Sequence[AntibodySequence],
    ) -> list[AntibodyDevelopabilityAssessment]:
        sequence_by_id = {sequence.sequence_id: sequence for sequence in sequences}
        assessments: list[AntibodyDevelopabilityAssessment] = []
        for candidate in candidates:
            candidate_sequences = [
                sequence_by_id[sequence_id]
                for sequence_id in candidate.sequence_ids
                if sequence_id in sequence_by_id
            ]
            if not candidate_sequences:
                continue
            assessments.append(
                assess_antibody_developability(
                    assessment_id=f"dev-{candidate.biologic_id}",
                    biologic_id=candidate.biologic_id,
                    sequences=candidate_sequences,
                )
            )
        return assessments

    def _rank_candidates(
        self,
        candidates: Sequence[BiologicCandidate],
        *,
        novelty: Sequence[AntibodyNoveltyAssessment],
        developability: Sequence[AntibodyDevelopabilityAssessment],
        antigen_contexts: Sequence[AntigenContext],
        limit: int,
    ) -> list[BiologicCandidate]:
        novelty_by_biologic = {item.biologic_id: item for item in novelty}
        developability_by_biologic = {item.biologic_id: item for item in developability}
        antigen_by_target = {item.target_symbol: item for item in antigen_contexts}
        scored: list[BiologicCandidate] = []
        for candidate in candidates:
            antigen_context = next(
                (
                    antigen_by_target[target_symbol]
                    for target_symbol in candidate.target_symbols
                    if target_symbol in antigen_by_target
                ),
                None,
            )
            components = score_biologic_candidate_components(
                candidate,
                antigen_context=antigen_context,
                novelty=novelty_by_biologic.get(candidate.biologic_id),
                developability=developability_by_biologic.get(candidate.biologic_id),
            )
            candidate.metadata["biologics_score_components"] = components
            candidate.metadata["biologics_score"] = components["total_score"]
            scored.append(candidate)
        return rank_biologic_candidates(scored)[:limit]

    def _maybe_generate_antibodies(
        self,
        *,
        context: PipelineContext,
        config: BiologicsDiscoveryConfig,
        seeds: Sequence[AntibodySequence],
        antigen_contexts: Sequence[AntigenContext],
    ) -> list[GeneratedAntibodyHypothesis]:
        if not config.enable_antibody_generation or config.max_generated_antibodies <= 0:
            return []
        source_backed_seeds = [
            sequence
            for sequence in seeds
            if not sequence.is_generated
            and sequence.source != "generated"
            and (sequence.source_record_id or sequence.metadata.get("source_backed"))
        ]
        if not source_backed_seeds:
            return []
        antigen_context = antigen_contexts[0] if antigen_contexts else None
        design_mode = (
            "cdr_mutation"
            if config.antibody_generation_method == "conservative_cdr_mutator"
            else "broad_target_context_design"
        )
        if (
            design_mode == "epitope_context_design"
            and config.require_epitope_for_epitope_design
            and not (
                antigen_context
                and antigen_context.epitope_description
                and antigen_context.epitope_source
            )
        ):
            return []
        target_symbol = (
            antigen_context.target_symbol
            if antigen_context is not None
            else (_target_symbols(context)[0] if _target_symbols(context) else "UNKNOWN")
        )
        objective = build_antibody_design_objective(
            objective_id="bio-obj-1",
            disease_name=_disease_name(context),
            target_symbol=target_symbol,
            design_mode=design_mode,
            antigen_context=antigen_context,
            seed_sequences=source_backed_seeds,
            metadata={"biologic_id": "bio-generated-antibody"},
        )
        generator = self._select_generator(config)
        generated = generator.generate(
            objective,
            list(source_backed_seeds),
            antigen_context,
            {
                "mode": objective.mode,
                "max_outputs": config.max_generated_antibodies,
                "random_seed": context.config.get("antibody_generation_random_seed", 0),
                "known_sequences": context.config.get("known_antibody_sequences") or {},
            },
        )
        return [
            hypothesis
            for hypothesis in generated
            if self._generated_hypothesis_allowed(hypothesis, config)
        ]

    def _select_generator(
        self,
        config: BiologicsDiscoveryConfig,
    ) -> AntibodyGenerator:
        if self._generator is not None:
            return self._generator
        if config.antibody_generation_method == "conservative_cdr_mutator":
            return ConservativeCDRMutator()
        return NullAntibodyGenerator()

    def _generated_hypothesis_allowed(
        self,
        hypothesis: GeneratedAntibodyHypothesis,
        config: BiologicsDiscoveryConfig,
    ) -> bool:
        validation = _mapping(hypothesis.metadata.get("validation"))
        if validation and validation.get("valid") is False:
            return False
        if not config.reject_generated_sequence_liabilities:
            return True
        developability = _mapping(hypothesis.metadata.get("developability"))
        sequence_flags = _string_list(developability.get("sequence_liability_flags"))
        cdr_flags = _string_list(developability.get("cdr_liability_flags"))
        return not sequence_flags and not cdr_flags

    def _write_artifacts(
        self,
        *,
        context: PipelineContext,
        candidates: Sequence[BiologicCandidate],
        sequences: Sequence[AntibodySequence],
        validation_results: Sequence[Mapping[str, Any]],
        numbering: Sequence[AntibodyNumbering],
        cdr_annotations: Sequence[CDRAnnotation],
        cdr_findings: Mapping[str, list[str]],
        antigen_contexts: Sequence[AntigenContext],
        novelty: Sequence[AntibodyNoveltyAssessment],
        developability: Sequence[AntibodyDevelopabilityAssessment],
        generated: Sequence[GeneratedAntibodyHypothesis],
        retrieval_warnings: Sequence[str],
    ) -> dict[str, str]:
        output_dir = _output_dir(context)
        if output_dir is None:
            return {}
        output_dir.mkdir(parents=True, exist_ok=True)
        generated_sequences = _generated_sequences(generated)
        artifacts = {
            "biologic_candidates": output_dir / "biologic_candidates.json",
            "antibody_sequences": output_dir / "antibody_sequences.json",
            "antibody_numbering": output_dir / "antibody_numbering.json",
            "antibody_developability": output_dir / "antibody_developability.json",
            "antibody_novelty": output_dir / "antibody_novelty.json",
            "generated_antibodies": output_dir / "generated_antibodies.json",
            "biologics_report": output_dir / "biologics_report.md",
        }
        _write_json(
            artifacts["biologic_candidates"],
            {
                "biologic_candidates": [_dump(candidate) for candidate in candidates],
                "ranked_biologic_ids": [candidate.biologic_id for candidate in candidates],
                "score_components": {
                    candidate.biologic_id: candidate.metadata.get(
                        "biologics_score_components",
                        {"total_score": score_biologic_candidate(candidate)},
                    )
                    for candidate in candidates
                },
                "warnings": list(retrieval_warnings),
            },
        )
        _write_json(
            artifacts["antibody_sequences"],
            {
                "antibody_sequences": [_dump(sequence) for sequence in sequences],
                "generated_sequences": generated_sequences,
                "validation": list(validation_results),
            },
        )
        _write_json(
            artifacts["antibody_numbering"],
            {
                "numbering": [_dump(item) for item in numbering],
                "cdr_annotations": [_dump(item) for item in cdr_annotations],
                "cdr_findings": dict(cdr_findings),
            },
        )
        _write_json(
            artifacts["antibody_developability"],
            {"assessments": [_dump(item) for item in developability]},
        )
        _write_json(
            artifacts["antibody_novelty"],
            {"assessments": [_dump(item) for item in novelty]},
        )
        _write_json(
            artifacts["generated_antibodies"],
            {
                "generated_antibody_hypotheses": [_dump(item) for item in generated],
                "ranked_generated_antibody_ids": [
                    item.generated_antibody_id for item in generated
                ],
                "limitations": [
                    "Generated antibodies are computational hypotheses only.",
                    "No binding, activity, safety, developability, or manufacturability "
                    "claim is made from model output or sequence similarity.",
                ],
            },
        )
        artifacts["biologics_report"].write_text(
            _report_markdown(
                candidates=candidates,
                sequences=sequences,
                antigen_contexts=antigen_contexts,
                novelty=novelty,
                developability=developability,
                generated=generated,
            ),
            encoding="utf-8",
        )
        return {key: str(path) for key, path in artifacts.items()}

    def _base_metadata(self, config: BiologicsDiscoveryConfig) -> dict[str, Any]:
        return {
            "enabled": config.enable_biologics,
            "generation_enabled": config.enable_antibody_generation,
            "antibody_generation_method": config.antibody_generation_method,
            "max_existing_biologics": config.max_existing_biologics,
            "max_generated_antibodies": config.max_generated_antibodies,
        }


def _config_from_context(context: PipelineContext) -> BiologicsDiscoveryConfig:
    return BiologicsDiscoveryConfig(
        enable_biologics=bool(context.config.get("enable_biologics", False)),
        enable_antibody_generation=bool(
            context.config.get("enable_antibody_generation", False)
        ),
        antibody_generation_method=str(
            context.config.get("antibody_generation_method") or "null"
        ),
        max_existing_biologics=int(context.config.get("max_existing_biologics", 25)),
        max_generated_antibodies=int(context.config.get("max_generated_antibodies", 0)),
        require_epitope_for_epitope_design=bool(
            context.config.get("require_epitope_for_epitope_design", True)
        ),
        reject_generated_sequence_liabilities=bool(
            context.config.get("reject_generated_sequence_liabilities", True)
        ),
        strict_biologics_validation=bool(
            context.config.get("strict_biologics_validation", False)
        ),
    )


def _target_symbols(context: PipelineContext) -> list[str]:
    configured = _string_list(context.config.get("biologics_target_symbols"))
    if configured:
        return configured
    symbols: list[str] = []
    for target in context.targets:
        if target.symbol not in symbols:
            symbols.append(target.symbol)
    return symbols


def _disease_name(context: PipelineContext) -> str | None:
    if context.disease is not None:
        return context.disease.canonical_name
    return context.disease_input or None


def _records(context: PipelineContext, *keys: str) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for key in keys:
        value = context.config.get(key)
        if isinstance(value, Mapping):
            records.append(value)
        elif isinstance(value, Iterable) and not isinstance(value, str):
            records.extend(item for item in value if isinstance(item, Mapping))
    return records


def _sequence_metadata_by_id(context: PipelineContext) -> dict[str, Mapping[str, Any]]:
    raw = context.config.get("biologics_sequence_metadata") or {}
    metadata_by_id: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if isinstance(value, Mapping):
                metadata_by_id[str(key)] = value
    elif isinstance(raw, Iterable) and not isinstance(raw, str):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else item
            for key_name in ("sequence_id", "source_record_id", "biologic_id"):
                key = item.get(key_name)
                if key and isinstance(metadata, Mapping):
                    metadata_by_id[str(key)] = metadata
    return metadata_by_id


def _output_dir(context: PipelineContext) -> Path | None:
    configured = context.config.get("biologics_output_dir")
    if configured:
        return Path(str(configured))
    return context.output_dir


def _generated_sequences(
    generated: Sequence[GeneratedAntibodyHypothesis],
) -> list[dict[str, Any]]:
    sequences: list[dict[str, Any]] = []
    for hypothesis in generated:
        raw_sequences = hypothesis.metadata.get("generated_sequences")
        if isinstance(raw_sequences, list):
            sequences.extend(item for item in raw_sequences if isinstance(item, dict))
    return sequences


def _report_markdown(
    *,
    candidates: Sequence[BiologicCandidate],
    sequences: Sequence[AntibodySequence],
    antigen_contexts: Sequence[AntigenContext],
    novelty: Sequence[AntibodyNoveltyAssessment],
    developability: Sequence[AntibodyDevelopabilityAssessment],
    generated: Sequence[GeneratedAntibodyHypothesis],
) -> str:
    lines = [
        "# Biologics Discovery Report",
        "",
        "This report is a governed research-planning artifact.",
        "Generated antibodies are computational hypotheses only.",
        "No binding, neutralization, treatment, safety, developability, or "
        "manufacturability claim is made.",
        "",
        "## Summary",
        "",
        f"- Existing biologic candidates ranked: {len(candidates)}",
        f"- Antibody sequences available: {len(sequences)}",
        f"- Antigen contexts built: {len(antigen_contexts)}",
        f"- Novelty assessments: {len(novelty)}",
        f"- Developability heuristic assessments: {len(developability)}",
        f"- Generated antibody hypotheses ranked: {len(generated)}",
        "",
        "## Ranked Existing Biologics",
        "",
    ]
    for index, candidate in enumerate(candidates, start=1):
        score = candidate.metadata.get("biologics_score")
        score_text = f"{float(score):.3f}" if isinstance(score, int | float) else "n/a"
        lines.append(
            f"{index}. {candidate.name} (`{candidate.biologic_id}`), score {score_text}"
        )
    if not candidates:
        lines.append("No existing biologic candidates were retained.")
    lines.extend(["", "## Generated Antibody Hypotheses", ""])
    for index, hypothesis in enumerate(generated, start=1):
        score_text = f"{hypothesis.score:.3f}" if hypothesis.score is not None else "n/a"
        lines.append(
            f"{index}. `{hypothesis.generated_antibody_id}`, score {score_text}, "
            "direct evidence: false"
        )
    if not generated:
        lines.append("No generated antibody hypotheses were retained.")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Antibody sequence validation and developability are heuristic triage only.",
            "- Novelty checks are limited to configured sources checked.",
            "- Exact imported experimental results are required before any generated "
            "antibody has direct evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dump(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if item is not None]
    return [str(value)]


__all__ = [
    "BiologicsDiscoveryAgent",
    "BiologicsDiscoveryConfig",
]
