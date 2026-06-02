from __future__ import annotations

from pathlib import Path

from molecule_ranker.migrations.reports import DatabaseMigrationReport
from molecule_ranker.platform.database import SCHEMA_VERSION, PlatformDatabase


def check_database_migrations(
    *,
    root_dir: str | Path = ".",
    database_url: str | None = None,
    db_path: str | Path | None = None,
) -> DatabaseMigrationReport:
    database = _database(
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        initialize=False,
    )
    try:
        check = database.check()
        applied = list(check.get("applied_migrations", []))
        migrations_current = SCHEMA_VERSION in applied and bool(check.get("ok"))
        warnings = []
        missing_tables = check.get("missing_tables", [])
        if missing_tables:
            missing = ", ".join(map(str, missing_tables))
            warnings.append(f"Missing platform database tables: {missing}")
    except Exception as exc:
        applied = []
        migrations_current = False
        warnings = [f"Database migration check failed: {exc}"]
    return DatabaseMigrationReport(
        database_url=database.safe_database_url,
        database_kind=database.database_kind,
        current_schema_version=SCHEMA_VERSION,
        applied_migrations=applied,
        migrations_current=migrations_current,
        warnings=warnings,
    )


def migrate_database(
    *,
    root_dir: str | Path = ".",
    database_url: str | None = None,
    db_path: str | Path | None = None,
    dry_run: bool = False,
) -> DatabaseMigrationReport:
    database = _database(
        root_dir=root_dir,
        database_url=database_url,
        db_path=db_path,
        initialize=False,
    )
    actions: list[str] = []
    warnings: list[str] = []
    if dry_run:
        actions.append("would_initialize_or_update_platform_schema")
    else:
        database.migrate()
        actions.append("initialized_or_updated_platform_schema")
    try:
        check = database.check()
        applied = list(check.get("applied_migrations", []))
        migrations_current = SCHEMA_VERSION in applied and bool(check.get("ok"))
        missing_tables = check.get("missing_tables", [])
        if missing_tables:
            missing = ", ".join(map(str, missing_tables))
            warnings.append(f"Missing platform database tables: {missing}")
    except Exception as exc:
        applied = []
        migrations_current = False
        warnings.append(f"Database migration verification failed: {exc}")
    return DatabaseMigrationReport(
        database_url=database.safe_database_url,
        database_kind=database.database_kind,
        current_schema_version=SCHEMA_VERSION,
        applied_migrations=applied,
        migrations_current=migrations_current,
        dry_run=dry_run,
        actions=actions,
        warnings=warnings,
    )


def _database(
    *,
    root_dir: str | Path,
    database_url: str | None,
    db_path: str | Path | None,
    initialize: bool,
) -> PlatformDatabase:
    return PlatformDatabase(
        Path(root_dir),
        database_url=database_url,
        db_path=Path(db_path) if db_path else None,
        initialize=initialize,
    )
