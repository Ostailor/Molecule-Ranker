from __future__ import annotations

from typing import Any

from molecule_ranker.project.schemas import MultiRunComparison, ProjectRun
from molecule_ranker.utils import slugify

COMPARISON_LIMITATIONS = [
    "Run comparison is an artifact inspection aid, not a biomedical conclusion.",
    "Score differences are reported from existing artifacts only; Codex does not alter scores.",
    "Candidate overlap does not imply activity, binding, safety, efficacy, or synthesizability.",
]


def compare_project_runs(runs: list[ProjectRun]) -> MultiRunComparison:
    if len(runs) < 2:
        raise ValueError("At least two project runs are required for comparison.")
    candidate_sets = [set(_candidate_names(run)) for run in runs]
    target_sets = [set(_target_symbols(run)) for run in runs]
    overlap = sorted(set.intersection(*candidate_sets)) if candidate_sets else []
    target_overlap = sorted(set.intersection(*target_sets)) if target_sets else []
    deltas = _score_deltas(runs, overlap)
    generated_counts = {run.run_id: run.generated_candidate_count for run in runs}
    differentiators = _differentiators(runs, overlap, target_overlap, deltas)
    return MultiRunComparison(
        comparison_id=slugify("comparison-" + "-".join(run.run_id for run in runs)),
        run_ids=[run.run_id for run in runs],
        disease_names=[run.disease_name for run in runs],
        candidate_overlap=overlap,
        target_overlap=target_overlap,
        score_deltas=deltas,
        generated_candidate_counts=generated_counts,
        differentiators=differentiators,
        limitations=list(COMPARISON_LIMITATIONS),
        metadata={
            "run_dirs": {run.run_id: run.run_dir for run in runs},
            "candidate_counts": {run.run_id: run.candidate_count for run in runs},
            "target_counts": {run.run_id: run.target_count for run in runs},
        },
    )


def render_run_comparison_markdown(comparison: MultiRunComparison) -> str:
    lines = [
        "# Multi-Run Comparison",
        "",
        "Artifact-grounded comparison for review and planning.",
        "",
        f"- Runs: {', '.join(comparison.run_ids)}",
        f"- Diseases: {', '.join(comparison.disease_names)}",
        f"- Shared candidates: {', '.join(comparison.candidate_overlap) or 'none'}",
        f"- Shared targets: {', '.join(comparison.target_overlap) or 'none'}",
        "",
        "## Score Deltas",
        "",
    ]
    if comparison.score_deltas:
        lines.append("| Candidate | Min score | Max score | Delta | Runs |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in comparison.score_deltas:
            lines.append(
                f"| {row['candidate_name']} | {row['min_score']} | {row['max_score']} | "
                f"{row['delta']} | {', '.join(row['runs'])} |"
            )
    else:
        lines.append("No overlapping scored candidates were available.")
    lines.extend(["", "## Differentiators", ""])
    lines.extend(f"- {item}" for item in comparison.differentiators)
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in comparison.limitations)
    return "\n".join(lines).rstrip() + "\n"


def _candidate_names(run: ProjectRun) -> list[str]:
    return [
        str(row["name"])
        for row in run.top_candidates
        if row.get("name") is not None
    ]


def _target_symbols(run: ProjectRun) -> list[str]:
    symbols: set[str] = set()
    for row in run.top_candidates:
        targets = row.get("known_targets", [])
        if isinstance(targets, list):
            symbols.update(str(target) for target in targets if target)
    return sorted(symbols)


def _score_deltas(runs: list[ProjectRun], overlap: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in overlap:
        scored: list[tuple[str, float]] = []
        for run in runs:
            for candidate in run.top_candidates:
                if candidate.get("name") == name and candidate.get("score") is not None:
                    scored.append((run.run_id, float(candidate["score"])))
        if len(scored) < 2:
            continue
        scores = [score for _run_id, score in scored]
        rows.append(
            {
                "candidate_name": name,
                "min_score": min(scores),
                "max_score": max(scores),
                "delta": round(max(scores) - min(scores), 6),
                "runs": [run_id for run_id, _score in scored],
            }
        )
    return sorted(rows, key=lambda row: row["delta"], reverse=True)


def _differentiators(
    runs: list[ProjectRun],
    overlap: list[str],
    target_overlap: list[str],
    deltas: list[dict[str, Any]],
) -> list[str]:
    messages: list[str] = []
    candidate_counts = {run.run_id: run.candidate_count for run in runs}
    if len(set(candidate_counts.values())) > 1:
        messages.append(f"Candidate counts differ across runs: {candidate_counts}.")
    generated_counts = {run.run_id: run.generated_candidate_count for run in runs}
    if len(set(generated_counts.values())) > 1:
        messages.append(f"Generated hypothesis counts differ across runs: {generated_counts}.")
    if overlap:
        messages.append(f"{len(overlap)} top candidates appear in every compared run.")
    if target_overlap:
        messages.append(f"{len(target_overlap)} known targets overlap across top candidates.")
    if deltas:
        top = deltas[0]
        messages.append(
            f"{top['candidate_name']} has the largest artifact score delta ({top['delta']})."
        )
    return messages or ["No major run differentiators were identified from registered artifacts."]
