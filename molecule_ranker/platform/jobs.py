from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import case, insert, select, update

from molecule_ranker.campaigns.schemas import contains_procedural_lab_detail
from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.database import artifact_records, platform_jobs
from molecule_ranker.platform.db import PlatformDatabase, PlatformDatabaseError
from molecule_ranker.platform.observability import log_event, metrics
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.retry_policy import (
    build_auto_idempotency_key,
    codex_context_hash,
    retry_decision,
)
from molecule_ranker.platform.schemas import PlatformJob, UserAccount

QUEUE_JOB_TYPES = {
    "ranking",
    "generation",
    "developability",
    "experiment_import",
    "integration_sync",
    "connector_health_check",
    "webhook_processing",
    "warehouse_export",
    "registry_mapping_review",
    "external_export",
    "active_learning",
    "model_training",
    "model_validation",
    "model_prediction",
    "model_dataset_build",
    "model_train",
    "model_evaluate",
    "model_predict",
    "model_calibrate",
    "structure_find",
    "structure_select",
    "receptor_prepare",
    "ligand_prepare",
    "binding_site_define",
    "structure_dock",
    "pose_qc",
    "structure_assess",
    "structure_benchmark",
    "structure_design_loop",
    "structure_retrieval",
    "structure_selection",
    "receptor_preparation",
    "ligand_preparation",
    "structure_docking",
    "structure_report_card",
    "review_export",
    "dashboard_build",
    "codex_task",
    "design_plan",
    "design_generate",
    "design_score",
    "design_loop",
    "design_benchmark",
    "portfolio_build_candidates",
    "portfolio_optimize",
    "portfolio_scenario_analysis",
    "portfolio_stage_gate",
    "portfolio_batch_build",
    "portfolio_memo",
    "graph_build",
    "graph_query",
    "graph_mechanism_extract",
    "graph_contradiction_scan",
    "graph_staleness_scan",
    "graph_recommendation",
    "graph_export",
    "hypothesis_generate",
    "hypothesis_rank",
    "hypothesis_questions",
    "hypothesis_report",
    "hypothesis_review",
    "campaign_create",
    "campaign_plan",
    "campaign_replan",
    "campaign_memo",
    "campaign_export",
    "eval_dataset_build",
    "eval_split",
    "eval_benchmark_run",
    "eval_prospective_freeze",
    "eval_prospective_evaluate",
    "eval_guardrail_benchmark",
    "eval_reproducibility",
    "eval_trend_report",
}

JOB_PERMISSION: dict[str, str] = {
    "ranking": "run:create",
    "generation": "run:create",
    "developability": "run:create",
    "experiment_import": "experiment:import",
    "integration_sync": "integration:sync",
    "connector_health_check": "integration:read",
    "webhook_processing": "integration:sync",
    "warehouse_export": "integration:sync",
    "registry_mapping_review": "integration:sync",
    "external_export": "integration:sync",
    "active_learning": "run:create",
    "model_training": "run:create",
    "model_validation": "run:create",
    "model_prediction": "run:create",
    "model_dataset_build": "model:train",
    "model_train": "model:train",
    "model_evaluate": "model:read",
    "model_predict": "model:predict",
    "model_calibrate": "model:train",
    "structure_find": "structure:run",
    "structure_select": "structure:run",
    "receptor_prepare": "structure:run",
    "ligand_prepare": "structure:run",
    "binding_site_define": "structure:run",
    "structure_dock": "structure:dock",
    "pose_qc": "structure:run",
    "structure_assess": "structure:run",
    "structure_benchmark": "structure:run",
    "structure_design_loop": "structure:run",
    "structure_retrieval": "run:create",
    "structure_selection": "run:create",
    "receptor_preparation": "run:create",
    "ligand_preparation": "run:create",
    "structure_docking": "run:create",
    "structure_report_card": "run:create",
    "review_export": "artifact:export",
    "dashboard_build": "project:read",
    "codex_task": "codex:run",
    "design_plan": "design:run",
    "design_generate": "design:run",
    "design_score": "design:run",
    "design_loop": "design:run",
    "design_benchmark": "design:run",
    "portfolio_build_candidates": "portfolio:run",
    "portfolio_optimize": "portfolio:run",
    "portfolio_scenario_analysis": "portfolio:run",
    "portfolio_stage_gate": "portfolio:run",
    "portfolio_batch_build": "portfolio:run",
    "portfolio_memo": "portfolio:run",
    "graph_build": "graph:build",
    "graph_query": "graph:query",
    "graph_mechanism_extract": "graph:query",
    "graph_contradiction_scan": "graph:query",
    "graph_staleness_scan": "graph:query",
    "graph_recommendation": "graph:query",
    "graph_export": "graph:export",
    "hypothesis_generate": "hypothesis:generate",
    "hypothesis_rank": "hypothesis:read",
    "hypothesis_questions": "hypothesis:read",
    "hypothesis_report": "hypothesis:export",
    "hypothesis_review": "hypothesis:review",
    "campaign_create": "campaign:create",
    "campaign_plan": "campaign:plan",
    "campaign_replan": "campaign:plan",
    "campaign_memo": "campaign:plan",
    "campaign_export": "campaign:export",
    "eval_dataset_build": "evaluation:run",
    "eval_split": "evaluation:run",
    "eval_benchmark_run": "evaluation:run",
    "eval_prospective_freeze": "evaluation:run",
    "eval_prospective_evaluate": "evaluation:run",
    "eval_guardrail_benchmark": "evaluation:run",
    "eval_reproducibility": "evaluation:run",
    "eval_trend_report": "evaluation:run",
}

PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}
LOGGER = logging.getLogger("molecule_ranker.jobs")
DESIGN_LARGE_GENERATION_THRESHOLD = 100
DESIGN_MAX_GENERATION_BUDGET = 1000
STRUCTURE_LARGE_DOCKING_THRESHOLD = 100
STRUCTURE_MAX_DOCKING_BUDGET = 1000
PORTFOLIO_JOB_TYPES = {
    "portfolio_build_candidates",
    "portfolio_optimize",
    "portfolio_scenario_analysis",
    "portfolio_stage_gate",
    "portfolio_batch_build",
    "portfolio_memo",
}
GRAPH_JOB_TYPES = {
    "graph_build",
    "graph_query",
    "graph_mechanism_extract",
    "graph_contradiction_scan",
    "graph_staleness_scan",
    "graph_recommendation",
    "graph_export",
}
HYPOTHESIS_JOB_TYPES = {
    "hypothesis_generate",
    "hypothesis_rank",
    "hypothesis_questions",
    "hypothesis_report",
    "hypothesis_review",
}
CAMPAIGN_JOB_TYPES = {
    "campaign_create",
    "campaign_plan",
    "campaign_replan",
    "campaign_memo",
    "campaign_export",
}
EVALUATION_JOB_TYPES = {
    "eval_dataset_build",
    "eval_split",
    "eval_benchmark_run",
    "eval_prospective_freeze",
    "eval_prospective_evaluate",
    "eval_guardrail_benchmark",
    "eval_reproducibility",
    "eval_trend_report",
}


