"""Design tokens shared by the Qt Widgets presentation layer."""

from __future__ import annotations

from dataclasses import dataclass, replace

FONT_FAMILY: tuple[str, ...] = ("Inter", "Segoe UI Variable", "Segoe UI")
FONT_MONO: tuple[str, ...] = (
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "monospace",
)

TITLE_FONT_PX = 20
STATUS_FONT_PX = 18
BODY_FONT_PX = 14
CAPTION_FONT_PX = 12
SMALL_FONT_PX = 11
LABEL_FONT_PX = 13
CHIP_VALUE_FONT_PX = 16
READY_TIMER_FONT_PX = 56
ACTIVE_TIMER_FONT_PX = 60
SECONDARY_TIMER_FONT_PX = 52

FONT_WEIGHT_REGULAR = 400
FONT_WEIGHT_MEDIUM = 500
FONT_WEIGHT_SEMIBOLD = 600
FONT_WEIGHT_BOLD = 700

SPACING_XXS = 4
SPACING_XS = 6
SPACING_SM = 10
SPACING_INPUT = 12
SPACING_MD = 16
SPACING_LG = 24
SPACING_XL = 32
SPACING_2XL = 64
SPACING_STATUS_VERTICAL = 5
SPACING_STATUS_GAP = 8
SPACING_CHIP_HORIZONTAL = 20
SPACING_CHIP_VERTICAL = 10

RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_CARD = 16
RADIUS_PILL = 20

BORDER_WIDTH = 1

BUTTON_ICON_SIZE = 16
BUTTON_ICON_SIZE_LARGE = 18
BUTTON_HEIGHT = 40
BUTTON_LARGE_MIN_WIDTH = 156
BUTTON_LARGE_HEIGHT = 48
SPINNER_SIZE = 20
LOGIN_SPINNER_SIZE = 18
WINDOW_ICON_SIZE = 18
BRAND_MARK_SIZE_SMALL = 22
BRAND_MARK_SIZE_LARGE = 56
PILL_DOT_RADIUS = 5

WINDOW_WIDTH = 900
WINDOW_HEIGHT = 600
TITLE_BAR_HEIGHT = 46
WINDOW_BUTTON_WIDTH = 42
WINDOW_BUTTON_HEIGHT = 34
PROFILE_TRIGGER_HEIGHT = 32
PROFILE_TRIGGER_MIN_WIDTH = 96
PROFILE_TRIGGER_MAX_WIDTH = 180
PROFILE_ICON_SIZE = 18
PROFILE_CHEVRON_SIZE = 14
WORKING_CAPTION_MAX_WIDTH = 600
LOGIN_CARD_MAX_WIDTH = 430
DIALOG_WIDTH = 420
DIALOG_MIN_HEIGHT = 180
TIMER_MIN_HEIGHT = 84


@dataclass(frozen=True)
class Palette:
    """Semantic color roles used by the declarative Qt stylesheets."""

    background: str
    surface: str
    elevated: str
    text_primary: str
    text_secondary: str
    border: str
    accent: str
    accent_end: str
    success: str
    warning: str
    danger: str
    accent_soft: str
    success_soft: str
    warning_soft: str
    danger_soft: str
    scrim: str
    surface_active: str = "#E8F1EC"
    surface_hover: str = "#F0ECE5"
    accent_hover: str = "#0B625B"
    danger_hover: str = "#8E3B2E"
    on_accent: str = "#FFFFFF"
    focus: str = "#0F766E"
    shadow: str = "rgba(24, 35, 32, 0.18)"


DARK: Palette = Palette(
    background="#111A18",
    surface="#182321",
    elevated="#22302D",
    text_primary="#F2F4EF",
    text_secondary="#AAB8B1",
    border="#30413C",
    accent="#56B5A6",
    accent_end="#479C8F",
    success="#79C6A5",
    warning="#D7A85C",
    danger="#D77C68",
    accent_soft="rgba(86, 181, 166, 0.14)",
    success_soft="rgba(121, 198, 165, 0.14)",
    warning_soft="rgba(215, 168, 92, 0.14)",
    danger_soft="rgba(215, 124, 104, 0.14)",
    scrim="rgba(4, 10, 9, 0.68)",
    surface_active="#1B302B",
    surface_hover="#263733",
    accent_hover="#479C8F",
    danger_hover="#B96857",
    on_accent="#10221E",
    focus="#56B5A6",
    shadow="rgba(0, 0, 0, 0.34)",
)

LIGHT: Palette = Palette(
    background="#F5F1EA",
    surface="#FCFAF6",
    elevated="#FFFFFF",
    text_primary="#20302C",
    text_secondary="#5F6D68",
    border="#D9DED8",
    accent="#0F766E",
    accent_end="#0B625B",
    success="#2D765E",
    warning="#A66A2C",
    danger="#A44736",
    accent_soft="rgba(15, 118, 110, 0.10)",
    success_soft="rgba(45, 118, 94, 0.12)",
    warning_soft="rgba(166, 106, 44, 0.12)",
    danger_soft="rgba(164, 71, 54, 0.12)",
    scrim="rgba(24, 35, 32, 0.52)",
    surface_active="#E8F1EC",
    surface_hover="#F0ECE5",
    accent_hover="#0B625B",
    danger_hover="#8E3B2E",
    on_accent="#FFFFFF",
    focus="#0F766E",
    shadow="rgba(24, 35, 32, 0.18)",
)

ACTIVE_PALETTE: Palette = LIGHT


def with_palette(palette: Palette, **overrides: str) -> Palette:
    """Derive a modified palette without duplicating component styling."""
    return replace(palette, **overrides)
