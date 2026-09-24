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
from app.domain.activity.provider import ActivityProvider, DummyActivityProvider
from app.domain.auth.auth import DummyAuthConfig, LocalDummyAuthProvider
from app.domain.sessions.state_machine import SessionMachine
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.security.credential_store import build_credential_store
from app.services.activity_service import ActivityService
from app.services.auth_service import AuthService
from app.services.cleanup_service import CleanupService
from app.services.screenshot_service import ScreenshotService
from app.services.session_service import SessionService
from app.services.sync_service import SyncService

if TYPE_CHECKING:
    import logging

    from app.domain.screenshots.provider import ScreenshotProvider
    from app.domain.sync.provider import SyncProvider


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

        # Phase 6 activity monitoring. Provider is platform-selected; service
        # owns idle-threshold policy and persisted activity_periods.
        self.activity_provider: ActivityProvider = self._build_activity_provider()
        self.activity_service = ActivityService(
            session_factory=self.database.session,
            idle_threshold_seconds=self.settings.server.idle_threshold_seconds,
        )
        # Let SessionService project real active/idle breakdown & pill state
        self.session_service.set_activity_service(self.activity_service)

        # Phase 7 screenshots — drift-resistant, atomic, idle-aware.
        self.screenshot_provider: ScreenshotProvider = self._build_screenshot_provider()
        self.screenshot_service = ScreenshotService(
            provider=self.screenshot_provider,
            session_factory=self.database.session,
            activity_service=self.activity_service,
            data_dir=self.settings.data_dir,
            interval_seconds=self.settings.server.screenshot_interval_seconds,
        )
        self.session_service.set_screenshot_service(self.screenshot_service)

        # Phase 8 offline queue & recovery — sync and cleanup.
        self.sync_provider: SyncProvider = self._build_sync_provider()
        self.sync_service = SyncService(
            provider=self.sync_provider,
            session_factory=self.database.session,
            batch_limit=self.settings.sync_batch_limit,
        )
        self.cleanup_service = CleanupService(
            session_factory=self.database.session,
            data_dir=self.settings.data_dir,
            retention_days=self.settings.retention_days,
        )

    def open_database(self) -> None:
        """Migrate the schema to the current version at startup."""
        version = migrate(self.database.engine)
        self.logger.info("database ready (schema v%s)", version)
        self._inject_persistence()

    def _inject_persistence(self) -> None:
        """Wire SessionRepository to SessionService (Phase 5 persistence)."""
        import contextlib

        # SessionService now only needs the factory; it creates short-lived
        # repositories per transaction. Passing a repo bound to a closed session
        # would be detached — don't do that.
        self.session_service.set_session_factory(self.database.session)
        self.activity_service.set_session_factory(self.database.session)
        self.screenshot_service.set_session_factory(self.database.session)
        self.screenshot_service.set_data_dir(self.settings.data_dir)
        self.sync_service.set_session_factory(self.database.session)
        self.cleanup_service.set_session_factory(self.database.session)
        self.cleanup_service.set_data_dir(self.settings.data_dir)
        # keep old dual-arg shim working for any external callers
        with contextlib.suppress(Exception):
            self.session_service.set_persistence(session_factory=self.database.session)

    @staticmethod
    def _build_activity_provider() -> ActivityProvider:
        """Select the activity provider for this platform/mode."""
        import sys

        if sys.platform == "win32":
            try:
                from app.infrastructure.activity.windows_activity_provider import (
                    WindowsActivityProvider,
                )

                return WindowsActivityProvider()
            except Exception:  # noqa: S110
                pass
        return DummyActivityProvider()

    @staticmethod
    def _build_screenshot_provider() -> ScreenshotProvider:
        """Select screenshot provider — MSS on Windows, dummy elsewhere."""
        import sys

        if sys.platform == "win32":
            try:
                from app.infrastructure.screenshots.screenshot_provider import (
                    MssScreenshotProvider,
                )

                return MssScreenshotProvider()
            except Exception:  # noqa: S110
                pass
        from app.infrastructure.screenshots.screenshot_provider import (
            DummyScreenshotProvider,
        )

        return DummyScreenshotProvider()

    def _build_sync_provider(self) -> SyncProvider:
        """Select sync provider by mode (dummy today, API tomorrow)."""
        if self.settings.mode == "api":
            try:
                from app.infrastructure.network.sync_adapter import ApiSyncProvider

                return ApiSyncProvider(base_url="")
            except Exception:  # noqa: S110
                pass
        from app.infrastructure.network.sync_adapter import DummySyncProvider

        return DummySyncProvider()

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
