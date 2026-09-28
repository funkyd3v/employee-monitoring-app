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
from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger, setup_logging
from app.domain.activity.provider import ActivityProvider, DummyActivityProvider
from app.domain.auth.auth import (
    AuthProvider,
    DummyAuthConfig,
    LocalDummyAuthProvider,
)
from app.domain.auth.token_holder import AuthTokenHolder
from app.domain.policy.provider import ConfigProvider, LocalConfigProvider
from app.domain.sessions.state_machine import SessionMachine
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.network.contract import ApiContract, load_contract
from app.infrastructure.network.http_client import ApiHttpClient
from app.infrastructure.security.credential_store import build_credential_store
from app.infrastructure.system.startup import StartupManager, build_startup_manager
from app.services.activity_service import ActivityService
from app.services.auth_service import AuthService
from app.services.cleanup_service import CleanupService
from app.services.policy_service import PolicyService
from app.services.screenshot_service import ScreenshotService
from app.services.session_service import SessionService
from app.services.sync_service import SyncService

if TYPE_CHECKING:
    import logging

    from app.domain.auth.workspace import WorkspaceProvider
    from app.domain.screenshots.provider import ScreenshotProvider
    from app.domain.sync.provider import SyncProvider


class Container:
    """Holds wired application objects; constructed once at bootstrap."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.logger: logging.Logger = get_logger("core")
        self._api_client: ApiHttpClient | None = None
        self._api_contract: ApiContract | None = None

        self.database: Database = Database(
            Path(settings.subdir(DATABASE_DIR)) / DB_FILENAME
        )

        # The single app-level state machine. Auth drives LOGIN/LOGOUT now;
        # the session engine drives CHECK_IN/… in Phase 5 — same instance.
        self.session_machine = SessionMachine()

        # The wire contract, built once. Every API provider receives this
        # object rather than hardcoding paths, which is what makes a new
        # endpoint (or a differently-shaped backend) a configuration change.
        # Only touched in api mode; local mode never loads the file.
        if settings.mode == "api":
            self._api_contract = load_contract(settings.local.api_contract_file)
            self.logger.info("api contract: %s", self._api_contract.describe())

        # Phase 3 authentication. The provider/credential-store selection is
        # mode-aware: the local dummy in local mode, the HTTP provider in api
        # mode. Both sit behind AuthProvider, so nothing downstream changes.
        self.credential_store = build_credential_store(settings, keyring_api=None)
        self.token_holder = AuthTokenHolder()
        self.auth_service = AuthService(
            auth_provider=self._build_auth_provider(self.token_holder),
            credential_store=self.credential_store,
            session_factory=self.database.session,
            machine=self.session_machine,
            token_holder=self.token_holder,
        )

        # Workspace profile — the label the app shows. Local mode returns
        # nothing to fetch; api mode polls the backend's workspace endpoint.
        self.workspace_provider: WorkspaceProvider = self._build_workspace_provider()

        # Operator policy (docs/ARCHITECTURE.md § Backend-readiness). Local
        # mode hands back the configured defaults; api mode can pull newer
        # values without a rebuild.
        self.config_provider: ConfigProvider = self._build_config_provider()

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
        self.sync_provider: SyncProvider = self._build_sync_provider(self.token_holder)
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

        # Remote policy, applied to the running services. In api mode this is
        # how an operator changes the screenshot interval or idle threshold for
        # an installed agent without shipping an update.
        self.policy_service = PolicyService(
            config_provider=self.config_provider,
            settings=self.settings.server,
            screenshot_service=self.screenshot_service,
            activity_service=self.activity_service,
        )

        # Phase 9 Windows integration — startup behaviour (registry Run key)
        self.startup_manager: StartupManager = build_startup_manager()

    def open_database(self) -> None:
        """Migrate the schema to the current version at startup."""
        # Phase 9 crash-recovery: quarantine a corrupt DB before migrate
        try:
            from pathlib import Path as _P

            from app.config.constants import DATABASE_DIR, DB_FILENAME
            from app.infrastructure.system.crash_handler import verify_database

            db_path = _P(self.settings.subdir(DATABASE_DIR)) / DB_FILENAME
            verify_database(db_path)
        except Exception:  # noqa: S110
            pass
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

    def _build_auth_provider(self, token_holder: AuthTokenHolder) -> AuthProvider:
        """Select the auth provider for this mode (dummy local, HTTP api).

        A misconfigured api mode raises :class:`ConfigurationError` at
        bootstrap instead of silently falling back to the dummy provider: an
        employee who believes their data is being uploaded must never end up
        quietly recording into a local-only void.
        """
        if self.settings.mode == "api":
            from app.infrastructure.network.auth_adapter import ApiAuthProvider

            return ApiAuthProvider(
                client=self._build_api_client(),
                token_holder=token_holder,
                contract=self._require_contract(),
            )

        return LocalDummyAuthProvider(
            DummyAuthConfig(
                email=self.settings.local.dummy_email,
                password=self.settings.local.dummy_password,
            )
        )

    def _build_workspace_provider(self) -> WorkspaceProvider:
        """Select the workspace-profile provider for this mode."""
        if self.settings.mode == "api":
            from app.infrastructure.network.auth_adapter import ApiWorkspaceProvider

            return ApiWorkspaceProvider(
                client=self._build_api_client(),
                token_holder=self.token_holder,
                contract=self._require_contract(),
            )

        from app.domain.auth.workspace import LocalWorkspaceProvider

        return LocalWorkspaceProvider()

    def _build_config_provider(self) -> ConfigProvider:
        """Select the policy provider for this mode."""
        if self.settings.mode == "api":
            from app.infrastructure.network.config_adapter import ApiConfigProvider

            return ApiConfigProvider(
                client=self._build_api_client(),
                token_holder=self.token_holder,
                endpoint=self._require_contract().agent_config,
                fallback=self.settings.server,
            )

        return LocalConfigProvider(self.settings.server)

    def _require_contract(self) -> ApiContract:
        """The api-mode wire contract, built on first use.

        Split from :meth:`_build_api_client` so the two are constructed
        independently: the contract needs no socket, and a caller that only
        wanted the contract must not be handed a client to close.
        """
        if self._api_contract is None:
            self._api_contract = load_contract(self.settings.local.api_contract_file)
        return self._api_contract

    def _build_api_client(self) -> ApiHttpClient:
        """Create the shared HTTP client (lazy — never opened in local mode).

        Memoised: every api-mode provider talks over one pooled connection set,
        and one client means one ``close()`` to get right at shutdown.
        """
        if self._api_client is not None:
            return self._api_client

        base_url = self.settings.api_base_url
        if not base_url:
            raise ConfigurationError(
                "mode='api' requires api_base_url to be set "
                "(e.g. http://127.0.0.1:8000/api/v1)"
            )

        self._api_client = ApiHttpClient(
            base_url=base_url,
            connect_timeout=self.settings.local.api_connect_timeout_seconds,
            timeout=self.settings.local.api_timeout_seconds,
        )
        return self._api_client

    def _build_sync_provider(self, token_holder: AuthTokenHolder) -> SyncProvider:
        """Select sync provider by mode (dummy local, HTTP api)."""
        if self.settings.mode == "api":
            from app.infrastructure.network.sync_adapter import ApiSyncProvider

            return ApiSyncProvider(
                client=self._build_api_client(),
                token_holder=token_holder,
                contract=self._require_contract(),
            )

        from app.infrastructure.network.sync_adapter import DummySyncProvider

        return DummySyncProvider()

    def close_database(self) -> None:
        self.database.dispose()
        if self._api_client is not None:
            self._api_client.close()

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
    # Phase 9 logging hardening — global handlers + banner (never crash bootstrap)
    try:
        from app.core.logging import install_global_handlers, log_startup_banner

        install_global_handlers()
        log_startup_banner(settings)
    except Exception:  # noqa: S110
        pass
    root_logger.info("Application started (mode=%s)", settings.mode)

    container = Container(settings)
    return container
