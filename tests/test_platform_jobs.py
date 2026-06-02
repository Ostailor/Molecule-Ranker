from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from molecule_ranker.platform.database import PlatformDatabase, artifact_records
from molecule_ranker.platform.db import PlatformDatabaseError
from molecule_ranker.platform.jobs import JobResult, PlatformJobQueue
from molecule_ranker.workers import PipelineWorker


def test_enqueue_job_enforces_permissions_and_stores_snapshot(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)

    job = queue.enqueue(
        job_type="ranking",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"disease": "Parkinson disease"},
        priority="high",
    )

    stored = queue.get(job.job_id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.requested_by_user_id == user.user_id
    assert stored.project_id == "project-1"
    assert stored.config_snapshot["disease"] == "Parkinson disease"
    assert stored.priority == "high"


def test_worker_runs_mocked_job_and_registers_artifact(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    job = queue.enqueue(job_type="ranking", requested_by=user, project_id="project-1")
    output_path = tmp_path / "result.json"

    def handler(_job):
        output_path.write_text('{"ok": true}\n')
        return JobResult(result={"ok": True}, artifact_paths=[output_path])

    finished = PipelineWorker(database=database, handlers={"ranking": handler}).run_once()

    assert finished is not None
    assert finished.job_id == job.job_id
    assert finished.status == "succeeded"
    assert finished.result_artifact_ids
    with database.engine.connect() as connection:
        rows = connection.execute(select(artifact_records)).mappings().fetchall()
    assert len(rows) == 1
    assert rows[0]["project_id"] == "project-1"
    assert rows[0]["provenance_json"]["job_id"] == job.job_id


def test_failed_job_records_error_summary(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    job = PlatformJobQueue(database).enqueue(
        job_type="developability",
        requested_by=user,
        project_id="project-1",
    )

    def handler(_job):
        raise RuntimeError("mock worker failed")

    finished = PipelineWorker(database=database, handlers={"developability": handler}).run_once()

    assert finished is not None
    assert finished.job_id == job.job_id
    assert finished.status == "failed"
    assert "mock worker failed" in (finished.error_summary or "")


def test_failed_job_can_be_retried_from_redacted_snapshot(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    job = queue.enqueue(
        job_type="ranking",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "disease": "Parkinson disease",
            "api_key": "secret-token-value",
        },
    )
    failed = queue.fail(job, RuntimeError("worker failed with api_key=secret-token-value"))

    retry = queue.retry_failed(failed.job_id, requested_by=user)

    assert retry.status == "queued"
    assert retry.job_id != failed.job_id
    assert retry.metadata["retry_of_job_id"] == failed.job_id
    assert retry.metadata["retry_attempt"] == 2
    assert retry.config_snapshot["disease"] == "Parkinson disease"
    assert retry.config_snapshot["api_key"] == "[REDACTED]"
    assert "secret-token-value" not in str(retry.config_snapshot)


def test_running_job_resume_summary_redacts_checkpoint_and_cancel_state(
    tmp_path: Path,
) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    queue.enqueue(
        job_type="generation",
        requested_by=user,
        project_id="project-1",
        metadata={
            "checkpoint_id": "batch-4",
            "resume_token": "resume-secret-token-value",
        },
    )
    running = queue.claim_next()
    assert running is not None
    queue.cancel(running.job_id, actor_user_id=user.user_id)

    summary = queue.resume_summary(running.job_id)

    assert summary == {
        "job_id": running.job_id,
        "status": "running",
        "resumable": True,
        "cancel_requested": True,
        "checkpoint_id": "batch-4",
        "resume_token": "[REDACTED]",
    }


def test_cancelled_queued_job_does_not_run(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    job = queue.enqueue(job_type="generation", requested_by=user, project_id="project-1")
    calls = 0

    def handler(_job):
        nonlocal calls
        calls += 1
        return JobResult()

    cancelled = queue.cancel(job.job_id, actor_user_id=user.user_id)
    finished = PipelineWorker(database=database, handlers={"generation": handler}).run_once()

    assert cancelled.status == "cancelled"
    assert finished is None
    assert calls == 0


def test_unauthorized_design_run_blocked(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    viewer = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=viewer.user_id,
        user_id=viewer.user_id,
    )

    try:
        PlatformJobQueue(database).enqueue(
            job_type="design_generate",
            requested_by=viewer,
            project_id="project-1",
            config_snapshot={"budget": 10},
        )
    except PermissionError as exc:
        assert "design:run" in str(exc)
    else:
        raise AssertionError("Expected design run permission denial.")


def test_codex_design_plan_requires_approval_for_large_generation(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)

    try:
        queue.enqueue(
            job_type="design_generate",
            requested_by=user,
            project_id="project-1",
            config_snapshot={
                "budget": 250,
                "budget_limit": 250,
                "use_codex_planner": True,
            },
        )
    except PermissionError as exc:
        assert "Codex-produced design plans require review approval" in str(exc)
    else:
        raise AssertionError("Expected Codex plan approval guardrail.")

    approved = queue.enqueue(
        job_type="design_generate",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "budget": 250,
            "budget_limit": 250,
            "use_codex_planner": True,
            "plan_approved": True,
        },
    )

    assert approved.status == "queued"


def test_model_training_and_validation_jobs_are_supported_but_guarded(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)

    training = queue.enqueue(
        job_type="model_training",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "endpoint_name": "binding_affinity",
            "target_symbol": "MAOB",
            "use_patient_data": False,
        },
    )
    validation = queue.enqueue(
        job_type="model_validation",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"model_id": "model-binding-affinity-1"},
    )
    dataset_build = queue.enqueue(
        job_type="model_dataset_build",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"endpoint_name": "binding_affinity", "target_symbol": "MAOB"},
    )
    train = queue.enqueue(
        job_type="model_train",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"dataset_id": "dataset-1"},
    )
    evaluate = queue.enqueue(
        job_type="model_evaluate",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"model_id": "model-binding-affinity-1"},
    )
    predict = queue.enqueue(
        job_type="model_predict",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"model_id": "model-binding-affinity-1"},
    )
    calibrate = queue.enqueue(
        job_type="model_calibrate",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"model_id": "model-binding-affinity-1"},
    )

    assert training.status == "queued"
    assert validation.status == "queued"
    assert dataset_build.status == "queued"
    assert train.status == "queued"
    assert evaluate.status == "queued"
    assert predict.status == "queued"
    assert calibrate.status == "queued"

    try:
        queue.enqueue(
            job_type="model_training",
            requested_by=user,
            project_id="project-1",
            config_snapshot={"endpoint_name": "binding_affinity", "use_patient_data": True},
        )
    except PermissionError as exc:
        assert "patient" in str(exc).lower()
    else:
        raise AssertionError("Expected model training patient-data guardrail.")


