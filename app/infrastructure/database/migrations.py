"""Schema migration runner (docs/DATA_MODEL.md § Access rules).

Schema changes are never applied ad hoc — each change is a versioned step
here, applied in order inside a transaction, with a ``schema_migrations``
ledger row committed atomically with the DDL. A crash mid-migration rolls
back both so a half-applied schema is impossible.

Policy:
* Migration ``1`` creates the baseline schema from ``models.Base.metadata``.
* Future changes append a new numbered migration (raw SQL DDL) so existing
  installs upgrade in place. Bump :data:`SCHEMA_VERSION` to the newest one.
* A migration must be safe on a database that was created *after* it was
  written: the baseline is generated from the current models, so a fresh
  install already has every column a later migration would add. Migrations
  therefore check for the column they are about to change and no-op when it is
  already in the desired state, instead of failing the startup of a new
  install.

Safe to call on every startup; no-op when already current.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, text

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy.engine import Connection

SCHEMA_VERSION = 2

_LOGGER = get_logger("migrations")


def _migrate_to_1(conn: Connection) -> None:
    """Baseline: create every table declared in models.py."""
    from app.infrastructure.database.models import Base

    Base.metadata.create_all(bind=conn)


def _migrate_to_2(conn: Connection) -> None:
    """``users.team_name`` → ``users.workspace_name``.

    The product calls it a workspace, and the backend's field was renamed to
    match. A cached identity row is cheap to rebuild, but the rename keeps
    installs that were upgraded from an older build from silently losing the
    name they had.
    """
    columns = {
        str(row[1]) for row in conn.execute(text("PRAGMA table_info(users)")).all()
    }

    if "team_name" not in columns:
        return  # fresh install: the baseline already created the new name

    if "workspace_name" in columns:
        conn.execute(text("ALTER TABLE users DROP COLUMN team_name"))
    else:
        conn.execute(
            text("ALTER TABLE users RENAME COLUMN team_name TO workspace_name")
        )


_MIGRATIONS: dict[int, Callable[[Connection], None]] = {
    1: _migrate_to_1,
    2: _migrate_to_2,
}


def migrate(engine: Engine) -> int:
    """Apply pending migrations; returns the resulting schema version.

    Runs entirely on one connection/transaction so the ledger row and DDL
    commit (or roll back) together.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        )
        applied = _applied_versions(conn)

        for version in sorted(_MIGRATIONS):
            if version in applied:
                continue
            _MIGRATIONS[version](conn)
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, :applied_at)"
                ),
                {
                    "version": version,
                    "applied_at": datetime.now(UTC).isoformat(),
                },
            )
            _LOGGER.info("applied schema migration %s", version)

        return max((*applied, *_MIGRATIONS))


def _applied_versions(conn: Connection) -> set[int]:
    rows: Sequence[Any] = conn.execute(
        text("SELECT version FROM schema_migrations")
    ).fetchall()
    return {int(row[0]) for row in rows}
