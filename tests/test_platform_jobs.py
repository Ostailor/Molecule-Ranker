from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from molecule_ranker.platform.database import PlatformDatabase, artifact_records
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
