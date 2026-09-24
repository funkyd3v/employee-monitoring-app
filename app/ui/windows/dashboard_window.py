"""Dashboard window (docs/UI_SPEC.md §Main dashboard).

Renders every work-session state — READY / WORKING / BREAK / COMPLETED — as
a crossfading center page, driven by a :class:`SessionPresenter` port. The
1s ticker only *re-reads* the presenter's recomputed projection; the widget
never accumulates time itself (docs/ENGINEERING_RULES.md §Timer Correctness).
Confirmations (Check Out, Logout while a session runs) use the in-app modal,
never an OS dialog.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QHideEvent,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.domain.activity.activity import ActivityState
from app.domain.sessions.state_machine import AppState
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.theme.tokens import DARK
from app.ui.windows.base_window import FramelessWindow
from app.ui.windows.components import StatusPill, TimerWidget
from app.ui.windows.components.status_pill import PillState

if TYPE_CHECKING:
    from app.services.session_service import SessionView
    from app.ui.view_models import DashboardUser, SessionPresenter

_STATE_INDEX: dict[AppState, int] = {
    AppState.READY: 0,
    AppState.WORKING: 1,
    AppState.BREAK: 2,
    AppState.COMPLETED: 3,
}

_PAGE_CAPTIONS: dict[AppState, str] = {
    AppState.READY: "",
    AppState.WORKING: "elapsed work time",
    AppState.BREAK: "break duration",
    AppState.COMPLETED: "Total Work Time",
}


def greeting_for(hour: int) -> str:
    """Time-of-day greeting used on the READY page (local render-time)."""
    if 5 <= hour < 12:
        return "Good Morning"
    if 12 <= hour < 18:
        return "Good Afternoon"
    return "Good Evening"


def local_time_label(utc: datetime) -> str:
    """Render a UTC timestamp in the user's local time (render-time only)."""
    return utc.astimezone().strftime("%H:%M")


