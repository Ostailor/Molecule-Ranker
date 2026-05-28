from __future__ import annotations

from pathlib import Path

from molecule_ranker.platform.readiness import ReadinessConfig, run_readiness_checks


def test_dev_readiness_passes_with_sqlite(tmp_path: Path) -> None:
    report = run_readiness_checks(
        ReadinessConfig(
            root_dir=tmp_path,
            database_path=tmp_path / "platform.sqlite",
            artifact_storage_root=tmp_path / "artifacts",
            backup_path=tmp_path / "backups",
        )
    )

    assert report.status == "pass"
    assert not [check for check in report.checks if check.status == "fail"]
    assert report.by_id["database_connection"].status == "pass"
    assert report.by_id["migrations_current"].status == "pass"
    assert report.by_id["background_worker_can_pick_up_test_job"].status == "pass"


def test_production_missing_secret_fails(tmp_path: Path) -> None:
    report = run_readiness_checks(
        ReadinessConfig(
            root_dir=tmp_path,
            environment="production",
            secret_key=None,
            allowed_hosts=["ranker.internal"],
            debug=False,
            database_path=tmp_path / "platform.sqlite",
            artifact_storage_root=tmp_path / "artifacts",
            backup_path=tmp_path / "backups",
        )
    )

    assert report.status == "fail"
    assert report.by_id["secret_key_configured_in_production"].status == "fail"
    assert report.by_id["allowed_hosts_configured_in_production"].status == "pass"
    assert report.by_id["debug_disabled_in_production"].status == "pass"


def test_worker_disabled_warns(tmp_path: Path) -> None:
    report = run_readiness_checks(
        ReadinessConfig(
            root_dir=tmp_path,
            database_path=tmp_path / "platform.sqlite",
            artifact_storage_root=tmp_path / "artifacts",
            backup_path=tmp_path / "backups",
            worker_enabled=False,
        )
    )

    assert report.status == "warn"
    assert report.by_id["worker_queue_reachable"].status == "warn"
    assert report.by_id["background_worker_can_pick_up_test_job"].status == "warn"


def test_invalid_artifact_path_fails(tmp_path: Path) -> None:
    invalid_path = tmp_path / "artifact-file"
    invalid_path.write_text("not a directory\n")

    report = run_readiness_checks(
        ReadinessConfig(
            root_dir=tmp_path,
            database_path=tmp_path / "platform.sqlite",
            artifact_storage_root=invalid_path,
            backup_path=tmp_path / "backups",
        )
    )

    assert report.status == "fail"
    assert report.by_id["artifact_storage_writable"].status == "fail"
