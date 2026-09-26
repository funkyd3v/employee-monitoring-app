"""System tray (docs/UI_SPEC.md §System tray).

State is surfaced through the canonical application icon + tooltip and a context menu with
Open Dashboard, current status, session actions (Check In / Take a Break /
Resume / Check Out), Logout, and Exit. The icon is rendered in grayscale when
checked out or on break (privacy/status transparency: the employee always
knows whether monitoring is running, per docs/SECURITY_PRIVACY.md §checklist).

Clicking the icon itself (single *or* double click) opens the app — the icon
is the primary affordance, and the menu is only for the actions. The context
menu stays right-click only, so a left click never opens a menu it should not.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app.core.logging import get_logger

_logger = get_logger("ui.tray")

from app.config.constants import APP_NAME
from app.domain.sessions.state_machine import AppState
from app.services.session_service import SessionView

# Windows emits Trigger for every click and DoubleClick for the second one, so
# a double click would open the app twice. This window collapses a burst of
# click reasons into a single open request.
_OPEN_COALESCE_MS = 250


class TrayState(StrEnum):
    """Icon states mapping the session (UI_SPEC tray states)."""

    CHECKED_IN = "CHECKED_IN"
    ON_BREAK = "ON_BREAK"
    CHECKED_OUT = "CHECKED_OUT"
    ATTENTION = "ATTENTION"


def tray_state_for(view: SessionView) -> TrayState:
    if view.state is AppState.WORKING:
        return TrayState.CHECKED_IN
    if view.state is AppState.BREAK:
        return TrayState.ON_BREAK
    return TrayState.CHECKED_OUT


def _asset_path(filename: str) -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = (
        Path(bundle_root)
        if isinstance(bundle_root, str)
        else Path(__file__).resolve().parents[3]
    )
    return root / "assets" / "icons" / filename


@lru_cache(maxsize=1)
def _app_icon() -> QIcon:
    return QIcon(str(_asset_path("app.ico")))


@lru_cache(maxsize=1)
def _gray_app_icon() -> QIcon:
    image = _app_icon().pixmap(256, 256).toImage()
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            gray = round(
                color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114
            )
            image.setPixelColor(x, y, QColor(gray, gray, gray, color.alpha()))
    return QIcon(QPixmap.fromImage(image))


def _paint_tray_icon(state: TrayState) -> QIcon:
    if state in (TrayState.ON_BREAK, TrayState.CHECKED_OUT):
        return _gray_app_icon()
    return _app_icon()


class TrayManager(QObject):
    """Owns the tray icon + menu; emits domain-ish signals back to the UI."""

    open_dashboard = Signal()
    check_in = Signal()
    take_break = Signal()
    resume = Signal()
    check_out = Signal()
    logout = Signal()
    exit_requested = Signal()

    def __init__(self, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = SessionView(state=AppState.LOGGED_OUT)
        self._available = QSystemTrayIcon.isSystemTrayAvailable()
        self._should_be_visible = False

        self._menu = QMenu()
        self._tray = QSystemTrayIcon()

        # Reliability: Explorer restart recovery (TaskbarCreated) and
        # sleep/resume can leave the icon orphaned. A lightweight poll
        # re-shows the tray if the shell is available but our icon is not
        # visible. Poll interval 5s mirrors worker polling (non-busy).
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setInterval(5000)
        self._recovery_timer.timeout.connect(self._ensure_visible)

        # Icon clicks (single or double) ask for the app to be opened. Deferred
        # by a beat so one physical double click produces one open request.
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.setInterval(_OPEN_COALESCE_MS)
        self._open_timer.timeout.connect(self.open_dashboard.emit)

        self._title_action = self._menu.addAction(APP_NAME)
        self._title_action.setEnabled(False)
        self._menu.addSeparator()

        self._open_action = self._menu.addAction("Open Dashboard")
        self._open_action.triggered.connect(self.open_dashboard.emit)

        self._status_action = self._menu.addAction("Current Status: Checked Out")
        self._status_action.setEnabled(False)
        self._menu.addSeparator()

        self._check_in_action = self._menu.addAction("Check In")
        self._check_in_action.triggered.connect(self.check_in.emit)

        self._break_action = self._menu.addAction("Take a Break")
        self._break_action.triggered.connect(self.take_break.emit)

        self._resume_action = self._menu.addAction("Resume")
        self._resume_action.triggered.connect(self.resume.emit)

        self._checkout_action = self._menu.addAction("Check Out")
        self._checkout_action.triggered.connect(self.check_out.emit)
        self._menu.addSeparator()

        self._logout_action = self._menu.addAction("Logout")
        self._logout_action.triggered.connect(self.logout.emit)

        self._exit_action = self._menu.addAction("Exit")
        self._exit_action.triggered.connect(self.exit_requested.emit)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self.handle_activated)
        self.set_view(self._view)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def tray_icon(self) -> QSystemTrayIcon:
        return self._tray

    def set_view(self, view: SessionView) -> None:
        """Refresh icon/tooltip/menu from the latest session projection."""
        self._view = view
        state = tray_state_for(view)
        self._tray.setIcon(_paint_tray_icon(state))
        self._tray.setToolTip(f"{APP_NAME} \u2014 {_state_label(state)}")
        self._status_action.setText(f"Current Status: {_state_label(state)}")
        self._check_in_action.setEnabled(view.can_check_in)
        self._break_action.setEnabled(view.can_take_break)
        self._resume_action.setEnabled(view.can_resume)
        self._checkout_action.setEnabled(view.can_check_out)

    def show(self) -> None:
        self._should_be_visible = True
        # Re-evaluate availability — Explorer may have restarted since init.
        self._available = QSystemTrayIcon.isSystemTrayAvailable()
        if self._available:
            self._tray.show()
            if not self._recovery_timer.isActive():
                self._recovery_timer.start()
        else:
            _logger.debug("tray show deferred — system tray not available yet")
            if not self._recovery_timer.isActive():
                self._recovery_timer.start()

    def hide(self) -> None:
        self._should_be_visible = False
        self._recovery_timer.stop()
        # A click that lands just before Exit must not re-open the window
        # behind the tray we are about to abandon.
        self._open_timer.stop()
        self._tray.hide()

    @Slot()
    def handle_taskbar_created(self) -> None:
        """Re-show the tray after Explorer restarts (TaskbarCreated)."""
        if self._should_be_visible:
            _logger.info("tray recovery: TaskbarCreated — re-showing icon")
            self._available = QSystemTrayIcon.isSystemTrayAvailable()
            self._tray.show()

    @Slot(QSystemTrayIcon.ActivationReason)
    def handle_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Open the app when the icon itself is clicked.

        Single click and double click both mean "show me the app". The open
        request is coalesced over a short window because Windows reports a
        double click as ``Trigger`` *and* ``DoubleClick``, which would
        otherwise surface the window twice. Middle click and context-menu
        reasons are ignored — the menu handles its own interaction.
        """
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._open_timer.start()

    @Slot()
    def handle_system_resume(self) -> None:
        """System resume can also orphan the icon — re-ensure visibility."""
        if self._should_be_visible:
            self._available = QSystemTrayIcon.isSystemTrayAvailable()
            if self._available and not self._tray.isVisible():
                _logger.info("tray recovery: system resume — re-showing icon")
                self._tray.show()

    @Slot()
    def _ensure_visible(self) -> None:
        """Poll-driven guard — re-shows if shell available but icon gone."""
        if not self._should_be_visible:
            return
        available = QSystemTrayIcon.isSystemTrayAvailable()
        if available != self._available:
            self._available = available
        if self._available and not self._tray.isVisible():
            _logger.info("tray recovery: poll detected missing icon — re-showing")
            self._tray.show()
        elif not self._available:
            _logger.debug("tray poll: system tray still unavailable")

    def message(self, title: str, body: str) -> None:
        """Non-intrusive tray balloon (best-effort on supported platforms)."""
        if self._available:
            self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information)


def _state_label(state: TrayState) -> str:
    return {
        TrayState.CHECKED_IN: "Checked In",
        TrayState.ON_BREAK: "On Break",
        TrayState.CHECKED_OUT: "Checked Out",
        TrayState.ATTENTION: "Attention Required",
    }[state]
