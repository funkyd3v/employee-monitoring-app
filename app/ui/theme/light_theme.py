"""Light theme stylesheet.

Builds the application QSS from :data:`app.ui.theme.tokens.LIGHT`. Object
names and dynamic properties (``primary``, ``secondary``, ``danger``,
``ghost``) select button variants — component code sets them once at
construction, never inlines color values.

Keep this file declarative: no logic beyond interpolating tokens.
"""

from __future__ import annotations

from app.ui.theme.tokens import (
    FONT_FAMILY,
    FONT_MONO,
    RADIUS_LG,
    RADIUS_MD,
    RADIUS_SM,
    Palette,
)

_FAMILY = ", ".join(f'"{f}"' for f in FONT_FAMILY)
_MONO = ", ".join(f'"{f}"' for f in FONT_MONO)


def build_light_stylesheet(p: Palette) -> str:
    """Return the full light-theme QSS for palette ``p``."""
    return f"""
* {{
    font-family: {_FAMILY};
    font-size: 14px;
    color: {p.text_primary};
}}

QWidget {{
    background: {p.background};
}}

/* ── Title bar ─────────────────────────────────────────────────────────── */
QWidget#TitleBar {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
}}
QWidget#TitleBarTitle {{
    color: {p.text_secondary};
    font-size: 12px;
    font-weight: 500;
}}

/* ── Top bar (dashboard) ───────────────────────────────────────────────── */
QWidget#TopBar {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
}}
QLabel#TeamNameLabel {{
    color: {p.text_secondary};
    font-size: 12px;
    font-weight: 500;
}}

/* ── Panels ────────────────────────────────────────────────────────────── */
QLabel#PanelTitle {{ font-size: 20px; font-weight: 600; }}
QLabel#PanelSubtitle {{
    color: {p.text_secondary};
    font-size: 14px;
}}
QLabel#PanelCaption {{ color: {p.text_secondary}; font-size: 13px; }}

/* Login card (subtle elevation via layered surface + border) */
QWidget#LoginCard {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 16px;
}}
QWidget#LoginCenter {{ background: transparent; }}

QLabel#TimerLabel {{
    font-family: {_MONO};
    font-size: 52px;
    font-weight: 600;
    color: {p.text_primary};
    background: transparent;
}}
QLabel#TimerCaption {{ color: {p.text_secondary}; font-size: 11px; }}
QLabel#TimerRunsCaption {{ color: {p.text_secondary}; font-size: 13px; }}

/* Status pill surface text */
QLabel#PillText {{ color: {p.text_primary}; font-size: 12px; font-weight: 500; }}
QLabel#PillSecondary {{ color: {p.text_secondary}; font-size: 12px; }}

QLabel#LastActivity {{ color: {p.text_secondary}; font-size: 12px; }}

/* Status pill surface: soft tinted fill keyed off its state property */
QWidget#StatusPill {{
    border-radius: 15px;
    border: 1px solid {p.border};
    background: {p.surface};
}}
QWidget#StatusPill[pillState="ACTIVE"] {{
    background: {p.success_soft};
    border-color: rgba(22, 163, 74, 0.35);
}}
QWidget#StatusPill[pillState="IDLE"] {{
    background: {p.warning_soft};
    border-color: rgba(217, 119, 6, 0.35);
}}
QWidget#StatusPill[pillState="BREAK"] {{
    background: {p.elevated};
    border-color: {p.border};
}}
QWidget#StatusPill[pillState="OFF"] {{
    background: {p.elevated};
    border-color: {p.border};
}}

/* Safety notice under the pill row (monitoring transparency, UI_SPEC) */

/* Breakdown chips on the COMPLETED screen */
QWidget#Chip {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
}}
QLabel#ChipValue {{ font-size: 16px; font-weight: 600; }}
QLabel#ChipLabel {{ color: {p.text_secondary}; font-size: 11px; }}

/* ── Buttons ────────────────────────────────────────────────────────────── */
QPushButton {{
    font-size: 14px;
    font-weight: 500;
    border: none;
    border-radius: {RADIUS_MD}px;
    padding: 10px 22px;
    background: {p.accent};
    color: #FFFFFF;
}}
QPushButton:hover {{
    background: {p.accent_end};
}}
QPushButton:pressed {{
    background: {p.accent};
}}
QPushButton:disabled {{
    color: rgba(255, 255, 255, 0.70);
    background: rgba(99, 102, 241, 0.35);
}}
QPushButton:focus {{ outline: none; border: 1px solid {p.accent_end}; }}

QPushButton[role="secondary"] {{
    background: {p.elevated};
    color: {p.text_primary};
    border: 1px solid {p.border};
}}
QPushButton[role="secondary"]:hover {{
    background: {p.accent_soft};
    border-color: {p.accent};
}}
QPushButton[role="secondary"]:pressed {{ background: {p.elevated}; }}
QPushButton[role="secondary"]:disabled {{
    color: rgba(107, 114, 128, 0.60);
    border-color: {p.border};
    background: {p.elevated};
}}

QPushButton[role="danger"] {{
    background: {p.danger_soft};
    color: {p.danger};
    border: 1px solid {p.danger};
}}
QPushButton[role="danger"]:hover {{ background: rgba(220, 38, 38, 0.12); }}
QPushButton[role="danger"]:pressed {{ background: {p.danger_soft}; }}
QPushButton[role="danger"]:disabled {{
    color: rgba(220, 38, 38, 0.45);
    border-color: rgba(220, 38, 38, 0.35);
    background: transparent;
}}

QPushButton[role="ghost"] {{
    background: transparent;
    color: {p.text_secondary};
    padding: 6px 10px;
    border-radius: {RADIUS_SM}px;
}}
QPushButton[role="ghost"]:hover {{
    background: {p.accent_soft};
    color: {p.text_primary};
}}
QPushButton[role="ghost"]:pressed {{ background: transparent; }}
QPushButton[role="ghost"]:disabled {{ color: rgba(107, 114, 128, 0.45); }}

/* ── Title-bar window buttons ──────────────────────────────────────────── */
QPushButton#TitleMinButton, QPushButton#TitleCloseButton {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    color: {p.text_secondary};
    font-size: 14px;
    font-weight: 500;
    padding: 0;
}}
QPushButton#TitleMinButton:hover {{
    background: {p.elevated};
    color: {p.text_primary};
}}
QPushButton#TitleCloseButton:hover {{ background: {p.danger}; color: #FFFFFF; }}
QPushButton#TitleMinButton:pressed, QPushButton#TitleCloseButton:pressed {{
    background: transparent;
}}

/* ── Primary CTA (gradient per UI_SPEC, primary actions only) ──────────── */
QPushButton#PrimaryCta {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent}, stop:1 {p.accent_end}
    );
    border-radius: {RADIUS_LG}px;
    padding: 12px 34px;
}}
QPushButton#PrimaryCta:hover {{ color: #FFFFFF; }}
QPushButton#PrimaryCta:focus {{ border: 1px solid {p.accent}; }}

/* ── Text inputs ───────────────────────────────────────────────────────── */
QLineEdit {{
    background: {p.elevated};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    padding: 10px 12px;
    color: {p.text_primary};
    selection-background-color: {p.accent};
    selection-color: #FFFFFF;
}}
QLineEdit:focus {{ border: 1px solid {p.accent}; }}
QLineEdit:disabled {{ color: rgba(107, 114, 128, 0.60); background: {p.surface}; }}
QLineEdit[error="true"] {{ border: 1px solid {p.danger}; }}
QLineEdit[error="true"]:focus {{ border: 1px solid {p.danger}; }}

QLabel#FieldLabel {{ color: {p.text_secondary}; font-size: 11px; font-weight: 500; }}
QLabel#FieldError {{ color: {p.danger}; font-size: 11px; }}

QToolButton#PasswordToggle {{
    background: transparent;
    border: none;
    color: {p.text_secondary};
    font-size: 11px;
    font-weight: 500;
    padding: 0 10px;
}}
QToolButton#PasswordToggle:hover {{ color: {p.text_primary}; }}
QToolButton#PasswordToggle:pressed {{ color: {p.accent}; }}

/* Avatar / menu */
QPushButton#AvatarButton {{
    background: transparent;
    border: none;
    padding: 2px 4px;
    border-radius: {RADIUS_MD}px;
}}
QPushButton#AvatarButton:hover {{ background: {p.elevated}; }}
QPushButton#AvatarButton:pressed {{ background: {p.surface}; }}

QMenu {{
    background: {p.elevated};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 16px;
    border-radius: {RADIUS_SM}px;
    color: {p.text_primary};
}}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QMenu::item:disabled {{ color: rgba(107, 114, 128, 0.55); background: transparent; }}
QMenu::separator {{
    height: 1px;
    background: {p.border};
    margin: 6px 8px;
}}

/* ── Dialogs / modal ───────────────────────────────────────────────────── */
QWidget#DialogScrim {{ background: {p.scrim}; }}
QWidget#DialogCard {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_LG}px;
}}
QLabel#DialogTitle {{ font-size: 17px; font-weight: 600; }}
QLabel#DialogMessage {{
    color: {p.text_secondary};
    font-size: 13px;
}}

/* ── Tooltips (accessibility) ──────────────────────────────────────────── */
QToolTip {{
    background: {p.elevated};
    color: {p.text_primary};
    border: 1px solid {p.border};
    padding: 4px 8px;
    font-size: 12px;
}}
"""


# Backwards-compat alias — some callers may still import build_dark_stylesheet.
build_dark_stylesheet = build_light_stylesheet
