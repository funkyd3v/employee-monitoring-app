"""Theme application: wire design tokens into a running QApplication.

Phase 4 ships the dark palette (docs/TESTING_AND_DOD.md, Phase 4 deliverable
"Dark theme + design tokens"). Components and windows never reference colors
directly — they select styling through object names / dynamic properties
defined in :mod:`app.ui.theme.dark_theme`; swapping the palette is a single
call here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ui.theme import dark_theme
from app.ui.theme.tokens import FONT_FAMILY, FONT_MONO, Palette

if TYPE_CHECKING:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication


def _installed_families() -> list[str]:
    from PySide6.QtGui import QFontDatabase

    return list(QFontDatabase.families())


def _resolve_family(preferred: tuple[str, ...], fallback: str) -> str:
    """Pick the first installed family from ``preferred``, else ``fallback``."""
    families = set(_installed_families())
    for candidate in preferred:
        if candidate in families:
            return candidate
    return fallback


def ui_font() -> QFont:
    """Primary UI font: Inter when installed, else a native Windows face."""
    from PySide6.QtGui import QFont

    return QFont(_resolve_family(FONT_FAMILY, fallback="Segoe UI"), 14)


def mono_font() -> QFont:
    """Monospaced numeral face for the timer (digit width must not jitter)."""
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    fallback = QFontInfo(system).family()
    font = QFont(_resolve_family(FONT_MONO, fallback=fallback), 14)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def apply_theme(app: QApplication, palette: Palette | None = None) -> None:
    """Apply the stylesheet + default fonts to ``app``.

    ``palette`` overrides the active palette (tests / future light theme).
    Styling is selected via object names and dynamic properties, so
    re-applying is cheap and safe.
    """
    from app.ui.theme.tokens import ACTIVE_PALETTE

    chosen = palette or ACTIVE_PALETTE
    app.setStyleSheet(dark_theme.build_dark_stylesheet(chosen))
    app.setFont(ui_font())


__all__ = ["apply_theme", "mono_font", "ui_font"]
