from __future__ import annotations

from typing import Any

from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig
from molecule_ranker.platform.codex_worker import CodexWorker, CodexWorkerConfig
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.schemas import PlatformJob
from molecule_ranker.platform.settings import PlatformSettings
from molecule_ranker.workers.base import BaseWorker
from molecule_ranker.workspace.store import ProjectWorkspaceStore


class CodexQueueWorker(BaseWorker):
    """Queue adapter for secure hosted-mode Codex execution."""

    def __init__(
        self,
        *,
        database: PlatformDatabase,
        workspace_store: ProjectWorkspaceStore,
        config: CodexWorkerConfig | None = None,
        settings: PlatformSettings | None = None,
        provider: Any | None = None,
        codex_config: CodexBackboneConfig | None = None,
    ) -> None:
        if config is None and codex_config is not None:
            config = CodexWorkerConfig(
                enable_codex_worker=True,
                codex_job_timeout_seconds=codex_config.codex_timeout_seconds,
                codex_worker_workspace_root=codex_config.codex_working_dir,
            )
        self.codex_worker = CodexWorker(
            database=database,
            workspace_store=workspace_store,
            config=config,
            settings=settings,
            provider=provider,
        )
        super().__init__(
            database=database,
            handlers={},
            job_types={"codex_task"},
        )

    def run_once(self) -> PlatformJob | None:
        return self.codex_worker.run_next()

    def run_job(self, job: PlatformJob) -> PlatformJob:
        return self.codex_worker.run_job(job)
