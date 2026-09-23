"""Integration tests for the migration runner and SQLite engine behavior.

Closes over docs/ENGINEERING_RULES.md (durability pragmas) and the
DATA_MODEL.md rule that schema changes go through migrations only.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from app.infrastructure.database.db import create_db_engine
from app.infrastructure.database.migrations import SCHEMA_VERSION, migrate
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


def test_migrate_creates_ledger_and_tables(tmp_path: Path) -> None:
    engine: Engine = create_db_engine(tmp_path / "m.db")
    version = migrate(engine)
    assert version == SCHEMA_VERSION

    expected = {
        "users",
        "work_sessions",
        "breaks",
        "activity_periods",
        "screenshots",
        "sync_queue",
        "app_state",
        "settings",
        "schema_migrations",
    }
    with engine.connect() as conn:
        found = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
    assert expected <= found
    engine.dispose()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    engine: Engine = create_db_engine(tmp_path / "m.db")
    assert migrate(engine) == SCHEMA_VERSION
    assert migrate(engine) == SCHEMA_VERSION  # no-op second run
    engine.dispose()


def test_engine_enforces_foreign_keys(
    tmp_path: Path,
) -> None:
    engine: Engine = create_db_engine(tmp_path / "fk.db")
    migrate(engine)
    with engine.connect() as conn:
        pragma = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert pragma == 1
    engine.dispose()


def test_engine_uses_wal_and_busy_timeout(
    tmp_path: Path,
) -> None:
    engine: Engine = create_db_engine(tmp_path / "wal.db")
    migrate(engine)
    with engine.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert journal == "wal"
    assert timeout == 5000
    engine.dispose()


def test_naive_datetime_refused_by_utc_column(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    from app.infrastructure.database.models import User
    from sqlalchemy.exc import StatementError

    engine: Engine = create_db_engine(tmp_path / "naive.db")
    migrate(engine)
    with pytest.raises(StatementError), engine.begin() as conn:
        conn.execute(
            User.__table__.insert().values(
                email="bad@example.com",
                created_at=datetime(2026, 9, 23, 10, 0),  # naive!
                updated_at=datetime(2026, 9, 23, 10, 0),  # naive!
            )
        )
    engine.dispose()


def test_schema_version_matches_code(tmp_path: Path) -> None:
    engine: Engine = create_db_engine(tmp_path / "v.db")
    migrate(engine)
    with sqlite3.connect(tmp_path / "v.db") as conn:
        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert [v[0] for v in versions] == [SCHEMA_VERSION]
    engine.dispose()
