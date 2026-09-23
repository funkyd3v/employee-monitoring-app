"""Application entry point.

Phase 1: bootstrap config + logging and run the lifecycle machinery. The Qt
UI, workers, and providers register lifecycle steps in their phases; this
function stays a thin, stable wrapper.
"""

from __future__ import annotations

import argparse
import sys

from app.config.constants import APP_VERSION
from app.core.container import bootstrap_container
from app.core.lifecycle import Lifecycle, LifecycleContext


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

    def _ready(context: LifecycleContext) -> None:  # noqa: ARG001
        # Phase 4+ replaces this with UI/dashboard startup.
        logger.info(
            "Bootstrap complete. Data dir: %s (mode=%s)",
            container.storage_root(),
            container.mode,
        )

    lifecycle.add("core", start=_ready, shutdown=None)

    lifecycle.start()
    try:
        # Future phases: run Qt event loop here; lifecycle.shutdown() runs
        # on clean exit.
        pass
    finally:
        lifecycle.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
