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
            active_job = self.queue.get(job.job_id) or job
            self._check_authorization(job)
            if active_job.metadata.get("cancel_requested"):
                return self.queue.cancel(
                    active_job.job_id,
                    actor_user_id=active_job.requested_by_user_id,
                )
            if _timed_out(active_job):
                return self.queue.mark_timed_out(
                    active_job,
                    summary=f"Job {active_job.job_id} timed out before execution.",
                )
            self.queue.heartbeat(active_job.job_id)
            handler = self.handlers.get(active_job.job_type)
            if handler is None:
                raise PlatformDatabaseError(
                    f"No worker handler registered for {active_job.job_type}."
                )
            with pipeline_step_timer(
                active_job.job_type,
                project_id=active_job.project_id,
                run_id=active_job.job_id,
            ):
                result = handler(active_job)
            refreshed = self.queue.get(active_job.job_id) or active_job
            if refreshed.metadata.get("cancel_requested"):
                return self.queue.cancel(
                    refreshed.job_id,
                    actor_user_id=refreshed.requested_by_user_id,
                )
            if _timed_out(refreshed):
                return self.queue.mark_timed_out(
                    refreshed,
                    summary=f"Job {refreshed.job_id} timed out.",
                )
            finished = self.queue.succeed(refreshed, result)
            record_pipeline_run(succeeded=True)
            return finished
        except Exception as exc:
            record_pipeline_run(succeeded=False)
            return self.queue.handle_failure(job, exc)

    def _check_authorization(self, job: PlatformJob) -> None:
        user = self.database.get_user(job.requested_by_user_id)
        if user is None or not user.is_active:
            raise PermissionError("Requesting user is no longer active.")
        permission = JOB_PERMISSION.get(job.job_type)
        if permission is None:
            raise PermissionError(f"Unsupported job type {job.job_type}.")
        if not user.is_admin and not has_permission(
            user,
            permission,
            org_id=job.org_id,
            project_id=job.project_id,
            database=self.database,
        ):
            raise PermissionError(f"Requesting user no longer has {permission}.")


def _timed_out(job: PlatformJob) -> bool:
    timeout = job.config_snapshot.get("timeout_seconds")
    if timeout is None:
        return False
    try:
        timeout_seconds = float(timeout)
    except (TypeError, ValueError):
        return False
    if timeout_seconds <= 0:
        return True
    if job.started_at is None:
        return False
    from datetime import UTC, datetime

    return (datetime.now(UTC) - job.started_at).total_seconds() > timeout_seconds
