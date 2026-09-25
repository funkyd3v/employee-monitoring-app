"""Light-theme Qt stylesheet generated from the centralized design tokens."""

from __future__ import annotations

from app.ui.theme.tokens import (
    ACTIVE_TIMER_FONT_PX,
    BODY_FONT_PX,
    BORDER_WIDTH,
    BUTTON_HEIGHT,
    BUTTON_LARGE_HEIGHT,
    BUTTON_LARGE_MIN_WIDTH,
    CAPTION_FONT_PX,
    CHIP_VALUE_FONT_PX,
    FONT_FAMILY,
    FONT_MONO,
    FONT_WEIGHT_BOLD,
    FONT_WEIGHT_MEDIUM,
    FONT_WEIGHT_REGULAR,
    FONT_WEIGHT_SEMIBOLD,
    LABEL_FONT_PX,
    RADIUS_CARD,
    RADIUS_MD,
    RADIUS_PILL,
    RADIUS_SM,
    SMALL_FONT_PX,
    SPACING_INPUT,
    SPACING_LG,
    SPACING_MD,
    SPACING_SM,
    SPACING_XS,
    SPACING_XXS,
    STATUS_FONT_PX,
    TITLE_FONT_PX,
    Palette,
)

_FAMILY = ", ".join(f'"{family}"' for family in FONT_FAMILY)
_MONO = ", ".join(f'"{family}"' for family in FONT_MONO)


