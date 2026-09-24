"""Compatibility entry point for the dark palette stylesheet."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ui.theme.light_theme import build_light_stylesheet

if TYPE_CHECKING:
    from app.ui.theme.tokens import Palette


def build_dark_stylesheet(p: Palette) -> str:
    """Return the shared stylesheet for the supplied dark palette."""
    return build_light_stylesheet(p)