@dataclass(frozen=True)
class JobResult:
    result: dict[str, Any] = field(default_factory=dict)
    artifact_paths: list[Path] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)


class PlatformJobQueue:
    def __init__(self, database: PlatformDatabase, *, root_dir: Path | None = None) -> None:
        self.database = database
        self.root_dir = (root_dir or database.root_dir).resolve()

    def enqueue(
        self,
        *,
        job_type: str,
        requested_by: UserAccount,
        org_id: str = "default",
        project_id: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
        priority: str = "normal",
        metadata: dict[str, Any] | None = None,
    ) -> PlatformJob:
        if job_type not in QUEUE_JOB_TYPES:
            raise PlatformDatabaseError(f"Unsupported job type: {job_type}")
        config = config_snapshot or {}
        permission = JOB_PERMISSION[job_type]
        if not requested_by.is_admin and not has_permission(
            requested_by,
            permission,
            org_id=org_id,
            project_id=project_id,
            database=self.database,
        ):
            self.database.write_audit(
                "job_permission_denied",
                actor_user_id=requested_by.user_id,
                org_id=org_id,
                project_id=project_id,
                summary=f"Denied job enqueue for {job_type}.",
                object_type="platform_job",
                object_id=job_type,
                metadata={"job_type": job_type, "permission": permission},
            )
            raise PermissionError(f"Missing permission {permission}.")
        _enforce_hosted_design_policy(
            database=self.database,
            requested_by=requested_by,
            job_type=job_type,
            project_id=project_id,
            config_snapshot=config,
        )
        _enforce_hosted_model_policy(job_type=job_type, config_snapshot=config)
        _enforce_hosted_structure_policy(
            database=self.database,
            requested_by=requested_by,
            job_type=job_type,
            project_id=project_id,
            config_snapshot=config,
        )
        _enforce_hosted_portfolio_policy(
            database=self.database,
            requested_by=requested_by,
            job_type=job_type,
            project_id=project_id,
            config_snapshot=config,
        )
        _enforce_hosted_graph_policy(
            database=self.database,
            requested_by=requested_by,
            job_type=job_type,
            project_id=project_id,
            config_snapshot=config,
        )
        _enforce_hosted_hypothesis_policy(
            database=self.database,
            requested_by=requested_by,
            job_type=job_type,
            project_id=project_id,
            config_snapshot=config,
        )
        _enforce_hosted_campaign_policy(
            database=self.database,
            requested_by=requested_by,
            job_type=job_type,
            project_id=project_id,
            config_snapshot=config,
        )
        _enforce_hosted_evaluation_policy(job_type=job_type, config_snapshot=config)
        job_metadata = metadata or {}
        explicit_idempotency_key = config.get("idempotency_key") or job_metadata.get(
            "idempotency_key"
        )
        job_metadata = {**job_metadata}
        if explicit_idempotency_key:
            job_metadata["idempotency_key"] = str(explicit_idempotency_key)
        else:
            job_metadata["auto_idempotency_key"] = build_auto_idempotency_key(job_type, config)
        if job_type == "codex_task":
            job_metadata.setdefault("codex_context_hash", codex_context_hash(config))
        if job_type in EVALUATION_JOB_TYPES:
            job_metadata = {
                **job_metadata,
                "evaluation_v1_8": True,
                "evaluation_reports_are_not_evidence": True,
                "not_clinical_validation": True,
            }
        now = _now()
        job = PlatformJob(
            job_id=f"job-{uuid.uuid4().hex[:16]}",
            org_id=org_id,
            project_id=project_id,
            requested_by_user_id=requested_by.user_id,
            job_type=job_type,  # type: ignore[arg-type]
            status="queued",
            priority=priority,  # type: ignore[arg-type]
            config_snapshot=config,
            created_at=now,
            metadata=job_metadata,
        )
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(platform_jobs).values(
                    job_id=job.job_id,
                    org_id=job.org_id,
                    project_id=job.project_id,
                    requested_by_user_id=job.requested_by_user_id,
                    job_type=job.job_type,
                    status=job.status,
                    priority=job.priority,
                    config_snapshot_json=job.config_snapshot,
                    created_at=job.created_at,
                    started_at=None,
                    completed_at=None,
                    result_artifact_ids_json=[],
                    error_summary=None,
                    metadata_json=job.metadata,
                    updated_at=now,
                    attempts=0,
                    result_json=None,
                )
            )
        self.database.write_audit(
            "job_enqueued",
            actor_user_id=requested_by.user_id,
            org_id=org_id,
            project_id=project_id,
            summary=f"Enqueued {job_type}.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"job_id": job.job_id, "job_type": job_type},
        )
        metrics.increment("jobs_queued_total")
        log_event(
            LOGGER,
            "job_enqueued",
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
        )
        return job

    def list_jobs(
        self,
        *,
        status: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PlatformJob]:
        statement = select(platform_jobs)
        if status is not None:
            statement = statement.where(platform_jobs.c.status == status)
        if project_id is not None:
            statement = statement.where(platform_jobs.c.project_id == project_id)
        statement = (
            statement.order_by(platform_jobs.c.created_at.desc())
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().fetchall()
        return [_platform_job(row) for row in rows]

    def get(self, job_id: str) -> PlatformJob | None:
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(select(platform_jobs).where(platform_jobs.c.job_id == job_id))
                .mappings()
                .first()
            )
        return _platform_job(row) if row else None

    def claim_next(self, *, job_types: set[str] | None = None) -> PlatformJob | None:
        now = _now()
        statement = select(platform_jobs).where(platform_jobs.c.status.in_(["queued", "retrying"]))
        if job_types is not None:
            statement = statement.where(platform_jobs.c.job_type.in_(job_types))
        priority_order = case(
            (platform_jobs.c.priority == "high", 0),
            (platform_jobs.c.priority == "normal", 1),
            (platform_jobs.c.priority == "low", 2),
            else_=1,
        )
        statement = statement.order_by(priority_order, platform_jobs.c.created_at).limit(25)
        with self.database.engine.begin() as connection:
            rows = connection.execute(statement).mappings().fetchall()
            row = next(
                (
                    item
                    for item in rows
                    if _retry_due(dict(item["metadata_json"] or {}), now=now)
                ),
                None,
            )
            if row is None:
                return None
            metadata = dict(row["metadata_json"] or {})
            next_attempt = int(row["attempts"] or 0) + 1
            metadata["queue_wait_ms"] = int(
                (now - _aware(row["created_at"])).total_seconds() * 1000
            )
            metadata.setdefault("progress", {"completed": 0, "total": None})
            metadata["heartbeat_at"] = now.isoformat()
            metadata["attempts"] = next_attempt
            result = connection.execute(
                update(platform_jobs)
                .where(
                    (platform_jobs.c.job_id == row["job_id"])
                    & (platform_jobs.c.status.in_(["queued", "retrying"]))
                )
                .values(
                    status="running",
                    started_at=now,
                    updated_at=now,
                    attempts=next_attempt,
                    metadata_json=metadata,
                )
            )
            if result.rowcount != 1:
                return None
            refreshed = (
                connection.execute(
                    select(platform_jobs).where(platform_jobs.c.job_id == row["job_id"])
                )
                .mappings()
                .one()
            )
        return _platform_job(refreshed)

    def heartbeat(self, job_id: str, *, worker_id: str | None = None) -> PlatformJob:
        job = self.get(job_id)
        if job is None:
            raise PlatformDatabaseError("Job not found.")
        now = _now()
        metadata = {
            **job.metadata,
            "heartbeat_at": now.isoformat(),
            "worker_id": worker_id or job.metadata.get("worker_id"),
        }
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job_id)
                .values(metadata_json=metadata, updated_at=now)
            )
        refreshed = self.get(job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after heartbeat.")
        return refreshed

    def save_checkpoint(
        self,
        job_id: str,
        *,
        checkpoint_id: str,
        payload: dict[str, Any] | None = None,
    ) -> PlatformJob:
        job = self.get(job_id)
        if job is None:
            raise PlatformDatabaseError("Job not found.")
        metadata = {
            **job.metadata,
            "checkpoint_id": redact_secrets(checkpoint_id),
            "checkpoint_payload": _redact_snapshot(payload or {}),
            "checkpoint_saved_at": _now().isoformat(),
        }
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job_id)
                .values(metadata_json=metadata, updated_at=_now())
            )
        refreshed = self.get(job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after checkpoint.")
        return refreshed

    def update_progress(
        self,
        job_id: str,
        *,
        completed: int,
        total: int | None = None,
        message: str | None = None,
    ) -> PlatformJob:
        job = self.get(job_id)
        if job is None:
            raise PlatformDatabaseError("Job not found.")
        progress = {
            "completed": max(0, int(completed)),
            "total": max(0, int(total)) if total is not None else None,
            "message": redact_secrets(message or ""),
            "updated_at": _now().isoformat(),
        }
        metadata = {**job.metadata, "progress": progress}
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job_id)
                .values(metadata_json=metadata, updated_at=_now())
            )
        refreshed = self.get(job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after progress update.")
        return refreshed

    def cancel(self, job_id: str, *, actor_user_id: str) -> PlatformJob:
        job = self.get(job_id)
        if job is None:
            raise PlatformDatabaseError("Job not found.")
        now = _now()
        if job.status in {"queued", "retrying", "waiting_for_approval"}:
            with self.database.engine.begin() as connection:
                connection.execute(
                    update(platform_jobs)
                    .where(
                        (platform_jobs.c.job_id == job_id)
                        & (
                            platform_jobs.c.status.in_(
                                ["queued", "retrying", "waiting_for_approval"]
                            )
                        )
                    )
                    .values(status="cancelled", completed_at=now, updated_at=now)
                )
        elif job.status == "running":
            if job.metadata.get("cancel_requested"):
                metadata = {**job.metadata, "cancelled_by": actor_user_id}
                with self.database.engine.begin() as connection:
                    connection.execute(
                        update(platform_jobs)
                        .where(platform_jobs.c.job_id == job_id)
                        .values(
                            status="cancelled",
                            completed_at=now,
                            metadata_json=metadata,
                            updated_at=now,
                        )
                    )
            else:
                metadata = {**job.metadata, "cancel_requested": True, "cancelled_by": actor_user_id}
                with self.database.engine.begin() as connection:
                    connection.execute(
                        update(platform_jobs)
                        .where(platform_jobs.c.job_id == job_id)
                        .values(metadata_json=metadata, updated_at=now)
                    )
        else:
            return job
        self.database.write_audit(
            "job_cancel_requested",
            actor_user_id=actor_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Cancel requested for job {job_id}.",
            object_type="platform_job",
            object_id=job_id,
            metadata={"previous_status": job.status},
        )
        refreshed = self.get(job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after cancellation.")
        return refreshed

    def retry_failed(
        self,
        job_id: str,
        *,
        requested_by: UserAccount,
        priority: str | None = None,
    ) -> PlatformJob:
        job = self.get(job_id)
        if job is None:
            raise PlatformDatabaseError("Job not found.")
        if job.status not in {"failed", "guardrail_failed"}:
            raise PlatformDatabaseError("Only failed jobs can be retried.")
        retry_metadata = {
            **_redact_snapshot(job.metadata),
            "retry_of_job_id": job.job_id,
            "retry_attempt": int(job.metadata.get("retry_attempt") or 1) + 1,
            "previous_status": job.status,
            "previous_error_summary": redact_secrets(job.error_summary or ""),
        }
        retry_metadata.pop("cancel_requested", None)
        retry_metadata.pop("cancelled_by", None)
        retry = self.enqueue(
            job_type=job.job_type,
            requested_by=requested_by,
            org_id=job.org_id,
            project_id=job.project_id,
            config_snapshot=_redact_snapshot(job.config_snapshot),
            priority=priority or job.priority,
            metadata=retry_metadata,
        )
        self.database.write_audit(
            "job_retry_enqueued",
            actor_user_id=requested_by.user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Retry enqueued for job {job_id}.",
            object_type="platform_job",
            object_id=retry.job_id,
            metadata={"retry_of_job_id": job.job_id, "retry_job_id": retry.job_id},
        )
        return retry

    def resume_summary(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise PlatformDatabaseError("Job not found.")
        checkpoint_id = job.metadata.get("checkpoint_id")
        resume_token = job.metadata.get("resume_token")
        return {
            "job_id": job.job_id,
            "status": job.status,
            "resumable": job.status == "running" and checkpoint_id is not None,
            "cancel_requested": bool(job.metadata.get("cancel_requested")),
            "checkpoint_id": redact_secrets(str(checkpoint_id)) if checkpoint_id else None,
            "resume_token": "[REDACTED]" if resume_token else None,
        }

    def handle_failure(self, job: PlatformJob, exc: Exception) -> PlatformJob:
        if _job_timed_out(job):
            return self.mark_timed_out(job, summary=f"Job {job.job_id} timed out.")
        attempts = _attempt_count(job)
        decision = retry_decision(
            job_type=job.job_type,
            config_snapshot=job.config_snapshot,
            metadata=job.metadata,
            attempts=attempts,
            exc=exc,
        )
        if decision.dead_letter:
            return self.dead_letter(job, exc, reason=decision.reason)
        if decision.should_retry:
            return self.schedule_retry(job, exc, decision=decision)
        return self.fail(job, exc, retry_metadata={"reason": decision.reason})

    def schedule_retry(self, job: PlatformJob, exc: Exception, *, decision: Any) -> PlatformJob:
        now = _now()
        next_retry_at = now + timedelta(seconds=float(decision.delay_seconds))
        retry_metadata = {
            **job.metadata,
            "retry": {
                "attempt": _attempt_count(job),
                "reason": decision.reason,
                "error_summary": redact_secrets(str(exc))[:2000],
                "delay_seconds": decision.delay_seconds,
                "next_retry_at": next_retry_at.isoformat(),
                "max_attempts": decision.policy.max_attempts,
            },
            "next_retry_at": next_retry_at.isoformat(),
        }
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status="retrying",
                    updated_at=now,
                    error_summary=redact_secrets(str(exc))[:2000],
                    metadata_json=retry_metadata,
                )
            )
        self.database.write_audit(
            "job_retry_scheduled",
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Retry scheduled for job {job.job_id}.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata=retry_metadata["retry"],
        )
        refreshed = self.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after retry scheduling.")
        return refreshed

    def mark_timed_out(self, job: PlatformJob, *, summary: str) -> PlatformJob:
        return self._terminal_failure(
            job,
            status="timed_out",
            error_summary=summary,
            event_type="job_timed_out",
            metadata={"reason": "timeout"},
        )

    def dead_letter(self, job: PlatformJob, exc: Exception, *, reason: str) -> PlatformJob:
        error_summary = redact_secrets(str(exc))[:2000]
        metadata = {
            **job.metadata,
            "dead_letter": {
                "reason": reason,
                "error_summary": error_summary,
                "attempts": _attempt_count(job),
            },
            "partial_artifacts": _mark_partial_artifacts(job.metadata),
        }
        return self._terminal_failure(
            job,
            status="dead_lettered",
            error_summary=error_summary,
            event_type="job_dead_lettered",
            metadata=metadata,
        )

    def recover_stale_running_jobs(self, *, stale_after_seconds: int = 300) -> list[str]:
        now = _now()
        cutoff = now - timedelta(seconds=max(0, stale_after_seconds))
        recovered: list[str] = []
        with self.database.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(platform_jobs).where(platform_jobs.c.status == "running")
                )
                .mappings()
                .fetchall()
            )
        for row in rows:
            metadata = dict(row["metadata_json"] or {})
            heartbeat = _metadata_datetime(metadata.get("heartbeat_at"))
            updated = _aware(row["updated_at"])
            marker = heartbeat or updated
            if marker > cutoff:
                continue
            job = _platform_job(row)
            stale_exc = TimeoutError("worker heartbeat stale")
            stale_job = job.model_copy(
                update={
                    "metadata": {
                        **job.metadata,
                        "recovered_from_stale_running": True,
                        "attempts": max(int(row["attempts"] or 1), 1),
                    }
                }
            )
            decision = retry_decision(
                job_type=job.job_type,
                config_snapshot=job.config_snapshot,
                metadata=stale_job.metadata,
                attempts=max(int(row["attempts"] or 1), 1),
                exc=stale_exc,
            )
            if decision.dead_letter:
                recovered_job = self.dead_letter(stale_job, stale_exc, reason=decision.reason)
            elif decision.should_retry:
                recovered_job = self.schedule_retry(stale_job, stale_exc, decision=decision)
            else:
                recovered_job = self.fail(
                    stale_job,
                    stale_exc,
                    retry_metadata={"reason": decision.reason},
                )
            recovered.append(recovered_job.job_id)
        return recovered

    def succeed(self, job: PlatformJob, result: JobResult) -> PlatformJob:
        artifact_ids = [*result.artifact_ids]
        for artifact_path in result.artifact_paths:
            artifact_ids.append(
                self.register_artifact(
                    job,
                    artifact_path,
                    artifact_type=str(result.result.get("artifact_type") or job.job_type),
                )
            )
        now = _now()
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status="succeeded",
                    completed_at=now,
                    updated_at=now,
                    result_artifact_ids_json=artifact_ids,
                    error_summary=None,
                    result_json=result.result,
                )
            )
        self.database.write_audit(
            "job_succeeded",
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Job {job.job_id} succeeded.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"artifact_ids": artifact_ids},
        )
        metrics.observe("job_duration_seconds", _job_duration_seconds(job, now))
        log_event(
            LOGGER,
            "job_succeeded",
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
            artifact_count=len(artifact_ids),
        )
        refreshed = self.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after success.")
        return refreshed

    def fail(
        self,
        job: PlatformJob,
        exc: Exception,
        *,
        retry_metadata: dict[str, Any] | None = None,
    ) -> PlatformJob:
        now = _now()
        error_summary = redact_secrets(str(exc))[:2000]
        metadata = {
            **job.metadata,
            "retry": retry_metadata or {"reason": "not_retried"},
            "partial_artifacts": _mark_partial_artifacts(job.metadata),
        }
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status="failed",
                    completed_at=now,
                    updated_at=now,
                    error_summary=error_summary,
                    metadata_json=metadata,
                )
            )
        self.database.write_audit(
            "job_failed",
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Job {job.job_id} failed.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"error_summary": error_summary},
        )
        metrics.increment("jobs_failed_total")
        metrics.observe("job_duration_seconds", _job_duration_seconds(job, now))
        log_event(
            LOGGER,
            "job_failed",
            level=logging.ERROR,
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
            error_summary=error_summary,
        )
        refreshed = self.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after failure.")
        return refreshed

    def _terminal_failure(
        self,
        job: PlatformJob,
        *,
        status: str,
        error_summary: str,
        event_type: str,
        metadata: dict[str, Any],
    ) -> PlatformJob:
        now = _now()
        clean_error = redact_secrets(error_summary)[:2000]
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status=status,
                    completed_at=now,
                    updated_at=now,
                    error_summary=clean_error,
                    metadata_json=metadata,
                )
            )
        self.database.write_audit(
            event_type,
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Job {job.job_id} {status}.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"error_summary": clean_error},
        )
        metrics.increment("jobs_failed_total")
        refreshed = self.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after terminal failure.")
        return refreshed

    def guardrail_fail(
        self,
        job: PlatformJob,
        *,
        summary: str,
        result: dict[str, Any] | None = None,
        artifact_ids: list[str] | None = None,
    ) -> PlatformJob:
        now = _now()
        error_summary = redact_secrets(summary)[:2000]
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status="guardrail_failed",
                    completed_at=now,
                    updated_at=now,
                    error_summary=error_summary,
                    result_json=_redact_result(result or {}),
                    result_artifact_ids_json=artifact_ids or [],
                )
            )
        self.database.write_audit(
            "job_guardrail_failed",
            actor_user_id=job.requested_by_user_id,
            org_id=job.org_id,
            project_id=job.project_id,
            summary=f"Job {job.job_id} failed Codex guardrails.",
            object_type="platform_job",
            object_id=job.job_id,
            metadata={"error_summary": error_summary},
        )
        metrics.increment("jobs_failed_total")
        metrics.observe("job_duration_seconds", _job_duration_seconds(job, now))
        log_event(
            LOGGER,
            "job_guardrail_failed",
            level=logging.ERROR,
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
            error_summary=error_summary,
        )
        refreshed = self.get(job.job_id)
        if refreshed is None:
            raise PlatformDatabaseError("Job disappeared after guardrail failure.")
        return refreshed

    def register_artifact(
        self,
        job: PlatformJob,
        path: Path,
        *,
        artifact_type: str,
    ) -> str:
        start = time.perf_counter()
        resolved = path.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise PlatformDatabaseError(f"Artifact path does not exist: {path}")
        data = resolved.read_bytes()
        artifact_id = f"artifact-{uuid.uuid4().hex[:16]}"
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(artifact_records).values(
                    artifact_id=artifact_id,
                    org_id=job.org_id,
                    project_id=job.project_id,
                    run_id=None,
                    artifact_type=artifact_type,
                    path=str(resolved),
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                    provenance_json={"job_id": job.job_id, "job_type": job.job_type},
                    created_at=_now(),
                    metadata_json={},
                )
            )
        metrics.increment("artifacts_written_total")
        metrics.observe("artifact_write_duration_seconds", time.perf_counter() - start)
        log_event(
            LOGGER,
            "artifact_registered",
            job_id=job.job_id,
            job_type=job.job_type,
            org_id=job.org_id,
            project_id=job.project_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            size_bytes=len(data),
        )
        return artifact_id


