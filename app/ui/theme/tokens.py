"""Design tokens (docs/UI_SPEC.md §Visual design system).

All colors, type scales, spacing, and radii live here. Component and window
files select styles through object names and dynamic properties.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

FONT_FAMILY: tuple[str, ...] = ("Inter", "Segoe UI Variable", "Segoe UI")
FONT_MONO: tuple[str, ...] = (
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "monospace",
)

TITLE_FONT_PT = 20
STATUS_FONT_PT = 18
BODY_FONT_PT = 14
CAPTION_FONT_PT = 12
SMALL_FONT_PT = 11
READY_TIMER_FONT_PT = 56
ACTIVE_TIMER_FONT_PT = 60
SECONDARY_TIMER_FONT_PT = 52

SPACING_XS = 6
SPACING_SM = 10
SPACING_MD = 16
SPACING_LG = 24
SPACING_XL = 32

RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_LG = 16
RADIUS_XL = 20


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
