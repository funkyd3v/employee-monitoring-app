"""Design tokens (docs/UI_SPEC.md §Visual design system).

All colors, type scales, and radii live here — component and window files
never hard-code a color or font value inline. The dark palette is the
phase-4 deliverable ("Dark theme + design tokens"); the structure is
palette-agnostic so a light palette can be added later without touching any
component file (UI_SPEC documents light as a future first-class palette).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# ── Typography ────────────────────────────────────────────────────────────
# Inter is preferred; native Windows fallbacks fill in when it is absent.
FONT_FAMILY: tuple[str, ...] = ("Inter", "Segoe UI Variable", "Segoe UI")
FONT_MONO: tuple[str, ...] = (
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "monospace",
)

TITLE_FONT_PT = 20
BODY_FONT_PT = 14
CAPTION_FONT_PT = 12
SMALL_FONT_PT = 11

# ── Shape ──────────────────────────────────────────────────────────────────
RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14


@dataclass(frozen=True)
class Palette:
    """Semantic color roles (docs/UI_SPEC.md token table)."""

    background: str
    surface: str
    elevated: str
    text_primary: str
    text_secondary: str
    border: str
    accent: str
    accent_end: str  # gradient partner for primary CTAs
    success: str
    warning: str
    danger: str

    # Soft tints used for status-pill surfaces and quiet hover fills.
    accent_soft: str
    success_soft: str
    warning_soft: str
    danger_soft: str
    # Modal scrim over the page behind a dialog.
    scrim: str


DARK: Palette = Palette(
    background="#0F1115",
    surface="#171A21",
    elevated="#1E222B",
    text_primary="#F5F7FA",
    text_secondary="#9AA3B2",
    border="#2A303B",
    accent="#6366F1",
    accent_end="#8B5CF6",
    success="#22C55E",
    warning="#F59E0B",
    danger="#EF4444",
    accent_soft="rgba(99, 102, 241, 0.14)",
    success_soft="rgba(34, 197, 94, 0.14)",
    warning_soft="rgba(245, 158, 11, 0.14)",
    danger_soft="rgba(239, 68, 68, 0.14)",
    scrim="rgba(6, 7, 10, 0.55)",
)

ACTIVE_PALETTE: Palette = DARK


def with_palette(palette: Palette, **overrides: str) -> Palette:
    """Derive a modified palette (tests / future light theme)."""
    return replace(palette, **overrides)
