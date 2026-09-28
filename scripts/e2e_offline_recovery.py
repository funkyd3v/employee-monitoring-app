"""End-to-end check: the offline-first guarantee.

Runs the desktop agent against a real backend in three phases:

  1. backend up    — data syncs, local outbox drains
  2. backend down  — check-in, break and a screenshot are recorded locally and
                     the outbox keeps them; nothing is lost
  3. backend up    — the backlog drains on its own

This is the "never delete local data on a failed or attempted upload" rule
(docs/ENGINEERING_RULES.md §Data Deletion Rule) verified against an actual
server rather than a mock.

    EM_E2E=1 .venv/bin/python scripts/e2e_offline_recovery.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import AppSettings, LocalConfig
from app.core.container import Container

BASE_URL = os.environ.get("EM_E2E_BASE_URL", "http://127.0.0.1:8000/api/v1")
EMAIL = os.environ.get("EM_E2E_EMAIL", "employee@example.com")
PASSWORD = os.environ.get("EM_E2E_PASSWORD", "password")


def show(label: str, value: object) -> None:
    print(f"  {label:<34}{value}")


def start_server() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["php", "artisan", "serve", "--host=127.0.0.1", "--port=8000"],  # noqa: S607
        cwd=Path(__file__).resolve().parents[2] / "empolee-monitoring-backend",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        container = Container(
            AppSettings(
                local=LocalConfig(
                    mode="api",
                    data_dir=Path(tmp),
                    credential_backend="none",
                    api_base_url=BASE_URL,
                )
            )
        )
        container.open_database()
        container.auth_service.login(EMAIL, PASSWORD)
        container.session_service.set_user(container.auth_service.current_user_id())

        print("== phase 1: backend up ==")
        container.session_service.check_in()
        container.screenshot_service.capture_once()
        show("drain", container.sync_service.sync_once())

        print("== phase 2: backend down ==")
        subprocess.run(["pkill", "-f", "artisan serve"], check=False)  # noqa: S607
        time.sleep(1.5)

        show("connectivity", container.sync_provider.check_connectivity())
        container.session_service.take_break()
        container.session_service.resume()
        container.screenshot_service.capture_once()
        container.session_service.check_out()

        offline = container.sync_service.sync_once()
        show("drain", offline)
        show("still queued", offline["pending"])
        assert offline["pending"] > 0, "offline work must stay queued"
        local_shots = len(list((Path(tmp) / "screenshots" / "pending").glob("*.jpg")))
        show("local screenshots kept", local_shots)
        assert local_shots > 0, "local screenshot must not be deleted while unsynced"

        print("== phase 3: backend back ==")
        start_server()
        time.sleep(3.0)
        show("connectivity", container.sync_provider.check_connectivity())

        # The first drain does the work (batch_limit is 20); the second proves
        # the outbox is empty rather than merely quiet for this tick.
        show("drain", container.sync_service.sync_once())
        final = container.sync_service.sync_once()
        show("drain (idle tick)", final)
        show("still queued", final["pending"])
        assert final["pending"] == 0, "backlog must drain once the backend returns"

        container.auth_service.logout()
        container.close_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
