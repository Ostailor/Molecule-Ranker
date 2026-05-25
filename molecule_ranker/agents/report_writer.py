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
from molecule_ranker.schemas import AgentTrace, EvidenceItem, MoleculeCandidate, Target
from molecule_ranker.utils import slugify

DEFAULT_LIMITATIONS = [
    "Public databases may be incomplete or stale.",
    "Scores are heuristic prioritization aids.",
    "No wet-lab validation has been performed by this software.",
    "No patient-specific recommendation is provided.",
    "Novel molecule generation is not implemented in V0.0.",
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
        lines.extend(["", "Source provenance:"])
        lines.extend(self._provenance_lines(candidate.evidence))
        lines.extend(["", "Warnings:"])
        if candidate.warnings:
            lines.extend(f"- {warning}" for warning in candidate.warnings)
        else:
            lines.append("- None recorded.")
        return lines

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
        return [
            (
                f"- [{item.source}] {item.title} "
                f"({item.evidence_type}, confidence {item.confidence:.3f}): {item.summary}"
            )
            for item in evidence
        ]

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
            lines.append(f"- {' | '.join(details)}")
        return lines

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
