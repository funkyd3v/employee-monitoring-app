"""Application entry point.

Phase 1: bootstrap config + logging and run the lifecycle machinery. The Qt
UI, workers, and providers register lifecycle steps in their phases; this
function stays a thin, stable wrapper.
"""

from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from app.config.constants import APP_VERSION
from app.core.container import bootstrap_container
from app.core.lifecycle import Lifecycle, LifecycleContext
from app.ui import controller as ui_controller
from app.ui.theme import apply_theme


def _make_app() -> QApplication:
    """Create (or reuse) the Qt application and apply the theme."""
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="employee-monitoring-agent",
        description="Employee monitoring desktop agent (client-side).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"employee-monitoring-agent {APP_VERSION}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)

    container = bootstrap_container()
    logger = container.logger

    # Qt infrastructure: one QApplication per process, themed, and never
    # quitting when the last window hides (close-to-tray is not an exit).
    app = _make_app()

    lifecycle = Lifecycle(LifecycleContext())

    # Database lifecycle: migrate at start, dispose on shutdown
    # (docs/ARCHITECTURE.md § Application lifecycle).
    lifecycle.add(
        "database",
        start=lambda _ctx: container.open_database(),
        shutdown=lambda _ctx: container.close_database(),
    )

    # Authentication restore: after the DB is ready, before the UI.
    # A saved session puts the app straight into READY; otherwise LOGGED_OUT.
    def _restore_auth(_ctx: LifecycleContext) -> None:
        user = container.auth_service.restore()
        if user is None:
            logger.info("No saved session — awaiting login.")
        else:
            logger.info("Authentication restored for %s", user.email)
            # Bind the restored user to the session engine so tick/restore
            # derive elapsed time from persisted timestamps.
            uid = container.auth_service.current_user_id()
            container.session_service.set_user(uid)
        lifecycle.context.set("auth_user", user)

    def _close_auth(_ctx: LifecycleContext) -> None:
        container.auth_service.shutdown()

    lifecycle.add("auth", start=_restore_auth, shutdown=_close_auth)

    # Session restore: after auth restore, before the UI starts.
    # Requires the user_id to be bound (see _restore_auth above).
    def _restore_session(_ctx: LifecycleContext) -> None:
        # Ensure user_id is bound even if auth restore was skipped in tests
        uid = container.auth_service.current_user_id()
        if uid is not None:
            container.session_service.set_user(uid)
        if container.session_service.restore_session():
            logger.info("Session restored successfully.")
        else:
            logger.info("No persisted session to restore.")

    lifecycle.add("session", start=_restore_session)

    # Activity worker — started after session restore, stopped before DB close.
    # Runs on a dedicated QThread; UI updates flow via Qt signals only.
    activity_supervisor: object | None = None

    def _start_activity(_ctx: LifecycleContext) -> None:
        nonlocal activity_supervisor
        try:
            from app.workers.activity_worker import ActivityWorkerSupervisor

            supervisor = ActivityWorkerSupervisor(
                container.activity_provider,
                container.activity_service,
                poll_interval_ms=5000,
            )
            supervisor.state_changed.connect(
                lambda _state: container.session_service.tick()  # keep service state warm
            )
            activity_supervisor = supervisor
            supervisor.start()
            lifecycle.context.set("activity_supervisor", supervisor)
            # If a session was restored as WORKING, re-attach tracking is already
            # done in SessionService.restore_session → ActivityService.restore.
            # The worker's poll will pick up idle after the first interval.
            logger.info("Activity worker started")
        except Exception as exc:
            logger.error("failed to start activity worker: %s", exc, exc_info=True)

    def _stop_activity(_ctx: LifecycleContext) -> None:
        nonlocal activity_supervisor
        import contextlib

        with contextlib.suppress(Exception):
            container.activity_service.shutdown()
        sup = lifecycle.context.get("activity_supervisor")
        if sup is not None:
            with contextlib.suppress(Exception):
                sup.stop()  # type: ignore[attr-defined]
        if activity_supervisor is not None:
            with contextlib.suppress(Exception):
                activity_supervisor.stop()  # type: ignore[attr-defined]
            activity_supervisor = None

    lifecycle.add("activity", start=_start_activity, shutdown=_stop_activity)

    ui = ui_controller.UiController(container, app)

    def _start_ui(_ctx: LifecycleContext) -> None:
        ui.start()
        logger.info(
            "Bootstrap complete. Data dir: %s (mode=%s)",
            container.storage_root(),
            container.mode,
        )

    def _stop_ui(_ctx: LifecycleContext) -> None:
        ui.shutdown()

    lifecycle.add("ui", start=_start_ui, shutdown=_stop_ui)

    lifecycle.start()
    try:
        exit_code = app.exec()
    finally:
        lifecycle.shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
