from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from molecule_ranker.agents.base import (
    DOMAIN_ERRORS,
    AgentExecutionError,
    BaseAgent,
    PipelineContext,
)
from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.evidence import (
    is_clinical_evidence,
    is_molecule_target_evidence,
    is_safety_warning,
    normalize_evidence_item,
)
from molecule_ranker.generation.schemas import GeneratedMolecule, GenerationRun
from molecule_ranker.schemas import (
    AgentTrace,
    EvidenceItem,
    GeneratedMoleculeHypothesis,
    MoleculeCandidate,
    Target,
)
from molecule_ranker.utils import slugify

DEFAULT_LIMITATIONS = [
    "Public databases may be incomplete or stale.",
    "Scores are heuristic prioritization aids.",
    "No wet-lab validation has been performed by this software.",
    "No patient-specific recommendation is provided.",
    "Generated molecule hypotheses are in-silico research hypotheses only.",
    (
        "Generated molecules are not claimed to cure, treat, bind, inhibit, "
        "activate, or be active against a disease or target."
    ),
    (
        "Generated molecule hypotheses are opt-in and ranked separately from "
        "evidence-backed molecules."
    ),
    "Record-level evidence provenance is reported for retrieved public-source records.",
    "Literature claims are conservative rule-based mentions from retrieved paper records.",
    "Absence of evidence is not evidence of absence.",
]