def _enforce_hosted_design_policy(
    *,
    database: PlatformDatabase,
    requested_by: UserAccount,
    job_type: str,
    project_id: str | None,
    config_snapshot: dict[str, Any],
) -> None:
    if job_type in {"design_generate", "design_loop"}:
        budget = _design_generation_budget(config_snapshot)
        budget_limit = _design_budget_limit(config_snapshot)
        if budget > DESIGN_MAX_GENERATION_BUDGET:
            raise PlatformDatabaseError(
                f"Design generation budget exceeds hosted limit {DESIGN_MAX_GENERATION_BUDGET}."
            )
        if budget > DESIGN_LARGE_GENERATION_THRESHOLD and budget_limit is None:
            raise PlatformDatabaseError(
                "Large design generation jobs require an explicit budget_limit."
            )
        if budget_limit is not None and budget > budget_limit:
            raise PlatformDatabaseError("Design generation budget exceeds budget_limit.")
        if budget > DESIGN_LARGE_GENERATION_THRESHOLD and _codex_plan_requires_approval(
            config_snapshot
        ):
            raise PermissionError(
                "Codex-produced design plans require review approval before large generation jobs."
            )
    if job_type == "external_export" and _exports_generated_molecules(config_snapshot):
        if not bool(config_snapshot.get("generated_molecule_warning_acknowledged")):
            raise PermissionError(
                "Generated molecule export requires acknowledgement that records are "
                "computational hypotheses."
            )
        if not requested_by.is_admin and not has_permission(
            requested_by,
            "design:export",
            project_id=project_id,
            database=database,
        ):
            raise PermissionError("Missing permission design:export.")


