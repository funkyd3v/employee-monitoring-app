"""Light-theme Qt stylesheet built from the centralized design tokens."""

from __future__ import annotations

from app.ui.theme.tokens import (
    ACTIVE_TIMER_FONT_PT,
    BODY_FONT_PT,
    CAPTION_FONT_PT,
    FONT_FAMILY,
    FONT_MONO,
    RADIUS_LG,
    RADIUS_MD,
    RADIUS_SM,
    RADIUS_XL,
    SPACING_LG,
    SPACING_SM,
    SPACING_XS,
    STATUS_FONT_PT,
    TITLE_FONT_PT,
    Palette,
)

_FAMILY = ", ".join(f'"{family}"' for family in FONT_FAMILY)
_MONO = ", ".join(f'"{family}"' for family in FONT_MONO)


def build_light_stylesheet(p: Palette) -> str:
    """Return the application QSS for the supplied palette."""
    return f"""
* {{
    font-family: {_FAMILY};
    font-size: {BODY_FONT_PT}px;
    color: {p.text_primary};
}}

QWidget {{
    background-color: {p.background};
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
    border-bottom: 1px solid {p.border};
}}

QWidget#TopBar {{
    background-color: {p.surface};
    border-bottom: 1px solid {p.border};
}}

QWidget#TitleBarTitle {{
    color: {p.text_primary};
    font-size: 13px;
    font-weight: 600;
}}

QWidget#TeamNameLabel {{
    color: {p.text_secondary};
    font-size: 12px;
    font-weight: 600;
}}

QLabel#PanelTitle {{
    color: {p.text_primary};
    font-size: {TITLE_FONT_PT}px;
    font-weight: 600;
}}

QLabel#StatusLine {{
    color: {p.text_primary};
    font-size: {STATUS_FONT_PT}px;
    font-weight: 600;
}}

QLabel#PanelSubtitle {{
    color: {p.text_secondary};
    font-size: {BODY_FONT_PT}px;
    font-weight: 400;
}}

QLabel#PanelCaption {{
    color: {p.text_secondary};
    font-size: {CAPTION_FONT_PT}px;
    font-weight: 400;
}}

QWidget#LoginCenter {{
    background: transparent;
}}

QWidget#LoginCard {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_LG}px;
}}

QLabel#TimerLabel {{
    font-family: {_MONO};
    font-size: {ACTIVE_TIMER_FONT_PT}px;
    font-weight: 600;
    color: {p.text_primary};
    background: transparent;
}}

QWidget#WorkingPage QLabel#TimerLabel {{
    color: {p.accent};
}}

QLabel#TimerCaption {{
    color: {p.text_secondary};
    font-size: {CAPTION_FONT_PT}px;
    font-weight: 600;
}}

QLabel#TimerRunsCaption {{
    color: {p.text_secondary};
    font-size: {CAPTION_FONT_PT}px;
    font-weight: 400;
}}

QLabel#PillText {{
    color: {p.text_primary};
    font-size: 12px;
    font-weight: 600;
}}

QLabel#PillSecondary {{
    color: {p.text_secondary};
    font-size: 12px;
    font-weight: 400;
}}

QLabel#LastActivity {{
    color: {p.text_secondary};
    font-size: 12px;
    font-weight: 500;
}}

QWidget#StatusPill {{
    border-radius: {RADIUS_XL}px;
    border: 1px solid {p.border};
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
    border: 1px solid {p.border};
    border-radius: {RADIUS_SM}px;
}}

QLabel#ChipValue {{
    color: {p.text_primary};
    font-size: 16px;
    font-weight: 600;
}}

QLabel#ChipLabel {{
    color: {p.text_secondary};
    font-size: 11px;
    font-weight: 500;
}}

QPushButton {{
    font-family: {_FAMILY};
    font-size: {BODY_FONT_PT}px;
    font-weight: 600;
    border: 1px solid transparent;
    border-radius: {RADIUS_MD}px;
    padding: {SPACING_SM}px 20px;
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
    border: 2px solid {p.focus};
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
    border: 2px solid {p.focus};
}}

QPushButton[role="secondary"]:disabled {{
    color: {p.text_secondary};
    background-color: transparent;
    border-color: {p.border};
}}

QPushButton[role="danger"] {{
    background-color: {p.danger};
    color: {p.on_accent};
    border-color: {p.danger};
}}

QPushButton[role="danger"]:hover {{
    background-color: {p.danger_hover};
    border-color: {p.danger_hover};
}}

QPushButton[role="danger"]:pressed {{
    background-color: {p.danger_hover};
    border-color: {p.danger_hover};
}}

QPushButton[role="danger"]:focus {{
    border: 2px solid {p.focus};
}}

QPushButton[role="danger"]:disabled {{
    color: {p.text_secondary};
    background-color: {p.surface_hover};
    border-color: {p.border};
}}

QPushButton[role="ghost"] {{
    background: transparent;
    color: {p.text_secondary};
    border: 1px solid transparent;
    padding: {SPACING_XS}px {SPACING_SM}px;
    border-radius: {RADIUS_SM}px;
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
    border: 1px solid {p.focus};
}}

QPushButton[role="ghost"]:disabled {{
    color: {p.text_secondary};
    background: transparent;
    border-color: transparent;
}}

QPushButton#PrimaryCta {{
    background-color: {p.accent};
    color: {p.on_accent};
    border: 1px solid {p.accent};
    border-radius: {RADIUS_LG}px;
    padding: 13px {SPACING_LG}px;
    min-width: 156px;
}}

QPushButton#PrimaryCta:hover {{
    background-color: {p.accent_hover};
    border-color: {p.accent_hover};
}}

QPushButton#PrimaryCta:pressed {{
    background-color: {p.accent_hover};
    border-color: {p.accent_hover};
}}

QPushButton#PrimaryCta:focus {{
    border: 2px solid {p.focus};
}}

QPushButton#TitleMinButton,
QPushButton#TitleCloseButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_SM}px;
    color: {p.text_secondary};
    font-size: 14px;
    font-weight: 600;
    padding: 0;
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
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    padding: 10px 12px;
    color: {p.text_primary};
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}

QLineEdit:focus {{
    border: 2px solid {p.focus};
}}

QLineEdit:disabled {{
    color: {p.text_secondary};
    background-color: {p.surface};
}}

QLineEdit[error="true"] {{
    border: 1px solid {p.danger};
}}

QLineEdit[error="true"]:focus {{
    border: 2px solid {p.danger};
}}

QLabel#FieldLabel {{
    color: {p.text_secondary};
    font-size: 11px;
    font-weight: 600;
}}

QLabel#FieldError {{
    color: {p.danger};
    font-size: 11px;
    font-weight: 500;
}}

QToolButton#PasswordToggle {{
    background: transparent;
    border: none;
    color: {p.text_secondary};
    font-size: 11px;
    font-weight: 600;
    padding: 0 10px;
}}

QToolButton#PasswordToggle:hover {{
    color: {p.text_primary};
}}

QToolButton#PasswordToggle:pressed {{
    color: {p.accent};
}}

QToolButton#ProfileTrigger {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    color: {p.text_primary};
    font-size: 12px;
    font-weight: 600;
    padding: 0 12px;
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
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    padding: {SPACING_XS}px;
}}

QMenu::item {{
    padding: 8px 16px;
    border-radius: {RADIUS_SM}px;
    color: {p.text_primary};
}}

QMenu::item:selected {{
    background-color: {p.accent_soft};
    color: {p.text_primary};
}}

QMenu::item:disabled {{
    color: {p.text_secondary};
    background: transparent;
}}

QMenu::separator {{
    height: 1px;
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
    border: 1px solid {p.border};
    border-radius: {RADIUS_LG}px;
}}

QLabel#DialogTitle {{
    color: {p.text_primary};
    font-size: 18px;
    font-weight: 700;
}}

QLabel#DialogMessage {{
    color: {p.text_secondary};
    font-size: 13px;
    font-weight: 400;
}}

QToolTip {{
    background-color: {p.elevated};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_SM}px;
    padding: 4px 8px;
    font-size: 12px;
}}
"""


build_dark_stylesheet = build_light_stylesheet