def test_model_job_permissions_are_enforced(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    viewer = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=viewer.user_id,
        user_id=viewer.user_id,
    )
    queue = PlatformJobQueue(database)

    readable = queue.enqueue(
        job_type="model_evaluate",
        requested_by=viewer,
        project_id="project-1",
        config_snapshot={"model_id": "model-binding-affinity-1"},
    )

    assert readable.status == "queued"
    try:
        queue.enqueue(
            job_type="model_train",
            requested_by=viewer,
            project_id="project-1",
            config_snapshot={"dataset_id": "dataset-1"},
        )
    except PermissionError as exc:
        assert "model:train" in str(exc)
    else:
        raise AssertionError("Expected model training permission denial.")


def test_structure_jobs_are_supported_and_docking_requires_acknowledgement(
    tmp_path: Path,
) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)

    retrieval = queue.enqueue(
        job_type="structure_retrieval",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"target_symbol": "LRRK2", "max_structures_per_target": 5},
    )
    report = queue.enqueue(
        job_type="structure_report_card",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"target_symbol": "LRRK2", "report_card_version": "1.3"},
    )

    assert retrieval.status == "queued"
    assert report.status == "queued"

    try:
        queue.enqueue(
            job_type="structure_docking",
            requested_by=user,
            project_id="project-1",
            config_snapshot={"enable_docking": True, "target_symbol": "LRRK2"},
        )
    except PermissionError as exc:
        assert "Docking jobs require acknowledgement" in str(exc)
    else:
        raise AssertionError("Expected hosted docking acknowledgement guardrail.")

    docking = queue.enqueue(
        job_type="structure_docking",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "enable_docking": True,
            "target_symbol": "LRRK2",
            "structure_warning_acknowledged": True,
            "docking_limitations_acknowledged": True,
        },
    )

    assert docking.status == "queued"


