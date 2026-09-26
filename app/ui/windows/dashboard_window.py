"""Dashboard window (docs/UI_SPEC.md §Main dashboard).

Renders every work-session state — READY / WORKING / BREAK / COMPLETED — as
a crossfading center page, driven by a :class:`SessionPresenter` port. The
1s ticker only *re-reads* the presenter's recomputed projection; the widget
never accumulates time itself (docs/ENGINEERING_RULES.md §Timer Correctness).
Logout while a session runs uses the in-app modal, never an OS dialog.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.domain.activity.activity import ActivityState
from app.domain.sessions.state_machine import AppState
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.theme import current_palette
from app.ui.theme.effects import MOTION
from app.ui.theme.icons import themed_icon
from app.ui.theme.tokens import (
    ACTIVE_TIMER_FONT_PX,
    PROFILE_CHEVRON_SIZE,
    PROFILE_ICON_SIZE,
    PROFILE_TRIGGER_HEIGHT,
    PROFILE_TRIGGER_MAX_WIDTH,
    PROFILE_TRIGGER_MIN_WIDTH,
    READY_TIMER_FONT_PX,
    SECONDARY_TIMER_FONT_PX,
    SPACING_2XL,
    SPACING_CHIP_HORIZONTAL,
    SPACING_CHIP_VERTICAL,
    SPACING_LG,
    SPACING_MD,
    SPACING_SM,
    SPACING_XL,
    SPACING_XS,
    SPACING_XXS,
    WORKING_CAPTION_MAX_WIDTH,
)
from app.ui.windows.base_window import FramelessWindow
from app.ui.windows.components import (
    Button,
    ButtonRole,
    ButtonSize,
    Card,
    StatusPill,
    TimerWidget,
)
from app.ui.windows.components.status_pill import PillState

if TYPE_CHECKING:
    from PySide6.QtGui import QHideEvent, QShowEvent
    from PySide6.QtWidgets import QGraphicsEffect

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


class _ProfileTrigger(QToolButton):
    """Rounded username trigger for the account menu."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProfileTrigger")
        self.setText("")
        self.setFixedHeight(PROFILE_TRIGGER_HEIGHT)
        self.setMinimumWidth(PROFILE_TRIGGER_MIN_WIDTH)
        self.setMaximumWidth(PROFILE_TRIGGER_MAX_WIDTH)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Account menu")
        self.setToolTip("Account menu")
        self._display_name = "User"

        self._avatar = QLabel(self)
        self._avatar.setFixedSize(QSize(PROFILE_ICON_SIZE, PROFILE_ICON_SIZE))
        self._avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._name = QLabel(self._display_name, self)
        self._name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._chevron = QLabel(self)
        self._chevron.setFixedSize(QSize(PROFILE_CHEVRON_SIZE, PROFILE_CHEVRON_SIZE))
        self._chevron.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING_SM, 0, SPACING_SM, 0)
        layout.setSpacing(SPACING_XS)
        layout.addWidget(self._avatar)
        layout.addWidget(self._name)
        layout.addWidget(self._chevron)
        self._refresh_icons()

    def text(self) -> str:
        return f"{self._display_name}  {chr(0x25BE)}"

    def set_user(self, user: DashboardUser) -> None:
        self._display_name = user.first_name
        self._name.setText(self._display_name)
        self.setToolTip(f"Account menu — {user.full_label}")
        self.setAccessibleDescription(user.full_label)
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        palette = current_palette().text_primary
        self._avatar.setPixmap(
            themed_icon("user-round", palette, PROFILE_ICON_SIZE).pixmap(
                QSize(PROFILE_ICON_SIZE, PROFILE_ICON_SIZE)
            )
        )
        self._chevron.setPixmap(
            themed_icon("chevron-down", palette, PROFILE_CHEVRON_SIZE).pixmap(
                QSize(PROFILE_CHEVRON_SIZE, PROFILE_CHEVRON_SIZE)
            )
        )


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

        self._profile_trigger = _ProfileTrigger()
        self._profile_trigger.clicked.connect(self._open_profile_menu)
        self.title_bar.add_trailing(self._profile_trigger)

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
        status_row.setSpacing(SPACING_SM)
        status_row.addWidget(self._pill)
        status_row.addWidget(self._last_activity)
        status_row_host = QWidget()
        status_row_host.setObjectName("ActivityMeta")
        status_row_host.setLayout(status_row)

        working_caption = QLabel("Work-session activity is recorded while you work.")
        working_caption.setObjectName("PanelCaption")
        working_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        working_caption.setMaximumWidth(WORKING_CAPTION_MAX_WIDTH)

        # READY page
        self._check_in_button = Button(
            "Check In",
            role=ButtonRole.PRIMARY,
            size=ButtonSize.LARGE,
            object_name="PrimaryCta",
        )
        self._check_in_button.clicked.connect(self._on_check_in)
        self._ready_timer = TimerWidget()
        self._ready_timer.set_font_size(READY_TIMER_FONT_PX)

        ready = self._page(
            subtitle="Ready to start your day?",
            headline=self._greeting,
            timer=self._ready_timer,
            buttons=[self._check_in_button],
            extras=[],
        )
        ready.setObjectName("ReadyPage")

        # WORKING page
        self._break_button = Button("Take a Break", role=ButtonRole.SECONDARY_SOLID)
        self._break_button.clicked.connect(self._on_take_break)
        self._checkout_button = Button("Check Out", role=ButtonRole.DANGER_SOLID)
        self._checkout_button.clicked.connect(self._on_check_out)
        self._working_timer = TimerWidget()
        self._working_timer.set_font_size(ACTIVE_TIMER_FONT_PX)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING_MD)
        button_row.addStretch(1)
        button_row.addWidget(self._break_button)
        button_row.addWidget(self._checkout_button)
        button_row.addStretch(1)
        button_row_host = QWidget()
        button_row_host.setObjectName("ActionRow")
        button_row_host.setLayout(button_row)

        working = self._page(
            subtitle="You're checked in",
            headline=None,
            timer=self._working_timer,
            buttons=[],
            extras=[button_row_host, status_row_host, working_caption],
        )
        working.setObjectName("WorkingPage")

        # BREAK page
        self._resume_button = Button(
            "Resume",
            role=ButtonRole.PRIMARY,
            size=ButtonSize.LARGE,
            object_name="PrimaryCta",
        )
        self._resume_button.clicked.connect(self._on_resume)
        self._break_timer = TimerWidget()
        self._break_timer.set_font_size(SECONDARY_TIMER_FONT_PX)
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
        break_page.setObjectName("BreakPage")

        # COMPLETED page
        self._again_button = Button(
            "Check In",
            role=ButtonRole.PRIMARY,
            size=ButtonSize.LARGE,
            object_name="PrimaryCta",
        )
        self._again_button.clicked.connect(self._on_check_in)
        self._completed_timer = TimerWidget()
        self._completed_timer.set_font_size(SECONDARY_TIMER_FONT_PX)
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
        completed.setObjectName("CompletedPage")

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
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(
            SPACING_2XL,
            SPACING_LG,
            SPACING_2XL,
            SPACING_XL,
        )
        layout.setSpacing(SPACING_SM)

        sub = QLabel(subtitle)
        sub.setObjectName("StatusLine")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(1)
        if headline is not None:
            layout.addWidget(headline)
            layout.addSpacing(SPACING_XS)
        layout.addWidget(sub)
        layout.addSpacing(SPACING_SM)
        layout.addWidget(timer)
        layout.addWidget(self._timer_caption)
        layout.addSpacing(SPACING_MD)

        if buttons:
            for button in buttons:
                layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        for extra in extras:
            layout.addSpacing(SPACING_SM)
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
        self._profile_trigger.set_user(user)
        self._greeting.setText(
            f"{greeting_for(datetime.now().hour)}, {user.first_name}"
        )

    def request_check_out(self) -> None:
        """Check out immediately (button and tray both call this)."""
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
                self._last_activity.setText("Idle - away from input")
            else:
                self._last_activity.setText("Last activity: now")
        else:
            self._last_activity.setText("Last activity: none")

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
        self._do_check_out()

    def _do_check_out(self) -> None:
        self.set_view(self._presenter.check_out())

    def _profile_menu_position(self, menu: QMenu) -> QPoint:
        menu.setMaximumWidth(max(self.width() - (2 * SPACING_SM), 1))
        menu.adjustSize()
        trigger = self._profile_trigger
        position = trigger.mapToGlobal(QPoint(trigger.width(), trigger.height()))
        position.setX(position.x() - menu.width())

        window_top_left = self.mapToGlobal(QPoint(0, 0))
        window_bottom_right = self.mapToGlobal(QPoint(self.width(), self.height()))
        position.setX(
            max(
                window_top_left.x(),
                min(position.x(), window_bottom_right.x() - menu.width()),
            )
        )
        position.setY(
            max(
                window_top_left.y(),
                min(position.y(), window_bottom_right.y() - menu.height()),
            )
        )
        return position

    def _open_profile_menu(self) -> None:
        user = self._user
        full = user.full_label if user else "Employee"
        email = user.email if user else ""

        menu = QMenu(self)
        menu.setObjectName("ProfileMenu")
        # A styled QMenu keeps the platform's rectangular popup frame and an
        # opaque backing store, so the stylesheet radius never clips and a
        # square box shows behind the rounded trigger. Dropping the native
        # frame and going translucent lets the QSS border-radius round it.
        menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
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

        menu.exec(self._profile_menu_position(menu))

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
        animation.setDuration(MOTION.page_duration_ms)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def clear_effect() -> None:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(cast("QGraphicsEffect", None))

        animation.finished.connect(clear_effect)
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
        layout.setSpacing(SPACING_SM)
        layout.addWidget(self._chip("active", "Active"))
        layout.addWidget(self._chip("idle", "Idle"))
        layout.addWidget(self._chip("break", "Break"))

    def set_values(self, *, active: int, idle: int, break_minutes: int) -> None:
        self._values["active"].setText(f"{active}m")
        self._values["idle"].setText(f"{idle}m")
        self._values["break"].setText(f"{break_minutes}m")

    def _chip(self, key: str, label: str) -> QWidget:
        c = Card(object_name="Chip")
        value = QLabel("0m")
        value.setObjectName("ChipValue")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = QLabel(label)
        name.setObjectName("ChipLabel")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(c)
        lay.setContentsMargins(
            SPACING_CHIP_HORIZONTAL,
            SPACING_CHIP_VERTICAL,
            SPACING_CHIP_HORIZONTAL,
            SPACING_CHIP_VERTICAL,
        )
        lay.setSpacing(SPACING_XXS)
        lay.addWidget(value)
        lay.addWidget(name)
        self._values[key] = value
        return c
