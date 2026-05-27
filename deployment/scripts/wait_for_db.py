from __future__ import annotations

import os
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def main() -> int:
    database_url = os.getenv("MOLECULE_RANKER_DATABASE_URL")
    if not database_url:
        return 0
    deadline = time.monotonic() + int(os.getenv("MOLECULE_RANKER_DB_WAIT_SECONDS", "60"))
    last_error = ""
    while time.monotonic() < deadline:
        try:
            engine = create_engine(database_url, future=True)
            with engine.connect() as connection:
                connection.execute(text("select 1"))
            return 0
        except SQLAlchemyError as exc:
            last_error = str(exc)
            time.sleep(2)
    print(f"Database was not ready before timeout: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