def _enforce_hosted_model_policy(
    *,
    job_type: str,
    config_snapshot: dict[str, Any],
) -> None:
    model_job_types = {
        "model_training",
        "model_validation",
        "model_prediction",
        "model_dataset_build",
        "model_train",
        "model_evaluate",
        "model_predict",
        "model_calibrate",
    }
    if job_type not in model_job_types:
        return
    forbidden_flags = {
        "use_patient_data": "patient data",
        "use_clinical_data": "clinical data",
        "use_dosing_data": "dosing data",
    }
    for key, label in forbidden_flags.items():
        if bool(config_snapshot.get(key)):
            raise PermissionError(f"Predictive model jobs must not use {label}.")
    if job_type in {"model_training", "model_dataset_build", "model_train"}:
        endpoint = str(config_snapshot.get("endpoint_name") or "").strip()
        if job_type != "model_train" and not endpoint:
            raise PlatformDatabaseError("Model training requires endpoint_name.")
        if bool(config_snapshot.get("allow_endpoint_pooling")) and not str(
            config_snapshot.get("pooled_endpoint_label") or ""
        ).strip():
            raise PlatformDatabaseError(
                "Endpoint pooling requires an explicit pooled_endpoint_label."
            )
    if job_type in {
        "model_validation",
        "model_prediction",
        "model_evaluate",
        "model_predict",
        "model_calibrate",
    } and not str(
        config_snapshot.get("model_id") or ""
    ).strip():
            raise PlatformDatabaseError(f"{job_type} requires model_id.")


