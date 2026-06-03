from __future__ import annotations

from pathlib import Path

from molecule_ranker.platform.db import PlatformDatabase
from molecule_ranker.platform.isolation import run_isolation_audit
from molecule_ranker.platform.readiness import ReadinessConfig, run_readiness_checks


def test_platform_readiness_probe_jobs_are_project_scoped(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    report = run_readiness_checks(
        ReadinessConfig(
            root_dir=tmp_path,
            database_path=db_path,
            artifact_storage_root=tmp_path / "artifacts",
            backup_path=tmp_path / "backups",
            secret_key="readiness-test-secret-value-32-chars",
        )
    )
    isolation = run_isolation_audit(PlatformDatabase(tmp_path, db_path=db_path))

    assert report.status == "pass"
    assert isolation["status"] == "pass"
