"""Widget tests for the dashboard window (docs/UI_SPEC.md §Main dashboard).

The window talks to a :class:`SessionPresenter` port — tests substitute a stub
that returns scripted ``SessionView`` projections, verifying the widget only
renders what the presenter says (no business logic in the window).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.domain.activity.activity import ActivityState
from app.domain.sessions.state_machine import AppState
from app.services.session_service import SessionView
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.view_models import DashboardUser
from app.ui.windows.dashboard_window import (
    DashboardWindow,
    greeting_for,
    local_time_label,
)
from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot

ALICE = DashboardUser(
    display_name="Ada Lovelace",
    email="ada@company.com",
    team_name="Analytics",
)


class StubPresenter:
    """Scripted presenter: returns the next queued projection per call."""

    def __init__(self, views: list[SessionView] | None = None) -> None:
        self.queue: list[SessionView] = list(views or [])
        self.calls: list[str] = []
        self.now = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)

    def _pop(self) -> SessionView:
        if self.queue:
            return self.queue.pop(0)
        return self.ready()

    def ready(self) -> SessionView:
        return SessionView(state=AppState.READY, can_check_in=True)

    def working(self, elapsed: int = 0, *, idle: bool = False) -> SessionView:
        return SessionView(
            state=AppState.WORKING,
            activity_state=ActivityState.IDLE if idle else ActivityState.ACTIVE,
            elapsed_work_seconds=elapsed,
            can_take_break=True,
            can_check_out=True,
            ticking=True,
        )

    def on_break(self, elapsed: int = 0) -> SessionView:
        return SessionView(
            state=AppState.BREAK,
            elapsed_work_seconds=elapsed,
            elapsed_break_seconds=elapsed,
            can_resume=True,
            can_check_out=True,
            ticking=True,
        )

    def completed(
        self, *, active: int = 0, idle: int = 0, break_minutes: int = 0
    ) -> SessionView:
        return SessionView(
            state=AppState.COMPLETED,
            elapsed_work_seconds=(active + idle) * 60,
            active_minutes=active,
            idle_minutes=idle,
            break_minutes=break_minutes,
            checked_out_at=self.now,
            can_check_in=True,
        )

    def tick(self) -> SessionView:
        self.calls.append("tick")
        return self._pop()

    def check_in(self) -> SessionView:
        self.calls.append("check_in")
        return self._pop()

    def take_break(self) -> SessionView:
        self.calls.append("take_break")
        return self._pop()

    def resume(self) -> SessionView:
        self.calls.append("resume")
        return self._pop()

    def check_out(self) -> SessionView:
        self.calls.append("check_out")
        return self._pop()


@pytest.fixture
def presenter() -> StubPresenter:
    return StubPresenter()


def make_window(qtbot: QtBot, presenter: StubPresenter) -> DashboardWindow:
    window = DashboardWindow(presenter, user=ALICE)
    window.resize(900, 600)
    qtbot.addWidget(window)
    return window


# ── Page mapping ───────────────────────────────────────────────────────────
@pytest.mark.ui
def test_renders_ready_page(qtbot: QtBot, presenter: StubPresenter) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.ready())

    assert window._stack.currentIndex() == 0
    assert window._check_in_button.isEnabled()


def test_renders_working_page(qtbot: QtBot, presenter: StubPresenter) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.working(elapsed=125))

    assert window._stack.currentIndex() == 1
    assert window._working_timer.text() == "00:02:05"
    assert window._break_button.isEnabled()
    assert window._checkout_button.isEnabled()


def test_working_active_vs_idle_pill(qtbot: QtBot, presenter: StubPresenter) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.working())
    assert window._pill.state.value == "ACTIVE"
    window.set_view(presenter.working(idle=True))
    assert window._pill.state.value == "IDLE"


def test_renders_break_page_with_duration(
    qtbot: QtBot, presenter: StubPresenter
) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.on_break(elapsed=61))

    assert window._stack.currentIndex() == 2
    assert window._break_timer.text() == "00:01:01"
    assert window._resume_button.isEnabled()
    assert window._pill.state.value == "BREAK"


def test_completed_page_shows_summary_chips(
    qtbot: QtBot, presenter: StubPresenter
) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.completed(active=30, idle=5, break_minutes=10))

    assert window._stack.currentIndex() == 3
    assert window._chips._values["active"].text() == "30m"
    assert window._chips._values["idle"].text() == "5m"
    assert window._chips._values["break"].text() == "10m"
    assert "Checked out at" in window._checked_out_label.text()
    assert window._again_button.isEnabled()


def test_set_user_updates_header_fields(qtbot: QtBot, presenter: StubPresenter) -> None:
    window = make_window(qtbot, presenter)
    assert window._team_label.text() == "Analytics"
    assert window._greeting.text().endswith("Ada")


# ── Actions route through the presenter ────────────────────────────────────
def test_check_in_button_asks_presenter(qtbot: QtBot, presenter: StubPresenter) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.ready())

    presenter.queue = [presenter.working()]
    qtbot.mouseClick(window._check_in_button, Qt.MouseButton.LeftButton)

    assert presenter.calls == ["check_in"]
    assert window._stack.currentIndex() == 1


def test_take_break_button_routes_to_presenter(
    qtbot: QtBot, presenter: StubPresenter
) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.working())

    presenter.queue = [presenter.on_break()]
    qtbot.mouseClick(window._break_button, Qt.MouseButton.LeftButton)

    assert presenter.calls == ["take_break"]
    assert window._stack.currentIndex() == 2


def test_resume_button_routes_to_presenter(
    qtbot: QtBot, presenter: StubPresenter
) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.on_break())

    presenter.queue = [presenter.working()]
    qtbot.mouseClick(window._resume_button, Qt.MouseButton.LeftButton)

    assert presenter.calls == ["resume"]
    assert window._stack.currentIndex() == 1


def test_check_out_completes_without_confirmation(
    qtbot: QtBot, presenter: StubPresenter
) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.working())
    presenter.queue = [presenter.completed(active=5)]

    qtbot.mouseClick(window._checkout_button, Qt.MouseButton.LeftButton)

    assert window.findChild(ConfirmDialog) is None
    assert presenter.calls == ["check_out"]
    assert window._stack.currentIndex() == 3


# ── Logout confirm only while a session is active ──────────────────────────
def test_logout_while_working_opens_confirm(
    qtbot: QtBot, presenter: StubPresenter
) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.working())

    emitted: list[bool] = []
    window.logout_requested.connect(lambda: emitted.append(True))

    window.request_logout()

    assert window.findChild(ConfirmDialog) is not None
    assert emitted == []


def test_logout_when_idle_emits_directly(
    qtbot: QtBot, presenter: StubPresenter
) -> None:
    window = make_window(qtbot, presenter)
    window.set_view(presenter.ready())

    with qtbot.waitSignal(window.logout_requested, timeout=300):
        window.request_logout()


# ── Pure helpers ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("hour", "expected"),
    [(8, "Good Morning"), (14, "Good Afternoon"), (22, "Good Evening")],
)
def test_greeting_for(hour: int, expected: str) -> None:
    assert greeting_for(hour) == expected


def test_local_time_label_renders_local_hhmm() -> None:
    local = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    label = local_time_label(local)
    assert label.endswith("30")


def test_user_initials() -> None:
    assert ALICE.initials == "AL"
    assert ALICE.first_name == "Ada"