def _enforce_hosted_structure_policy(
    *,
    database: PlatformDatabase,
    requested_by: UserAccount,
    job_type: str,
    project_id: str | None,
    config_snapshot: dict[str, Any],
) -> None:
    structure_job_types = {
        "structure_find",
        "structure_select",
        "receptor_prepare",
        "ligand_prepare",
        "binding_site_define",
        "structure_dock",
        "pose_qc",
        "structure_assess",
        "structure_benchmark",
        "structure_design_loop",
        "structure_retrieval",
        "structure_selection",
        "receptor_preparation",
        "ligand_preparation",
        "structure_docking",
        "structure_report_card",
    }
    if job_type not in structure_job_types:
        return
    if bool(config_snapshot.get("use_patient_data")) or bool(
        config_snapshot.get("use_dosing_data")
    ):
        raise PermissionError("Structure jobs must not use patient or dosing data.")
    if _codex_structure_plan_requires_approval(config_snapshot):
        raise PermissionError("Codex-planned structure jobs require approval before execution.")
    if _has_codex_structure_approval(config_snapshot) and not requested_by.is_admin:
        if not has_permission(
            requested_by,
            "structure:approve",
            project_id=project_id,
            database=database,
        ):
            raise PermissionError("Missing permission structure:approve.")
    if job_type in {
        "structure_find",
        "structure_select",
        "structure_benchmark",
        "structure_design_loop",
        "structure_retrieval",
        "structure_selection",
        "structure_report_card",
    }:
        target_symbol = str(config_snapshot.get("target_symbol") or "").strip()
        if not target_symbol:
            raise PlatformDatabaseError(f"{job_type} requires target_symbol.")
    if job_type in {"structure_dock", "structure_docking"}:
        if not bool(config_snapshot.get("enable_docking")):
            raise PlatformDatabaseError(f"{job_type} requires enable_docking=true.")
        if not (
            bool(config_snapshot.get("structure_warning_acknowledged"))
            and bool(config_snapshot.get("docking_limitations_acknowledged"))
        ):
            raise PermissionError(
                "Docking jobs require acknowledgement that scores, poses, and "
                "structure-derived interactions are computational heuristics only."
            )
        docking_budget = _structure_docking_budget(config_snapshot)
        budget_limit = _structure_budget_limit(config_snapshot)
        if docking_budget > STRUCTURE_MAX_DOCKING_BUDGET:
            raise PlatformDatabaseError(
                f"Docking budget exceeds hosted limit {STRUCTURE_MAX_DOCKING_BUDGET}."
            )
        if docking_budget > STRUCTURE_LARGE_DOCKING_THRESHOLD and budget_limit is None:
            raise PlatformDatabaseError("Large docking jobs require an explicit budget_limit.")
        if budget_limit is not None and docking_budget > budget_limit:
            raise PlatformDatabaseError("Docking budget exceeds budget_limit.")
    if bool(config_snapshot.get("codex_may_create_pose")) or bool(
        config_snapshot.get("codex_may_invent_interactions")
    ):
        raise PermissionError(
            "Codex may plan or summarize structure workflows but must not invent poses, "
            "scores, binding sites, or interactions."
        )


