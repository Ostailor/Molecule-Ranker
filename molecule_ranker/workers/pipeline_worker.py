from __future__ import annotations

from pathlib import Path

from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.jobs import JobResult
from molecule_ranker.platform.schemas import PlatformJob
from molecule_ranker.workers.base import BaseWorker, JobHandler


def default_pipeline_handlers(root_dir: Path) -> dict[str, JobHandler]:
    def placeholder(job: PlatformJob) -> JobResult:
        output_dir = root_dir / ".molecule-ranker" / "job_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job.job_id}.json"
        output_path.write_text(
            "{\n"
            f'  "job_id": "{job.job_id}",\n'
            f'  "job_type": "{job.job_type}",\n'
            '  "status": "succeeded"\n'
            "}\n"
        )
        return JobResult(
            result={"message": f"{job.job_type} completed by placeholder worker."},
            artifact_paths=[output_path],
        )

    return {
        "ranking": placeholder,
        "generation": placeholder,
        "developability": placeholder,
        "experiment_import": placeholder,
        "active_learning": placeholder,
        "review_export": placeholder,
        "dashboard_build": placeholder,
    }


class PipelineWorker(BaseWorker):
    def __init__(
        self,
        *,
        database: PlatformDatabase,
        root_dir: Path | None = None,
        handlers: dict[str, JobHandler] | None = None,
    ) -> None:
        active_root = root_dir or database.root_dir
        super().__init__(
            database=database,
            handlers=handlers or default_pipeline_handlers(active_root),
            job_types={
                "ranking",
                "generation",
                "developability",
                "experiment_import",
                "active_learning",
                "review_export",
                "dashboard_build",
            },
        )
