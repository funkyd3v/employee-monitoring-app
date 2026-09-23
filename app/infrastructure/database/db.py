"""SQLite engine construction and session factory.

Threading (docs/ENGINEERING_RULES.md §Threading Discipline): workers and UI
access the DB off the main thread whenever blocking is involved, and SQLite
runs in WAL mode with a busy timeout so concurrent readers/writers get
correct behavior instead of "database is locked" crashes.

Only this module may construct the engine/sessionmaker; repositories receive
a :class:`sqlalchemy.orm.Session` from the application's session factory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.engine.interfaces import DBAPIConnection


def create_db_engine(db_path: Path) -> Engine:
    """Build the SQLite engine with durability/concurrency pragmas.

    * ``journal_mode=WAL`` — concurrent readers during writes.
    * ``busy_timeout=5000`` — wait instead of raising "locked".
    * ``foreign_keys=ON`` — enforced per-connection (cheap, correct).
    * ``synchronous=NORMAL`` — durable enough with WAL, far fewer fsyncs.

    Verified at row level; each is the documented recommended setting for a
    local-first agent whose other option is a single shared file.
    """

    if db_path.parent:
        db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn: DBAPIConnection, _record: object) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


class Database:
    """Owns the engine and creates sessions; the single DB entry point.

    Repository and service code receive sessions from :meth:`session` (or
    use :meth:`run_in_session`), never a second engine.
    """

    def __init__(self, db_path: Path) -> None:
        self._engine: Engine = create_db_engine(db_path)
        self._factory: sessionmaker[Session] = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )
        self._logger = get_logger("database")

    @property
    def engine(self) -> Engine:
        return self._engine

    def session(self) -> Session:
        return self._factory()

    def run_in_session(self, fn: Callable[[Session], object]) -> object:
        """Run ``fn(session)`` in one transaction; rollback on error."""
        with self._factory() as session:
            try:
                result = fn(session)
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    def dispose(self) -> None:
        self._engine.dispose()
        self._logger.info("database engine disposed")
