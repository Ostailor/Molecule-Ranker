from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, update

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.platform.database import artifact_records, platform_jobs
from molecule_ranker.platform.db import PlatformDatabase, PlatformDatabaseError
from molecule_ranker.platform.observability import log_event, metrics
from molecule_ranker.platform.rbac import has_permission
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
            metadata=metadata or {},
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
    ) -> list[PlatformJob]:
        statement = select(platform_jobs)
        if status is not None:
            statement = statement.where(platform_jobs.c.status == status)
        if project_id is not None:
            statement = statement.where(platform_jobs.c.project_id == project_id)
        statement = statement.order_by(platform_jobs.c.created_at.desc()).limit(limit)
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
        statement = select(platform_jobs).where(platform_jobs.c.status == "queued")
        if job_types is not None:
            statement = statement.where(platform_jobs.c.job_type.in_(job_types))
        with self.database.engine.begin() as connection:
            rows = connection.execute(statement).mappings().fetchall()
            if not rows:
                return None
            row = min(
                rows,
                key=lambda item: (
                    PRIORITY_RANK.get(str(item["priority"]), 1),
                    _aware(item["created_at"]),
                ),
            )
            now = _now()
            result = connection.execute(
                update(platform_jobs)
                .where(
                    (platform_jobs.c.job_id == row["job_id"])
                    & (platform_jobs.c.status == "queued")
                )
                .values(
                    status="running",
                    started_at=now,
                    updated_at=now,
                    attempts=int(row["attempts"] or 0) + 1,
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

    def cancel(self, job_id: str, *, actor_user_id: str) -> PlatformJob:
        job = self.get(job_id)
        if job is None:
            raise PlatformDatabaseError("Job not found.")
        now = _now()
        if job.status == "queued":
            with self.database.engine.begin() as connection:
                connection.execute(
                    update(platform_jobs)
                    .where(
                        (platform_jobs.c.job_id == job_id)
                        & (platform_jobs.c.status == "queued")
                    )
                    .values(status="cancelled", completed_at=now, updated_at=now)
                )
        elif job.status == "running":
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

    def fail(self, job: PlatformJob, exc: Exception) -> PlatformJob:
        now = _now()
        error_summary = redact_secrets(str(exc))[:2000]
        with self.database.engine.begin() as connection:
            connection.execute(
                update(platform_jobs)
                .where(platform_jobs.c.job_id == job.job_id)
                .values(
                    status="failed",
                    completed_at=now,
                    updated_at=now,
                    error_summary=error_summary,
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


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


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
