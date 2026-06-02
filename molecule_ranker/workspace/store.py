from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.provider import CodexBackboneProvider
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTaskResult
from molecule_ranker.utils import slugify
from molecule_ranker.utils.json_io import load_json_file
from molecule_ranker.workspace.artifact_registry import ArtifactRegistry
from molecule_ranker.workspace.audit import WorkspaceAuditLogger
from molecule_ranker.workspace.run_manager import (
    ProjectRunManager,
    build_project_codex_task,
    project_codex_input_payload,
)
from molecule_ranker.workspace.schemas import ProjectWorkspace


class ProjectWorkspaceStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        self.state_dir = self.root_dir / ".molecule-ranker"
        self.workspace_path = self.state_dir / "workspace.json"
        self.summary_cache_path = self.state_dir / "workspace_summary_cache.json"
        self.codex_output_dir = self.state_dir / "codex_project_outputs"
        self.audit = WorkspaceAuditLogger(self.root_dir)

    def create(
        self,
        *,
        workspace_id: str | None = None,
        name: str | None = None,
        overwrite: bool = False,
    ) -> ProjectWorkspace:
        if self.workspace_path.exists() and not overwrite:
            return self.load()
        workspace = ProjectWorkspace(
            workspace_id=workspace_id or slugify(self.root_dir.name or "molecule-ranker-project"),
            name=name or self.root_dir.name or "molecule-ranker-project",
            root_dir=str(self.root_dir),
        )
        self.save(workspace)
        self.audit.write("workspace_created", {"workspace_id": workspace.workspace_id})
        return workspace

    def load(self) -> ProjectWorkspace:
        if not self.workspace_path.exists():
            raise ValueError(f"Workspace does not exist: {self.workspace_path}")
        return ProjectWorkspace.model_validate(load_json_file(self.workspace_path))

    def load_or_create(
        self,
        *,
        workspace_id: str | None = None,
        name: str | None = None,
    ) -> ProjectWorkspace:
        if self.workspace_path.exists():
            return self.load()
        return self.create(workspace_id=workspace_id, name=name)

    def save(self, workspace: ProjectWorkspace) -> ProjectWorkspace:
        workspace.updated_at = datetime.now(UTC)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_path.write_text(
            json.dumps(workspace.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        self.invalidate_summary_cache()
        return workspace

    def workspace_summary(
        self,
        workspace: ProjectWorkspace | None = None,
    ) -> dict[str, Any]:
        marker = self._summary_cache_marker()
        if workspace is None and self.summary_cache_path.exists():
            try:
                cached = json.loads(self.summary_cache_path.read_text())
            except (OSError, json.JSONDecodeError):
                cached = None
            if isinstance(cached, dict) and cached.get("cache_marker") == marker:
                payload = dict(cached.get("summary") or {})
                payload["cache_status"] = "hit"
                return payload
        active_workspace = workspace or self.load()
        summary = {
            "workspace_id": active_workspace.workspace_id,
            "name": active_workspace.name,
            "root_dir": active_workspace.root_dir,
            "run_count": len(active_workspace.runs),
            "artifact_count": len(active_workspace.artifacts),
            "codex_output_count": len(active_workspace.codex_outputs),
            "updated_at": active_workspace.updated_at.isoformat(),
            "cache_status": "miss",
        }
        self.summary_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_cache_path.write_text(
            json.dumps(
                {
                    "cache_marker": marker,
                    "summary": {
                        key: value
                        for key, value in summary.items()
                        if key != "cache_status"
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return summary

    def invalidate_summary_cache(self) -> None:
        try:
            self.summary_cache_path.unlink()
        except FileNotFoundError:
            return

    def _summary_cache_marker(self) -> dict[str, int]:
        if not self.workspace_path.exists():
            return {"mtime_ns": 0, "size_bytes": 0}
        stat = self.workspace_path.stat()
        return {"mtime_ns": stat.st_mtime_ns, "size_bytes": stat.st_size}

    def register_run_dir(
        self,
        run_dir: Path,
        *,
        run_id: str | None = None,
        workspace: ProjectWorkspace | None = None,
    ) -> ProjectWorkspace:
        active_workspace = workspace or self.load_or_create()
        manager = ProjectRunManager(self.root_dir, workspace_id=active_workspace.workspace_id)
        project_run = manager.load_run(run_dir, run_id=run_id)
        runs = [run for run in active_workspace.runs if run.run_id != project_run.run_id]
        runs.append(project_run)
        active_workspace.runs = sorted(runs, key=lambda run: run.created_at.isoformat())
        artifact_map = {artifact.artifact_id: artifact for artifact in active_workspace.artifacts}
        for artifact in project_run.artifacts:
            artifact_map[artifact.artifact_id] = artifact
        active_workspace.artifacts = sorted(
            artifact_map.values(), key=lambda artifact: artifact.artifact_id
        )
        self.audit.write(
            "run_registered",
            {"workspace_id": active_workspace.workspace_id, "run_id": project_run.run_id},
        )
        return self.save(active_workspace)

    def artifact_manifest(
        self,
        workspace: ProjectWorkspace | None = None,
    ) -> list[dict[str, object]]:
        active_workspace = workspace or self.load_or_create()
        registry = ArtifactRegistry(self.root_dir, workspace_id=active_workspace.workspace_id)
        return registry.manifest(active_workspace.artifacts)

    def run_codex_project_task(
        self,
        task_type: str,
        *,
        config: CodexBackboneConfig | None = None,
        provider: CodexBackboneProvider | None = None,
    ) -> tuple[ProjectWorkspace, CodexTaskResult, Path]:
        workspace = self.load_or_create()
        input_path = self._write_codex_input(workspace, task_type=task_type)
        active_config = config or CodexBackboneConfig(
            enable_codex_backbone=True,
            codex_working_dir=self.root_dir,
            codex_dry_run=True,
        )
        task = build_project_codex_task(
            workspace,
            task_type=task_type,
            working_directory=self.root_dir,
            input_artifact_path=input_path,
            timeout_seconds=active_config.codex_timeout_seconds,
        )
        active_provider = provider or CodexBackboneProvider(active_config)
        result = active_provider.run_task(task)
        output_path = self.store_codex_result(workspace, task_type=task_type, result=result)
        workspace = self.load()
        self.audit.write(
            "codex_project_task",
            {
                "workspace_id": workspace.workspace_id,
                "task_type": task_type,
                "status": result.status,
                "output_path": str(output_path),
            },
        )
        return workspace, result, output_path

    def store_codex_result(
        self,
        workspace: ProjectWorkspace,
        *,
        task_type: str,
        result: CodexTaskResult,
    ) -> Path:
        self.codex_output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_path = self.codex_output_dir / f"{task_type}-{timestamp}.json"
        artifact_refs = [artifact.artifact_id for artifact in workspace.artifacts]
        payload: dict[str, Any] = {
            "task_type": task_type,
            "workspace_id": workspace.workspace_id,
            "status": result.status,
            "output_text": result.output_text,
            "output_json": result.output_json,
            "artifact_refs": artifact_refs,
            "result": result.model_dump(mode="json"),
        }
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        record = {
            "task_type": task_type,
            "status": result.status,
            "path": str(output_path.resolve()),
            "artifact_refs": artifact_refs,
            "created_at": datetime.now(UTC).isoformat(),
        }
        refreshed = self.load_or_create(workspace_id=workspace.workspace_id, name=workspace.name)
        refreshed.codex_outputs = [*refreshed.codex_outputs, record]
        self.save(refreshed)
        return output_path

    def _write_codex_input(self, workspace: ProjectWorkspace, *, task_type: str) -> Path:
        self.codex_output_dir.mkdir(parents=True, exist_ok=True)
        input_path = self.codex_output_dir / f"{task_type}-input.json"
        input_path.write_text(
            json.dumps(project_codex_input_payload(workspace), indent=2, sort_keys=True) + "\n"
        )
        return input_path
