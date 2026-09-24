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
from app.domain.auth.auth import DummyAuthConfig, LocalDummyAuthProvider
from app.domain.sessions.state_machine import SessionMachine
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.security.credential_store import build_credential_store
from app.services.auth_service import AuthService
from app.services.session_service import SessionService

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

        # The single app-level state machine. Auth drives LOGIN/LOGOUT now;
        # the session engine drives CHECK_IN/… in Phase 5 — same instance.
        self.session_machine = SessionMachine()

        # Phase 3 authentication. The provider/credential-store selection is
        # mode-aware (dummy provider now; a future ApiAuthProvider slots in
        # without touching the service).
        self.credential_store = build_credential_store(settings, keyring_api=None)
        self.auth_service = AuthService(
            auth_provider=LocalDummyAuthProvider(
                DummyAuthConfig(
                    email=settings.local.dummy_email,
                    password=settings.local.dummy_password,
                )
            ),
            credential_store=self.credential_store,
            session_factory=self.database.session,
            machine=self.session_machine,
        )

        # Phase 4 UI orchestration. The session service shares the app-level
        # machine with auth, so today's in-memory projection and Phase 5's
        # persistence live behind the same surface.
        self.session_service = SessionService(machine=self.session_machine)

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