def _enforce_hosted_portfolio_policy(
    *,
    database: PlatformDatabase,
    requested_by: UserAccount,
    job_type: str,
    project_id: str | None,
    config_snapshot: dict[str, Any],
) -> None:
    if job_type not in PORTFOLIO_JOB_TYPES:
        return
    export_requested = any(
        bool(config_snapshot.get(key))
        for key in (
            "external_export",
            "external_write",
            "export_selected_portfolio",
            "selected_portfolio_export",
        )
    )
    if not export_requested:
        return
    explicit_permission = bool(config_snapshot.get("explicit_export_permission"))
    allowed = requested_by.is_admin or has_permission(
        requested_by,
        "portfolio:export",
        project_id=project_id,
        database=database,
    )
    if explicit_permission and allowed:
        return
    database.write_audit(
        "portfolio_external_export_denied",
        actor_user_id=requested_by.user_id,
        project_id=project_id,
        summary="Denied external export of selected portfolio.",
        object_type="portfolio_job",
        object_id=job_type,
        metadata={
            "job_type": job_type,
            "explicit_export_permission": explicit_permission,
        },
    )
    raise PermissionError(
        "External portfolio exports require portfolio:export and explicit permission."
    )


def _enforce_hosted_graph_policy(
    *,
    database: PlatformDatabase,
    requested_by: UserAccount,
    job_type: str,
    project_id: str | None,
    config_snapshot: dict[str, Any],
) -> None:
    if job_type not in GRAPH_JOB_TYPES:
        return
    permission = JOB_PERMISSION[job_type]
    project_ids = _graph_scope_project_ids(project_id, config_snapshot)
    for scoped_project_id in project_ids:
        if requested_by.is_admin:
            continue
        if not has_permission(
            requested_by,
            "graph:read",
            project_id=scoped_project_id,
            database=database,
        ):
            raise PermissionError(
                f"Missing permission graph:read for graph project {scoped_project_id}."
            )
        if not has_permission(
            requested_by,
            permission,
            project_id=scoped_project_id,
            database=database,
        ):
            raise PermissionError(
                f"Missing permission {permission} for graph project {scoped_project_id}."
            )
    if job_type == "graph_recommendation":
        config_snapshot["graph_recommendations_are_advisory"] = True
        config_snapshot["automatic_decisions_disabled"] = True
    if bool(config_snapshot.get("graph_inference_creates_evidence")) or bool(
        config_snapshot.get("graph_inference_creates_assay_results")
    ):
        raise PermissionError(
            "Graph inference may not create EvidenceItem records or assay results."
        )
    if any(
        key in config_snapshot
        for key in ("graph_path", "artifact_paths", "artifact_dir", "from_project")
    ):
        raise PlatformDatabaseError(
            "Hosted graph jobs must use registered project artifacts, not arbitrary file paths."
        )
    for key in ("run_id", "from_run"):
        if key in config_snapshot and _looks_like_path(config_snapshot.get(key)):
            raise PlatformDatabaseError("Hosted graph run references must be run IDs, not paths.")
    if job_type == "graph_export":
        config_snapshot["graph_export_permission_checked"] = True


def _enforce_hosted_hypothesis_policy(
    *,
    database: PlatformDatabase,
    requested_by: UserAccount,
    job_type: str,
    project_id: str | None,
    config_snapshot: dict[str, Any],
) -> None:
    if job_type not in HYPOTHESIS_JOB_TYPES:
        return
    if not requested_by.is_admin and project_id is not None and not has_permission(
        requested_by,
        "hypothesis:read",
        project_id=project_id,
        database=database,
    ):
        raise PermissionError(f"Missing permission hypothesis:read for project {project_id}.")
    if bool(config_snapshot.get("use_codex_hypothesis_drafting")):
        if config_snapshot.get("strict_hypothesis_guardrails") is False:
            raise PermissionError(
                "Codex-drafted hypotheses require strict deterministic validation."
            )
        config_snapshot["deterministic_hypothesis_validation_required"] = True
        config_snapshot["codex_may_only_draft_wording"] = True
    if bool(config_snapshot.get("codex_output_validated") is False):
        raise PermissionError("Codex-drafted hypotheses require deterministic validation.")
    if bool(config_snapshot.get("hypothesis_as_evidence")) or bool(
        config_snapshot.get("review_decision_as_evidence")
    ):
        raise PermissionError("Hypotheses and review decisions must not be treated as evidence.")
    if bool(config_snapshot.get("follow_up_planning")) and _generated_hypothesis_context(
        config_snapshot
    ) and not bool(config_snapshot.get("human_review_approved")):
        raise PermissionError(
            "Generated-molecule hypotheses require human review before follow-up planning."
        )
    if job_type == "hypothesis_review":
        decision = str(config_snapshot.get("decision") or "")
        reviewer_id = str(config_snapshot.get("reviewer_id") or requested_by.user_id)
        if decision == "accept_for_planning" and _is_codex_actor(reviewer_id):
            raise PermissionError("Codex cannot approve hypotheses.")
        if (
            decision == "accept_for_planning"
            and _generated_hypothesis_context(config_snapshot)
            and not bool(config_snapshot.get("human_review_approved"))
        ):
            raise PermissionError(
                "Generated-molecule hypotheses require explicit human approval."
            )
    if job_type == "hypothesis_report":
        config_snapshot["hypothesis_report_disclaimers_required"] = True


