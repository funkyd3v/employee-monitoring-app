"""Second-instance activation channel (docs/UI_SPEC.md §Window behavior).

The agent lives in the tray, so launching it again — desktop shortcut, pinned
taskbar icon — must surface the window of the *running* instance instead of
exiting as a silent no-op. These tests drive the transport in both directions:
request delivered, request refused, endpoint lifecycle, stale-endpoint
recovery after a hard kill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.infrastructure.system.activation import (
    InstanceActivator,
    activation_server_name,
    request_show,
)
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtNetwork import QLocalServer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_EVENT_LOOP_TIMEOUT_MS = 3000


@pytest.fixture(scope="module")
def core_app() -> Iterator[QCoreApplication]:
    """One QCoreApplication for the module.

    The channel is a named pipe, not a GUI widget, so it needs an event loop
    but no display — this keeps the test runnable on a headless machine.
    """
    app = QCoreApplication.instance()
    created = app is None
    if app is None:
        app = QCoreApplication([])
    yield app
    if created:
        app.quit()


def test_second_launch_reaches_the_running_instance(
    core_app: QCoreApplication, tmp_path: Path
) -> None:
    """The copy that loses the guard can always hand off to the winner."""
    name = activation_server_name(tmp_path)
    activator = InstanceActivator(name)
    assert activator.start() is True
    assert activator.listening is True

    received: list[str] = []
    loop = QEventLoop()
    activator.show_requested.connect(
        lambda token: (received.append(token), loop.quit())
    )

    try:
        assert request_show(name) is True
        QTimer.singleShot(_EVENT_LOOP_TIMEOUT_MS, loop.quit)
        loop.exec()
    finally:
        activator.stop()

    assert received == ["show"]


def test_repeated_launches_keep_the_channel_healthy(
    core_app: QCoreApplication, tmp_path: Path
) -> None:
    """Many launches in one session: every request lands, loop stays sane.

    Guards the socket lifetime — a connection object that outlives (or is
    deferred past) its event loop takes the next loop down with it.
    """
    name = activation_server_name(tmp_path)
    activator = InstanceActivator(name)
    assert activator.start() is True

    received: list[str] = []
    activator.show_requested.connect(received.append)

    try:
        for _ in range(3):
            assert request_show(name) is True
            loop = QEventLoop()
            QTimer.singleShot(_EVENT_LOOP_TIMEOUT_MS, loop.quit)
            with_timeout = activator.show_requested.connect(
                lambda _token, _loop=loop: _loop.quit()
            )
            loop.exec()
            activator.show_requested.disconnect(with_timeout)
    finally:
        activator.stop()

    assert received == ["show", "show", "show"]


def test_request_is_refused_when_nothing_is_running(
    core_app: QCoreApplication, tmp_path: Path
) -> None:
    """No listener must mean "refused", never a hang or a fake success."""
    assert (
        request_show(activation_server_name(tmp_path), attempts=2, timeout_ms=200)
        is False
    )


def test_stop_releases_the_endpoint(core_app: QCoreApplication, tmp_path: Path) -> None:
    """A closed app leaves nothing behind for the next launch to trip on."""
    name = activation_server_name(tmp_path)
    activator = InstanceActivator(name)
    assert activator.start() is True

    activator.stop()
    assert activator.listening is False
    assert request_show(name, attempts=1, timeout_ms=200) is False

    # Idempotent: shutdown paths may release twice.
    activator.stop()


def test_start_recovers_from_a_stale_endpoint(
    core_app: QCoreApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hard-killed instance can orphan the endpoint; bind anyway.

    Only reachable while the single-instance guard is held, so dropping the
    stale endpoint can never steal another live instance's channel.
    """
    name = activation_server_name(tmp_path)
    real_listen = QLocalServer.listen
    attempts: list[str] = []

    def flaky_listen(self: QLocalServer, server_name: str) -> bool:
        attempts.append(server_name)
        if len(attempts) == 1:
            return False
        return real_listen(self, server_name)

    monkeypatch.setattr(QLocalServer, "listen", flaky_listen)
    activator = InstanceActivator(name)
    try:
        assert activator.start() is True
    finally:
        monkeypatch.undo()
        activator.stop()

    assert attempts == [name, name]


def test_start_is_idempotent(core_app: QCoreApplication, tmp_path: Path) -> None:
    name = activation_server_name(tmp_path)
    activator = InstanceActivator(name)
    try:
        assert activator.start() is True
        assert activator.start() is True
    finally:
        activator.stop()


def test_channel_name_is_stable_and_per_data_dir(tmp_path: Path) -> None:
    """Both copies must agree on the name, and it must not be shared."""
    first = activation_server_name(tmp_path)
    assert first == activation_server_name(tmp_path)
    assert first != activation_server_name(tmp_path / "other-install")
    # Windows named-pipe names stay well under the ~256 char limit.
    assert len(first) < 64
