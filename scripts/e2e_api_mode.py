"""End-to-end check: desktop agent in api mode against a real backend.

Not part of the test suite — it needs a running HTTP server. Point it at one
with ``EM_E2E_BASE_URL`` (default: the local Laravel dev server):

    EM_E2E=1 .venv/bin/python scripts/e2e_api_mode.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import AppSettings, LocalConfig
from app.core.container import Container
from app.domain.sync.provider import ConnectivityState

BASE_URL = os.environ.get("EM_E2E_BASE_URL", "http://127.0.0.1:8000/api/v1")
EMAIL = os.environ.get("EM_E2E_EMAIL", "employee@example.com")
PASSWORD = os.environ.get("EM_E2E_PASSWORD", "password")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        container = Container(
            AppSettings(
                local=LocalConfig(
                    mode="api",
                    data_dir=Path(tmp),
                    # "none" keeps the token in memory only: api mode refuses the
                    # plaintext dev-file backend, and this dev box has no keyring.
                    credential_backend="none",
                    api_base_url=BASE_URL,
                )
            )
        )
        container.open_database()

        def show(label: str, value: object) -> None:
            print(f"  {label:<34}{value}")

        print("== login ==")
        user = container.auth_service.login(EMAIL, PASSWORD)
        show("email", user.email)
        show("external_user_id", user.external_user_id)
        show("display_name", user.display_name)
        show("token published to holder", container.token_holder.has_token())
        container.session_service.set_user(container.auth_service.current_user_id())

        print("== connectivity ==")
        state = container.sync_provider.check_connectivity()
        show("check_connectivity()", state)
        assert state == ConnectivityState.ONLINE, state

        print("== check in ==")
        container.session_service.check_in()
        session = container.session_service.machine.session
        assert session is not None
        show("local session id", session.id)

        print("== break / resume ==")
        container.session_service.take_break()
        container.session_service.resume()
        show("state after resume", container.session_service.state.value)

        print("== screenshot ==")
        path = container.screenshot_service.capture_once()
        show("stored", path.name if path else None)

        print("== check out ==")
        view = container.session_service.check_out()
        show("worked seconds", view.elapsed_work_seconds)

        print("== sync drain 1 (outbox metadata) ==")
        show("result", container.sync_service.sync_once())

        print("== sync drain 2 (screenshot binary) ==")
        show("result", container.sync_service.sync_once())

        pending_dir = Path(tmp) / "screenshots" / "pending"
        show("local screenshot files left", len(list(pending_dir.glob("*.jpg"))))

        print("== workspace + policy (server-owned settings) ==")
        show("workspace at login", container.auth_service.current_user().workspace_name)
        show(
            "fetched workspace",
            container.workspace_provider.fetch_workspace().workspace_name,
        )
        container.policy_service.refresh()
        show(
            "screenshot interval",
            container.policy_service.policy.screenshot_interval_seconds,
        )
        show("idle threshold", container.policy_service.policy.idle_threshold_seconds)

        print("== logout ==")
        container.auth_service.logout()
        show("token after logout", container.token_holder.has_token())
        show("connectivity without token", container.sync_provider.check_connectivity())

        container.close_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
