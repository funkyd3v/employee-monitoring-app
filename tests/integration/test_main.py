"""Integration tests for the CLI entry point."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from app import main
from app.infrastructure.system.activation import (
    InstanceActivator,
    activation_server_name,
    request_show,
)
from app.infrastructure.system.single_instance import SingleInstanceGuard
from app.ui.windows.login_window import LoginWindow
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from pathlib import Path

_ACTIVATE_TIMEOUT_S = 5.0


def _visible(cls: type) -> list[object]:
    app = QApplication.instance()
    if app is None:
        return []
    return [w for w in app.topLevelWidgets() if isinstance(w, cls) and w.isVisible()]


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main.main(["--version"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "employee-monitoring-agent" in captured.out


def test_main_boots_with_temp_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Headless boot: offscreen platform + a QApplication.exec that returns
    # immediately so the event loop never blocks the test.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("EM_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)  # noqa: ARG005

    assert main.main([]) == 0
    assert (tmp_path / "runtime" / "logs").exists()


def test_second_launch_opens_the_window_of_the_running_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-clicking the shortcut must open the app, not silently exit.

    The reported defect: with the app sitting in the tray, a second launch
    hit the single-instance guard and disappeared, leaving the user with a
    shortcut that appeared broken (docs/UI_SPEC.md §Window behavior).
    """
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    data_dir = tmp_path / "runtime"
    monkeypatch.setenv("EM_DATA_DIR", str(data_dir))
    outcome: list[bool] = []

    def fake_exec(self: QApplication) -> int:
        # The agent starts on the login screen; the user closes it to the
        # tray, then double-clicks the shortcut.
        windows = _visible(LoginWindow)
        assert windows, "agent should start on the login screen"
        window = windows[0]
        window.hide()

        assert request_show(activation_server_name(data_dir)) is True
        deadline = time.monotonic() + _ACTIVATE_TIMEOUT_S
        while time.monotonic() < deadline and not window.isVisible():
            QCoreApplication.processEvents()
            time.sleep(0.01)
        outcome.append(window.isVisible())
        return 0

    monkeypatch.setattr(QApplication, "exec", fake_exec)

    assert main.main([]) == 0
    assert outcome == [True]


def test_second_copy_exits_quietly_when_an_instance_is_already_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second copy hands its request over and leaves without a UI."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    data_dir = tmp_path / "runtime"
    monkeypatch.setenv("EM_DATA_DIR", str(data_dir))

    # Stand in for the instance already running in the tray.
    guard = SingleInstanceGuard(data_dir / "app.lock")
    assert guard.acquire() is True
    activator = InstanceActivator(activation_server_name(data_dir))
    assert activator.start() is True

    received: list[str] = []
    loop = QEventLoop()
    activator.show_requested.connect(
        lambda token: (received.append(token), loop.quit())
    )

    started_loop: list[bool] = []
    monkeypatch.setattr(
        QApplication,
        "exec",
        lambda self: started_loop.append(True) or 0,  # noqa: ARG005
    )

    try:
        assert main.main([]) == 0
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
    finally:
        activator.stop()
        guard.release()

    assert received == ["show"]
    assert started_loop == []