class _Avatar(QToolButton):
    """Circular initials badge with a chevron; opens the user menu."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AvatarButton")
        self.setFixedSize(84, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Account menu")
        self._label = "?"

    def set_label(self, label: str) -> None:
        self._label = label
        self.setToolTip(f"Account menu \u2212 {label}")
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        circle = self.rect().adjusted(2, 3, -46, -3)
        gradient = QLinearGradient(circle.topLeft(), circle.bottomRight())
        gradient.setColorAt(0.0, QColor(DARK.accent))
        gradient.setColorAt(1.0, QColor(DARK.accent_end))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(circle)

        font = QFont("Inter")
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, self._label)

        chevron = QFont("Inter")
        chevron.setPixelSize(12)
        painter.setFont(chevron)
        painter.setPen(QPen(QColor(DARK.text_secondary)))
        painter.drawText(QPoint(self.width() - 16, self.height() // 2 + 4), "\u25be")
        painter.end()


class DashboardWindow(FramelessWindow):
    """The main dashboard; all work-session states render here."""

    logout_requested = Signal()

    def __init__(
        self,
        presenter: SessionPresenter,
        *,
        user: DashboardUser | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("DashboardWindow")
        self._presenter = presenter
        self._view: SessionView | None = None
        self._user = user

        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.setTimerType(Qt.TimerType.PreciseTimer)
        self._ticker.timeout.connect(self._on_tick)

        self._build_shell()
        if user is not None:
            self.set_user(user)

    # ── Construction ───────────────────────────────────────────────────────
    def _build_shell(self) -> None:
        self._team_label = QLabel("Workspace")
        self._team_label.setObjectName("TeamNameLabel")
        self._team_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.title_bar.add_trailing(self._team_label)

        self._avatar = _Avatar()
        self._avatar.clicked.connect(self._open_avatar_menu)
        self.title_bar.add_trailing(self._avatar)

        self._greeting = QLabel()
        self._greeting.setObjectName("PanelTitle")
        self._greeting.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._timer_caption = QLabel()
        self._timer_caption.setObjectName("TimerCaption")
        self._timer_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._pill = StatusPill()
        self._last_activity = QLabel("Last activity: now")
        self._last_activity.setObjectName("LastActivity")
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        status_row.addWidget(self._pill)
        status_row.addWidget(self._last_activity)

        working_caption = QLabel("Work-session activity is recorded while you work.")
        working_caption.setObjectName("PanelCaption")
        working_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # READY page
        self._check_in_button = QPushButton("Check In")
        self._check_in_button.setObjectName("PrimaryCta")
        self._check_in_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_in_button.clicked.connect(self._on_check_in)
        self._ready_timer = TimerWidget()
        self._ready_timer.set_font_size(44)

        ready = self._page(
            subtitle="Ready to start your day?",
            headline=self._greeting,
            timer=self._ready_timer,
            buttons=[self._check_in_button],
            extras=[],
        )

        # WORKING page
        self._break_button = QPushButton("Take a Break")
        self._break_button.setProperty("role", "secondary")
        self._break_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._break_button.clicked.connect(self._on_take_break)
        self._checkout_button = QPushButton("Check Out")
        self._checkout_button.setProperty("role", "danger")
        self._checkout_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checkout_button.clicked.connect(self._on_check_out)
        self._working_timer = TimerWidget()
        self._working_timer.set_font_size(48)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addStretch(1)
        button_row.addWidget(self._break_button)
        button_row.addWidget(self._checkout_button)
        button_row.addStretch(1)

        working = self._page(
            subtitle="You're checked in",
            headline=None,
            timer=self._working_timer,
            buttons=[],
            extras=[button_row, status_row, working_caption],
        )

        # BREAK page
        self._resume_button = QPushButton("Resume")
        self._resume_button.setObjectName("PrimaryCta")
        self._resume_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._resume_button.clicked.connect(self._on_resume)
        self._break_timer = TimerWidget()
        self._break_timer.set_font_size(44)
        break_caption = QLabel("Break time is separate from work time.")
        break_caption.setObjectName("PanelCaption")
        break_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        break_page = self._page(
            subtitle="On Break",
            headline=None,
            timer=self._break_timer,
            buttons=[self._resume_button],
            extras=[break_caption],
        )

        # COMPLETED page
        self._again_button = QPushButton("Check In")
        self._again_button.setObjectName("PrimaryCta")
        self._again_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._again_button.clicked.connect(self._on_check_in)
        self._completed_timer = TimerWidget()
        self._checked_out_label = QLabel()
        self._checked_out_label.setObjectName("PanelCaption")
        self._checked_out_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chips = self._build_chips()

        completed = self._page(
            subtitle="Workday Complete",
            headline=None,
            timer=self._completed_timer,
            buttons=[self._again_button],
            extras=[self._checked_out_label, self._chips],
        )

        self._stack = QStackedWidget(self.body())
        self._stack.addWidget(ready)
        self._stack.addWidget(working)
        self._stack.addWidget(break_page)
        self._stack.addWidget(completed)

        body_layout = QVBoxLayout(self.body())
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(self._stack)

    def _page(
        self,
        *,
        subtitle: str,
        headline: QWidget | None,
        timer: TimerWidget,
        buttons: list[QWidget],
        extras: list[QWidget | QHBoxLayout],
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(48, 20, 48, 36)
        layout.setSpacing(10)

        sub = QLabel(subtitle)
        sub.setObjectName("PanelSubtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(1)
        if headline is not None:
            layout.addWidget(headline)
            layout.addSpacing(6)
        layout.addWidget(sub)
        layout.addWidget(timer)
        layout.addWidget(self._timer_caption)
        layout.addSpacing(12)

        if buttons:
            for button in buttons:
                layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        for extra in extras:
            layout.addSpacing(8)
            if isinstance(extra, QHBoxLayout):
                row = QWidget()
                row.setLayout(extra)
                layout.addWidget(row, 0, Qt.AlignmentFlag.AlignHCenter)
            else:
                layout.addWidget(extra, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(2)
        return page

    def _build_chips(self) -> _ChipsRow:
        self._chips = _ChipsRow()
        return self._chips

    # ── Public API (controller-driven) ─────────────────────────────────────
    def set_user(self, user: DashboardUser) -> None:
        self._user = user
        self._team_label.setText(user.team_name or "Workspace")
        self._team_label.setToolTip(user.team_name or "No team assigned")
        self._avatar.set_label(user.initials)
        self._greeting.setText(
            f"{greeting_for(datetime.now().hour)}, {user.first_name}"
        )

    def request_check_out(self) -> None:
        """Open the Check Out confirmation (button and tray both call this)."""
        self._on_check_out()

    def request_logout(self) -> None:
        """Open the Logout confirmation, or emit directly when idle."""
        self._request_logout()

    def set_view(self, view: SessionView) -> None:
        """Render a fresh projection; manage the 1s re-read ticker."""
        self._view = view
        index = _STATE_INDEX[view.state]
        if index != self._stack.currentIndex():
            self._stack.setCurrentIndex(index)
            widget = self._stack.currentWidget()
            if widget is not None:
                self._fade_in(widget)

        self._timer_caption.setText(_PAGE_CAPTIONS[view.state])
        self._pill.set_state(_pill_state(view))
        # Live "Last activity" label — reflects idle vs active without raw input.
        if view.state is AppState.WORKING:
            if view.activity_state is ActivityState.IDLE:
                self._last_activity.setText("Idle — away from input")
            else:
                self._last_activity.setText("Last activity: now")
        else:
            self._last_activity.setText("Last activity: —")

        if view.state is AppState.WORKING:
            self._working_timer.set_elapsed(view.elapsed_work_seconds)
            self._break_button.setEnabled(view.can_take_break)
            self._checkout_button.setEnabled(view.can_check_out)
        elif view.state is AppState.BREAK:
            self._break_timer.set_elapsed(view.elapsed_break_seconds)
            self._resume_button.setEnabled(view.can_resume)
            self._checkout_button.setEnabled(view.can_check_out)
        elif view.state is AppState.COMPLETED:
            self._completed_timer.set_elapsed(view.elapsed_work_seconds)
            self._again_button.setEnabled(view.can_check_in)
            self._chips.set_values(
                active=view.active_minutes,
                idle=view.idle_minutes,
                break_minutes=view.break_minutes,
            )
            self._checked_out_label.setText(
                f"Checked out at {local_time_label(view.checked_out_at)}"
                if view.checked_out_at is not None
                else ""
            )
        else:  # READY
            self._ready_timer.set_elapsed(0)
            self._check_in_button.setEnabled(view.can_check_in)

        if view.ticking and self.isVisible():
            self._ticker.start()
        else:
            self._ticker.stop()

    # ── Event overrides ────────────────────────────────────────────────────
    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._view is not None and self._view.ticking:
            self._ticker.start()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._ticker.stop()
        super().hideEvent(event)

    # ── Internal handlers ──────────────────────────────────────────────────
    def _on_tick(self) -> None:
        if self._view is not None and self._view.ticking:
            self.set_view(self._presenter.tick())

    def _on_check_in(self) -> None:
        self.set_view(self._presenter.check_in())

    def _on_take_break(self) -> None:
        self.set_view(self._presenter.take_break())

    def _on_resume(self) -> None:
        self.set_view(self._presenter.resume())

    def _on_check_out(self) -> None:
        dialog = ConfirmDialog(
            self,
            title="Check out?",
            message="This ends your work session. Your total time stays visible.",
            confirm_text="Check out",
            danger=True,
        )
        dialog.confirmed.connect(self._do_check_out)
        dialog.show_overlay()

    def _do_check_out(self) -> None:
        self.set_view(self._presenter.check_out())

    def _open_avatar_menu(self) -> None:
        user = self._user
        full = user.full_label if user else "Employee"
        email = user.email if user else ""

        menu = QMenu(self)
        user_action = menu.addAction(full)
        user_action.setEnabled(False)
        if email:
            email_action = menu.addAction(email)
            email_action.setEnabled(False)
        menu.addSeparator()
        account = menu.addAction("Account")
        account.setEnabled(False)  # future phase
        menu.addSeparator()
        logout = menu.addAction("Logout")
        logout.triggered.connect(self._request_logout)

        position = self._avatar.mapToGlobal(QPoint(0, self._avatar.height()))
        menu.exec(position)

    def _request_logout(self) -> None:
        if self._view is not None and self._view.state in (
            AppState.WORKING,
            AppState.BREAK,
        ):
            dialog = ConfirmDialog(
                self,
                title="Log out?",
                message="An active work session is running. It will be checked "
                "out before you log out.",
                confirm_text="Log out",
                danger=True,
            )
            dialog.confirmed.connect(self.logout_requested.emit)
            dialog.show_overlay()
        else:
            self.logout_requested.emit()

    # ── Motion ─────────────────────────────────────────────────────────────
    @staticmethod
    def _fade_in(widget: QWidget) -> None:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(220)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def _pill_state(view: SessionView) -> PillState:
    """Map a projection to the pill display state."""
    if view.state is AppState.BREAK:
        return PillState.BREAK
    if view.state is AppState.WORKING:
        return (
            PillState.ACTIVE
            if view.activity_state is ActivityState.ACTIVE
            else PillState.IDLE
        )
    return PillState.OFF


class _ChipsRow(QWidget):
    """Active / Idle / Break minute chips on the COMPLETED page."""

    def __init__(self) -> None:
        super().__init__()
        self._values: dict[str, QLabel] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._chip("active", "Active"))
        layout.addWidget(self._chip("idle", "Idle"))
        layout.addWidget(self._chip("break", "Break"))

    def set_values(self, *, active: int, idle: int, break_minutes: int) -> None:
        self._values["active"].setText(f"{active}m")
        self._values["idle"].setText(f"{idle}m")
        self._values["break"].setText(f"{break_minutes}m")

    def _chip(self, key: str, label: str) -> QWidget:
        c = QWidget()
        c.setObjectName("Chip")
        value = QLabel("0m")
        value.setObjectName("ChipValue")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel(label)
        name.setObjectName("ChipLabel")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(c)
        lay.setContentsMargins(20, 10, 20, 10)
        lay.setSpacing(2)
        lay.addWidget(value)
        lay.addWidget(name)
        self._values[key] = value
        return c
