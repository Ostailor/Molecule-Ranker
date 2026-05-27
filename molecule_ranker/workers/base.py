from __future__ import annotations

from collections.abc import Callable

from molecule_ranker.platform.db import PlatformDatabase, PlatformDatabaseError
from molecule_ranker.platform.jobs import JOB_PERMISSION, JobResult, PlatformJobQueue
from molecule_ranker.platform.observability import pipeline_step_timer, record_pipeline_run
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import PlatformJob

JobHandler = Callable[[PlatformJob], JobResult]


class BaseWorker:
    def __init__(
        self,
        *,
        database: PlatformDatabase,
        handlers: dict[str, JobHandler] | None = None,
        job_types: set[str] | None = None,
    ) -> None:
        self.database = database
        self.queue = PlatformJobQueue(database)
        self.handlers = handlers or {}
        self.job_types = job_types

    def run_once(self) -> PlatformJob | None:
        job = self.queue.claim_next(job_types=self.job_types)
        if job is None:
            return None
        return self.run_job(job)

    def run_job(self, job: PlatformJob) -> PlatformJob:
        try:
            self._check_authorization(job)
            if job.metadata.get("cancel_requested"):
                return self.queue.cancel(job.job_id, actor_user_id=job.requested_by_user_id)
            handler = self.handlers.get(job.job_type)
            if handler is None:
                raise PlatformDatabaseError(f"No worker handler registered for {job.job_type}.")
            with pipeline_step_timer(
                job.job_type,
                project_id=job.project_id,
                run_id=job.job_id,
            ):
                result = handler(job)
            finished = self.queue.succeed(job, result)
            record_pipeline_run(succeeded=True)
            return finished
        except Exception as exc:
            record_pipeline_run(succeeded=False)
            return self.queue.fail(job, exc)

    def _check_authorization(self, job: PlatformJob) -> None:
        user = self.database.get_user(job.requested_by_user_id)
        if user is None or not user.is_active:
            raise PermissionError("Requesting user is no longer active.")
        permission = JOB_PERMISSION.get(job.job_type)
        if permission is None:
            raise PermissionError(f"Unsupported job type {job.job_type}.")
        if not has_permission(
            user,
            permission,
            org_id=job.org_id,
            project_id=job.project_id,
            database=self.database,
        ):
            raise PermissionError(f"Requesting user no longer has {permission}.")
