from __future__ import annotations

from pathlib import Path
from typing import Any

from molecule_ranker.platform.database import PlatformDatabase


def database_from_config(
    *,
    root_dir: Path,
    database_url: str | None = None,
    db_path: Path | None = None,
    initialize: bool = False,
) -> PlatformDatabase:
    return PlatformDatabase(
        root_dir,
        database_url=database_url,
        db_path=db_path,
        initialize=initialize,
    )


def init_database(database: PlatformDatabase) -> dict[str, Any]:
    database.initialize()
    return database.check()


def run_migrations(database: PlatformDatabase) -> list[str]:
    return database.migrate()


def check_database(database: PlatformDatabase) -> dict[str, Any]:
    return database.check()


__all__ = [
    "check_database",
    "database_from_config",
    "init_database",
    "run_migrations",
]