def _generated_hypothesis_context(config_snapshot: dict[str, Any]) -> bool:
    hypothesis_type = str(config_snapshot.get("hypothesis_type") or "")
    if hypothesis_type == "generated_molecule":
        return True
    if bool(config_snapshot.get("generated_molecule")):
        return True
    entity_ids = config_snapshot.get("generated_molecule_entity_ids")
    return isinstance(entity_ids, list) and bool(entity_ids)


def _enforce_hosted_campaign_policy(
    *,
    database: PlatformDatabase,
    requested_by: UserAccount,
    job_type: str,
    project_id: str | None,
    config_snapshot: dict[str, Any],
) -> None:
    if job_type not in CAMPAIGN_JOB_TYPES:
        return
    if not requested_by.is_admin and project_id is not None and not has_permission(
        requested_by,
        "campaign:read",
        project_id=project_id,
        database=database,
    ):
        raise PermissionError(f"Missing permission campaign:read for project {project_id}.")
    if _campaign_approval_requested(config_snapshot) and not _can_approve_campaign(
        database=database,
        requested_by=requested_by,
        project_id=project_id,
    ):
        database.write_audit(
            "campaign_approval_denied",
            actor_user_id=requested_by.user_id,
            project_id=project_id,
            summary="Denied campaign or stage gate approval.",
            object_type="campaign_job",
            object_id=job_type,
            metadata={"job_type": job_type, "permission": "campaign:approve"},
        )
        raise PermissionError("Campaign approval requires campaign:approve.")
    if _generated_campaign_follow_up(config_snapshot) and not _generated_review_gate_present(
        config_snapshot
    ):
        raise PermissionError(
            "Generated molecule follow-up requires a generated molecule review gate."
        )
    if _campaign_config_contains_protocol_text(config_snapshot):
        raise PermissionError("Campaign work packages are planning objects, not protocols.")
    config_snapshot["campaign_plans_are_research_management_guidance"] = True
    config_snapshot["campaign_work_packages_are_not_protocols"] = True
    if job_type == "campaign_plan":
        config_snapshot["deterministic_campaign_plan_required"] = True
        config_snapshot["human_approval_required_by_default"] = True
    if job_type == "campaign_memo":
        if bool(config_snapshot.get("codex_memo_overwrites_plan")):
            raise PermissionError(
                "Codex campaign memos must remain separate from deterministic plans."
            )
        if bool(config_snapshot.get("use_codex")):
            config_snapshot["codex_memo_label"] = "assistant_output"
            config_snapshot["codex_memo_separate_from_deterministic_plan"] = True
            config_snapshot["codex_may_only_draft_campaign_summary"] = True


def _enforce_hosted_evaluation_policy(
    *,
    job_type: str,
    config_snapshot: dict[str, Any],
) -> None:
    if job_type not in EVALUATION_JOB_TYPES:
        return
    if job_type == "eval_prospective_freeze" and (
        bool(config_snapshot.get("edit_existing"))
        or bool(config_snapshot.get("update_existing"))
        or bool(config_snapshot.get("frozen_prediction_set_id"))
    ):
        raise PlatformDatabaseError("Prospective freeze cannot be edited after creation.")
    config_snapshot["evaluation_reports_are_not_evidence"] = True
    config_snapshot["evaluation_is_not_clinical_validation"] = True
    config_snapshot["no_efficacy_safety_activity_claims"] = True


def _campaign_approval_requested(config_snapshot: dict[str, Any]) -> bool:
    return any(
        bool(config_snapshot.get(key))
        for key in (
            "campaign_approval",
            "approve_campaign",
            "stage_gate_approval",
            "approve_stage_gate",
        )
    )


def _can_approve_campaign(
    *,
    database: PlatformDatabase,
    requested_by: UserAccount,
    project_id: str | None,
) -> bool:
    return requested_by.is_admin or has_permission(
        requested_by,
        "campaign:approve",
        project_id=project_id,
        database=database,
    )


def _generated_campaign_follow_up(config_snapshot: dict[str, Any]) -> bool:
    if any(
        bool(config_snapshot.get(key))
        for key in (
            "generated_molecule_followup",
            "generated_molecule_follow_up",
            "generated_follow_up",
        )
    ):
        return True
    return bool(config_snapshot.get("follow_up_planning")) and bool(
        config_snapshot.get("generated_molecule")
    )


def _generated_review_gate_present(config_snapshot: dict[str, Any]) -> bool:
    return any(
        bool(config_snapshot.get(key))
        for key in (
            "generated_review_gate_present",
            "generated_molecule_review_gate_present",
            "generated_review_gate_approved",
            "generated_molecule_review_gate_approved",
        )
    )


def _campaign_config_contains_protocol_text(config_snapshot: dict[str, Any]) -> bool:
    for key, value in config_snapshot.items():
        if "protocol" in key.lower() and bool(value):
            return True
        if isinstance(value, str) and contains_procedural_lab_detail(value):
            return True
        if isinstance(value, list) and any(
            isinstance(item, str) and contains_procedural_lab_detail(item) for item in value
        ):
            return True
        if isinstance(value, dict) and _campaign_config_contains_protocol_text(value):
            return True
    return False


def _is_codex_actor(actor: str) -> bool:
    normalized = actor.lower().replace("_", "-").replace(" ", "-")
    return "codex" in normalized


def _graph_scope_project_ids(
    project_id: str | None,
    config_snapshot: dict[str, Any],
) -> list[str]:
    raw_values: list[Any] = []
    for key in (
        "included_project_ids",
        "project_ids",
        "include_project_ids",
        "cross_program_project_ids",
    ):
        value = config_snapshot.get(key)
        if isinstance(value, list):
            raw_values.extend(value)
        elif value is not None:
            raw_values.append(value)
    if project_id is not None:
        raw_values.append(project_id)
    seen: set[str] = set()
    project_ids: list[str] = []
    for value in raw_values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        project_ids.append(text)
    return project_ids


def _looks_like_path(value: Any) -> bool:
    text = str(value or "")
    return "/" in text or "\\" in text or text.startswith(".")


def _structure_docking_budget(config_snapshot: dict[str, Any]) -> int:
    for key in ("docking_budget", "max_ligands", "ligand_count", "max_docked_ligands"):
        if config_snapshot.get(key) is not None:
            try:
                return max(int(config_snapshot[key]), 0)
            except (TypeError, ValueError):
                raise PlatformDatabaseError("Docking budget must be an integer.") from None
    return 0


