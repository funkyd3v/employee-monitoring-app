"""System tray (docs/UI_SPEC.md §System tray).

State is surfaced through a colored icon + tooltip and a context menu with
Open Dashboard, current status, session actions (Take a Break / Check Out),
Logout, and Exit. No binary assets exist yet, so tray icons are painted from
theme tokens (privacy/status transparency: the employee always knows whether
monitoring is running, per docs/SECURITY_PRIVACY.md §checklist).
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app.core.logging import get_logger

_logger = get_logger("ui.tray")

from app.config.constants import APP_NAME
from app.domain.sessions.state_machine import AppState
from app.services.session_service import SessionView
from app.ui.theme.tokens import ACTIVE_PALETTE


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


def _paint_tray_icon(state: TrayState) -> QPixmap:
    """Draw a 32px tray glyph at 2x device-pixel resolution."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    center = size // 4
    colors = {
        TrayState.CHECKED_IN: ACTIVE_PALETTE.success,
        TrayState.ON_BREAK: ACTIVE_PALETTE.warning,
        TrayState.CHECKED_OUT: ACTIVE_PALETTE.text_secondary,
        TrayState.ATTENTION: ACTIVE_PALETTE.danger,
    }
    color = QColor(colors[state])

    if state is TrayState.CHECKED_OUT:
        pen = QPen(color, 5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center - 14, center - 14, 28, 28)
    elif state is TrayState.ON_BREAK:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center - 14, center - 14, 28, 28)
        painter.setBrush(Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.drawRect(center - 14, center, 28, 14)
    else:
        painter.setPen(QPen(QColor(colors[state]), 1, Qt.PenStyle.SolidLine))
        painter.setBrush(color)
        painter.drawEllipse(center - 12, center - 12, 24, 24)

    painter.end()
    return pixmap


class TrayManager(QObject):
    """Owns the tray icon + menu; emits domain-ish signals back to the UI."""

    open_dashboard = Signal()
    take_break = Signal()
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

        self._title_action = self._menu.addAction(APP_NAME)
        self._title_action.setEnabled(False)
        self._menu.addSeparator()

        self._open_action = self._menu.addAction("Open Dashboard")
        self._open_action.triggered.connect(self.open_dashboard.emit)

        self._status_action = self._menu.addAction("Current Status: Checked Out")
        self._status_action.setEnabled(False)
        self._menu.addSeparator()

        self._break_action = self._menu.addAction("Take a Break")
        self._break_action.triggered.connect(self.take_break.emit)

        self._checkout_action = self._menu.addAction("Check Out")
        self._checkout_action.triggered.connect(self.check_out.emit)
        self._menu.addSeparator()

        self._logout_action = self._menu.addAction("Logout")
        self._logout_action.triggered.connect(self.logout.emit)

        self._exit_action = self._menu.addAction("Exit")
        self._exit_action.triggered.connect(self.exit_requested.emit)

        self._tray.setContextMenu(self._menu)
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
        self._tray.setIcon(QIcon(_paint_tray_icon(state)))
        self._tray.setToolTip(f"{APP_NAME} \u2014 {_state_label(state)}")
        self._status_action.setText(f"Current Status: {_state_label(state)}")
        self._break_action.setEnabled(view.can_take_break)
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
        self._tray.hide()

    @Slot()
    def handle_taskbar_created(self) -> None:
        """Re-show the tray after Explorer restarts (TaskbarCreated)."""
        if self._should_be_visible:
            _logger.info("tray recovery: TaskbarCreated — re-showing icon")
            self._available = QSystemTrayIcon.isSystemTrayAvailable()
            self._tray.show()

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
