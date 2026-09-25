"""Theme application and bundled resource loading."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ui.theme import light_theme
from app.ui.theme.assets import application_icon, font_asset_paths
from app.ui.theme.tokens import (
    ACTIVE_PALETTE,
    BODY_FONT_PX,
    FONT_FAMILY,
    FONT_MONO,
    Palette,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

_active_palette: Palette = ACTIVE_PALETTE


def _load_bundled_fonts() -> None:
    from PySide6.QtGui import QFontDatabase

    for path in font_asset_paths():
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))


def _installed_families() -> list[str]:
    from PySide6.QtGui import QFontDatabase

    return list(QFontDatabase.families())


def _resolve_family(preferred: tuple[str, ...], fallback: str) -> str:
    families = set(_installed_families())
    for candidate in preferred:
        if candidate in families:
            return candidate
    return fallback


def current_palette() -> Palette:
    """Return the palette selected for the active QApplication."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        value = app.property("theme_palette")
        if isinstance(value, Palette):
            return value
    return _active_palette


def ui_font() -> QFont:
    """Return the bundled UI font with a consistent pixel size."""
    from PySide6.QtGui import QFont

    font = QFont(_resolve_family(FONT_FAMILY, fallback="Segoe UI"))
    font.setPixelSize(BODY_FONT_PX)
    font.setWeight(QFont.Weight.Normal)
    return font


def mono_font() -> QFont:
    """Return the timer font, falling back to the platform fixed face."""
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    fallback = QFontInfo(system).family()
    font = QFont(_resolve_family(FONT_MONO, fallback=fallback))
    font.setPixelSize(BODY_FONT_PX)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def apply_theme(app: QApplication, palette: Palette | None = None) -> None:
    """Apply the selected palette, stylesheet, font, and application icon."""
    global _active_palette

    chosen = palette or ACTIVE_PALETTE
    _active_palette = chosen
    _load_bundled_fonts()
    app.setProperty("theme_palette", chosen)
    app.setStyleSheet(light_theme.build_light_stylesheet(chosen))
    app.setFont(ui_font())
    app.setWindowIcon(application_icon())


__all__ = ["apply_theme", "current_palette", "mono_font", "ui_font"]
