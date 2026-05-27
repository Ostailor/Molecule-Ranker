from __future__ import annotations

import hashlib
from pathlib import Path

from molecule_ranker.utils import slugify
from molecule_ranker.workspace.schemas import ArtifactRecord

KNOWN_ARTIFACT_TYPES: dict[str, str] = {
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
    "codex_backbone.json": "codex_backbone",
}


class ArtifactRegistry:
    def __init__(self, root_dir: Path, *, workspace_id: str) -> None:
        self.root_dir = root_dir.resolve()
        self.workspace_id = workspace_id

    def register_path(
        self,
        path: Path,
        *,
        run_id: str | None = None,
        artifact_type: str | None = None,
    ) -> ArtifactRecord:
        resolved = path.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"Artifact path does not exist or is not a file: {path}")
        data = resolved.read_bytes()
        relative_path = _relative_to(resolved, self.root_dir)
        prefix = run_id or self.workspace_id
        return ArtifactRecord(
            artifact_id=slugify(f"{prefix}-{relative_path}"),
            workspace_id=self.workspace_id,
            run_id=run_id,
            path=str(resolved),
            artifact_type=artifact_type or KNOWN_ARTIFACT_TYPES.get(resolved.name, "artifact"),
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            metadata={"relative_path": relative_path},
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

    def manifest(self, artifacts: list[ArtifactRecord]) -> list[dict[str, object]]:
        return [
            {
                "artifact_id": artifact.artifact_id,
                "run_id": artifact.run_id,
                "path": artifact.path,
                "artifact_type": artifact.artifact_type,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "relative_path": artifact.metadata.get("relative_path"),
            }
            for artifact in sorted(artifacts, key=lambda item: item.artifact_id)
        ]


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name
