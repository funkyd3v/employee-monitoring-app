"""Shared motion and elevation tokens for the Qt Widgets UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from app.ui.theme.tokens import Palette


@dataclass(frozen=True)
class Motion:
    hover_duration_ms: int = 160
    press_duration_ms: int = 120
    dialog_duration_ms: int = 180
    page_duration_ms: int = 180
    shake_duration_ms: int = 360
    pulse_duration_ms: int = 1800
    spinner_interval_ms: int = 16
    spinner_angle_step: int = 9


@dataclass(frozen=True)
class Elevation:
    blur_radius: int = 28
    x_offset: int = 0
    y_offset: int = 6


MOTION = Motion()
ELEVATION_CARD = Elevation()
ELEVATION_OVERLAY = Elevation()


def apply_drop_shadow(
    widget: QWidget,
    palette: Palette,
    elevation: Elevation = ELEVATION_CARD,
) -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(elevation.blur_radius)
    effect.setOffset(elevation.x_offset, elevation.y_offset)
    effect.setColor(QColor(palette.shadow))
    widget.setGraphicsEffect(effect)
