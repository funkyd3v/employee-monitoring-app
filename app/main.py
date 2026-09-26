"""Application entry point.

Phase 1: bootstrap config + logging and run the lifecycle machinery. The Qt
UI, workers, and providers register lifecycle steps in their phases; this
function stays a thin, stable wrapper.
"""

from __future__ import annotations

import argparse
import contextlib
import sys

from PySide6.QtWidgets import QApplication

from app.config.constants import APP_VERSION
from app.core.container import bootstrap_container
from app.core.lifecycle import Lifecycle, LifecycleContext
from app.infrastructure.system.activation import bring_to_front
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
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized to tray (used by autostart registry entry).",
    )
    return parser


class _AlreadyRunning(SystemExit):
    """Another instance owns the data dir and was asked to open its window.

    Raised out of the single-instance step so this copy exits as quietly as a
    successful double-click on the shortcut: exit code 0, no second window, no
    second writer on the SQLite file.
    """


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    container = bootstrap_container()
    logger = container.logger

    # Qt infrastructure: one QApplication per process, themed, and never
    # quitting when the last window hides (close-to-tray is not an exit).
    app = _make_app()

    lifecycle = Lifecycle(LifecycleContext())

    # Phase 9: single-instance guard — prevents concurrent DB corruption.
    # Must be the first lifecycle step so its shutdown is last (reverse).
    from pathlib import Path as _P

    guard: object | None = None
    activator: object | None = None

    def _acquire_single_instance(_ctx: LifecycleContext) -> None:
        nonlocal activator, guard
        from app.infrastructure.system.activation import (
            InstanceActivator,
            activation_server_name,
            request_show,
        )
        from app.infrastructure.system.single_instance import SingleInstanceGuard

        data_dir = container.settings.data_dir
        g = SingleInstanceGuard(_P(data_dir) / "app.lock")
        if not g.acquire():
            # Another instance owns the data dir. A second copy must never
            # exit as a silent no-op (double-clicking the shortcut has to
            # open the app), so ask the running instance to surface its
            # window and leave quietly.
            if request_show(activation_server_name(data_dir)):
                logger.info("Another instance is already running — asked it to open.")
                raise _AlreadyRunning(0)
            logger.error("Another instance is already running — exiting.")
            raise SystemExit(1)
        guard = g
        lifecycle.context.set("single_instance_guard", g)

        # The activation channel starts together with the guard: a second
        # launch must never find the lock held but nobody listening.
        act = InstanceActivator(activation_server_name(data_dir))
        act.start()
        activator = act
        lifecycle.context.set("instance_activator", act)

    def _release_single_instance(_ctx: LifecycleContext) -> None:
        nonlocal activator, guard
        act = lifecycle.context.get("instance_activator")
        if act is not None:
            with contextlib.suppress(Exception):
                act.stop()  # type: ignore[attr-defined]
        if activator is not None:
            with contextlib.suppress(Exception):
                activator.stop()  # type: ignore[attr-defined]
            activator = None
        g = lifecycle.context.get("single_instance_guard")
        if g is not None:
            try:
                g.release()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("single-instance release failed", exc_info=True)
        if guard is not None:
            try:
                guard.release()  # type: ignore[attr-defined]
            except Exception:
                pass
            guard = None

    lifecycle.add(
        "single_instance",
        start=_acquire_single_instance,
        shutdown=_release_single_instance,
    )

    # Database lifecycle: migrate at start, dispose on shutdown
    # (docs/ARCHITECTURE.md § Application lifecycle).
    lifecycle.add(
        "database",
        start=lambda _ctx: container.open_database(),
        shutdown=lambda _ctx: container.close_database(),
    )

    # Phase 9: system power / session events (sleep/lock/resume, TaskbarCreated)
    # Installed right after DB so it outlives all workers (reverse shutdown).
    power_manager: object | None = None

    def _start_power(_ctx: LifecycleContext) -> None:
        nonlocal power_manager
        try:
            from app.infrastructure.system.power import SystemPowerManager

            mgr = SystemPowerManager()
            mgr.install(app)
            power_manager = mgr
            lifecycle.context.set("power_manager", mgr)
            logger.info("Power event manager installed (native=%s)", mgr.is_installed)
        except Exception as exc:
            logger.warning("power manager install failed: %s", exc, exc_info=True)

    def _stop_power(_ctx: LifecycleContext) -> None:
        nonlocal power_manager
        mgr = lifecycle.context.get("power_manager")
        if mgr is not None:
            try:
                mgr.uninstall(app)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("power manager uninstall failed", exc_info=True)
        if power_manager is not None:
            try:
                power_manager.uninstall(app)  # type: ignore[attr-defined]
            except Exception:
                pass
            power_manager = None

    lifecycle.add("power", start=_start_power, shutdown=_stop_power)

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
                lambda _state: (
                    container.session_service.tick()
                )  # keep service state warm
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
                    supervisor.set_schedule(
                        session.started_at,
                        container.screenshot_service.interval_seconds,
                    )
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
                        supervisor.set_schedule(
                            sess.started_at,
                            container.screenshot_service.interval_seconds,
                        )
                        supervisor.resume()
                except Exception:
                    pass
                return view

            def break_wrapper(*a, **kw):  # type: ignore[no-untyped-def]
                view = orig_take_break(*a, **kw)
                with contextlib.suppress(Exception):
                    supervisor.pause()
                return view

            def resume_wrapper(*a, **kw):  # type: ignore[no-untyped-def]
                view = orig_resume(*a, **kw)
                with contextlib.suppress(Exception):
                    supervisor.resume()
                return view

            def checkout_wrapper(*a, **kw):  # type: ignore[no-untyped-def]
                view = orig_check_out(*a, **kw)
                with contextlib.suppress(Exception):
                    supervisor.pause()
                return view

            container.session_service.check_in = check_in_wrapper  # type: ignore[method-assign]
            container.session_service.take_break = break_wrapper  # type: ignore[method-assign]
            container.session_service.resume = resume_wrapper  # type: ignore[method-assign]
            container.session_service.check_out = checkout_wrapper  # type: ignore[method-assign]

            logger.info(
                "Screenshot worker started interval=%ss",
                container.screenshot_service.interval_seconds,
            )
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
            stale = container.sync_service.recover_stale()
            orphans = container.sync_service.recover_orphans()
            # Also run screenshot file↔DB orphan scan here so sync cleanup sees consistent state
            try:
                shot_orphans = container.screenshot_service.recover_orphans()
            except Exception:
                shot_orphans = {"orphan_files_removed": 0, "orphan_records_removed": 0}
                logger.debug(
                    "screenshot orphan recovery in sync step failed", exc_info=True
                )
            combined = {**stale, **orphans, **shot_orphans}
            if any(combined.values()):
                logger.info("Sync recovery: %s", combined)
            else:
                logger.debug("Sync recovery: no orphans/stale")
        except Exception as exc:
            logger.error("sync recovery failed: %s", exc, exc_info=True)

    def _start_sync(_ctx: LifecycleContext) -> None:
        nonlocal sync_supervisor
        _recover_sync(_ctx)
        try:
            from app.workers.sync_worker import SyncWorkerSupervisor

            supervisor = SyncWorkerSupervisor(
                container.sync_service,
                poll_interval_ms=container.settings.sync_poll_seconds * 1000,
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
                poll_interval_ms=container.settings.cleanup_poll_seconds * 1000,
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

    def _surface_window(_request: str = "show") -> None:
        """A second launch relayed its "show yourself" request to us."""
        ui.show_main_window()

    # Second-launch activation — a copy that lost the single-instance guard
    # asks the winner to surface its window. Connections are made once the
    # UI exists, same as the power wiring below.
    def _wire_activation_signals() -> None:
        # Anything the UI presents also gets the OS-level foreground nudge:
        # Windows refuses foreground changes from a background process, so a
        # window raised from the tray (or from another process) can otherwise
        # land behind whatever the user was already looking at.
        ui.window_presented.connect(bring_to_front)
        act = lifecycle.context.get("instance_activator")
        if act is None:
            return
        try:
            act.show_requested.connect(  # type: ignore[attr-defined]
                _surface_window
            )
            logger.info("Activation channel wired to the UI")
        except Exception:
            logger.warning("activation→ui wiring failed", exc_info=True)

    # Phase 9 wiring helper — connect power manager signals to workers/tray.
    # The manager is installed as a lifecycle step, but connections must be
    # (re)established after workers/tray exist. Use lifecycle context lookup
    # so the lambdas stay valid even if workers restart.
    def _wire_power_signals() -> None:
        mgr = lifecycle.context.get("power_manager")
        if mgr is None:
            return
        try:
            from PySide6.QtCore import QMetaObject, Qt

            # Tray — TaskbarCreated and resume orphan recovery
            try:
                mgr.taskbar_created.connect(ui.tray.handle_taskbar_created)  # type: ignore[attr-defined]
                mgr.system_resume.connect(ui.tray.handle_system_resume)  # type: ignore[attr-defined]
            except Exception:
                logger.debug("power→tray wiring failed", exc_info=True)

            # Workers — suspend/resume with lock/unlock mapping
            # Use queued dispatch so cross-thread slots are safe.
            def _suspend_workers() -> None:
                for key in ("activity_supervisor", "screenshot_supervisor"):
                    sup = lifecycle.context.get(key)
                    if sup is not None:
                        try:
                            QMetaObject.invokeMethod(
                                sup.worker,  # type: ignore[attr-defined]
                                "handle_system_suspend",
                                Qt.ConnectionType.QueuedConnection,
                            )
                        except Exception:
                            try:
                                sup.worker.handle_system_suspend()  # type: ignore[attr-defined]
                            except Exception:
                                pass
                    # Fallback to service-level pause for immediate effect
                    if key == "activity_supervisor":
                        try:
                            # Don't synthesize activity — just pause polling
                            pass
                        except Exception:
                            pass

            def _resume_workers() -> None:
                for key in ("activity_supervisor", "screenshot_supervisor"):
                    sup = lifecycle.context.get(key)
                    if sup is not None:
                        try:
                            QMetaObject.invokeMethod(
                                sup.worker,  # type: ignore[attr-defined]
                                "handle_system_resume",
                                Qt.ConnectionType.QueuedConnection,
                            )
                        except Exception:
                            try:
                                sup.worker.handle_system_resume()  # type: ignore[attr-defined]
                            except Exception:
                                pass
                # Also refresh session tick so timer doesn't show gap
                with contextlib.suppress(Exception):
                    container.session_service.tick()

            mgr.system_suspend.connect(_suspend_workers)  # type: ignore[attr-defined]
            mgr.system_resume.connect(_resume_workers)  # type: ignore[attr-defined]
            mgr.session_locked.connect(_suspend_workers)  # type: ignore[attr-defined]
            mgr.session_unlocked.connect(_resume_workers)  # type: ignore[attr-defined]
            logger.info("Power → workers/tray wiring complete")
        except Exception as exc:
            logger.warning("power wiring failed: %s", exc, exc_info=True)

    def _start_ui(_ctx: LifecycleContext) -> None:
        ui.start()
        # Wire power → workers/tray now that all lifecycle objects exist
        _wire_power_signals()
        _wire_activation_signals()
        # Phase 9: autostart entry handled via settings? StartupManager is
        # container-provided for external toggles; no auto-enable here.
        # Minimize-to-tray when launched via registry ( --minimized )
        if getattr(args, "minimized", False):
            try:
                # Keep tray visible but hide any dashboard/login that UiController showed
                ui.dashboard.hide()
                ui.login.hide()
                logger.info("Started minimized to tray (autostart).")
            except Exception:
                logger.debug("minimized hide failed", exc_info=True)
        logger.info(
            "Bootstrap complete. Data dir: %s (mode=%s)",
            container.storage_root(),
            container.mode,
        )

    def _stop_ui(_ctx: LifecycleContext) -> None:
        ui.shutdown()

    lifecycle.add("ui", start=_start_ui, shutdown=_stop_ui)

    try:
        lifecycle.start()
        exit_code = app.exec()
    except _AlreadyRunning:
        return 0
    finally:
        lifecycle.shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
