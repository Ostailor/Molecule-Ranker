from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.schemas import CodexTask
from molecule_ranker.utils import slugify
from molecule_ranker.workspace.artifact_registry import ArtifactRegistry
from molecule_ranker.workspace.schemas import ProjectRun, ProjectWorkspace


class ProjectRunManager:
    def __init__(self, root_dir: Path, *, workspace_id: str) -> None:
        self.root_dir = root_dir.resolve()
        self.workspace_id = workspace_id
        self.registry = ArtifactRegistry(self.root_dir, workspace_id=workspace_id)

    def load_run(self, run_dir: Path, *, run_id: str | None = None) -> ProjectRun:
        return load_project_run(
            run_dir.resolve(),
            self.registry,
            workspace_id=self.workspace_id,
            run_id=run_id,
        )


def load_project_run(
    run_dir: Path,
    registry: ArtifactRegistry,
    *,
    workspace_id: str,
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
        workspace_id=workspace_id,
        run_dir=str(run_dir.resolve()),
        disease_name=str(
            disease.get("canonical_name")
            or disease.get("input_name")
            or payload.get("disease_name")
            or run_dir.name
        ),
        candidate_count=int(summary.get("candidate_count") or len(candidates)),
        generated_candidate_count=int(summary.get("generated_candidate_count") or len(generated)),
        target_count=int(summary.get("target_count") or len(targets)),
        top_candidates=_top_candidates(candidates),
        artifacts=artifacts,
        summary={
            "success": payload.get("success"),
            "candidate_count": int(summary.get("candidate_count") or len(candidates)),
            "generated_candidate_count": int(
                summary.get("generated_candidate_count") or len(generated)
            ),
            "target_count": int(summary.get("target_count") or len(targets)),
            "limitations": payload.get("limitations", []),
        },
        metadata={"source_candidates_json": str(candidates_path.resolve())},
    )


def build_project_codex_task(
    workspace: ProjectWorkspace,
    *,
    task_type: str,
    working_directory: Path,
    input_artifact_path: Path,
    timeout_seconds: int = 300,
) -> CodexTask:
    return CodexTask(
        task_id=slugify(f"{workspace.workspace_id}-{task_type}"),
        task_type=task_type,  # type: ignore[arg-type]
        prompt=_project_task_prompt(workspace, task_type=task_type),
        working_directory=str(working_directory.resolve()),
        input_artifact_paths=[str(input_artifact_path.resolve())],
        allowed_commands=[],
        forbidden_commands=[],
        expected_output_format="json",
        timeout_seconds=timeout_seconds,
        require_json=True,
        metadata={
            "workspace_id": workspace.workspace_id,
            "project_task": True,
            "artifact_refs": [artifact.artifact_id for artifact in workspace.artifacts],
        },
    )


def project_codex_input_payload(workspace: ProjectWorkspace) -> dict[str, Any]:
    return {
        "workspace_id": workspace.workspace_id,
        "name": workspace.name,
        "root_dir": workspace.root_dir,
        "run_summaries": [
            {
                "run_id": run.run_id,
                "disease_name": run.disease_name,
                "candidate_count": run.candidate_count,
                "generated_candidate_count": run.generated_candidate_count,
                "target_count": run.target_count,
                "top_candidates": run.top_candidates,
                "summary": run.summary,
                "artifact_refs": [artifact.artifact_id for artifact in run.artifacts],
            }
            for run in workspace.runs
        ],
        "artifact_manifest": [
            {
                "artifact_id": artifact.artifact_id,
                "run_id": artifact.run_id,
                "path": artifact.path,
                "artifact_type": artifact.artifact_type,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
            }
            for artifact in workspace.artifacts
        ],
        "constraints": [
            "Codex may inspect artifact manifests and run summaries only.",
            "Codex cannot create or modify scientific evidence.",
            (
                "Codex cannot create EvidenceItem, assay results, generated molecules, "
                "or score changes."
            ),
        ],
    }


def _project_task_prompt(workspace: ProjectWorkspace, *, task_type: str) -> str:
    schemas: dict[str, dict[str, Any]] = {
        "summarize_project": {
            "project_summary": "string",
            "run_highlights": ["artifact-backed run summary strings"],
            "main_uncertainties": ["uncertainty strings"],
            "artifact_refs": ["artifact IDs used"],
        },
        "explain_run_changes": {
            "change_summary": "string",
            "run_differences": ["artifact-backed differences"],
            "limitations": ["limitation strings"],
            "artifact_refs": ["artifact IDs used"],
        },
        "compare_runs": {
            "comparison_summary": "string",
            "shared_findings": ["artifact-backed shared facts"],
            "differences": ["artifact-backed differences"],
            "limitations": ["limitation strings"],
            "artifact_refs": ["artifact IDs used"],
        },
        "draft_project_update": {
            "project_update": "string",
            "evidence_status": ["artifact-backed status strings"],
            "risks": ["risk or limitation strings"],
            "artifact_refs": ["artifact IDs used"],
        },
        "suggest_next_project_actions": {
            "recommended_actions": [
                {
                    "action_type": (
                        "review|rerun|compare|summarize|experiment_import|active_learning"
                    ),
                    "rationale": "string",
                    "safe_cli_command": "string",
                }
            ],
            "limitations": ["limitation strings"],
            "artifact_refs": ["artifact IDs used"],
        },
    }
    return json.dumps(
        {
            "task": task_type,
            "workspace_id": workspace.workspace_id,
            "instructions": [
                "Use only the provided project Codex input artifact.",
                "Cite artifact IDs in artifact_refs.",
                (
                    "Do not invent evidence, targets, molecules, assay results, scores, "
                    "citations, PMIDs, or DOIs."
                ),
                (
                    "Do not claim cure, treatment, safety, efficacy, binding, activity, "
                    "or synthesizability."
                ),
                (
                    "No medical advice, synthesis routes, lab protocols, dosing, or "
                    "patient treatment guidance."
                ),
                "Do not create or modify scientific evidence.",
                "Return valid JSON only.",
            ],
            "output_json_schema": schemas.get(task_type, schemas["summarize_project"]),
        },
        indent=2,
        sort_keys=True,
    )


def _top_candidates(candidates: list[Any], limit: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:limit], start=1):
        if not isinstance(candidate, dict):
            continue
        score_breakdown = candidate.get("score_breakdown")
        confidence = (
            score_breakdown.get("confidence") if isinstance(score_breakdown, dict) else None
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
