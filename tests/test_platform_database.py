from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from typer.testing import CliRunner

from molecule_ranker.cli import app
from molecule_ranker.platform.database import (
    REQUIRED_TABLES,
    PlatformDatabase,
    platform_audit_events,
    users,
)


def test_platform_database_initializes_required_sqlite_tables(tmp_path: Path) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")

    table_names = set(inspect(database.engine).get_table_names())
    check = database.check()

    assert REQUIRED_TABLES <= table_names
    assert check["ok"] is True
    assert check["missing_tables"] == []
    assert check["database"] == "sqlite"


def test_platform_database_does_not_store_plaintext_passwords_or_audit_secrets(
    tmp_path: Path,
) -> None:
    database = PlatformDatabase(tmp_path, db_path=tmp_path / "platform.sqlite")
    user = database.create_user(
        email="scientist@example.com",
        password="Not-plaintext-password-1",
        roles=["platform_admin", "user"],
    )
    database.write_audit(
        "secret_test",
        actor_user_id=user.user_id,
        summary="Stored API key sk-test-secret-value-1234567890",
        metadata={"api_key": "sk-test-secret-value-1234567890"},
    )
    database.enqueue_job(
        job_type="codex_task",
        requested_by_user_id=user.user_id,
        payload={"api_key": "sk-test-secret-value-1234567890"},
    )

    with database.engine.connect() as connection:
        row = (
            connection.execute(select(users).where(users.c.user_id == user.user_id))
            .mappings()
            .one()
        )
        audit_row = connection.execute(
            select(platform_audit_events).where(platform_audit_events.c.event_type == "secret_test")
        ).mappings().one()

    assert row["password_hash"] != "Not-plaintext-password-1"
    assert row["password_salt"] != "Not-plaintext-password-1"
    combined_audit = json.dumps(
        {
            "summary": audit_row["summary"],
            "metadata": audit_row["metadata_json"],
        },
        sort_keys=True,
    )
    assert "sk-test-secret-value" not in combined_audit
    exported = json.dumps(database.export_user_data(user.user_id), sort_keys=True)
    assert "sk-test-secret-value" not in exported


def test_db_cli_init_migrate_and_check_with_sqlite(tmp_path: Path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "platform.sqlite"

    init_result = runner.invoke(
        app,
        ["db", "init", "--root", str(tmp_path), "--db-path", str(db_path), "--json"],
    )
    migrate_result = runner.invoke(
        app,
        ["db", "migrate", "--root", str(tmp_path), "--db-path", str(db_path), "--json"],
    )
    check_result = runner.invoke(
        app,
        ["db", "check", "--root", str(tmp_path), "--db-path", str(db_path), "--json"],
    )

    assert init_result.exit_code == 0, init_result.stdout
    assert migrate_result.exit_code == 0, migrate_result.stdout
    assert check_result.exit_code == 0, check_result.stdout
    payload = json.loads(check_result.stdout)
    assert payload["ok"] is True
    assert payload["database"] == "sqlite"
    assert payload["missing_tables"] == []


@pytest.mark.skipif(
    not os.getenv("MOLECULE_RANKER_TEST_POSTGRES_URL"),
    reason="MOLECULE_RANKER_TEST_POSTGRES_URL is not configured.",
)
def test_platform_database_postgres_check_when_configured(tmp_path: Path) -> None:
    database = PlatformDatabase(
        tmp_path,
        database_url=os.environ["MOLECULE_RANKER_TEST_POSTGRES_URL"],
    )

    check = database.check()

    assert check["ok"] is True
    assert check["database"].startswith("postgresql")
