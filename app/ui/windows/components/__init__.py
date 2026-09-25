"""Reusable dashboard/login UI components (docs/PROJECT_STRUCTURE.md)."""

from app.ui.windows.components.brand_mark import BrandMark
from app.ui.windows.components.button import Button, ButtonRole, ButtonSize
from app.ui.windows.components.card import Card
from app.ui.windows.components.input_field import FormField
from app.ui.windows.components.spinner import Spinner
from app.ui.windows.components.status_pill import PillState, StatusPill
from app.ui.windows.components.timer_widget import TimerWidget, format_elapsed_hms
from app.ui.windows.components.title_bar import TitleBar

__all__ = [
    "BrandMark",
    "Button",
    "ButtonRole",
    "ButtonSize",
    "Card",
    "FormField",
    "PillState",
    "Spinner",
    "StatusPill",
    "TimerWidget",
    "TitleBar",
    "format_elapsed_hms",
]
