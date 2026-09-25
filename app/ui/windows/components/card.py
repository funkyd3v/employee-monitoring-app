"""Shared surface container for cards and panels."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from app.ui.theme import current_palette
from app.ui.theme.effects import ELEVATION_CARD, apply_drop_shadow

if TYPE_CHECKING:
    from app.ui.theme.effects import Elevation
    from app.ui.theme.tokens import Palette


class Card(QWidget):
    """A styled surface that can opt into the shared elevation treatment."""

    def __init__(
        self,
        *,
        object_name: str = "Card",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def apply_shadow(
        self,
        *,
        palette: Palette | None = None,
        elevation: Elevation = ELEVATION_CARD,
    ) -> None:
        apply_drop_shadow(self, palette or current_palette(), elevation)


__all__ = ["Card"]
