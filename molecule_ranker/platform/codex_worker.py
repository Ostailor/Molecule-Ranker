from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import insert, update

from molecule_ranker.codex.provider import (
    CodexArtifact,
    CodexCLIProvider,
    CodexProviderConfig,
    CodexRequest,
    CodexResponse,
)
from molecule_ranker.codex_backbone.guardrails import (
    MODEL_CODEX_TASK_TYPES,
    STRUCTURE_CODEX_TASK_TYPES,
    collect_allowed_refs_from_artifacts,
    has_blocking_task_guardrail,
    is_secret_path,
    output_guardrail_warnings,
    redact_secrets,
)
from molecule_ranker.evaluation.codex_explanations import EVALUATION_CODEX_TASK_TYPES
from molecule_ranker.platform.database import codex_worker_jobs
from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.jobs import JobResult, PlatformJobQueue
from molecule_ranker.platform.observability import log_event, metrics
from molecule_ranker.platform.rbac import has_permission
from molecule_ranker.platform.schemas import PlatformJob
from molecule_ranker.platform.settings import PlatformSettings
from molecule_ranker.workspace.schemas import ArtifactRecord, ProjectWorkspace
from molecule_ranker.workspace.store import ProjectWorkspaceStore

SAFE_CODEX_TASK_TYPES = {
    "summarize_project",
    "suggest_next_project_actions",
    "candidate_dossier",
    "project_dashboard",
    "review_export",
    "summarize_model_card",
    "explain_model_metrics",
    "explain_prediction_batch",
    "suggest_feature_debugging",
    "draft_model_limitations",
    "explain_active_design_model_influence",
    "suggest_structure_selection_review_questions",
    "summarize_structure_assessment",
    "explain_pose_qc_failure",
    "draft_structure_report_summary",
    "plan_followup_structure_workflow",
    *EVALUATION_CODEX_TASK_TYPES,
}
CACHE_PATH_MARKERS = {".cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
DEFAULT_FORBIDDEN_COMMANDS = [
    "cat .env",
    "printenv",
    "env",
    "curl",
    "wget",
    "ssh",
    "scp",
    "sudo",
    "rm -rf",
    "git push",
    "git reset --hard",
]
LOGGER = logging.getLogger("molecule_ranker.codex_worker")


class CodexWorkerConfig(BaseModel):
    enable_codex_worker: bool = True
    codex_worker_concurrency: int = Field(default=1, ge=1)
    codex_job_timeout_seconds: int = Field(default=300, gt=0)
    codex_artifact_context_max_bytes: int = Field(default=1_000_000, ge=0)
    codex_worker_workspace_root: Path | None = None
    codex_worker_allow_engineering_tasks: bool = False
    codex_worker_allow_runtime_tasks: bool = False
    store_transcripts: bool = True

    @classmethod
    def from_settings(cls, settings: PlatformSettings) -> CodexWorkerConfig:
        return cls(
            enable_codex_worker=settings.enable_codex_worker or settings.codex_worker_enabled,
            codex_worker_concurrency=settings.codex_worker_concurrency,
            codex_job_timeout_seconds=settings.codex_job_timeout_seconds,
            codex_artifact_context_max_bytes=settings.codex_artifact_context_max_bytes,
            codex_worker_workspace_root=settings.codex_worker_workspace_root,
            codex_worker_allow_engineering_tasks=settings.codex_worker_allow_engineering_tasks,
            codex_worker_allow_runtime_tasks=settings.codex_worker_allow_runtime_tasks,
        )


class CodexGuardrailError(RuntimeError):
    def __init__(self, warnings: list[str]) -> None:
        self.warnings = warnings
        super().__init__("; ".join(warnings))


class ScopedArtifact(BaseModel):
    source_artifact_id: str
    source_artifact_type: str
    isolated_path: str
    sha256: str
    size_bytes: int


class ScopedArtifactContext(BaseModel):
    included: list[ScopedArtifact]
    skipped: list[dict[str, str]]
    total_size_bytes: int


class CodexWorker:
    """Hosted-mode Codex worker with scoped artifact and subprocess boundaries."""

    def __init__(
        self,
        *,
        database: PlatformDatabase,
        workspace_store: ProjectWorkspaceStore,
        config: CodexWorkerConfig | None = None,
        settings: PlatformSettings | None = None,
        provider: Any | None = None,
        codex_config: Any | None = None,
    ) -> None:
        if config is None and codex_config is not None:
            config = CodexWorkerConfig(
                enable_codex_worker=True,
                codex_job_timeout_seconds=int(
                    getattr(codex_config, "codex_timeout_seconds", 300)
                ),
                codex_worker_workspace_root=getattr(codex_config, "codex_working_dir", None),
            )
        self.database = database
        self.workspace_store = workspace_store
        self.config = config or (
            CodexWorkerConfig.from_settings(settings) if settings else CodexWorkerConfig()
        )
        self.queue = PlatformJobQueue(database)
        self.workspace_root = (
            self.config.codex_worker_workspace_root
            or database.root_dir / ".molecule-ranker" / "codex-worker"
        ).resolve()
        self.provider = provider

    def run_next(self) -> PlatformJob | None:
        job = self.queue.claim_next(job_types={"codex_task"})
        if job is None:
            return None
        return self.run_job(job)

    def run_job(self, job: PlatformJob) -> PlatformJob:
        metrics.increment("codex_tasks_total")
        transcript_artifact_id: str | None = None
        codex_worker_job_id = f"codex-job-{uuid.uuid4().hex[:16]}"
        task_type = str(job.config_snapshot.get("task_type") or "summarize_project")
        created_at = _now()
        log_event(
            LOGGER,
            "codex_task_started",
            job_id=job.job_id,
            org_id=job.org_id,
            project_id=job.project_id,
            requested_by_user_id=job.requested_by_user_id,
            task_type=task_type,
        )

        try:
            self._check_worker_enabled()
            self._check_job_authorization(job)
            self._check_task_type(task_type)
            work_dir = self._isolated_work_dir(job)
            request, context = self._build_request(job, task_type=task_type, work_dir=work_dir)
            self._insert_codex_worker_job(
                codex_worker_job_id,
                job=job,
                task_type=task_type,
                status="running",
                guardrail_status="passed_pre",
                allowed_artifact_ids=[artifact.source_artifact_id for artifact in context.included],
                created_at=created_at,
                metadata={"skipped_artifacts": context.skipped},
            )
            response = self._provider_for(work_dir).invoke(request)
            transcript_artifact_id = self._store_transcript(
                job,
                task_type=task_type,
                work_dir=work_dir,
                request=request,
                response=response,
                context=context,
            )
            warnings = self._post_guardrail_warnings(task_type, response, context)
            if warnings:
                metrics.increment("codex_guardrail_failures_total")
                result = self._job_result(
                    task_type=task_type,
                    response=response,
                    context=context,
                    guardrail_warnings=warnings,
                    transcript_artifact_id=transcript_artifact_id,
                )
                self._complete_codex_worker_job(
                    codex_worker_job_id,
                    status="guardrail_failed",
                    guardrail_status="failed_post",
                    transcript_artifact_id=transcript_artifact_id,
                    metadata={"guardrail_warnings": warnings},
                )
                return self.queue.guardrail_fail(
                    job,
                    summary="; ".join(warnings),
                    result=result,
                    artifact_ids=_ids(transcript_artifact_id),
                )
            if response.status not in {"ok", "dry_run"}:
                raise RuntimeError(
                    redact_secrets(response.stderr or f"Codex returned status {response.status}.")
                )
            result = self._job_result(
                task_type=task_type,
                response=response,
                context=context,
                guardrail_warnings=[],
                transcript_artifact_id=transcript_artifact_id,
            )
            self._complete_codex_worker_job(
                codex_worker_job_id,
                status="succeeded",
                guardrail_status="passed",
                transcript_artifact_id=transcript_artifact_id,
                metadata={"codex_status": response.status},
            )
            return self.queue.succeed(
                job,
                result=JobResult(result=result, artifact_ids=_ids(transcript_artifact_id)),
            )
        except CodexGuardrailError as exc:
            metrics.increment("codex_guardrail_failures_total")
            self._insert_or_complete_guardrail_failure(
                codex_worker_job_id,
                job=job,
                task_type=task_type,
                created_at=created_at,
                transcript_artifact_id=transcript_artifact_id,
                warnings=exc.warnings,
            )
            return self.queue.guardrail_fail(
                job,
                summary=str(exc),
                result={"task_type": task_type, "guardrail_warnings": exc.warnings},
                artifact_ids=_ids(transcript_artifact_id),
            )
        except Exception as exc:
            self._complete_codex_worker_job(
                codex_worker_job_id,
                status="failed",
                guardrail_status="unknown",
                transcript_artifact_id=transcript_artifact_id,
                metadata={"error_summary": redact_secrets(str(exc))[:500]},
                tolerate_missing=True,
            )
            return self.queue.fail(job, exc)

    def _check_worker_enabled(self) -> None:
        if not self.config.enable_codex_worker:
            raise CodexGuardrailError(["Codex worker is disabled by platform configuration."])

    def _check_job_authorization(self, job: PlatformJob) -> None:
        user = self.database.get_user(job.requested_by_user_id)
        if user is None or not user.is_active:
            raise PermissionError("Requesting user is no longer active.")
        if not has_permission(
            user,
            "codex:run",
            org_id=job.org_id,
            project_id=job.project_id,
            database=self.database,
        ):
            raise PermissionError("Requesting user no longer has codex:run.")

    def _check_task_type(self, task_type: str) -> None:
        if task_type in SAFE_CODEX_TASK_TYPES:
            return
        if (
            task_type.startswith("engineering:")
            and self.config.codex_worker_allow_engineering_tasks
        ):
            return
        if task_type.startswith("runtime:") and self.config.codex_worker_allow_runtime_tasks:
            return
        raise CodexGuardrailError([f"Codex task type is not allowlisted: {task_type}"])

    def _isolated_work_dir(self, job: PlatformJob) -> Path:
        work_dir = (self.workspace_root / job.job_id).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        if self.workspace_root not in work_dir.parents and work_dir != self.workspace_root:
            raise CodexGuardrailError(["Codex worker workspace path escaped its root."])
        return work_dir

    def _build_request(
        self,
        job: PlatformJob,
        *,
        task_type: str,
        work_dir: Path,
    ) -> tuple[CodexRequest, ScopedArtifactContext]:
        workspace = self.workspace_store.load()
        context = self._scoped_artifact_context(
            workspace,
            requested_artifact_ids=_requested_artifact_ids(job.config_snapshot),
            work_dir=work_dir,
        )
        if not context.included:
            raise CodexGuardrailError(["No permitted artifacts are available for this Codex job."])
        artifact_paths = [artifact.isolated_path for artifact in context.included]
        allowed_refs, citation_ids = collect_allowed_refs_from_artifacts(artifact_paths)
        task_text = f"{task_type} for molecule-ranker project artifacts"
        prompt_sections: dict[str, Any] = {
            "research_use_only": (
                "Internal research platform output. Do not provide medical advice, "
                "synthesis instructions, lab protocols, dosing, or treatment guidance."
            ),
            "scientific_grounding": [
                "Use only the included registered artifacts as context.",
                "Do not invent evidence, citations, assay results, molecules, or scores.",
                "Clearly label Codex output as a generated summary, not evidence.",
            ],
            "scope": {
                "org_id": job.org_id,
                "project_id": job.project_id,
                "workspace_id": workspace.workspace_id,
                "job_id": job.job_id,
            },
            "artifact_context": {
                "included": [artifact.model_dump(mode="json") for artifact in context.included],
                "skipped": context.skipped,
                "total_size_bytes": context.total_size_bytes,
                "allowed_reference_count": len(allowed_refs),
                "allowed_citation_count": len(citation_ids),
            },
            "allowed_commands": [],
            "forbidden_commands": DEFAULT_FORBIDDEN_COMMANDS,
        }
        if task_type in MODEL_CODEX_TASK_TYPES:
            prompt_sections["predictive_model_boundaries"] = [
                "Codex is limited to model artifact summarization and debugging.",
                "Do not invent metrics, predictions, assay results, or model-card content.",
                "Do not change model cards, approve models, create EvidenceItem records, or "
                "create AssayResult records.",
                "Do not recommend clinical use or claim activity, safety, efficacy, binding, "
                "treatment, or cure.",
                "Cite model_id, dataset_id, training_run_id, evaluation_id, and "
                "prediction_batch_artifact_id.",
            ]
            expected_schema = {
                "type": "object",
                "required": [
                    "status",
                    "summary",
                    "limitations",
                    "model_id",
                    "dataset_id",
                    "training_run_id",
                    "evaluation_id",
                    "prediction_batch_artifact_id",
                ],
                "properties": {
                    "status": {"type": "string"},
                    "summary": {"type": "string"},
                    "limitations": {"type": "array"},
                    "model_id": {"type": "string"},
                    "dataset_id": {"type": "string"},
                    "training_run_id": {"type": "string"},
                    "evaluation_id": {"type": "string"},
                    "prediction_batch_artifact_id": {"type": "string"},
                },
            }
        elif task_type in STRUCTURE_CODEX_TASK_TYPES:
            prompt_sections["structure_workflow_boundaries"] = [
                "Codex is limited to planning, review-question generation, and summarization.",
                "Do not invent structures, binding sites, residues, docking scores, poses, "
                "or protein-ligand interactions.",
                "Do not claim binding, activity, safety, efficacy, inhibition, activation, "
                "treatment, or cure from structure artifacts.",
                "Do not generate lab protocols, dosing guidance, or synthesis instructions.",
                "State that docking scores, poses, and interactions are computational "
                "annotations only, not experimental evidence.",
                "Cite structure_id, selection_id, receptor_prep_id, docking_run_id, pose_id, "
                "interaction_profile_id, and artifact_ids.",
            ]
            expected_schema = {
                "type": "object",
                "required": [
                    "status",
                    "summary",
                    "limitations",
                    "structure_id",
                    "selection_id",
                    "receptor_prep_id",
                    "docking_run_id",
                    "pose_id",
                    "interaction_profile_id",
                    "artifact_ids",
                ],
                "properties": {
                    "status": {"type": "string"},
                    "summary": {"type": "string"},
                    "limitations": {"type": "array"},
                    "structure_id": {"type": "string"},
                    "selection_id": {"type": "string"},
                    "receptor_prep_id": {"type": "string"},
                    "docking_run_id": {"type": "string"},
                    "pose_id": {"type": "string"},
                    "interaction_profile_id": {"type": "string"},
                    "artifact_ids": {"type": "array"},
                },
            }
        elif task_type in EVALUATION_CODEX_TASK_TYPES:
            prompt_sections["evaluation_boundaries"] = [
                "Codex is limited to evaluation explanation.",
                "Do not invent metrics, outcomes, labels, benchmark results, assay results, "
                "or conclusions.",
                "Do not alter benchmark results, hide guardrail failures, claim clinical "
                "validation, or create evidence.",
                "Benchmark results are evaluation artifacts, not biomedical evidence.",
                "Prospective validation analytics are not clinical validation.",
                "Cite evaluation_id, task_id, dataset_id, split_id, metric IDs, and artifact IDs.",
            ]
            expected_schema = {
                "type": "object",
                "required": [
                    "status",
                    "evaluation_id",
                    "task_id",
                    "dataset_id",
                    "split_id",
                    "metric_ids",
                    "artifact_ids",
                ],
                "properties": {
                    "status": {"type": "string"},
                    "summary": {"type": "string"},
                    "evaluation_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "dataset_id": {"type": "string"},
                    "split_id": {"type": "string"},
                    "metric_ids": {"type": "array"},
                    "artifact_ids": {"type": "array"},
                    "limitations": {"type": "array"},
                },
            }
        else:
            expected_schema = {
                "type": "object",
                "required": ["status", "summary", "limitations"],
                "properties": {
                    "status": {"type": "string"},
                    "summary": {"type": "string"},
                    "limitations": {"type": "array"},
                },
            }
        request = CodexRequest(
            task=task_text,
            prompt_sections=prompt_sections,
            artifacts=[
                CodexArtifact(
                    artifact_id=artifact.source_artifact_id,
                    path=artifact.isolated_path,
                    artifact_type=artifact.source_artifact_type,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                )
                for artifact in context.included
            ],
            expected_json_schema=expected_schema,
            output_format="json",
            metadata={"job_id": job.job_id, "task_type": task_type},
        )
        warnings = _request_guardrail_warnings(request)
        if has_blocking_task_guardrail(warnings):
            raise CodexGuardrailError(warnings)
        return request, context

    def _scoped_artifact_context(
        self,
        workspace: ProjectWorkspace,
        *,
        requested_artifact_ids: set[str] | None,
        work_dir: Path,
    ) -> ScopedArtifactContext:
        artifact_dir = work_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        included: list[ScopedArtifact] = []
        skipped: list[dict[str, str]] = []
        total_size = 0
        workspace_root = Path(workspace.root_dir).resolve()
        max_bytes = self.config.codex_artifact_context_max_bytes
        for artifact in workspace.artifacts:
            if (
                requested_artifact_ids is not None
                and artifact.artifact_id not in requested_artifact_ids
            ):
                continue
            source_path = Path(artifact.path).resolve()
            reason = _artifact_skip_reason(source_path, workspace_root)
            if reason:
                skipped.append({"artifact_id": artifact.artifact_id, "reason": reason})
                continue
            source_size = source_path.stat().st_size
            if total_size + source_size > max_bytes:
                skipped.append(
                    {
                        "artifact_id": artifact.artifact_id,
                        "reason": "artifact context byte limit exceeded",
                    }
                )
                continue
            isolated_path = _copy_artifact_to_scope(source_path, artifact, artifact_dir)
            codex_artifact = CodexArtifact.from_path(
                isolated_path,
                artifact_id=artifact.artifact_id,
                artifact_type=artifact.artifact_type,
            )
            included.append(
                ScopedArtifact(
                    source_artifact_id=artifact.artifact_id,
                    source_artifact_type=artifact.artifact_type,
                    isolated_path=codex_artifact.path,
                    sha256=codex_artifact.sha256,
                    size_bytes=codex_artifact.size_bytes,
                )
            )
            total_size += codex_artifact.size_bytes
        return ScopedArtifactContext(
            included=included,
            skipped=skipped,
            total_size_bytes=total_size,
        )

    def _provider_for(self, work_dir: Path) -> Any:
        if self.provider is not None and hasattr(self.provider, "invoke"):
            return self.provider
        return CodexCLIProvider(
            CodexProviderConfig(
                command=[
                    "codex",
                    "exec",
                    "--json",
                    "--skip-git-repo-check",
                    "--ignore-user-config",
                    "--ignore-rules",
                ],
                mode="enabled",
                timeout_seconds=float(self.config.codex_job_timeout_seconds),
                working_dir=str(work_dir),
                require_json_output=True,
            )
        )

    def _store_transcript(
        self,
        job: PlatformJob,
        *,
        task_type: str,
        work_dir: Path,
        request: CodexRequest,
        response: CodexResponse,
        context: ScopedArtifactContext,
    ) -> str | None:
        if not self.config.store_transcripts:
            return None
        transcript_path = work_dir / "codex_transcript.json"
        payload = _redact_json(
            {
                "job_id": job.job_id,
                "task_type": task_type,
                "request": request.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
                "artifact_context": context.model_dump(mode="json"),
            }
        )
        transcript_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return self.queue.register_artifact(job, transcript_path, artifact_type="codex_transcript")

    def _post_guardrail_warnings(
        self,
        task_type: str,
        response: CodexResponse,
        context: ScopedArtifactContext,
    ) -> list[str]:
        warnings = [
            violation.message for violation in response.guardrail_violations
        ]
        if response.status == "guardrail_violation" and not warnings:
            warnings.append("Codex provider reported a guardrail violation.")
        stdout = response.stdout or json.dumps(response.parsed_json or {})
        artifact_paths = [artifact.isolated_path for artifact in context.included]
        allowed_refs, _citation_ids = collect_allowed_refs_from_artifacts(artifact_paths)
        for artifact in context.included:
            allowed_refs.add(artifact.source_artifact_id)
            allowed_refs.add(Path(artifact.isolated_path).name)
        warnings.extend(
            output_guardrail_warnings(
                stdout,
                task_type=task_type,
                allowed_artifact_refs=allowed_refs,
            )
        )
        if redact_secrets(stdout) != stdout or redact_secrets(response.stderr) != response.stderr:
            warnings.append("Codex output contained secret-like material.")
        isolated_roots = {
            Path(artifact.isolated_path).resolve().parent for artifact in context.included
        }
        for artifact in response.parsed_json.get("artifacts", []) if response.parsed_json else []:
            if isinstance(artifact, str) and not _path_is_under_any(Path(artifact), isolated_roots):
                warnings.append("Codex response referenced an artifact outside the scoped context.")
                break
        return _dedupe(warnings)

    def _job_result(
        self,
        *,
        task_type: str,
        response: CodexResponse,
        context: ScopedArtifactContext,
        guardrail_warnings: list[str],
        transcript_artifact_id: str | None,
    ) -> dict[str, Any]:
        return _redact_json(
            {
                "task_type": task_type,
                "codex_status": response.status,
                "codex_returncode": response.returncode,
                "codex_request_id": response.request_id,
                "codex_usage": response.usage,
                "parsed_json": response.parsed_json,
                "included_artifact_ids": [
                    artifact.source_artifact_id for artifact in context.included
                ],
                "skipped_artifacts": context.skipped,
                "guardrail_warnings": guardrail_warnings,
                "transcript_artifact_id": transcript_artifact_id,
            }
        )

    def _insert_codex_worker_job(
        self,
        codex_worker_job_id: str,
        *,
        job: PlatformJob,
        task_type: str,
        status: str,
        guardrail_status: str,
        allowed_artifact_ids: list[str],
        created_at: datetime,
        metadata: dict[str, Any],
    ) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(
                insert(codex_worker_jobs).values(
                    codex_job_id=codex_worker_job_id,
                    platform_job_id=job.job_id,
                    org_id=job.org_id,
                    project_id=job.project_id,
                    requested_by_user_id=job.requested_by_user_id,
                    task_type=task_type,
                    codex_task_id=job.job_id,
                    status=status,
                    allowed_artifact_ids_json=allowed_artifact_ids,
                    allowed_commands_json=[],
                    forbidden_commands_json=DEFAULT_FORBIDDEN_COMMANDS,
                    transcript_artifact_id=None,
                    guardrail_status=guardrail_status,
                    created_at=created_at,
                    completed_at=None,
                    metadata_json=_redact_json(metadata),
                )
            )

    def _complete_codex_worker_job(
        self,
        codex_worker_job_id: str,
        *,
        status: str,
        guardrail_status: str,
        transcript_artifact_id: str | None,
        metadata: dict[str, Any],
        tolerate_missing: bool = False,
    ) -> None:
        with self.database.engine.begin() as connection:
            result = connection.execute(
                update(codex_worker_jobs)
                .where(codex_worker_jobs.c.codex_job_id == codex_worker_job_id)
                .values(
                    status=status,
                    guardrail_status=guardrail_status,
                    transcript_artifact_id=transcript_artifact_id,
                    completed_at=_now(),
                    metadata_json=_redact_json(metadata),
                )
            )
        if result.rowcount != 1 and not tolerate_missing:
            raise RuntimeError("Codex worker job disappeared before completion.")

    def _insert_or_complete_guardrail_failure(
        self,
        codex_worker_job_id: str,
        *,
        job: PlatformJob,
        task_type: str,
        created_at: datetime,
        transcript_artifact_id: str | None,
        warnings: list[str],
    ) -> None:
        try:
            self._complete_codex_worker_job(
                codex_worker_job_id,
                status="guardrail_failed",
                guardrail_status="failed_pre",
                transcript_artifact_id=transcript_artifact_id,
                metadata={"guardrail_warnings": warnings},
            )
        except RuntimeError:
            self._insert_codex_worker_job(
                codex_worker_job_id,
                job=job,
                task_type=task_type,
                status="guardrail_failed",
                guardrail_status="failed_pre",
                allowed_artifact_ids=[],
                created_at=created_at,
                metadata={"guardrail_warnings": warnings},
            )
            self._complete_codex_worker_job(
                codex_worker_job_id,
                status="guardrail_failed",
                guardrail_status="failed_pre",
                transcript_artifact_id=transcript_artifact_id,
                metadata={"guardrail_warnings": warnings},
            )


def _requested_artifact_ids(config_snapshot: dict[str, Any]) -> set[str] | None:
    raw = config_snapshot.get("allowed_artifact_ids")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise CodexGuardrailError(["allowed_artifact_ids must be a list when provided."])
    return {str(item) for item in raw}


def _artifact_skip_reason(path: Path, workspace_root: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return "artifact file does not exist"
    if not _path_is_under(path, workspace_root):
        return "artifact path is outside the project workspace"
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts & CACHE_PATH_MARKERS:
        return "cache files are not exposed to Codex jobs"
    if is_secret_path(path) or path.name == ".env":
        return "secret-like files are not exposed to Codex jobs"
    if _artifact_contains_secret(path):
        return "artifact content contains secret-like material"
    return None


def _copy_artifact_to_scope(
    source_path: Path,
    artifact: ArtifactRecord,
    artifact_dir: Path,
) -> Path:
    suffix = source_path.suffix if source_path.suffix else ".artifact"
    target = (artifact_dir / f"{_safe_name(artifact.artifact_id)}{suffix}").resolve()
    if not _path_is_under(target, artifact_dir.resolve()):
        raise CodexGuardrailError(["Scoped artifact target escaped the isolated workspace."])
    shutil.copyfile(source_path, target)
    return target


def _artifact_contains_secret(path: Path) -> bool:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return True
    return redact_secrets(text) != text


def _request_guardrail_warnings(request: CodexRequest) -> list[str]:
    text = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    warnings: list[str] = []
    lowered = text.lower()
    if ".env" in lowered:
        warnings.append("Codex prompt references .env files.")
    if any(marker in lowered for marker in CACHE_PATH_MARKERS):
        warnings.append("Codex prompt references cache files.")
    return _dedupe(warnings)


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _path_is_under_any(path: Path, roots: set[Path]) -> bool:
    return any(_path_is_under(path, root) for root in roots)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)[:96]


def _ids(value: str | None) -> list[str]:
    return [value] if value else []


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = ["CodexWorker", "CodexWorkerConfig", "CodexGuardrailError"]