def test_v13_structure_jobs_permissions_acknowledgement_and_budget(
    tmp_path: Path,
) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)

    for job_type in [
        "structure_find",
        "structure_select",
        "receptor_prepare",
        "ligand_prepare",
        "binding_site_define",
        "pose_qc",
        "structure_assess",
        "structure_benchmark",
        "structure_design_loop",
    ]:
        job = queue.enqueue(
            job_type=job_type,
            requested_by=user,
            project_id="project-1",
            config_snapshot={"target_symbol": "LRRK2"},
        )
        assert job.status == "queued"

    runner = database.create_user(email="runner@example.com", password="Runner-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="runner",
        actor_user_id=user.user_id,
        user_id=runner.user_id,
    )
    try:
        queue.enqueue(
            job_type="structure_dock",
            requested_by=runner,
            project_id="project-1",
            config_snapshot={
                "enable_docking": True,
                "target_symbol": "LRRK2",
                "structure_warning_acknowledged": True,
                "docking_limitations_acknowledged": True,
            },
        )
    except PermissionError as exc:
        assert "structure:dock" in str(exc)
    else:
        raise AssertionError("Expected structure:dock permission denial.")

    try:
        queue.enqueue(
            job_type="structure_dock",
            requested_by=user,
            project_id="project-1",
            config_snapshot={"enable_docking": True, "target_symbol": "LRRK2"},
        )
    except PermissionError as exc:
        assert "Docking jobs require acknowledgement" in str(exc)
    else:
        raise AssertionError("Expected hosted docking acknowledgement guardrail.")

    try:
        queue.enqueue(
            job_type="structure_dock",
            requested_by=user,
            project_id="project-1",
            config_snapshot={
                "enable_docking": True,
                "target_symbol": "LRRK2",
                "structure_warning_acknowledged": True,
                "docking_limitations_acknowledged": True,
                "max_ligands": 250,
            },
        )
    except PlatformDatabaseError as exc:
        assert "budget_limit" in str(exc)
    else:
        raise AssertionError("Expected large docking budget limit guardrail.")

    docked = queue.enqueue(
        job_type="structure_dock",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "enable_docking": True,
            "target_symbol": "LRRK2",
            "structure_warning_acknowledged": True,
            "docking_limitations_acknowledged": True,
            "max_ligands": 150,
            "budget_limit": 150,
        },
    )
    assert docked.status == "queued"


def test_codex_planned_structure_jobs_require_structure_approval(
    tmp_path: Path,
) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)

    try:
        queue.enqueue(
            job_type="structure_select",
            requested_by=user,
            project_id="project-1",
            config_snapshot={"target_symbol": "LRRK2", "use_codex_planner": True},
        )
    except PermissionError as exc:
        assert "Codex-planned structure jobs require approval" in str(exc)
    else:
        raise AssertionError("Expected Codex structure approval guardrail.")

    approved = queue.enqueue(
        job_type="structure_select",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "target_symbol": "LRRK2",
            "use_codex_planner": True,
            "structure_plan_approved": True,
        },
    )
    assert approved.status == "queued"


def test_portfolio_optimize_job_requires_permission_and_runs(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    viewer = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    editor = database.create_user(email="editor@example.com", password="Editor-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=viewer.user_id,
        user_id=viewer.user_id,
    )
    database.grant_project_permission(
        project_id="project-1",
        role="editor",
        actor_user_id=editor.user_id,
        user_id=editor.user_id,
    )

    try:
        PlatformJobQueue(database).enqueue(
            job_type="portfolio_optimize",
            requested_by=viewer,
            project_id="project-1",
        )
    except PermissionError as exc:
        assert "portfolio:run" in str(exc)
    else:
        raise AssertionError("Expected portfolio run permission denial.")

    job = PlatformJobQueue(database).enqueue(
        job_type="portfolio_optimize",
        requested_by=editor,
        project_id="project-1",
        config_snapshot={
            "algorithm": "greedy",
            "candidates": [
                {
                    "portfolio_candidate_id": "pc-1",
                    "candidate_name": "Candidate 1",
                    "origin": "existing",
                    "target_symbols": ["MAOB"],
                    "evidence_score": 0.8,
                    "developability_score": 0.7,
                },
                {
                    "portfolio_candidate_id": "pc-2",
                    "candidate_name": "Candidate 2",
                    "origin": "generated",
                    "target_symbols": ["LRRK2"],
                    "generation_score": 0.8,
                    "uncertainty_score": 0.6,
                },
            ],
        },
    )
    finished = PipelineWorker(database=database).run_once()

    assert finished is not None
    assert finished.job_id == job.job_id
    assert finished.status == "succeeded"
    assert finished.result_artifact_ids
    with database.engine.connect() as connection:
        artifact = connection.execute(select(artifact_records)).mappings().first()
    assert artifact is not None
    payload = json.loads(Path(artifact["path"]).read_text())
    assert payload["portfolio_boundary"] == "advisory_until_approved"
    assert payload["approved"] is False
    assert payload["deterministic_validation"] is True
    assert payload["optimization_run"]["input_candidate_count"] == 2


def test_portfolio_external_export_requires_explicit_permission(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)

    try:
        PlatformJobQueue(database).enqueue(
            job_type="portfolio_memo",
            requested_by=user,
            project_id="project-1",
            config_snapshot={"external_export": True},
        )
    except PermissionError as exc:
        assert "portfolio:export" in str(exc)
        assert "explicit permission" in str(exc)
    else:
        raise AssertionError("Expected explicit portfolio export permission denial.")

    job = PlatformJobQueue(database).enqueue(
        job_type="portfolio_memo",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "external_export": True,
            "explicit_export_permission": True,
        },
    )

    assert job.status == "queued"


