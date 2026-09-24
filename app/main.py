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

    # Screenshot worker — drift-resistant, atomic pipeline, idle-aware.
    screenshot_supervisor: object | None = None

    def _recover_screenshots(_ctx: LifecycleContext) -> None:
        try:
            result = container.screenshot_service.recover_orphans()
            if result["orphan_files_removed"] or result["orphan_records_removed"]:
                logger.info("Screenshot orphan recovery: %s", result)
            else:
                logger.debug("Screenshot orphan recovery: no orphans")
        except Exception as exc:
            logger.error("screenshot orphan recovery failed: %s", exc, exc_info=True)

    def _start_screenshots(_ctx: LifecycleContext) -> None:
        nonlocal screenshot_supervisor
        _recover_screenshots(_ctx)
        try:
            from app.workers.screenshot_worker import ScreenshotWorkerSupervisor

            supervisor = ScreenshotWorkerSupervisor(
                container.screenshot_service,
                poll_interval_ms=1000,
            )
            screenshot_supervisor = supervisor
            supervisor.start()
            lifecycle.context.set("screenshot_supervisor", supervisor)

            # If a session was restored as WORKING, configure schedule now
            session = container.session_service.machine.session
            if session is not None and session.id is not None:
                from app.domain.sessions.session import WorkSessionStatus

                if session.status == WorkSessionStatus.WORKING:
                    # Use session start as schedule anchor (drift-resistant)
                    supervisor.set_schedule(session.started_at, container.screenshot_service.interval_seconds)
                elif session.status == WorkSessionStatus.BREAK:
                    supervisor.pause()

            # Hook session actions to schedule updates (via container signal would be ideal;
            # for now lifecycle observes via polling would work, but we hook via monkey-patch
            # of session_service methods for immediate reaction without circular import).
            orig_check_in = container.session_service.check_in
            orig_take_break = container.session_service.take_break
            orig_resume = container.session_service.resume
            orig_check_out = container.session_service.check_out

            def check_in_wrapper(*a, **kw):  # type: ignore[no-untyped-def]
                view = orig_check_in(*a, **kw)
                try:
                    sess = container.session_service.machine.session
                    if sess is not None:
                        supervisor.set_schedule(sess.started_at, container.screenshot_service.interval_seconds)
                        supervisor.resume()
                except Exception:
                    pass
                return view

            def break_wrapper(*a, **kw):  # type: ignore[no-untyped-def]
                view = orig_take_break(*a, **kw)
                try:
                    supervisor.pause()
                except Exception:
                    pass
                return view

            def resume_wrapper(*a, **kw):  # type: ignore[no-untyped-def]
                view = orig_resume(*a, **kw)
                try:
                    supervisor.resume()
                except Exception:
                    pass
                return view

            def checkout_wrapper(*a, **kw):  # type: ignore[no-untyped-def]
                view = orig_check_out(*a, **kw)
                try:
                    supervisor.pause()
                except Exception:
                    pass
                return view

            container.session_service.check_in = check_in_wrapper  # type: ignore[method-assign]
            container.session_service.take_break = break_wrapper  # type: ignore[method-assign]
            container.session_service.resume = resume_wrapper  # type: ignore[method-assign]
            container.session_service.check_out = checkout_wrapper  # type: ignore[method-assign]

            logger.info("Screenshot worker started interval=%ss", container.screenshot_service.interval_seconds)
        except Exception as exc:
            logger.error("failed to start screenshot worker: %s", exc, exc_info=True)

    def _stop_screenshots(_ctx: LifecycleContext) -> None:
        nonlocal screenshot_supervisor
        sup = lifecycle.context.get("screenshot_supervisor")
        if sup is not None:
            import contextlib

            with contextlib.suppress(Exception):
                sup.stop()  # type: ignore[attr-defined]
        if screenshot_supervisor is not None:
            import contextlib

            with contextlib.suppress(Exception):
                screenshot_supervisor.stop()  # type: ignore[attr-defined]
            screenshot_supervisor = None

    lifecycle.add("screenshots", start=_start_screenshots, shutdown=_stop_screenshots)

    # Sync worker — offline queue drain with backoff, confirm-then-delete.
    sync_supervisor: object | None = None

    def _recover_sync(_ctx: LifecycleContext) -> None:
        try:
            result = container.sync_service.recover_stale()
            if result["queue_reset"] or result["screenshots_reset"]:
                logger.info("Sync stale recovery: %s", result)
            else:
                logger.debug("Sync stale recovery: no stale rows")
        except Exception as exc:
            logger.error("sync stale recovery failed: %s", exc, exc_info=True)

    def _start_sync(_ctx: LifecycleContext) -> None:
        nonlocal sync_supervisor
        _recover_sync(_ctx)
        try:
            from app.workers.sync_worker import SyncWorkerSupervisor

            supervisor = SyncWorkerSupervisor(
                container.sync_service,
                poll_interval_ms=30000,
            )
            sync_supervisor = supervisor
            supervisor.start()
            lifecycle.context.set("sync_supervisor", supervisor)
            logger.info("Sync worker started")
        except Exception as exc:
            logger.error("failed to start sync worker: %s", exc, exc_info=True)

    def _stop_sync(_ctx: LifecycleContext) -> None:
        nonlocal sync_supervisor
        sup = lifecycle.context.get("sync_supervisor")
        if sup is not None:
            import contextlib

            with contextlib.suppress(Exception):
                sup.stop()  # type: ignore[attr-defined]
        if sync_supervisor is not None:
            import contextlib

            with contextlib.suppress(Exception):
                sync_supervisor.stop()  # type: ignore[attr-defined]
            sync_supervisor = None

    lifecycle.add("sync", start=_start_sync, shutdown=_stop_sync)

    # Cleanup worker — post-sync deletion with retention.
    cleanup_supervisor: object | None = None

    def _start_cleanup(_ctx: LifecycleContext) -> None:
        nonlocal cleanup_supervisor
        try:
            from app.workers.cleanup_worker import CleanupWorkerSupervisor

            supervisor = CleanupWorkerSupervisor(
                container.cleanup_service,
                poll_interval_ms=300000,
            )
            cleanup_supervisor = supervisor
            supervisor.start()
            lifecycle.context.set("cleanup_supervisor", supervisor)
            logger.info("Cleanup worker started")
        except Exception as exc:
            logger.error("failed to start cleanup worker: %s", exc, exc_info=True)

    def _stop_cleanup(_ctx: LifecycleContext) -> None:
        nonlocal cleanup_supervisor
        sup = lifecycle.context.get("cleanup_supervisor")
        if sup is not None:
            import contextlib

            with contextlib.suppress(Exception):
                sup.stop()  # type: ignore[attr-defined]
        if cleanup_supervisor is not None:
            import contextlib

            with contextlib.suppress(Exception):
                cleanup_supervisor.stop()  # type: ignore[attr-defined]
            cleanup_supervisor = None

    lifecycle.add("cleanup", start=_start_cleanup, shutdown=_stop_cleanup)

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
