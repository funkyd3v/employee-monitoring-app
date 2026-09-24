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
        lifecycle.context.set("auth_user", user)

    def _close_auth(_ctx: LifecycleContext) -> None:
        container.auth_service.shutdown()

    lifecycle.add("auth", start=_restore_auth, shutdown=_close_auth)

    # UI: owns the windows + tray, pumps the event loop until Exit (tray).
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