def test_graph_jobs_require_permissions_and_worker_outputs_are_advisory(
    tmp_path: Path,
) -> None:
    database, user = _database_with_project_user(tmp_path)
    viewer = database.create_user(email="viewer@example.com", password="Viewer-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="viewer",
        actor_user_id=user.user_id,
        user_id=viewer.user_id,
    )
    _write_graph_candidates(tmp_path)
    queue = PlatformJobQueue(database)

    try:
        queue.enqueue(job_type="graph_build", requested_by=viewer, project_id="project-1")
    except PermissionError as exc:
        assert "graph:build" in str(exc)
    else:
        raise AssertionError("Expected graph build permission denial.")

    build = queue.enqueue(job_type="graph_build", requested_by=user, project_id="project-1")
    finished_build = PipelineWorker(database=database).run_once()

    assert finished_build is not None
    assert finished_build.job_id == build.job_id
    assert finished_build.status == "succeeded"
    assert finished_build.result_artifact_ids
    with database.engine.connect() as connection:
        graph_artifact = (
            connection.execute(
                select(artifact_records).where(
                    artifact_records.c.artifact_id == finished_build.result_artifact_ids[0]
                )
            )
            .mappings()
            .one()
        )
    graph_payload = json.loads(Path(graph_artifact["path"]).read_text())
    assert graph_payload["limitations"]
    assert graph_payload["entities"]

    query = queue.enqueue(
        job_type="graph_query",
        requested_by=viewer,
        project_id="project-1",
        config_snapshot={
            "graph_artifact_id": finished_build.result_artifact_ids[0],
            "query": "candidates_for_target",
            "target_symbol": "MAOB",
        },
    )
    finished_query = PipelineWorker(database=database).run_once()

    assert finished_query is not None
    assert finished_query.job_id == query.job_id
    assert finished_query.status == "succeeded"
    with database.engine.connect() as connection:
        query_artifact = (
            connection.execute(
                select(artifact_records).where(
                    artifact_records.c.artifact_id == finished_query.result_artifact_ids[0]
                )
            )
            .mappings()
            .one()
        )
    query_payload = json.loads(Path(query_artifact["path"]).read_text())
    assert query_payload["query_results"][0]["provenance"]

    try:
        queue.enqueue(
            job_type="graph_export",
            requested_by=viewer,
            project_id="project-1",
            config_snapshot={"graph_artifact_id": finished_build.result_artifact_ids[0]},
        )
    except PermissionError as exc:
        assert "graph:export" in str(exc)
    else:
        raise AssertionError("Expected graph export permission denial.")


def test_cross_program_graph_jobs_require_permission_across_all_projects(
    tmp_path: Path,
) -> None:
    database, user = _database_with_project_user(tmp_path)

    try:
        PlatformJobQueue(database).enqueue(
            job_type="graph_query",
            requested_by=user,
            project_id="project-1",
            config_snapshot={
                "query": "generated_molecules_without_direct_evidence",
                "included_project_ids": ["project-1", "project-2"],
            },
        )
    except PermissionError as exc:
        assert "project-2" in str(exc)
    else:
        raise AssertionError("Expected cross-program graph permission denial.")


def test_design_generation_budget_limit_enforced(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)

    try:
        PlatformJobQueue(database).enqueue(
            job_type="design_loop",
            requested_by=user,
            project_id="project-1",
            config_snapshot={"budget": 250},
        )
    except Exception as exc:
        assert "budget_limit" in str(exc)
    else:
        raise AssertionError("Expected budget limit guardrail.")


