from __future__ import annotations

from molecule_ranker.experimental.schemas import ExperimentSummaryReport


def render_experiment_summary_markdown(summary: ExperimentSummaryReport) -> str:
    lines = [
        "# Experimental Assay Result Summary",
        "",
        (
            "Imported experimental evidence is kept separate from literature evidence "
            "and expert review."
        ),
        (
            "An assay result does not establish clinical efficacy, patient benefit, "
            "disease treatment, or cure."
        ),
        "Expert review decisions are tracked separately from experimental evidence.",
        "",
        "## Counts",
        "",
        f"- Results: {summary.result_count}",
        f"- Valid: {summary.valid_count}",
        f"- Incomplete: {summary.incomplete_count}",
        f"- Invalid: {summary.invalid_count}",
        f"- Experiments: {summary.experiment_count}",
        f"- Assays: {summary.assay_count}",
        f"- Linked candidates: {summary.linked_candidate_count}",
        f"- Linked review items: {summary.review_link_count}",
        "",
        "## Outcomes",
        "",
    ]
    if summary.outcome_counts:
        lines.extend(f"- {outcome}: {count}" for outcome, count in summary.outcome_counts.items())
    else:
        lines.append("- No outcomes recorded.")
    lines.extend(["", "## Experiments", ""])
    if summary.experiments:
        for experiment in summary.experiments:
            outcomes = ", ".join(
                f"{key}={value}" for key, value in experiment.get("outcome_counts", {}).items()
            )
            lines.append(
                f"- {experiment['experiment_id']}: {experiment['result_count']} result(s)"
                + (f" ({outcomes})" if outcomes else "")
            )
    else:
        lines.append("- No experiments recorded.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in summary.limitations)
    return "\n".join(lines).rstrip() + "\n"
