from __future__ import annotations

from typing import Any

from molecule_ranker.utils import slugify
from molecule_ranker.workspace.schemas import ProjectComparison, ProjectRun

COMPARISON_LIMITATIONS = [
    "Project comparison is an artifact inspection aid, not a biomedical conclusion.",
    "Score differences are read from registered artifacts only; Codex does not alter scores.",
    "Candidate overlap does not imply activity, binding, safety, efficacy, or synthesizability.",
]


def compare_project_runs(runs: list[ProjectRun]) -> ProjectComparison:
    if len(runs) < 2:
        raise ValueError("At least two project runs are required for comparison.")
    workspace_ids = {run.workspace_id for run in runs}
    if len(workspace_ids) != 1:
        raise ValueError("All runs must belong to the same workspace.")
    candidate_sets = [set(_candidate_names(run)) for run in runs]
    target_sets = [set(_target_symbols(run)) for run in runs]
    candidate_overlap = sorted(set.intersection(*candidate_sets)) if candidate_sets else []
    target_overlap = sorted(set.intersection(*target_sets)) if target_sets else []
    deltas = _score_deltas(runs, candidate_overlap)
    return ProjectComparison(
        comparison_id=slugify("comparison-" + "-".join(run.run_id for run in runs)),
        workspace_id=runs[0].workspace_id,
        run_ids=[run.run_id for run in runs],
        disease_names=[run.disease_name for run in runs],
        candidate_overlap=candidate_overlap,
        target_overlap=target_overlap,
        score_deltas=deltas,
        run_summaries=[
            {
                "run_id": run.run_id,
                "disease_name": run.disease_name,
                "candidate_count": run.candidate_count,
                "generated_candidate_count": run.generated_candidate_count,
                "target_count": run.target_count,
                "artifact_refs": [artifact.artifact_id for artifact in run.artifacts],
            }
            for run in runs
        ],
        limitations=list(COMPARISON_LIMITATIONS),
        metadata={
            "run_dirs": {run.run_id: run.run_dir for run in runs},
            "candidate_counts": {run.run_id: run.candidate_count for run in runs},
        },
    )


def render_project_comparison_markdown(comparison: ProjectComparison) -> str:
    lines = [
        "# Project Run Comparison",
        "",
        "Artifact-grounded project comparison for review and planning.",
        "",
        f"- Workspace: {comparison.workspace_id}",
        f"- Runs: {', '.join(comparison.run_ids)}",
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
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in comparison.limitations)
    return "\n".join(lines).rstrip() + "\n"


def _candidate_names(run: ProjectRun) -> list[str]:
    return [str(row["name"]) for row in run.top_candidates if row.get("name") is not None]


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