def test_retry_transient_failure_requeues_with_backoff(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    job = queue.enqueue(
        job_type="ranking",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"idempotency_key": "rank-1", "retry_backoff_seconds": 0},
    )
    attempts = 0

    def handler(_job):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("temporary worker timeout")
        return JobResult(result={"ok": True})

    first = PipelineWorker(database=database, handlers={"ranking": handler}).run_once()
    retry = queue.get(job.job_id)
    second = PipelineWorker(database=database, handlers={"ranking": handler}).run_once()

    assert first is not None
    assert first.status == "retrying"
    assert retry is not None
    assert retry.status == "retrying"
    assert retry.metadata["retry"]["attempt"] == 1
    assert retry.metadata["retry"]["next_retry_at"]
    assert second is not None
    assert second.status == "succeeded"
    assert attempts == 2


def test_non_idempotent_external_write_is_not_retried(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    job = PlatformJobQueue(database).enqueue(
        job_type="external_export",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "generated_molecule_warning_acknowledged": True,
            "external_write": True,
        },
    )

    def handler(_job):
        raise TimeoutError("temporary export timeout")

    finished = PipelineWorker(database=database, handlers={"external_export": handler}).run_once()

    assert finished is not None
    assert finished.job_id == job.job_id
    assert finished.status == "failed"
    assert finished.metadata["retry"]["reason"] == "external_write_not_idempotent"


def test_stale_running_job_recovery_requeues_idempotent_job(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    queued = queue.enqueue(
        job_type="ranking",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"idempotency_key": "rank-stale"},
    )
    running = queue.claim_next()
    assert running is not None

    recovered = queue.recover_stale_running_jobs(stale_after_seconds=0)
    refreshed = queue.get(queued.job_id)

    assert recovered == [queued.job_id]
    assert refreshed is not None
    assert refreshed.status == "retrying"
    assert refreshed.metadata["recovered_from_stale_running"] is True


def test_running_cancellation_is_cooperative(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    queue.enqueue(job_type="generation", requested_by=user, project_id="project-1")
    running = queue.claim_next()
    assert running is not None
    queue.cancel(running.job_id, actor_user_id=user.user_id)

    refreshed = queue.get(running.job_id)
    assert refreshed is not None
    finished = PipelineWorker(
        database=database,
        handlers={"generation": lambda _job: JobResult()},
    ).run_job(refreshed)

    assert finished is not None
    assert finished.status == "cancelled"


def test_job_timeout_enforcement_marks_timed_out(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    job = queue.enqueue(
        job_type="developability",
        requested_by=user,
        project_id="project-1",
        config_snapshot={"timeout_seconds": 0, "idempotency_key": "dev-timeout"},
    )

    finished = PipelineWorker(
        database=database,
        handlers={"developability": lambda _job: JobResult()},
    ).run_once()

    assert finished is not None
    assert finished.job_id == job.job_id
    assert finished.status == "timed_out"
    assert "timed out" in (finished.error_summary or "")


def test_dead_letter_transition_after_max_attempts(tmp_path: Path) -> None:
    database, user = _database_with_project_user(tmp_path)
    queue = PlatformJobQueue(database)
    job = queue.enqueue(
        job_type="ranking",
        requested_by=user,
        project_id="project-1",
        config_snapshot={
            "idempotency_key": "rank-dead-letter",
            "max_attempts": 1,
            "retry_backoff_seconds": 0,
        },
    )

    def handler(_job):
        raise TimeoutError("temporary worker timeout")

    finished = PipelineWorker(database=database, handlers={"ranking": handler}).run_once()

    assert finished is not None
    assert finished.job_id == job.job_id
    assert finished.status == "dead_lettered"
    assert finished.metadata["dead_letter"]["reason"] == "max_attempts_exceeded"


def _database_with_project_user(tmp_path: Path):
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(email="scientist@example.com", password="Scientist-password-1")
    database.grant_project_permission(
        project_id="project-1",
        role="editor",
        actor_user_id=user.user_id,
        user_id=user.user_id,
    )
    return database, user


def _write_graph_candidates(path: Path) -> None:
    path.joinpath("candidates.json").write_text(
        json.dumps(
            {
                "disease": {"canonical_name": "Parkinson disease"},
                "targets": [
                    {
                        "symbol": "MAOB",
                        "evidence": [
                            {
                                "source": "opentargets",
                                "source_id": "OTAR-test",
                                "confidence": 0.8,
                            }
                        ],
                    }
                ],
                "candidates": [
                    {
                        "candidate_id": "candidate-rasagiline",
                        "name": "Rasagiline",
                        "known_targets": ["MAOB"],
                        "score": 0.82,
                        "scaffold": "indane",
                    }
                ],
            },
            sort_keys=True,
        )
    )