def build_light_stylesheet(p: Palette) -> str:
    """Return the application QSS for the supplied palette."""
    return f"""
* {{
    font-family: {_FAMILY};
    font-size: {BODY_FONT_PX}px;
    color: {p.text_primary};
}}

QWidget {{
    background-color: {p.background};
}}

QWidget[windowSurface="true"] {{
    border: {BORDER_WIDTH}px solid {p.border};
}}

QWidget#WindowBody {{
    background-color: {p.background};
}}

QLabel {{
    background: transparent;
}}

QStackedWidget {{
    background: transparent;
}}

QWidget#ReadyPage,
QWidget#BreakPage,
QWidget#CompletedPage {{
    background-color: {p.background};
}}

QWidget#WorkingPage {{
    background-color: {p.surface_active};
}}

QWidget#ActionRow,
QWidget#ActivityMeta {{
    background: transparent;
}}

QWidget#TitleBar {{
    background-color: {p.surface};
    border-bottom: {BORDER_WIDTH}px solid {p.border};
}}

QLabel#TitleBarTitle {{
    color: {p.text_primary};
    font-size: {LABEL_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QLabel#TeamNameLabel {{
    color: {p.text_secondary};
    font-size: {CAPTION_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QLabel#PanelTitle {{
    color: {p.text_primary};
    font-size: {TITLE_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QLabel#StatusLine {{
    color: {p.text_primary};
    font-size: {STATUS_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QLabel#PanelSubtitle {{
    color: {p.text_secondary};
    font-size: {BODY_FONT_PX}px;
    font-weight: {FONT_WEIGHT_REGULAR};
}}

QLabel#PanelCaption {{
    color: {p.text_secondary};
    font-size: {CAPTION_FONT_PX}px;
    font-weight: {FONT_WEIGHT_REGULAR};
}}

QWidget#LoginCenter {{
    background: transparent;
}}

QWidget#LoginCard,
QWidget#Card {{
    background-color: {p.surface};
    border: {BORDER_WIDTH}px solid {p.border};
    border-radius: {RADIUS_CARD}px;
}}

QLabel#TimerLabel {{
    font-family: {_MONO};
    font-size: {ACTIVE_TIMER_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
    color: {p.text_primary};
    background: transparent;
}}

QWidget#WorkingPage QLabel#TimerLabel {{
    color: {p.accent};
}}

QLabel#TimerCaption {{
    color: {p.text_secondary};
    font-size: {CAPTION_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QLabel#PillText {{
    color: {p.text_primary};
    font-size: {CAPTION_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QLabel#LastActivity {{
    color: {p.text_secondary};
    font-size: {CAPTION_FONT_PX}px;
    font-weight: {FONT_WEIGHT_MEDIUM};
}}

QWidget#StatusPill {{
    border-radius: {RADIUS_PILL}px;
    border: {BORDER_WIDTH}px solid {p.border};
    background-color: {p.surface};
}}

QWidget#StatusPill[pillState="ACTIVE"] {{
    background-color: {p.success_soft};
    border-color: {p.success};
}}

QWidget#StatusPill[pillState="IDLE"] {{
    background-color: {p.warning_soft};
    border-color: {p.warning};
}}

QWidget#StatusPill[pillState="BREAK"],
QWidget#StatusPill[pillState="OFF"] {{
    background-color: {p.surface_hover};
    border-color: {p.border};
}}

QWidget#Chip {{
    background-color: {p.surface};
    border: {BORDER_WIDTH}px solid {p.border};
    border-radius: {RADIUS_SM}px;
}}

QLabel#ChipValue {{
    color: {p.text_primary};
    font-size: {CHIP_VALUE_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QLabel#ChipLabel {{
    color: {p.text_secondary};
    font-size: {CAPTION_FONT_PX}px;
    font-weight: {FONT_WEIGHT_MEDIUM};
}}

QPushButton {{
    font-family: {_FAMILY};
    font-size: {BODY_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
    border: {BORDER_WIDTH}px solid transparent;
    border-radius: {RADIUS_MD}px;
    padding: {SPACING_SM}px {SPACING_INPUT}px;
    min-height: {BUTTON_HEIGHT}px;
    background-color: {p.accent};
    color: {p.on_accent};
}}

QPushButton:hover {{
    background-color: {p.accent_hover};
}}

QPushButton:pressed {{
    background-color: {p.accent_hover};
}}

QPushButton:focus {{
    border: {BORDER_WIDTH}px solid {p.focus};
}}

QPushButton:disabled {{
    color: {p.text_secondary};
    background-color: {p.surface_hover};
    border-color: {p.border};
}}

QPushButton[role="primary"] {{
    background-color: {p.accent};
    color: {p.on_accent};
    border-color: {p.accent};
}}

QPushButton[role="primary"]:hover {{
    background-color: {p.accent_hover};
    border-color: {p.accent_hover};
}}

QPushButton[role="primary"]:pressed {{
    background-color: {p.accent_hover};
    border-color: {p.accent_hover};
}}

QPushButton[role="primary"]:focus {{
    border: {BORDER_WIDTH}px solid {p.focus};
}}

QPushButton[role="primary"][size="large"] {{
    border-radius: {RADIUS_CARD}px;
    min-width: {BUTTON_LARGE_MIN_WIDTH}px;
    min-height: {BUTTON_LARGE_HEIGHT}px;
    padding: {SPACING_MD}px {SPACING_LG}px;
}}

QPushButton[role="secondary"] {{
    background-color: transparent;
    color: {p.text_primary};
    border-color: {p.border};
}}

QPushButton[role="secondary"]:hover {{
    background-color: {p.surface_hover};
    border-color: {p.accent};
    color: {p.text_primary};
}}

QPushButton[role="secondary"]:pressed {{
    background-color: {p.accent_soft};
    border-color: {p.accent};
}}

QPushButton[role="secondary"]:focus {{
    border: {BORDER_WIDTH}px solid {p.focus};
}}

QPushButton[role="secondary"]:disabled {{
    color: {p.text_secondary};
    background-color: transparent;
    border-color: {p.border};
}}

QPushButton[role="danger"] {{
    background-color: {p.danger_soft};
    color: {p.danger};
    border-color: {p.danger_soft};
}}

QPushButton[role="danger"]:hover {{
    background-color: {p.danger_soft};
    color: {p.danger_hover};
    border-color: {p.danger};
}}

QPushButton[role="danger"]:pressed {{
    background-color: {p.danger_soft};
    color: {p.danger_hover};
    border-color: {p.danger_hover};
}}

QPushButton[role="danger"]:focus {{
    border: {BORDER_WIDTH}px solid {p.focus};
}}

QPushButton[role="danger"]:disabled {{
    color: {p.text_secondary};
    background-color: {p.surface_hover};
    border-color: {p.border};
}}

QPushButton[role="ghost"] {{
    background-color: transparent;
    color: {p.text_secondary};
    border: {BORDER_WIDTH}px solid transparent;
    padding: {SPACING_XS}px {SPACING_SM}px;
    border-radius: {RADIUS_SM}px;
    min-height: 32px;
}}

QPushButton[role="ghost"]:hover {{
    background-color: {p.surface_hover};
    color: {p.text_primary};
}}

QPushButton[role="ghost"]:pressed {{
    background-color: {p.accent_soft};
    color: {p.text_primary};
}}

QPushButton[role="ghost"]:focus {{
    border: {BORDER_WIDTH}px solid {p.focus};
}}

QPushButton[role="ghost"]:disabled {{
    color: {p.text_secondary};
    background-color: transparent;
    border-color: transparent;
}}

QPushButton#TitleMinButton,
QPushButton#TitleCloseButton {{
    min-width: 0;
    min-height: 0;
    padding: 0;
    background-color: transparent;
    border: {BORDER_WIDTH}px solid transparent;
    border-radius: {RADIUS_SM}px;
    color: {p.text_secondary};
}}

QPushButton#TitleMinButton:hover,
QPushButton#TitleCloseButton:hover {{
    background-color: {p.surface_hover};
    color: {p.text_primary};
    border-color: {p.border};
}}

QPushButton#TitleCloseButton:hover {{
    background-color: {p.danger_soft};
    color: {p.danger};
    border-color: {p.danger_soft};
}}

QPushButton#TitleMinButton:pressed,
QPushButton#TitleCloseButton:pressed {{
    background-color: {p.accent_soft};
    color: {p.text_primary};
    border-color: {p.border};
}}

QWidget#FieldContainer {{
    background: transparent;
}}

QLineEdit {{
    background-color: {p.elevated};
    border: {BORDER_WIDTH}px solid {p.border};
    border-radius: {RADIUS_MD}px;
    padding: {SPACING_SM}px {SPACING_INPUT}px;
    color: {p.text_primary};
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}

QLineEdit:focus {{
    border: {BORDER_WIDTH}px solid {p.focus};
}}

QLineEdit:disabled {{
    color: {p.text_secondary};
    background-color: {p.surface};
}}

QLineEdit[error="true"] {{
    border: {BORDER_WIDTH}px solid {p.danger};
}}

QLineEdit[error="true"]:focus {{
    border: {BORDER_WIDTH}px solid {p.danger};
}}

QLabel#FieldLabel {{
    color: {p.text_secondary};
    font-size: {SMALL_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QLabel#FieldError {{
    color: {p.danger};
    font-size: {SMALL_FONT_PX}px;
    font-weight: {FONT_WEIGHT_MEDIUM};
}}

QToolButton#PasswordToggle {{
    background-color: transparent;
    border: none;
    color: {p.text_secondary};
    font-size: {SMALL_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
    padding: 0 {SPACING_SM}px;
}}

QToolButton#PasswordToggle:hover {{
    color: {p.text_primary};
}}

QToolButton#PasswordToggle:pressed {{
    color: {p.accent};
}}

QToolButton#ProfileTrigger {{
    background-color: {p.surface};
    border: {BORDER_WIDTH}px solid {p.border};
    border-radius: {RADIUS_MD}px;
    color: {p.text_primary};
    font-size: {CAPTION_FONT_PX}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
    padding: 0 {SPACING_INPUT}px;
    min-height: 32px;
    text-align: left;
}}

QToolButton#ProfileTrigger:hover {{
    background-color: {p.surface_hover};
    border-color: {p.accent};
}}

QToolButton#ProfileTrigger:pressed,
QToolButton#ProfileTrigger:focus {{
    background-color: {p.accent_soft};
    border-color: {p.accent};
}}

QMenu {{
    background-color: {p.elevated};
    border: {BORDER_WIDTH}px solid {p.border};
    border-radius: {RADIUS_MD}px;
    padding: {SPACING_XS}px;
}}

QMenu::item {{
    padding: {SPACING_XXS}px {SPACING_MD}px;
    border-radius: {RADIUS_SM}px;
    color: {p.text_primary};
}}

QMenu::item:selected {{
    background-color: {p.accent_soft};
    color: {p.text_primary};
}}

QMenu::item:disabled {{
    color: {p.text_secondary};
    background-color: transparent;
}}

QMenu::separator {{
    height: {BORDER_WIDTH}px;
    background-color: {p.border};
    margin: {SPACING_XS}px {SPACING_SM}px;
}}

QWidget#DialogScrim {{
    background-color: {p.scrim};
}}

QWidget#DialogCardShell {{
    background: transparent;
}}

QWidget#DialogCard {{
    background-color: {p.surface};
    border: {BORDER_WIDTH}px solid {p.border};
    border-radius: {RADIUS_CARD}px;
}}

QLabel#DialogTitle {{
    color: {p.text_primary};
    font-size: {STATUS_FONT_PX}px;
    font-weight: {FONT_WEIGHT_BOLD};
}}

QLabel#DialogMessage {{
    color: {p.text_secondary};
    font-size: {BODY_FONT_PX}px;
    font-weight: {FONT_WEIGHT_REGULAR};
}}

QToolTip {{
    background-color: {p.elevated};
    color: {p.text_primary};
    border: {BORDER_WIDTH}px solid {p.border};
    border-radius: {RADIUS_SM}px;
    padding: {SPACING_XXS}px {SPACING_XXS * 2}px;
    font-size: {CAPTION_FONT_PX}px;
}}
"""


build_dark_stylesheet = build_light_stylesheet