class ReportWriterAgent(BaseAgent):
    name = "ReportWriterAgent"

    def run(self, context: PipelineContext) -> PipelineContext:
        input_summary = self.summarize_input(context)
        warnings: list[str] = []
        try:
            updated = self.process(context)
            output_summary = self.summarize_output(updated)
        except Exception as exc:  # pragma: no cover - behavior covered through tests
            updated = context
            warning = f"{self.name} failed gracefully: {exc}"
            warnings.append(warning)
            updated.traces.append(
                AgentTrace(
                    agent_name=self.name,
                    input_summary=input_summary,
                    output_summary=warning,
                    warnings=warnings,
                    metadata=self.trace_metadata(updated),
                )
            )
            if isinstance(exc, DOMAIN_ERRORS):
                raise
            raise AgentExecutionError(f"{self.name} failed unexpectedly: {exc}") from exc

        trace = AgentTrace(
            agent_name=self.name,
            input_summary=input_summary,
            output_summary=output_summary,
            warnings=warnings,
            metadata={},
        )
        updated.traces.append(trace)
        updated.config["report_md"] = self.render(updated)
        trace.metadata = self.trace_metadata(updated)
        self._write_outputs(updated)
        self.logger.info("%s completed", self.name)
        return updated

    def process(self, context: PipelineContext) -> PipelineContext:
        self._validate_success_context(context)
        if context.disease is None:  # pragma: no cover - guarded by validation
            raise NoCandidatesFoundError("Report requires a resolved disease.")

        results_dir = Path(context.config.get("results_dir", "results"))
        context.output_dir = results_dir / slugify(context.disease.canonical_name)
        context.config["limitations"] = list(DEFAULT_LIMITATIONS)
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        output_dir = context.output_dir or "unknown output directory"
        return f"Wrote report artifacts for {len(context.candidates)} candidates to {output_dir}."

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        output_dir = context.output_dir
        return {
            "report_chars": len(str(context.config.get("report_md", ""))),
            "output_dir": str(output_dir) if output_dir else None,
            "artifacts": self._artifact_paths(output_dir) if output_dir else {},
        }

    def render(self, context: PipelineContext) -> str:
        if context.disease is None:  # pragma: no cover - guarded by validation
            raise NoCandidatesFoundError("Report requires a resolved disease.")

        disease = context.disease
        evidence = list(self._all_evidence(context.targets, context.candidates))
        sources = sorted(
            {item.source for item in evidence} | set(self._literature_sources(context))
        )
        source_limitations = self._source_limitations(context)
        top_candidates = context.candidates[:5]

        lines = [
            f"# Molecule Ranking Report: {disease.canonical_name}",
            "",
            "## Research-use disclaimer",
            "",
            (
                "This report is for research use only. It is not medical advice, does not "
                "predict that any molecule cures a disease, and does not provide dosage or "
                "patient treatment instructions. Each candidate is a therapeutic relevance "
                "hypothesis that requires experimental validation and clinical validation."
            ),
            "",
            "## Data provenance",
            "",
            f"- Data sources used: {', '.join(sources) if sources else 'None recorded'}",
            f"- Retrieval timestamps: {self._retrieval_timestamp_summary(evidence)}",
            "- Disease identifiers used:",
            *self._identifier_lines(disease.identifiers),
            f"- Number of real evidence items retrieved: {len(evidence)}",
            "- API/source limitations:",
            *[f"  - {limitation}" for limitation in source_limitations],
            "",
            "## Data Sources and Retrieval",
            "",
            *self._data_sources_retrieval_lines(context, evidence),
            "",
            "## Disease Resolution",
            "",
            *self._disease_resolution_lines(context),
            "",
            "## Target Mapping",
            "",
            *self._target_mapping_lines(context),
            "",
            "## Evidence Coverage",
            "",
            *self._evidence_coverage_lines(context),
            "",
            "## Literature Evidence Summary",
            "",
            *self._literature_summary_lines(context),
            "",
            "## Literature Query Audit",
            "",
            *self._literature_query_audit_lines(context),
            "",
            "## Candidate Literature Evidence",
            "",
            *self._candidate_literature_evidence_overview_lines(context),
            "",
            "## Citations",
            "",
            *self._citation_lines(context),
            "",
            "## Summary",
            "",
            f"- Disease input: {disease.input_name}",
            f"- Canonical disease: {disease.canonical_name}",
            f"- Number of targets: {len(context.targets)}",
            f"- Number of molecule candidates: {len(context.candidates)}",
            f"- Number of generated molecule hypotheses: {len(context.generated_candidates)}",
            "- Top 5 candidates:",
            *[
                f"  - {candidate.name} ({candidate.score:.3f})"
                for candidate in top_candidates
                if candidate.score is not None
            ],
            "",
            "## Generated Molecule Hypotheses",
            "",
            *self._generated_molecule_hypothesis_lines(context),
            "",
            "## Ranked Candidates",
        ]

        for index, candidate in enumerate(context.candidates, start=1):
            lines.extend(self._candidate_section(index, candidate))

        lines.extend(["", "## Targets Considered"])
        for target in context.targets:
            lines.extend(self._target_section(target))

        lines.extend(["", "## Pipeline Trace"])
        for trace in context.traces:
            lines.extend(
                [
                    f"- **{trace.agent_name}**",
                    f"  - Input: {trace.input_summary}",
                    f"  - Output: {trace.output_summary}",
                ]
            )
            if trace.warnings:
                lines.append(f"  - Warnings: {'; '.join(trace.warnings)}")

        lines.extend(["", "## Limitations"])
        lines.extend(f"- {limitation}" for limitation in DEFAULT_LIMITATIONS)
        return "\n".join(lines) + "\n"

    def _write_outputs(self, context: PipelineContext) -> None:
        if context.disease is None or context.output_dir is None:
            raise NoCandidatesFoundError("Report requires a resolved disease and output directory.")

        output_dir = context.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts = self._artifact_paths(output_dir)
        limitations = list(context.config.get("limitations", DEFAULT_LIMITATIONS))

        (output_dir / "candidates.json").write_text(
            _json_dumps(
                {
                    "success": True,
                    "disease": context.disease,
                    "targets": context.targets,
                    "candidates": context.candidates,
                    "generated_molecule_hypotheses": context.generated_candidates,
                    "literature_evidence_summary": self._literature_summary_payload(
                        context
                    ),
                    "literature_queries": self._literature_queries_payload(context),
                    "literature_papers": self._literature_papers_payload(context),
                    "extracted_claims": self._extracted_claims_payload(context),
                    "summary": {
                        "target_count": len(context.targets),
                        "candidate_count": len(context.candidates),
                        "generated_candidate_count": len(context.generated_candidates),
                        "evidence_item_count": len(
                            list(self._all_evidence(context.targets, context.candidates))
                        ),
                    },
                    "limitations": limitations,
                }
            )
        )
        generation_run = self._generation_run(context)
        generation_enabled = self._generation_enabled(context, generation_run)
        if generation_enabled:
            generated_payload = self._generated_candidates_payload(
                context,
                generation_run,
                limitations,
            )
            generation_trace_payload = self._generation_trace_payload(
                context,
                generation_run,
            )
            (output_dir / "generated_molecules.json").write_text(
                _json_dumps(generated_payload)
            )
            (output_dir / "generated_candidates.json").write_text(
                _json_dumps(generated_payload)
            )
            (output_dir / "generation_trace.json").write_text(
                _json_dumps(generation_trace_payload)
            )
        (output_dir / "report.md").write_text(str(context.config["report_md"]))
        (output_dir / "trace.json").write_text(
            _json_dumps(
                {
                    "success": True,
                    "disease": context.disease,
                    "traces": context.traces,
                    "config": context.config.get("ranker_config", {}),
                    "limitations": limitations,
                    "artifacts": artifacts,
                }
            )
        )

    def _validate_success_context(self, context: PipelineContext) -> None:
        if context.disease is None:
            raise NoCandidatesFoundError("Report requires a resolved disease.")
        if not context.targets:
            raise NoCandidatesFoundError("Report requires evidence-backed targets.")
        if not context.candidates:
            raise NoCandidatesFoundError("Report requires scored molecule candidates.")

        unscored = [
            candidate.name
            for candidate in context.candidates
            if candidate.score is None or candidate.score_breakdown is None
        ]
        if unscored:
            raise NoCandidatesFoundError(
                "Report requires scored candidates; missing score breakdown for "
                f"{', '.join(unscored)}."
            )

        no_evidence = [
            candidate.name
            for candidate in context.candidates
            if not candidate.evidence
            and candidate.origin != "generated"
        ]
        if no_evidence:
            raise NoCandidatesFoundError(
                "Report requires evidence-backed candidates; missing evidence for "
                f"{', '.join(no_evidence)}."
            )

    def _generation_enabled(
        self,
        context: PipelineContext,
        generation_run: GenerationRun | None,
    ) -> bool:
        return bool(
            context.config.get("enable_generation")
            or context.config.get("enable_novel_generation")
            or generation_run is not None
            or context.generated_candidates
        )

    def _generation_run(self, context: PipelineContext) -> GenerationRun | None:
        value = context.config.get("generation_run")
        return value if isinstance(value, GenerationRun) else None

    def _generated_candidates_payload(
        self,
        context: PipelineContext,
        generation_run: GenerationRun | None,
        limitations: list[str],
    ) -> dict[str, Any]:
        retained = generation_run.retained if generation_run is not None else []
        rejected = generation_run.rejected if generation_run is not None else []
        generated = generation_run.generated if generation_run is not None else []
        return {
            "success": True,
            "disease": context.disease,
            "generation_enabled": True,
            "objectives": generation_run.objectives if generation_run is not None else [],
            "seeds": generation_run.seeds if generation_run is not None else [],
            "generated_count": len(generated),
            "retained_count": len(retained) or len(context.generated_candidates),
            "rejected_count": len(rejected),
            "retained_generated_molecules": retained or context.generated_candidates,
            "rejected_generated_molecules": [
                {
                    "generated_molecule": candidate,
                    "rejection_reasons": self._generated_rejection_reasons(candidate),
                }
                for candidate in rejected
            ],
            "warnings": list(generation_run.warnings) if generation_run is not None else [],
            "generation_config": self._generation_config_payload(context),
            "limitations": limitations,
        }

    def _generation_trace_payload(
        self,
        context: PipelineContext,
        generation_run: GenerationRun | None,
    ) -> dict[str, Any]:
        trace_metadata = self._novel_molecule_trace_metadata(context)
        run_metadata = generation_run.metadata if generation_run is not None else {}
        return {
            "seed_selection_trace": trace_metadata.get("seed_selection", {}),
            "objective_building_trace": trace_metadata.get("objective_building", {}),
            "generator_trace": trace_metadata.get("generator_trace", {}),
            "validation_filtering_trace": trace_metadata.get(
                "validation_filtering_trace",
                {},
            ),
            "scoring_trace": trace_metadata.get("scoring_trace", {}),
            "random_seed": self._generation_config_payload(context).get(
                "generation_random_seed"
            ),
            "generator_method": run_metadata.get(
                "generation_method",
                trace_metadata.get("generator"),
            ),
            "generator_version": run_metadata.get("generator_version", "v0.3"),
            "run_timestamp": run_metadata.get("run_timestamp"),
        }

    def _novel_molecule_trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        for trace in context.traces:
            if trace.agent_name == "NovelMoleculeAgent":
                return dict(trace.metadata)
        return {}

    def _generation_config_payload(self, context: PipelineContext) -> dict[str, Any]:
        ranker_config = context.config.get("ranker_config")
        if isinstance(ranker_config, dict):
            return {
                key: value
                for key, value in ranker_config.items()
                if key.startswith("generation")
                or key.startswith("max_generation")
                or key
                in {
                    "enable_generation",
                    "enable_novel_generation",
                    "strict_generation",
                    "include_generated_in_main_ranking",
                    "max_seed_molecules",
                    "generated_per_objective",
                    "max_generated_before_filtering",
                    "max_retained_generated",
                    "max_mutations_per_child",
                    "enable_crossover",
                    "min_seed_score",
                    "min_seed_target_relevance",
                    "min_target_relevance_for_generation",
                    "duplicate_similarity_threshold",
                    "near_duplicate_similarity_threshold",
                    "distant_similarity_threshold",
                    "reject_distant_generated",
                    "reject_basic_alerts",
                    "allowed_generation_elements",
                }
            }
        return {
            key: value
            for key, value in context.config.items()
            if key.startswith("generation")
            or key.startswith("max_generation")
            or key
            in {
                "enable_generation",
                "enable_novel_generation",
                "strict_generation",
                "include_generated_in_main_ranking",
                "max_seed_molecules",
                "generated_per_objective",
                "max_generated_before_filtering",
                "max_retained_generated",
                "max_mutations_per_child",
                "enable_crossover",
                "min_seed_score",
                "min_seed_target_relevance",
                "min_target_relevance_for_generation",
                "duplicate_similarity_threshold",
                "near_duplicate_similarity_threshold",
                "distant_similarity_threshold",
                "reject_distant_generated",
                "reject_basic_alerts",
                "allowed_generation_elements",
            }
        }

    def _generated_rejection_reasons(self, candidate: GeneratedMolecule) -> list[str]:
        reasons = list(candidate.validation.rejection_reasons)
        if candidate.novelty is not None and candidate.novelty.novelty_class in {
            "duplicate",
            "near_duplicate",
            "distant",
        }:
            reasons.append(candidate.novelty.novelty_class)
        if not reasons:
            reasons.append("diversity_or_retention_limit")
        return sorted(set(reasons))

    def _candidate_section(self, rank: int, candidate: MoleculeCandidate) -> list[str]:
        score = candidate.score_breakdown
        confidence = score.confidence if score else 0.0
        lines = [
            "",
            f"### {rank}. {candidate.name}",
            "",
            f"- Rank: {rank}",
            f"- Final score: {(candidate.score or 0.0):.3f}",
            f"- Confidence: {confidence:.3f}",
            f"- Development status: {candidate.development_status or 'Unavailable'}",
            f"- Known targets: {', '.join(candidate.known_targets) or 'Unavailable'}",
            f"- Mechanism of action: {candidate.mechanism_of_action or 'Unavailable'}",
            "",
            "| Component | Score |",
            "| --- | ---: |",
        ]
        if score:
            lines.extend(
                [
                    f"| Disease-target relevance | {score.disease_target_relevance:.3f} |",
                    f"| Molecule-target evidence | {score.molecule_target_evidence:.3f} |",
                    f"| Mechanism plausibility | {score.mechanism_plausibility:.3f} |",
                    f"| Clinical precedence | {score.clinical_precedence:.3f} |",
                    f"| Safety prior | {score.safety_prior:.3f} |",
                    f"| Data quality | {score.data_quality:.3f} |",
                    (
                        "| Novelty or repurposing value | "
                        f"{score.novelty_or_repurposing_value:.3f} |"
                    ),
                    f"| Literature quality | {score.literature_quality:.3f} |",
                    f"| Final score | {score.final_score:.3f} |",
                    f"| Confidence | {score.confidence:.3f} |",
                    "",
                    f"Score explanation: {score.explanation}",
                ]
            )

        lines.extend(["", "Evidence summary:"])
        lines.extend(self._evidence_summary_lines(candidate.evidence))
        lines.extend(["", "Literature evidence:"])
        lines.extend(self._candidate_literature_lines(candidate))
        lines.extend(["", "Candidate evidence coverage:"])
        lines.extend(self._candidate_coverage_lines(candidate))
        lines.extend(["", "Known indications and warnings:"])
        lines.extend(self._known_indication_warning_lines(candidate.evidence))
        lines.extend(["", "Source provenance:"])
        lines.extend(self._provenance_lines(candidate.evidence))
        lines.extend(["", "Warnings:"])
        if candidate.warnings:
            lines.extend(f"- {warning}" for warning in candidate.warnings)
        else:
            lines.append("- None recorded.")
        return lines

    def _generated_molecule_hypothesis_lines(
        self,
        context: PipelineContext,
    ) -> list[str]:
        generation_run = self._generation_run(context)
        retained = generation_run.retained if generation_run is not None else []
        rejected = generation_run.rejected if generation_run is not None else []
        generated = generation_run.generated if generation_run is not None else []
        header = [
            "- Generated molecules are computational structures.",
            "- Generated molecules have no direct experimental evidence.",
            (
                "- Their scores are generation-prioritization scores, not efficacy "
                "predictions."
            ),
            (
                "- They require chemical review, synthesis feasibility review, ADMET "
                "review, wet-lab testing, and clinical validation."
            ),
            "- No synthesis instructions are provided.",
            "- No invented evidence is attached to generated molecules.",
            "- Existing evidence-backed candidates and generated hypotheses are listed separately.",
        ]

        if not retained and not context.generated_candidates:
            return [
                *header,
                "- Generation was not run or produced no retained hypotheses.",
            ]

        lines = [
            *header,
            "",
            "### Generated Summary",
            "",
            "| Metric | Count |",
            "| --- | ---: |",
            f"| Generated attempted | {len(generated) or len(context.generated_candidates)} |",
            f"| Valid retained | {len(retained) or len(context.generated_candidates)} |",
            f"| Rejected invalid | {self._rejected_invalid_count(rejected)} |",
            (
                "| Rejected duplicate/near-duplicate | "
                f"{self._rejected_novelty_count(rejected, {'duplicate', 'near_duplicate'})} |"
            ),
            (
                "| Rejected distant/unconditioned | "
                f"{self._rejected_novelty_count(rejected, {'distant'})} |"
            ),
            "",
            "### Retained By Target",
            "",
            "| Target | Retained |",
            "| --- | ---: |",
            *[
                f"| {target} | {count} |"
                for target, count in self._retained_by_target(retained, context).items()
            ],
        ]

        if not retained:
            lines.extend(self._legacy_generated_candidate_lines(context.generated_candidates))
            return lines

        seed_names_by_id = self._seed_names_by_id(generation_run)
        for rank, candidate in enumerate(retained, start=1):
            breakdown = candidate.score_breakdown
            validation = candidate.validation
            novelty = candidate.novelty
            parent_seed_names = [
                seed_names_by_id.get(seed_id, seed_id) for seed_id in candidate.parent_seed_ids
            ]
            explanation = breakdown.explanation if breakdown else "No explanation recorded."
            lines.extend(
                [
                    "",
                    f"### Generated {rank}. {candidate.generated_id}",
                    "",
                    "| Field | Value |",
                    "| --- | --- |",
                    f"| Rank within generated list | {rank} |",
                    f"| Generated ID | `{candidate.generated_id}` |",
                    f"| Canonical SMILES | `{candidate.canonical_smiles}` |",
                    f"| InChIKey | {candidate.inchi_key or 'Unavailable'} |",
                    (
                        "| Conditioned target(s) | "
                        f"{', '.join(candidate.conditioned_targets) or 'Unavailable'} |"
                    ),
                    (
                        "| Parent seed molecule(s) | "
                        f"{', '.join(parent_seed_names) or 'Unavailable'} |"
                    ),
                    f"| Generation method | {candidate.generation_method} |",
                    f"| Final generation score | {(candidate.generation_score or 0.0):.3f} |",
                    (
                        "| Confidence | "
                        f"{breakdown.confidence:.3f} |"
                        if breakdown
                        else "| Confidence | Unavailable |"
                    ),
                    "",
                    "Score breakdown:",
                    "",
                    "| Component | Score |",
                    "| --- | ---: |",
                    *self._generated_score_breakdown_lines(candidate),
                    "",
                    "Descriptor table:",
                    "",
                    "| Descriptor | Value |",
                    "| --- | ---: |",
                    *self._generated_descriptor_lines(candidate.descriptors),
                    "",
                    "Novelty assessment:",
                    "",
                    *self._generated_novelty_lines(novelty),
                    "",
                    "Validation status:",
                    "",
                    *self._generated_validation_lines(validation),
                    "",
                    "Warnings:",
                    *[
                        f"- {warning}"
                        for warning in (
                            candidate.warnings
                            or ["Generated hypothesis requires independent review."]
                        )
                    ],
                    "",
                    f"Explanation: {explanation}",
                ]
            )
        return lines

    def _legacy_generated_candidate_lines(
        self,
        generated_candidates: list[GeneratedMoleculeHypothesis],
    ) -> list[str]:
        lines: list[str] = []
        for candidate in generated_candidates:
            rank = candidate.rank or "-"
            lines.extend(
                [
                    "",
                    f"### Generated {rank}. {candidate.name}",
                    "",
                    f"- Canonical SMILES: `{candidate.canonical_smiles}`",
                    f"- Conditioned target(s): {candidate.target_symbol}",
                    f"- Generation method: {candidate.source}",
                    f"- Final generation score: {candidate.generation_score:.3f}",
                    (
                        "- Parent seed molecule(s): "
                        f"{', '.join(candidate.seed_molecule_names) or 'Unavailable'}"
                    ),
                    "- Warnings:",
                    *[
                        f"  - {warning}"
                        for warning in (
                            candidate.warnings
                            or ["Generated hypothesis requires independent review."]
                        )
                    ],
                ]
            )
        return lines

    def _generated_score_breakdown_lines(
        self,
        candidate: GeneratedMolecule,
    ) -> list[str]:
        breakdown = candidate.score_breakdown
        if breakdown is None:
            return ["| Unavailable | 0.000 |"]
        return [
            f"| Target conditioning | {breakdown.target_conditioning_score:.3f} |",
            f"| Seed evidence | {breakdown.seed_evidence_score:.3f} |",
            f"| Novelty | {breakdown.novelty_score:.3f} |",
            f"| Diversity | {breakdown.diversity_score:.3f} |",
            f"| Chemical validity | {breakdown.chemical_validity_score:.3f} |",
            f"| Property profile | {breakdown.property_profile_score:.3f} |",
            f"| Literature context | {breakdown.literature_context_score:.3f} |",
            f"| Final generation score | {breakdown.final_generation_score:.3f} |",
            f"| Confidence | {breakdown.confidence:.3f} |",
        ]

    def _generated_descriptor_lines(self, descriptors: dict[str, Any]) -> list[str]:
        if not descriptors:
            return ["| Unavailable | 0 |"]
        return [
            f"| {key} | {self._format_descriptor(descriptors, key)} |"
            for key in sorted(descriptors)
        ]

    def _generated_novelty_lines(self, novelty: Any | None) -> list[str]:
        if novelty is None:
            return ["- Novelty assessment unavailable."]
        return [
            f"- Novelty class: {novelty.novelty_class}",
            f"- Duplicate of existing: {novelty.duplicate_of_existing}",
            f"- Duplicate of generated: {novelty.duplicate_of_generated}",
            (
                "- Max similarity to existing: "
                f"{novelty.max_similarity_to_existing:.3f}"
            ),
            f"- Nearest existing: {novelty.nearest_existing_name or 'Unavailable'}",
            f"- Max similarity to seed: {novelty.max_similarity_to_seed:.3f}",
            f"- Nearest seed: {novelty.nearest_seed_name or 'Unavailable'}",
        ]

    def _generated_validation_lines(self, validation: Any) -> list[str]:
        rejection_reasons = (
            ", ".join(validation.rejection_reasons)
            if validation.rejection_reasons
            else "None"
        )
        return [
            f"- RDKit molecule valid: {validation.valid_rdkit_mol}",
            f"- Sanitization OK: {validation.sanitization_ok}",
            f"- Canonicalization OK: {validation.canonicalization_ok}",
            f"- Allowed elements OK: {validation.allowed_elements_ok}",
            f"- Descriptor bounds OK: {validation.descriptor_bounds_ok}",
            (
                "- Alerts: "
                f"{', '.join(validation.pains_or_alerts) if validation.pains_or_alerts else 'None'}"
            ),
            f"- Rejection reasons: {rejection_reasons}",
        ]

    def _rejected_invalid_count(self, rejected: list[GeneratedMolecule]) -> int:
        return sum(1 for candidate in rejected if candidate.validation.rejection_reasons)

    def _rejected_novelty_count(
        self,
        rejected: list[GeneratedMolecule],
        novelty_classes: set[str],
    ) -> int:
        return sum(
            1
            for candidate in rejected
            if candidate.novelty is not None
            and candidate.novelty.novelty_class in novelty_classes
        )

    def _retained_by_target(
        self,
        retained: list[GeneratedMolecule],
        context: PipelineContext,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in retained:
            for target in candidate.conditioned_targets or ["Unavailable"]:
                counts[target] = counts.get(target, 0) + 1
        if counts:
            return dict(sorted(counts.items()))
        fallback: dict[str, int] = {}
        for candidate in context.generated_candidates:
            fallback[candidate.target_symbol] = fallback.get(candidate.target_symbol, 0) + 1
        return dict(sorted(fallback.items())) or {"Unavailable": 0}

    def _seed_names_by_id(self, generation_run: GenerationRun | None) -> dict[str, str]:
        if generation_run is None:
            return {}
        names: dict[str, str] = {}
        for seed in generation_run.seeds:
            for key in ("chembl", "pubchem_cid", "cid", "inchikey", "name"):
                value = seed.identifiers.get(key)
                if value:
                    names[str(value)] = seed.name
            names[seed.name] = seed.name
        return names

    def _format_descriptor(
        self,
        descriptors: dict[str, Any],
        key: str,
    ) -> str:
        value = descriptors.get(key)
        if isinstance(value, float):
            return f"{value:.3f}"
        if value in (None, ""):
            return "unavailable"
        return str(value)

    def _data_sources_retrieval_lines(
        self,
        context: PipelineContext,
        evidence: list[EvidenceItem],
    ) -> list[str]:
        config = dict(context.config.get("ranker_config", {}))
        return [
            f"- Open Targets endpoint: {self._source_endpoint(evidence, 'Open Targets')}",
            f"- ChEMBL endpoint: {self._source_endpoint(evidence, 'ChEMBL')}",
            f"- PubChem endpoint: {self._source_endpoint(evidence, 'PubChem')}",
            f"- PubMed endpoint: {self._literature_source_endpoint(context, 'PubMed')}",
            f"- OpenAlex endpoint: {self._literature_source_endpoint(context, 'OpenAlex')}",
            f"- Cache usage: {self._cache_usage_text(config)}",
            f"- Retrieval timestamps: {self._retrieval_timestamp_summary(evidence)}",
            "- Source versions/status: unavailable",
        ]

    def _literature_overview_lines(self, context: PipelineContext) -> list[str]:
        lines: list[str] = []
        for candidate in context.candidates:
            lines.append(f"- Candidate: {candidate.name}")
            for line in self._candidate_literature_lines(candidate):
                lines.append(f"  {line}" if line.startswith("-") else f"  - {line}")
        return lines or ["- No molecule candidates available for literature review."]

    def _literature_summary_lines(self, context: PipelineContext) -> list[str]:
        summary = self._literature_summary_payload(context)
        warnings = summary.get("warnings", [])
        lines = [
            f"- Literature sources used: {', '.join(summary['sources_used']) or 'None recorded'}",
            f"- Number of queries generated: {summary['queries_generated']}",
            f"- Number of papers retrieved: {summary['papers_retrieved']}",
            f"- Number of unique papers retained: {summary['unique_papers_retained']}",
            f"- Number of claims extracted: {summary['claims_extracted']}",
            f"- Number of evidence items attached: {summary['evidence_items_attached']}",
            f"- strict_literature mode: {summary['strict_literature']}",
            "- Warnings:",
        ]
        if warnings:
            lines.extend(f"  - {warning}" for warning in warnings)
        else:
            lines.append("  - None recorded.")
        return lines

    def _literature_query_audit_lines(self, context: PipelineContext) -> list[str]:
        queries = self._literature_queries_payload(context)
        if not queries:
            return ["- No literature queries recorded."]
        lines: list[str] = []
        for query in queries:
            lines.extend(
                [
                    f"- query_id: {query['query_id']}",
                    f"  - query_type: {query['query_type']}",
                    f"  - query_text: {query['query_text']}",
                    f"  - source: {query['source']}",
                    f"  - papers returned: {query['papers_returned']}",
                    f"  - claims extracted: {query['claims_extracted']}",
                ]
            )
        return lines

    def _candidate_literature_evidence_overview_lines(
        self,
        context: PipelineContext,
    ) -> list[str]:
        lines: list[str] = []
        for candidate in context.candidates:
            literature = self._candidate_literature_items(candidate)
            literature_papers = self._candidate_literature_papers_from_config(
                context, candidate
            )
            counts = self._literature_claim_counts(literature)
            paper_keys = self._paper_keys(literature) | {
                self._paper_payload_key(paper) for paper in literature_papers
            }
            citations = self._top_citations(literature, limit=5)
            if not citations:
                citations = self._top_paper_citations(literature_papers, limit=5)
            lines.extend(
                [
                    f"- Candidate: {candidate.name}",
                    f"  - Total literature papers: {len(paper_keys)}",
                    f"  - Supportive claims: {counts['supportive']}",
                    f"  - Clinical claims: {counts['clinical']}",
                    f"  - Safety claims: {counts['safety']}",
                    f"  - Contradictory claims: {counts['contradictory']}",
                    f"  - Mention-only claims: {counts['mention_only']}",
                    "  - Top citations:",
                ]
            )
            if citations:
                lines.extend(f"    - {citation}" for citation in citations)
            else:
                lines.append("    - None recorded.")
        return lines or ["- No candidate literature evidence recorded."]

    def _citation_lines(self, context: PipelineContext) -> list[str]:
        papers = self._literature_papers_payload(context)
        if not papers:
            return ["- No cited literature papers recorded."]
        lines: list[str] = []
        for paper in papers:
            identifiers = self._citation_identifier_text(paper)
            lines.extend(
                [
                    f"- {paper['citation']['citation_text']}",
                    f"  - IDs: {identifiers or 'unavailable'}",
                    f"  - URL: {paper.get('url') or 'unavailable'}",
                    f"  - Source: {paper.get('source') or 'unavailable'}",
                    f"  - Retrieved at: {paper.get('retrieved_at') or 'unavailable'}",
                    (
                        "  - Retraction status: "
                        f"{'retracted' if paper.get('is_retracted') else 'not retracted'}"
                    ),
                ]
            )
        return lines

    def _candidate_literature_lines(self, candidate: MoleculeCandidate) -> list[str]:
        normalized_literature = self._candidate_literature_items(candidate)
        bundle = candidate.literature_evidence
        if bundle is None and not normalized_literature:
            return ["- Literature evidence is absent: retrieval was not run."]
        if bundle is not None and not bundle.items and not normalized_literature:
            reason = bundle.absent_reason or "No supported literature evidence was retrieved."
            return [f"- Literature evidence is absent: {reason}"]
        if normalized_literature:
            counts = self._literature_claim_counts(normalized_literature)
            lines = [
                f"- Total literature papers: {len(self._paper_keys(normalized_literature))}",
                f"- Supportive claims: {counts['supportive']}",
                f"- Clinical claims: {counts['clinical']}",
                f"- Safety claims: {counts['safety']}",
                f"- Contradictory claims: {counts['contradictory']}",
                f"- Mention-only claims: {counts['mention_only']}",
                "- Supporting snippets:",
                *self._snippet_lines(
                    [
                        item
                        for item in normalized_literature
                        if item.evidence_type
                        not in {"literature_safety", "literature_contradictory"}
                    ],
                    limit=5,
                ),
                "- Safety/contradictory snippets:",
                *self._snippet_lines(
                    [
                        item
                        for item in normalized_literature
                        if item.evidence_type
                        in {"literature_safety", "literature_contradictory"}
                    ],
                    limit=5,
                    contradictory_label=True,
                ),
                "- Citation IDs:",
                *self._citation_id_lines(normalized_literature),
            ]
            if bundle is None:
                return lines
        else:
            lines = []
        if bundle is None:
            return lines
        lines = [
            *lines,
            f"- Literature quality: {bundle.quality_score:.3f}",
            f"- Queries run: {bundle.query_count}",
        ]
        for item in bundle.items:
            citation = item.citation
            citation_text = citation.formatted or citation.title
            if citation.url:
                citation_text = f"{citation_text} ({citation.url})"
            lines.extend(
                [
                    f"- Citation: {citation_text}",
                    f"- Study type: {item.claims[0].study_type if item.claims else 'unknown'}",
                    f"- Paper quality: {item.quality_score:.3f}",
                ]
            )
            for claim in item.claims:
                lines.append(f"- Claim: {claim.text}")
            lines.append(f"- Query: {item.query.query_text}")
        return lines

    def _disease_resolution_lines(self, context: PipelineContext) -> list[str]:
        disease = context.disease
        resolution_trace = self._trace_by_name(context, "DiseaseResolverAgent")
        metadata = resolution_trace.metadata if resolution_trace else {}
        selected_entity = (
            metadata.get("selected_disease_name")
            or (disease.canonical_name if disease is not None else None)
            or "unavailable"
        )
        selected_id = metadata.get("selected_disease_id")
        match_reason = metadata.get("match_reason") or "unavailable"
        ambiguity = metadata.get("ambiguity")
        ambiguity_text = (
            "unavailable"
            if ambiguity is None
            else ("ambiguous" if ambiguity else "not ambiguous")
        )
        identifiers = disease.identifiers if disease is not None else {}
        lines = [
            f"- Selected disease entity: {selected_entity}",
        ]
        if selected_id:
            lines.append(f"- Selected disease ID: {selected_id}")
        lines.extend(
            [
                "- Identifiers:",
                *self._identifier_lines(identifiers),
                f"- Match reason: {match_reason}",
                f"- Ambiguity handling result: {ambiguity_text}",
            ]
        )
        return lines

    def _target_mapping_lines(self, context: PipelineContext) -> list[str]:
        lines: list[str] = []
        for target in context.targets:
            mapping = self._target_mapping(target, context.candidates)
            molecules_found = any(
                target.symbol in candidate.known_targets for candidate in context.candidates
            )
            mapping_confidence = self._format_optional_float(mapping.get("confidence"))
            lines.extend(
                [
                    f"- {target.symbol}",
                    f"  - Open Targets ID: {self._target_open_targets_id(target)}",
                    f"  - ChEMBL target mapping: {mapping.get('chembl_target_id', 'unavailable')}",
                    f"  - Mapping method: {mapping.get('mapping_method', 'unavailable')}",
                    f"  - Mapping confidence: {mapping_confidence}",
                    f"  - Molecules found: {'yes' if molecules_found else 'no'}",
                ]
            )
        return lines or ["- None recorded."]

    def _evidence_coverage_lines(self, context: PipelineContext) -> list[str]:
        evidence = list(self._all_evidence(context.targets, context.candidates))
        counts = self._coverage_counts(evidence)
        lines = [
            f"- Disease-target evidence count: {counts['disease_target']}",
            f"- Mechanism evidence count: {counts['mechanism']}",
            f"- Activity evidence count: {counts['activity']}",
            f"- Indication evidence count: {counts['indication']}",
            f"- Safety warning evidence count: {counts['safety_warning']}",
            f"- Chemical annotation count: {counts['chemical_annotation']}",
        ]
        for candidate in context.candidates:
            lines.extend(
                [
                    f"- Candidate: {candidate.name}",
                    *[f"  - {line}" for line in self._candidate_coverage_lines(candidate)],
                ]
            )
        return lines

    def _candidate_coverage_lines(self, candidate: MoleculeCandidate) -> list[str]:
        molecule_target = [
            item for item in candidate.evidence if is_molecule_target_evidence(item)
        ]
        return [
            f"Molecule-target evidence: {len(molecule_target)}",
            "Activity evidence summary:",
            *self._activity_summary_lines(candidate.evidence),
            "Indication evidence summary:",
            *self._indication_summary_lines(candidate.evidence),
            "Safety warnings:",
            *self._safety_warning_summary_lines(candidate.evidence),
            "Chemical identifiers:",
            *self._chemical_identifier_lines(candidate),
            "Deduplication metadata:",
            *self._deduplication_lines(candidate),
        ]

    def _target_section(self, target: Target) -> list[str]:
        lines = [
            "",
            f"### {target.symbol}",
            "",
            f"- Symbol: {target.symbol}",
            f"- Name: {target.name or 'Unavailable'}",
            f"- Disease relevance score: {target.disease_relevance_score:.3f}",
            f"- Mechanism: {target.mechanism or 'Unavailable'}",
            "",
            "Evidence summaries:",
            *self._evidence_summary_lines(target.evidence),
            "",
            "Source provenance:",
            *self._provenance_lines(target.evidence),
        ]
        return lines

    def _evidence_summary_lines(self, evidence: list[EvidenceItem]) -> list[str]:
        if not evidence:
            return ["- None recorded."]
        lines: list[str] = []
        for item in evidence:
            normalized = normalize_evidence_item(item)
            lines.append(
                f"- [{item.source}] {item.title} "
                f"({normalized.evidence_type}, confidence {item.confidence:.3f}): {item.summary}"
            )
        return lines

    def _known_indication_warning_lines(self, evidence: list[EvidenceItem]) -> list[str]:
        relevant = [
            item
            for item in evidence
            if is_clinical_evidence(item) or is_safety_warning(item)
        ]
        if not relevant:
            return ["- None retrieved from ChEMBL."]
        lines: list[str] = []
        for item in relevant:
            if is_clinical_evidence(item):
                indication = item.metadata.get("indication") or item.summary
                phase = item.metadata.get("max_phase_for_ind")
                identifiers = []
                if item.metadata.get("mesh_id"):
                    identifiers.append(f"mesh_id={item.metadata['mesh_id']}")
                if item.metadata.get("efo_id"):
                    identifiers.append(f"efo_id={item.metadata['efo_id']}")
                phase_text = f"; max_phase_for_ind={phase}" if phase not in (None, "") else ""
                id_text = f"; {'; '.join(identifiers)}" if identifiers else ""
                lines.append(
                    f"- Indication: {indication}{phase_text}{id_text}; "
                    f"record_id={item.source_record_id or 'unavailable'}"
                )
            else:
                warning_type = item.metadata.get("warning_type") or item.summary
                country = item.metadata.get("country")
                year = item.metadata.get("year")
                warning_class = item.metadata.get("warning_class")
                details = [
                    f"record_id={item.source_record_id or 'unavailable'}",
                    f"type={warning_type}",
                ]
                if warning_class:
                    details.append(f"class={warning_class}")
                if country:
                    details.append(f"country={country}")
                if year:
                    details.append(f"year={year}")
                lines.append(f"- Warning: {'; '.join(details)}")
        return lines

    def _activity_summary_lines(self, evidence: list[EvidenceItem]) -> list[str]:
        activities = [
            item
            for item in evidence
            if normalize_evidence_item(item).evidence_type == "molecule_target_activity"
        ]
        if not activities:
            return ["- None retrieved from ChEMBL."]
        lines: list[str] = []
        for item in activities:
            standard_type = item.metadata.get("standard_type") or "activity"
            standard_value = item.metadata.get("standard_value")
            standard_units = item.metadata.get("standard_units")
            pchembl = item.metadata.get("pchembl_value")
            value_text = ""
            if standard_value not in (None, ""):
                value_text = f"={standard_value}"
                if standard_units:
                    value_text = f"{value_text} {standard_units}"
            pchembl_text = f"; pChEMBL={pchembl}" if pchembl not in (None, "") else ""
            lines.append(
                f"- {standard_type}{value_text}{pchembl_text}; "
                f"record_id={item.source_record_id or 'unavailable'}"
            )
        return lines

    def _indication_summary_lines(self, evidence: list[EvidenceItem]) -> list[str]:
        indications = [item for item in evidence if is_clinical_evidence(item)]
        if not indications:
            return ["- None retrieved from ChEMBL."]
        lines: list[str] = []
        for item in indications:
            indication = item.metadata.get("indication") or item.summary
            phase = item.metadata.get("max_phase_for_ind")
            phase_text = f"; max_phase_for_ind={phase}" if phase not in (None, "") else ""
            lines.append(
                f"- {indication}{phase_text}; "
                f"record_id={item.source_record_id or 'unavailable'}"
            )
        return lines

    def _safety_warning_summary_lines(self, evidence: list[EvidenceItem]) -> list[str]:
        warnings = [item for item in evidence if is_safety_warning(item)]
        if not warnings:
            return ["- None retrieved from ChEMBL."]
        lines: list[str] = []
        for item in warnings:
            warning_type = item.metadata.get("warning_type") or item.summary
            warning_class = item.metadata.get("warning_class")
            class_text = f"; class={warning_class}" if warning_class else ""
            lines.append(
                f"- {warning_type}{class_text}; "
                f"record_id={item.source_record_id or 'unavailable'}"
            )
        return lines

    def _chemical_identifier_lines(self, candidate: MoleculeCandidate) -> list[str]:
        identifiers = {
            **candidate.identifiers,
            **{
                key: value
                for key, value in candidate.chemical_metadata.items()
                if key in {"inchikey", "inchi", "canonical_smiles", "isomeric_smiles", "cid"}
            },
        }
        if not identifiers:
            return ["- None recorded."]
        return [f"- {key}: {value}" for key, value in sorted(identifiers.items())]

    def _deduplication_lines(self, candidate: MoleculeCandidate) -> list[str]:
        warnings = [
            warning
            for warning in candidate.warnings
            if "dedup" in warning.lower() or "duplicate" in warning.lower()
        ]
        if not warnings:
            return ["- No candidate-level deduplication warnings recorded."]
        return [f"- {warning}" for warning in warnings]

    def _provenance_lines(self, evidence: list[EvidenceItem]) -> list[str]:
        if not evidence:
            return ["- None recorded."]
        lines: list[str] = []
        for item in evidence:
            details = [
                f"source={item.source}",
                f"record_id={item.source_record_id or 'unavailable'}",
                f"retrieved={item.retrieval_timestamp.isoformat()}",
            ]
            if item.url:
                details.append(f"url={item.url}")
            query = item.metadata.get("query")
            if query:
                details.append(f"query={query}")
            response_provenance = item.metadata.get("response_provenance")
            if isinstance(response_provenance, dict):
                cache_mode = response_provenance.get("mode")
                cache_key = response_provenance.get("cache_key")
                if cache_mode:
                    details.append(f"response_mode={cache_mode}")
                if cache_key:
                    details.append(f"cache_key={cache_key}")
            lines.append(f"- {' | '.join(details)}")
        return lines

    def _source_endpoint(self, evidence: list[EvidenceItem], source: str) -> str:
        for item in evidence:
            if item.source != source:
                continue
            response_provenance = item.metadata.get("response_provenance")
            if isinstance(response_provenance, dict):
                endpoint = response_provenance.get("endpoint")
                if endpoint:
                    return str(endpoint)
            if item.url:
                return item.url
        return "unavailable"

    def _literature_source_endpoint(self, context: PipelineContext, source: str) -> str:
        config = self._literature_config(context)
        bundles = config.get("bundles", [])
        if isinstance(bundles, list):
            for bundle in bundles:
                if not isinstance(bundle, dict):
                    continue
                for paper in bundle.get("papers", []):
                    if not isinstance(paper, dict) or paper.get("source") != source:
                        continue
                    metadata = paper.get("metadata")
                    if isinstance(metadata, dict):
                        response_provenance = metadata.get("response_provenance")
                        if isinstance(response_provenance, dict):
                            endpoint = response_provenance.get("endpoint")
                            if endpoint:
                                return str(endpoint)
                        openalex = metadata.get("openalex_response_provenance")
                        if source == "OpenAlex" and isinstance(openalex, dict):
                            endpoint = openalex.get("endpoint")
                            if endpoint:
                                return str(endpoint)
        for candidate in context.candidates:
            bundle = candidate.literature_evidence
            if bundle is None:
                continue
            for item in bundle.items:
                paper = item.paper
                response_provenance = paper.metadata.get("response_provenance")
                if paper.source == source and isinstance(response_provenance, dict):
                    endpoint = response_provenance.get("endpoint")
                    if endpoint:
                        return str(endpoint)
                if paper.source == source and paper.url:
                    return paper.url
                openalex = paper.metadata.get("openalex_response_provenance")
                if source == "OpenAlex" and isinstance(openalex, dict):
                    endpoint = openalex.get("endpoint")
                    if endpoint:
                        return str(endpoint)
        sources = set(self._literature_sources(context))
        if source == "PubMed" and "PubMed" in sources:
            return "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        if source == "OpenAlex" and "OpenAlex" in sources:
            return "https://api.openalex.org/works"
        return "unavailable"

    def _cache_usage_text(self, config: dict[str, Any]) -> str:
        use_cache = bool(config.get("use_cache"))
        allow_cached = bool(config.get("allow_cached_real_data"))
        if use_cache and allow_cached:
            return "enabled; cached-real-data fallback allowed"
        if use_cache:
            return "enabled; live-first cache writes only"
        return "disabled"

    def _trace_by_name(self, context: PipelineContext, name: str) -> AgentTrace | None:
        for trace in context.traces:
            if trace.agent_name == name:
                return trace
        return None

    def _target_mapping(
        self,
        target: Target,
        candidates: Iterable[MoleculeCandidate] = (),
    ) -> dict[str, Any]:
        mapping = target.metadata.get("chembl_target_mapping")
        if isinstance(mapping, dict):
            return mapping
        for item in target.evidence:
            metadata = item.metadata
            if metadata.get("chembl_target_id") or metadata.get("target_chembl_id"):
                return {
                    "chembl_target_id": metadata.get("chembl_target_id")
                    or metadata.get("target_chembl_id"),
                    "mapping_method": metadata.get("mapping_method"),
                    "confidence": metadata.get("mapping_confidence")
                    or metadata.get("target_mapping_confidence"),
                }
        for candidate in candidates:
            if target.symbol not in candidate.known_targets:
                continue
            for item in candidate.evidence:
                metadata = item.metadata
                chembl_target_id = metadata.get("chembl_target_id") or metadata.get(
                    "target_chembl_id"
                )
                if chembl_target_id:
                    return {
                        "chembl_target_id": chembl_target_id,
                        "mapping_method": metadata.get("mapping_method"),
                        "confidence": metadata.get("mapping_confidence")
                        or metadata.get("target_mapping_confidence"),
                    }
        return {}

    def _target_open_targets_id(self, target: Target) -> str:
        return (
            target.identifiers.get("open_targets")
            or target.identifiers.get("ensembl")
            or "unavailable"
        )

    def _format_optional_float(self, value: Any) -> str:
        if value in (None, ""):
            return "unavailable"
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return str(value)

    def _coverage_counts(self, evidence: list[EvidenceItem]) -> dict[str, int]:
        counts = {
            "disease_target": 0,
            "mechanism": 0,
            "activity": 0,
            "indication": 0,
            "safety_warning": 0,
            "chemical_annotation": 0,
        }
        for item in evidence:
            normalized = normalize_evidence_item(item).evidence_type
            if normalized == "disease_target_association":
                counts["disease_target"] += 1
            elif normalized == "molecule_target_mechanism":
                counts["mechanism"] += 1
            elif normalized == "molecule_target_activity":
                counts["activity"] += 1
            elif normalized == "molecule_indication":
                counts["indication"] += 1
            elif normalized == "molecule_safety_warning":
                counts["safety_warning"] += 1
            elif normalized == "chemical_annotation":
                counts["chemical_annotation"] += 1
        return counts

    def _source_limitations(self, context: PipelineContext) -> list[str]:
        limitations = list(context.config.get("warnings", []))
        for trace in context.traces:
            limitations.extend(trace.warnings)
        return limitations or ["No API/source limitations were recorded by the pipeline."]

    def _identifier_lines(self, identifiers: dict[str, str]) -> list[str]:
        if not identifiers:
            return ["  - None recorded."]
        return [f"  - {key}: {value}" for key, value in sorted(identifiers.items())]

    def _retrieval_timestamp_summary(self, evidence: list[EvidenceItem]) -> str:
        if not evidence:
            return "None recorded"
        by_source: dict[str, set[str]] = {}
        for item in evidence:
            by_source.setdefault(item.source, set()).add(item.retrieval_timestamp.isoformat())
        parts = [
            f"{source}: {', '.join(sorted(timestamps))}"
            for source, timestamps in sorted(by_source.items())
        ]
        return "; ".join(parts)

    def _literature_sources(self, context: PipelineContext) -> list[str]:
        sources: set[str] = set()
        sources.update(
            str(source)
            for source in self._literature_config(context).get("sources_used", [])
        )
        for candidate in context.candidates:
            bundle = candidate.literature_evidence
            if bundle is None:
                continue
            for item in bundle.items:
                sources.add(item.paper.source)
                if item.paper.metadata.get("openalex_id"):
                    sources.add("OpenAlex")
            for item in self._candidate_literature_items(candidate):
                sources.add(item.source)
                if item.metadata.get("openalex_id"):
                    sources.add("OpenAlex")
        return sorted(sources)

    def _literature_config(self, context: PipelineContext) -> dict[str, Any]:
        value = context.config.get("literature_evidence", {})
        return dict(value) if isinstance(value, dict) else {}

    def _candidate_literature_items(self, candidate: MoleculeCandidate) -> list[EvidenceItem]:
        return [
            item
            for item in candidate.evidence
            if item.evidence_type.startswith("literature_")
        ]

    def _all_literature_items(self, context: PipelineContext) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for candidate in context.candidates:
            items.extend(self._candidate_literature_items(candidate))
        for target in context.targets:
            items.extend(
                item
                for item in target.evidence
                if item.evidence_type.startswith("literature_")
            )
        return items

    def _literature_summary_payload(self, context: PipelineContext) -> dict[str, Any]:
        config = self._literature_config(context)
        items = self._all_literature_items(context)
        warnings = list(config.get("warnings", []))
        sources = set(str(source) for source in config.get("sources_used", []))
        sources.update(item.source for item in items)
        if any(item.metadata.get("openalex_id") for item in items):
            sources.add("OpenAlex")
        return {
            "sources_used": sorted(source for source in sources if source),
            "queries_generated": int(config.get("queries_generated", 0) or 0),
            "queries_executed": int(config.get("queries_executed", 0) or 0),
            "papers_retrieved": int(config.get("papers_retrieved", 0) or 0),
            "unique_papers_retained": int(config.get("unique_papers_retained", 0) or 0),
            "claims_extracted": int(config.get("claims_extracted", 0) or 0),
            "evidence_items_attached": len(items),
            "strict_literature": bool(config.get("strict_literature", False)),
            "warnings": warnings,
        }

    def _literature_queries_payload(self, context: PipelineContext) -> list[dict[str, Any]]:
        config = self._literature_config(context)
        bundles = config.get("bundles", [])
        if not isinstance(bundles, list):
            return []
        queries: list[dict[str, Any]] = []
        for bundle in bundles:
            if not isinstance(bundle, dict):
                continue
            query = bundle.get("query", {})
            if not isinstance(query, dict):
                continue
            papers = bundle.get("papers", [])
            claims = bundle.get("claims", [])
            paper_sources = {
                str(paper.get("source"))
                for paper in papers
                if isinstance(paper, dict) and paper.get("source")
            }
            queries.append(
                {
                    "query_id": query.get("query_id"),
                    "query_type": query.get("query_type"),
                    "query_text": query.get("query_text"),
                    "source": ", ".join(sorted(paper_sources)) or "PubMed",
                    "papers_returned": len(papers) if isinstance(papers, list) else 0,
                    "claims_extracted": len(claims) if isinstance(claims, list) else 0,
                }
            )
        return queries

    def _literature_papers_payload(self, context: PipelineContext) -> list[dict[str, Any]]:
        papers_by_key: dict[str, dict[str, Any]] = {}
        for item in self._all_literature_items(context):
            paper = self._paper_payload_from_evidence(item)
            papers_by_key.setdefault(self._paper_payload_key(paper), paper)
        for paper in self._papers_from_config(context):
            papers_by_key.setdefault(self._paper_payload_key(paper), paper)
        return list(papers_by_key.values())

    def _extracted_claims_payload(self, context: PipelineContext) -> list[dict[str, Any]]:
        claims: list[dict[str, Any]] = []
        for item in self._all_literature_items(context):
            claims.append(
                {
                    "paper_id": item.metadata.get("paper_id"),
                    "candidate_name": item.metadata.get("candidate_name"),
                    "target_symbol": item.metadata.get("target_symbol"),
                    "disease_name": item.metadata.get("disease_name"),
                    "claim_type": item.metadata.get("claim_type"),
                    "direction": item.metadata.get("direction"),
                    "confidence": item.confidence,
                    "supporting_snippet": self._short_text(
                        str(item.metadata.get("supporting_snippet") or "")
                    ),
                    "query_id": item.metadata.get("query_id"),
                    "query_text": item.metadata.get("query_text"),
                    "study_type": item.metadata.get("study_type"),
                    "evidence_level": item.metadata.get("evidence_level"),
                    "citation": item.metadata.get("citation"),
                }
            )
        config = self._literature_config(context)
        bundles = config.get("bundles", [])
        if isinstance(bundles, list):
            for bundle in bundles:
                if not isinstance(bundle, dict):
                    continue
                for claim in bundle.get("claims", []):
                    if not isinstance(claim, dict):
                        continue
                    claims.append(
                        {
                            "claim_id": claim.get("claim_id"),
                            "paper_id": claim.get("paper_id"),
                            "candidate_name": claim.get("candidate_name"),
                            "target_symbol": claim.get("target_symbol"),
                            "disease_name": claim.get("disease_name"),
                            "claim_type": claim.get("claim_type"),
                            "direction": claim.get("direction"),
                            "confidence": claim.get("confidence"),
                            "supporting_snippet": self._short_text(
                                str(claim.get("supporting_snippet") or "")
                            ),
                            "query_id": (claim.get("metadata") or {}).get("query_id")
                            if isinstance(claim.get("metadata"), dict)
                            else None,
                            "study_type": (claim.get("metadata") or {}).get("study_type")
                            if isinstance(claim.get("metadata"), dict)
                            else None,
                        }
                    )
        return claims

    def _papers_from_config(self, context: PipelineContext) -> list[dict[str, Any]]:
        config = self._literature_config(context)
        bundles = config.get("bundles", [])
        papers: list[dict[str, Any]] = []
        if not isinstance(bundles, list):
            return papers
        for bundle in bundles:
            if not isinstance(bundle, dict):
                continue
            for paper in bundle.get("papers", []):
                if isinstance(paper, dict):
                    papers.append(self._paper_payload_from_mapping(paper))
        return papers

    def _candidate_literature_papers_from_config(
        self, context: PipelineContext, candidate: MoleculeCandidate
    ) -> list[dict[str, Any]]:
        config = self._literature_config(context)
        bundles = config.get("bundles", [])
        papers: list[dict[str, Any]] = []
        if not isinstance(bundles, list):
            return papers
        candidate_name = candidate.name.lower()
        for bundle in bundles:
            if not isinstance(bundle, dict):
                continue
            query = bundle.get("query", {})
            if not isinstance(query, dict):
                continue
            molecule_name = str(query.get("molecule_name") or "").lower()
            query_text = str(query.get("query_text") or "").lower()
            if molecule_name != candidate_name and candidate_name not in query_text:
                continue
            for paper in bundle.get("papers", []):
                if isinstance(paper, dict):
                    papers.append(self._paper_payload_from_mapping(paper))
        return papers

    def _paper_payload_from_evidence(self, item: EvidenceItem) -> dict[str, Any]:
        citation = item.metadata.get("citation")
        citation_payload = citation if isinstance(citation, dict) else {}
        return {
            "paper_id": item.metadata.get("paper_id"),
            "source": item.source,
            "title": item.title,
            "pmid": item.metadata.get("pmid"),
            "doi": item.metadata.get("doi"),
            "pmcid": item.metadata.get("pmcid"),
            "openalex_id": item.metadata.get("openalex_id"),
            "publication_type": item.metadata.get("publication_type"),
            "is_retracted": item.metadata.get("is_retracted"),
            "cited_by_count": item.metadata.get("cited_by_count"),
            "url": item.url,
            "retrieved_at": item.retrieval_timestamp.isoformat(),
            "citation": self._citation_payload(citation_payload, item),
        }

    def _paper_payload_from_mapping(self, paper: dict[str, Any]) -> dict[str, Any]:
        item_like = EvidenceItem(
            source=str(paper.get("source") or "PubMed"),
            source_record_id=str(
                paper.get("pmid")
                or paper.get("doi")
                or paper.get("openalex_id")
                or paper.get("paper_id")
            ),
            title=str(paper.get("title") or "Untitled literature record"),
            url=paper.get("url"),
            evidence_type="literature_mention",
            summary="Literature paper metadata.",
            confidence=0.0,
            metadata={
                "pmid": paper.get("pmid"),
                "doi": paper.get("doi"),
                "pmcid": paper.get("pmcid"),
                "openalex_id": paper.get("openalex_id"),
            },
        )
        return {
            "paper_id": paper.get("paper_id"),
            "source": paper.get("source"),
            "title": paper.get("title"),
            "pmid": paper.get("pmid"),
            "doi": paper.get("doi"),
            "pmcid": paper.get("pmcid"),
            "openalex_id": paper.get("openalex_id"),
            "publication_type": paper.get("publication_type"),
            "is_retracted": paper.get("is_retracted"),
            "cited_by_count": paper.get("cited_by_count"),
            "url": paper.get("url"),
            "retrieved_at": paper.get("retrieved_at"),
            "citation": self._citation_payload({}, item_like),
        }

    def _citation_payload(
        self,
        citation: dict[str, Any],
        item: EvidenceItem,
    ) -> dict[str, Any]:
        title = str(citation.get("title") or item.title)
        pmid = citation.get("pmid") or item.metadata.get("pmid")
        doi = citation.get("doi") or item.metadata.get("doi")
        year = citation.get("year")
        identifiers = []
        if pmid:
            identifiers.append(f"PMID:{pmid}")
        if doi:
            identifiers.append(f"doi:{doi}")
        id_text = f" {'; '.join(identifiers)}" if identifiers else ""
        year_text = f" ({year})" if year else ""
        return {
            "title": title,
            "authors": citation.get("authors", []),
            "journal": citation.get("journal"),
            "publication_date": citation.get("publication_date"),
            "year": year,
            "doi": doi,
            "pmid": pmid,
            "pmcid": citation.get("pmcid") or item.metadata.get("pmcid"),
            "openalex_id": citation.get("openalex_id") or item.metadata.get("openalex_id"),
            "url": citation.get("url") or item.url,
            "citation_text": citation.get("citation_text")
            or f"{title}.{year_text}{id_text}".strip(),
        }

    def _paper_payload_key(self, paper: dict[str, Any]) -> str:
        return str(
            paper.get("pmid")
            or paper.get("doi")
            or paper.get("openalex_id")
            or paper.get("paper_id")
            or paper.get("title")
        )

    def _literature_claim_counts(self, items: list[EvidenceItem]) -> dict[str, int]:
        counts = {
            "supportive": 0,
            "clinical": 0,
            "safety": 0,
            "contradictory": 0,
            "mention_only": 0,
        }
        for item in items:
            claim_type = str(item.metadata.get("claim_type") or "")
            direction = str(item.metadata.get("direction") or "")
            if item.evidence_type == "literature_clinical" or claim_type == "clinical_support":
                counts["clinical"] += 1
            if item.evidence_type == "literature_safety" or direction == "safety_concern":
                counts["safety"] += 1
            if item.evidence_type == "literature_contradictory" or direction == "contradictory":
                counts["contradictory"] += 1
            if item.evidence_type == "literature_mention" or claim_type == "mention_only":
                counts["mention_only"] += 1
            if (
                direction == "supportive"
                and item.evidence_type
                not in {"literature_safety", "literature_contradictory"}
            ):
                counts["supportive"] += 1
        return counts

    def _paper_keys(self, items: list[EvidenceItem]) -> set[str]:
        return {
            str(
                item.metadata.get("paper_id")
                or item.metadata.get("pmid")
                or item.source_record_id
            )
            for item in items
        }

    def _top_citations(self, items: list[EvidenceItem], *, limit: int) -> list[str]:
        citations: list[str] = []
        seen: set[str] = set()
        for item in items:
            citation = item.metadata.get("citation")
            if not isinstance(citation, dict):
                continue
            text = str(citation.get("citation_text") or citation.get("title") or "")
            key = text or str(item.source_record_id)
            if not text or key in seen:
                continue
            seen.add(key)
            citations.append(text)
            if len(citations) >= limit:
                break
        return citations

    def _top_paper_citations(self, papers: list[dict[str, Any]], *, limit: int) -> list[str]:
        citations: list[str] = []
        seen: set[str] = set()
        for paper in papers:
            citation = paper.get("citation")
            if not isinstance(citation, dict):
                continue
            text = str(citation.get("citation_text") or citation.get("title") or "")
            key = text or self._paper_payload_key(paper)
            if not text or key in seen:
                continue
            seen.add(key)
            citations.append(text)
            if len(citations) >= limit:
                break
        return citations

    def _snippet_lines(
        self,
        items: list[EvidenceItem],
        *,
        limit: int,
        contradictory_label: bool = False,
    ) -> list[str]:
        if not items:
            return ["- None recorded."]
        lines: list[str] = []
        for item in items[:limit]:
            snippet = self._short_text(str(item.metadata.get("supporting_snippet") or ""))
            if not snippet:
                continue
            label = "Contradictory evidence" if contradictory_label else "Snippet"
            citation_id = self._citation_id(item)
            lines.append(f"- {label} [{citation_id}]: {snippet}")
        return lines or ["- None recorded."]

    def _citation_id_lines(self, items: list[EvidenceItem]) -> list[str]:
        ids = sorted({self._citation_id(item) for item in items if self._citation_id(item)})
        return [f"- {citation_id}" for citation_id in ids] or ["- None recorded."]

    def _citation_id(self, item: EvidenceItem) -> str:
        if item.metadata.get("pmid"):
            return f"PMID:{item.metadata['pmid']}"
        if item.metadata.get("doi"):
            return f"doi:{item.metadata['doi']}"
        if item.metadata.get("openalex_id"):
            return f"OpenAlex:{item.metadata['openalex_id']}"
        return str(item.source_record_id or "")

    def _citation_identifier_text(self, paper: dict[str, Any]) -> str:
        identifiers = []
        if paper.get("pmid"):
            identifiers.append(f"PMID:{paper['pmid']}")
        if paper.get("doi"):
            identifiers.append(f"doi:{paper['doi']}")
        if paper.get("pmcid"):
            identifiers.append(f"PMCID:{paper['pmcid']}")
        if paper.get("openalex_id"):
            identifiers.append(f"OpenAlex:{paper['openalex_id']}")
        return "; ".join(identifiers)

    def _short_text(self, value: str, limit: int = 500) -> str:
        return " ".join(value.split())[:limit]

    def _all_evidence(
        self, targets: Iterable[Target], candidates: Iterable[MoleculeCandidate]
    ) -> Iterable[EvidenceItem]:
        for target in targets:
            yield from target.evidence
        for candidate in candidates:
            yield from candidate.evidence

    def _artifact_paths(self, output_dir: Path | None) -> dict[str, str]:
        if output_dir is None:
            return {}
        return {
            "candidates_json": str(output_dir / "candidates.json"),
            "generated_candidates_json": str(output_dir / "generated_candidates.json"),
            "generated_molecules_json": str(output_dir / "generated_molecules.json"),
            "generation_trace_json": str(output_dir / "generation_trace.json"),
            "report_md": str(output_dir / "report.md"),
            "trace_json": str(output_dir / "trace.json"),
        }


def _json_dumps(payload: dict[str, Any]) -> str:
    def default(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    return json.dumps(payload, default=default, indent=2, sort_keys=True) + "\n"
