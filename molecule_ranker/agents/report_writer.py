from __future__ import annotations

from molecule_ranker.agents.base import BaseAgent, PipelineContext
from molecule_ranker.schemas import Disease, MoleculeCandidate, Target


class ReportWriterAgent(BaseAgent):
    name = "ReportWriterAgent"

    def process(self, context: PipelineContext) -> PipelineContext:
        if context.disease is None:
            context.config.setdefault("warnings", []).append("Report skipped: no disease.")
            context.config["report_md"] = ""
            return context
        context.config["report_md"] = self.render(
            context.disease, context.targets, context.candidates
        )
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        return f"Wrote transparent evidence report for {len(context.candidates)} candidates."

    def trace_metadata(self, context: PipelineContext) -> dict[str, object]:
        return {"report_chars": len(str(context.config.get("report_md", "")))}

    def render(
        self,
        disease: Disease,
        targets: list[Target],
        candidates: list[MoleculeCandidate],
    ) -> str:
        lines = [
            f"# Molecule ranking report: {disease.canonical_name}",
            "",
            "This V0.0 research prototype ranks existing molecules as candidates for "
            "therapeutic relevance hypotheses. It is not medical advice, does not provide "
            "patient treatment instructions, and every candidate requires experimental "
            "validation.",
            "",
            "## Target hypotheses",
        ]
        for target in targets:
            mechanism = target.mechanism or "No mechanism summary available."
            lines.append(f"- **{target.symbol}** ({target.name}): {mechanism}")
        lines.extend(["", "## Ranked existing-molecule candidates"])
        for index, candidate in enumerate(candidates, start=1):
            score = candidate.score_breakdown
            total = candidate.score or 0.0
            explanation = score.explanation if score else "No score breakdown available."
            lines.extend(
                [
                    "",
                    f"### {index}. {candidate.name}",
                    "",
                    "Existing-molecule candidate hypothesis with transparent supporting "
                    "evidence. Requires experimental validation.",
                    "",
                    f"- Molecule type: {candidate.molecule_type}",
                    f"- Mechanism hypothesis: {candidate.mechanism_of_action}",
                    f"- Final score: {total:.3f}",
                    f"- Score explanation: {explanation}",
                    "- Evidence:",
                ]
            )
            for item in candidate.evidence:
                lines.append(f"  - {item.title}: {item.summary}")
            if candidate.warnings:
                lines.append("- Warnings:")
                for warning in candidate.warnings:
                    lines.append(f"  - {warning}")
        lines.extend(
            [
                "",
                "## Limitations",
                "",
                "- Results depend on public biomedical sources available at retrieval time.",
                "- Scores are transparent prioritization aids, not black-box predictions.",
                "- Candidate relevance is a research hypothesis and requires experimental "
                "validation.",
            ]
        )
        return "\n".join(lines) + "\n"
