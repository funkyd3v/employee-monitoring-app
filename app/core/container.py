"""Dependency-injection container.

The single place concrete implementations are wired to interfaces
(docs/ARCHITECTURE.md § Backend-readiness, docs/PROJECT_STRUCTURE.md).
Business logic and UI never import a concrete provider directly — they
receive one from here, selected by a single ``mode: "local" | "api"`` flag.

Phase 1 wired bootstrap concerns (settings, logging, paths). Phase 2 adds
the :class:`Database` handle and its session creation; repositories and
domain services register here as their phases land.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.config.constants import DATABASE_DIR, DB_FILENAME
from app.config.settings import AppSettings, LocalConfig, ServerPolicy
from app.core.logging import get_logger, setup_logging
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate

if TYPE_CHECKING:
    import logging


class Container:
    """Holds wired application objects; constructed once at bootstrap."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.logger: logging.Logger = get_logger("core")

        self.database: Database = Database(
            Path(settings.subdir(DATABASE_DIR)) / DB_FILENAME
        )
        # Phase 3+: repositories, domain services, workers — registered
        # here, never imported by callers.

    def open_database(self) -> None:
        """Migrate the schema to the current version at startup."""
        version = migrate(self.database.engine)
        self.logger.info("database ready (schema v%s)", version)

    def close_database(self) -> None:
        self.database.dispose()

    @property
    def mode(self) -> str:
        return self.settings.mode

    def storage_root(self) -> str:
        return str(self.settings.data_dir)


def bootstrap_container(
    local: LocalConfig | None = None,
    server: ServerPolicy | None = None,
    *,
    console_logging: bool = True,
) -> Container:
    """Build a fully wired container.

    Order matters and mirrors docs/ARCHITECTURE.md § Application lifecycle:
    load config → init logging → (open db at lifecycle start, later phases:
    services, workers).
    """
    settings = AppSettings(local=local, server=server)

    root_logger = setup_logging(
        log_dir=settings.subdir("logs"),
        level=settings.local.log_level,
        max_bytes=settings.local.log_max_bytes,
        backup_count=settings.local.log_backup_count,
        console=console_logging,
    )
    root_logger.info("Application started (mode=%s)", settings.mode)

    container = Container(settings)
    return container
