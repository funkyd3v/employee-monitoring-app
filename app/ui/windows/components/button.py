"""Shared button primitive for the Qt Widgets UI."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QPushButton,
    QWidget,
)

from app.ui.theme import current_palette
from app.ui.theme.effects import MOTION
from app.ui.theme.icons import themed_icon
from app.ui.theme.tokens import (
    BUTTON_ICON_SIZE,
    BUTTON_ICON_SIZE_LARGE,
    Palette,
)

if TYPE_CHECKING:
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QEnterEvent, QMouseEvent


class ButtonRole(StrEnum):
    """Semantic button treatments backed by the generated stylesheet."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    DANGER = "danger"
    GHOST = "ghost"
    SECONDARY_SOLID = "secondary_solid"
    DANGER_SOLID = "danger_solid"


class ButtonSize(StrEnum):
    """Button density variants."""

    DEFAULT = "default"
    LARGE = "large"


_SOLID_ROLES = frozenset(
    {ButtonRole.PRIMARY, ButtonRole.SECONDARY_SOLID, ButtonRole.DANGER_SOLID}
)


class Button(QPushButton):
    """A themed QPushButton with semantic roles and restrained motion."""

    def __init__(
        self,
        text: str = "",
        *,
        role: ButtonRole | str = ButtonRole.PRIMARY,
        size: ButtonSize | str = ButtonSize.DEFAULT,
        icon_name: str | None = None,
        object_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._role = str(role)
        self._size = str(size)
        self._icon_name = icon_name
        self._palette = current_palette()
        if object_name is not None:
            self.setObjectName(object_name)
        self.setProperty("role", self._role)
        self.setProperty("size", self._size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._motion_animation = QPropertyAnimation(self._opacity, b"opacity", self)
        self._motion_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.set_icon_name(icon_name)

    @property
    def role(self) -> str:
        return self._role

    def set_icon_name(self, name: str | None) -> None:
        self._icon_name = name
        if name is None:
            self.setIcon(QIcon())
            self.setIconSize(
                QSize(self._default_icon_size(), self._default_icon_size())
            )
            return
        color = self._icon_color()
        self.setIcon(themed_icon(name, color, self._default_icon_size()))
        self.setIconSize(QSize(self._default_icon_size(), self._default_icon_size()))

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.set_icon_name(self._icon_name)

    def _default_icon_size(self) -> int:
        return (
            BUTTON_ICON_SIZE_LARGE
            if self._size == ButtonSize.LARGE
            else BUTTON_ICON_SIZE
        )

    def _icon_color(self) -> str:
        if self._role in _SOLID_ROLES:
            return self._palette.on_accent
        if self._role == ButtonRole.GHOST:
            return self._palette.text_secondary
        if self._role == ButtonRole.DANGER:
            return self._palette.danger
        return self._palette.text_primary

    def _animate_opacity(self, value: float, duration: int) -> None:
        self._motion_animation.stop()
        self._motion_animation.setDuration(duration)
        self._motion_animation.setStartValue(self._opacity.opacity())
        self._motion_animation.setEndValue(value)
        self._motion_animation.start()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        super().enterEvent(event)
        if self.isEnabled():
            self._animate_opacity(1.0, MOTION.hover_duration_ms)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        super().leaveEvent(event)
        if self.isEnabled():
            self._animate_opacity(0.96, MOTION.hover_duration_ms)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.isEnabled():
            self._animate_opacity(0.90, MOTION.press_duration_ms)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.isEnabled():
            self._animate_opacity(1.0, MOTION.press_duration_ms)
        super().mouseReleaseEvent(event)


__all__ = ["Button", "ButtonRole", "ButtonSize"]
