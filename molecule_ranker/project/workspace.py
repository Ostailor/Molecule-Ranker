from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.project.schemas import ArtifactRecord, ProjectRun, ProjectWorkspace
from molecule_ranker.utils import slugify

KNOWN_ARTIFACT_TYPES = {
    "candidates.json": "candidates",
    "generated_candidates.json": "generated_candidates",
    "generated_molecules.json": "generated_candidates",
    "developability.json": "developability",
    "developability_assessments.json": "developability",
    "experimental_results.json": "experimental_results",
    "experimental_evidence.json": "experimental_evidence",
    "active_learning_batch.json": "active_learning",
    "review_queue.json": "review_queue",
    "trace.json": "trace",
    "report.md": "report",
    "developability_report.md": "developability_report",
    "experimental_report.md": "experimental_report",
}


class ArtifactRegistry:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()

    def register_path(
        self,
        path: Path,
        *,
        run_id: str | None = None,
        artifact_type: str | None = None,
    ) -> ArtifactRecord:
        resolved = path.resolve()
        data = resolved.read_bytes()
        rel = _relative_to(resolved, self.root_dir)
        return ArtifactRecord(
            artifact_id=slugify(f"{run_id or 'project'}-{rel}"),
            run_id=run_id,
            path=str(resolved),
            artifact_type=artifact_type or KNOWN_ARTIFACT_TYPES.get(resolved.name, "artifact"),
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            metadata={"relative_path": rel},
        )

    def discover_run_artifacts(self, run_dir: Path, *, run_id: str) -> list[ArtifactRecord]:
        artifacts: list[ArtifactRecord] = []
        for name, artifact_type in KNOWN_ARTIFACT_TYPES.items():
            path = run_dir / name
            if path.exists() and path.is_file():
                artifacts.append(
                    self.register_path(path, run_id=run_id, artifact_type=artifact_type)
                )
        return artifacts


class ProjectWorkspaceStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        self.state_dir = self.root_dir / ".molecule-ranker"
        self.workspace_path = self.state_dir / "project.json"
        self.registry = ArtifactRegistry(self.root_dir)

    def load_or_create(self, *, project_id: str | None = None) -> ProjectWorkspace:
        if self.workspace_path.exists():
            payload = json.loads(self.workspace_path.read_text())
            return ProjectWorkspace.model_validate(payload)
        workspace = ProjectWorkspace(
            project_id=project_id or slugify(self.root_dir.name or "molecule-ranker-project"),
            root_dir=str(self.root_dir),
        )
        self.save(workspace)
        return workspace

    def save(self, workspace: ProjectWorkspace) -> ProjectWorkspace:
        workspace.updated_at = datetime.now(UTC)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_path.write_text(
            json.dumps(workspace.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        return workspace

    def register_run_dir(
        self,
        run_dir: Path,
        *,
        run_id: str | None = None,
        workspace: ProjectWorkspace | None = None,
    ) -> ProjectWorkspace:
        resolved = run_dir.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(f"Run directory does not exist: {run_dir}")
        active_workspace = workspace or self.load_or_create()
        project_run = load_project_run(resolved, self.registry, run_id=run_id)
        runs = [run for run in active_workspace.runs if run.run_id != project_run.run_id]
        runs.append(project_run)
        active_workspace.runs = sorted(runs, key=lambda run: run.created_at.isoformat())
        artifact_map = {
            artifact.artifact_id: artifact for artifact in active_workspace.artifacts
        }
        for artifact in project_run.artifacts:
            artifact_map[artifact.artifact_id] = artifact
        active_workspace.artifacts = sorted(
            artifact_map.values(), key=lambda artifact: artifact.artifact_id
        )
        return self.save(active_workspace)


def load_project_run(
    run_dir: Path,
    registry: ArtifactRegistry,
    *,
    run_id: str | None = None,
) -> ProjectRun:
    candidates_path = run_dir / "candidates.json"
    if not candidates_path.exists():
        raise ValueError(f"Missing candidates.json in run directory: {run_dir}")
    payload = json.loads(candidates_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("candidates.json must contain a JSON object.")
    raw_disease = payload.get("disease")
    disease: dict[str, Any] = raw_disease if isinstance(raw_disease, dict) else {}
    disease_name = str(
        disease.get("canonical_name")
        or disease.get("input_name")
        or payload.get("disease_name")
        or run_dir.name
    )
    raw_summary = payload.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    raw_candidates = payload.get("candidates")
    candidates: list[Any] = raw_candidates if isinstance(raw_candidates, list) else []
    raw_targets = payload.get("targets")
    targets: list[Any] = raw_targets if isinstance(raw_targets, list) else []
    raw_generated = payload.get("generated_molecule_hypotheses")
    generated: list[Any] = raw_generated if isinstance(raw_generated, list) else []
    resolved_run_id = run_id or slugify(f"{run_dir.name}-{_short_hash(candidates_path)}")
    artifacts = registry.discover_run_artifacts(run_dir, run_id=resolved_run_id)
    return ProjectRun(
        run_id=resolved_run_id,
        run_dir=str(run_dir.resolve()),
        disease_name=disease_name,
        candidate_count=int(summary.get("candidate_count") or len(candidates)),
        generated_candidate_count=int(summary.get("generated_candidate_count") or len(generated)),
        target_count=int(summary.get("target_count") or len(targets)),
        top_candidates=_top_candidates(candidates),
        artifacts=artifacts,
        metadata={
            "source_candidates_json": str(candidates_path.resolve()),
            "limitations": payload.get("limitations", []),
        },
    )


def _top_candidates(candidates: list[Any], limit: int = 10) -> list[dict[str, Any]]:
    rows = []
    for index, candidate in enumerate(candidates[:limit], start=1):
        if not isinstance(candidate, dict):
            continue
        score_breakdown = candidate.get("score_breakdown")
        confidence = (
            score_breakdown.get("confidence")
            if isinstance(score_breakdown, dict)
            else None
        )
        rows.append(
            {
                "rank": index,
                "name": candidate.get("name"),
                "score": candidate.get("score"),
                "confidence": confidence,
                "origin": candidate.get("origin", "existing"),
                "known_targets": candidate.get("known_targets", []),
            }
        )
    return rows


def _short_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name