def _structure_budget_limit(config_snapshot: dict[str, Any]) -> int | None:
    value = config_snapshot.get("budget_limit", config_snapshot.get("docking_budget_limit"))
    if value is None:
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        raise PlatformDatabaseError("Docking budget_limit must be an integer.") from None


def _codex_structure_plan_requires_approval(config_snapshot: dict[str, Any]) -> bool:
    codex_planned = bool(
        config_snapshot.get("use_codex_planner")
        or config_snapshot.get("codex_task_result_id")
        or str(config_snapshot.get("structure_plan_source", "")).lower() == "codex"
    )
    return codex_planned and not _has_codex_structure_approval(config_snapshot)


def _has_codex_structure_approval(config_snapshot: dict[str, Any]) -> bool:
    return bool(
        config_snapshot.get("structure_plan_approved")
        or config_snapshot.get("plan_approved")
        or config_snapshot.get("codex_plan_approved")
        or config_snapshot.get("approval_id")
    )


def _design_generation_budget(config_snapshot: dict[str, Any]) -> int:
    value = (
        config_snapshot.get("budget")
        if "budget" in config_snapshot
        else config_snapshot.get("generation_budget")
    )
    if value is None:
        value = config_snapshot.get("max_retained", 0)
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        raise PlatformDatabaseError("Design generation budget must be an integer.") from None


def _design_budget_limit(config_snapshot: dict[str, Any]) -> int | None:
    value = config_snapshot.get("budget_limit", config_snapshot.get("max_budget"))
    if value is None:
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        raise PlatformDatabaseError("Design budget_limit must be an integer.") from None


def _codex_plan_requires_approval(config_snapshot: dict[str, Any]) -> bool:
    codex_planned = bool(
        config_snapshot.get("use_codex_planner")
        or config_snapshot.get("codex_task_result_id")
        or str(config_snapshot.get("design_plan_source", "")).lower() == "codex"
    )
    return codex_planned and not bool(
        config_snapshot.get("plan_approved")
        or config_snapshot.get("codex_plan_approved")
        or config_snapshot.get("approval_id")
    )


def _exports_generated_molecules(config_snapshot: dict[str, Any]) -> bool:
    export_type = str(config_snapshot.get("export_type") or config_snapshot.get("artifact_type"))
    return bool(
        config_snapshot.get("contains_generated_molecules")
        or config_snapshot.get("generated_molecules")
        or export_type in {
            "generated_candidates",
            "generated_candidates_v2",
            "design_generated_candidates",
        }
    )


def _retry_due(metadata: dict[str, Any], *, now: datetime) -> bool:
    raw_next_retry = metadata.get("next_retry_at")
    if raw_next_retry is None:
        return True
    next_retry = _metadata_datetime(raw_next_retry)
    return next_retry is None or next_retry <= now


def _metadata_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _aware(parsed)


def _attempt_count(job: PlatformJob) -> int:
    try:
        return int(job.metadata.get("attempts") or 0)
    except (TypeError, ValueError):
        return 0


def _job_timed_out(job: PlatformJob) -> bool:
    timeout = job.config_snapshot.get("timeout_seconds")
    if timeout is None:
        return False
    try:
        timeout_seconds = float(timeout)
    except (TypeError, ValueError):
        return False
    if timeout_seconds <= 0:
        return True
    started_at = job.started_at or datetime.now(UTC)
    return (datetime.now(UTC) - started_at).total_seconds() > timeout_seconds


def _mark_partial_artifacts(metadata: dict[str, Any]) -> list[dict[str, str]]:
    raw_paths = metadata.get("partial_artifact_paths") or []
    if not isinstance(raw_paths, list):
        return []
    return [
        {"path": redact_secrets(str(path)), "status": "partial_unregistered"}
        for path in raw_paths
    ]


class RedisJobQueueAdapter:
    """Placeholder for future Redis/RQ/Celery queue integration.

    V1.0 intentionally uses the SQL-backed queue so local and hosted MVP
    deployments do not require Redis.
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("Redis/RQ/Celery adapters are planned after the V1.0 MVP.")


def enqueue_platform_job(
    database: PlatformDatabase,
    job: PlatformJob,
    *,
    requested_by: UserAccount | None = None,
) -> PlatformJob:
    user = requested_by or database.get_user(job.requested_by_user_id)
    if user is None:
        raise PlatformDatabaseError("Requesting user does not exist.")
    return PlatformJobQueue(database).enqueue(
        job_type=job.job_type,
        requested_by=user,
        org_id=job.org_id,
        project_id=job.project_id,
        config_snapshot=job.config_snapshot,
        priority=job.priority,
        metadata=job.metadata,
    )


def _platform_job(row: Any) -> PlatformJob:
    return PlatformJob(
        job_id=str(row["job_id"]),
        org_id=str(row["org_id"]),
        project_id=row["project_id"],
        requested_by_user_id=str(row["requested_by_user_id"]),
        job_type=str(row["job_type"]),  # type: ignore[arg-type]
        status=str(row["status"]),  # type: ignore[arg-type]
        priority=str(row["priority"]),  # type: ignore[arg-type]
        config_snapshot=dict(row["config_snapshot_json"] or {}),
        created_at=_aware(row["created_at"]),
        started_at=_aware(row["started_at"]) if row["started_at"] else None,
        completed_at=_aware(row["completed_at"]) if row["completed_at"] else None,
        result_artifact_ids=list(row["result_artifact_ids_json"] or []),
        error_summary=row["error_summary"],
        metadata=dict(row["metadata_json"] or {}),
    )


def _redact_result(value: dict[str, Any]) -> dict[str, Any]:
    return _redact_json(value)


def _redact_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    return _redact_json(value, redact_sensitive_keys=True)


def _redact_json(value: Any, *, redact_sensitive_keys: bool = False) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if redact_sensitive_keys and _is_sensitive_snapshot_key(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = _redact_json(
                    item,
                    redact_sensitive_keys=redact_sensitive_keys,
                )
        return redacted
    if isinstance(value, list):
        return [_redact_json(item, redact_sensitive_keys=redact_sensitive_keys) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def _is_sensitive_snapshot_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(
        marker in lowered
        for marker in (
            "api_key",
            "apikey",
            "authorization",
            "credential",
            "password",
            "secret",
            "token",
        )
    )


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _job_duration_seconds(job: PlatformJob, completed_at: datetime) -> float:
    started_at = job.started_at or job.created_at
    return max((completed_at - started_at).total_seconds(), 0.0)


__all__ = [
    "JOB_PERMISSION",
    "QUEUE_JOB_TYPES",
    "JobResult",
    "PlatformJob",
    "PlatformJobQueue",
    "RedisJobQueueAdapter",
    "enqueue_platform_job",
]
