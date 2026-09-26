"""Frameless, fixed-size app window base (docs/UI_SPEC.md §Window behavior).

Both login and dashboard windows derive from here so the "no fullscreen, no
maximize" product rule (#8) is structurally impossible to violate on any
consumer screen. Close is *never* a quit: it emits :attr:`closed_to_tray`
and hides, because an explicit Exit exists only in the tray menu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.ui.theme.tokens import WINDOW_HEIGHT, WINDOW_WIDTH
from app.ui.windows.components.title_bar import TitleBar

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent


class FramelessWindow(QWidget):
    """Fixed-size frameless shell with a custom drag title bar."""

    closed_to_tray = Signal()

    def __init__(
        self,
        *,
        window_size: tuple[int, int] = (WINDOW_WIDTH, WINDOW_HEIGHT),
    ) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        super().__init__(None, flags)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("windowSurface", True)

        self.setFixedSize(*window_size)

        self._title_bar = TitleBar()
        self._title_bar.minimize_clicked.connect(self.showMinimized)
        self._title_bar.close_clicked.connect(self._on_title_close)

        self._body = QWidget(self)
        self._body.setObjectName("WindowBody")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._title_bar)
        root.addWidget(self._body, 1)

    @property
    def title_bar(self) -> TitleBar:
        return self._title_bar

    def body(self) -> QWidget:
        """The content region beneath the title bar (fill with a layout)."""
        return self._body

    def present(self) -> None:
        """Bring the window in front of the user, whatever state it is in.

        The window spends most of its life hidden or minimized in the tray,
        so "the user asked for the app" must always end with a usable window:
        un-minimize, show if hidden, then raise and take focus. This is the
        only supported way for the tray or a second launch to surface a
        window — it never resizes, so rule #8 (no fullscreen/maximize)
        cannot be violated from here.
        """
        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def _on_title_close(self) -> None:
        self.closed_to_tray.emit()
        self.hide()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Swallow native close: emit ``closed_to_tray`` and hide instead."""
        event.accept()
        self.closed_to_tray.emit()
        self.hide()
