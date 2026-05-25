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
from molecule_ranker.schemas import AgentTrace, EvidenceItem, MoleculeCandidate, Target
from molecule_ranker.utils import slugify

DEFAULT_LIMITATIONS = [
    "Public databases may be incomplete or stale.",
    "Scores are heuristic prioritization aids.",
    "No wet-lab validation has been performed by this software.",
    "No patient-specific recommendation is provided.",
    "Novel molecule generation is not implemented in V0.1.",
    "Record-level evidence provenance is reported for retrieved public-source records.",
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
        sources = sorted({item.source for item in evidence})
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
            "## Summary",
            "",
            f"- Disease input: {disease.input_name}",
            f"- Canonical disease: {disease.canonical_name}",
            f"- Number of targets: {len(context.targets)}",
            f"- Number of molecule candidates: {len(context.candidates)}",
            "- Top 5 candidates:",
            *[
                f"  - {candidate.name} ({candidate.score:.3f})"
                for candidate in top_candidates
                if candidate.score is not None
            ],
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
                    "summary": {
                        "target_count": len(context.targets),
                        "candidate_count": len(context.candidates),
                        "evidence_item_count": len(
                            list(self._all_evidence(context.targets, context.candidates))
                        ),
                    },
                    "limitations": limitations,
                }
            )
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

        no_evidence = [candidate.name for candidate in context.candidates if not candidate.evidence]
        if no_evidence:
            raise NoCandidatesFoundError(
                "Report requires evidence-backed candidates; missing evidence for "
                f"{', '.join(no_evidence)}."
            )

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
                    f"| Final score | {score.final_score:.3f} |",
                    f"| Confidence | {score.confidence:.3f} |",
                    "",
                    f"Score explanation: {score.explanation}",
                ]
            )

        lines.extend(["", "Evidence summary:"])
        lines.extend(self._evidence_summary_lines(candidate.evidence))
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
            f"- Cache usage: {self._cache_usage_text(config)}",
            f"- Retrieval timestamps: {self._retrieval_timestamp_summary(evidence)}",
            "- Source versions/status: unavailable",
        ]

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
