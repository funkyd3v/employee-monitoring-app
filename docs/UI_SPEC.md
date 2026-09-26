# UI/UX Specification

## Design principles

Premium, minimal, calm, dark-mode-first, low cognitive load, clear state
communication at all times, no unnecessary screens, no fullscreen, no
maximize/expand control, no animations that don't serve a functional
purpose.

## Window behavior

- Fixed, non-resizable window (suggested: 900×600, adjustable during
  implementation — a compact card-like footprint reads as more "premium"
  than a large resizable pane).
- Custom frameless title bar: app icon, drag region, minimize + close
  only — **no maximize/expand button anywhere in the UI**.
- Centered on open; optionally remembers last position.
- Close button minimizes to tray while monitoring is active rather than
  terminating the agent, preventing accidental loss of an active work
  session. An explicit Exit is available only from the tray menu, with a
  confirmation dialog if a session is running.

### Bringing the window back

The window spends most of its life hidden in the tray, so "show me the app"
must never end in a dead gesture. Three gestures, one outcome — the window
un-minimizes if needed, shows, and takes focus (`FramelessWindow.present()`):

| Gesture | Behavior |
|---|---|
| Single click on the tray icon | Opens the app (double click does the same, once) |
| `Open Dashboard` in the tray menu | Opens the app |
| Launching the app again (desktop shortcut, pinned icon) | The already-running instance opens its window; the second copy exits quietly |

Launching twice never produces a second window: only one instance may own the
data directory, so the losing copy hands a "show yourself" request to the
winner over a per-data-dir local named pipe and exits with code 0
(`app/infrastructure/system/activation.py`). Windows foreground rules are
worked around in the composition root (`main.py`), never in UI code.

## Login screen

```
┌─────────────────────────────────────────────────────┐
│                  Application Logo                    │
│                  Welcome back                        │
│         Sign in to continue to your workspace        │
│         ┌───────────────────────────────────┐        │
│         │ Email / Username                  │        │
│         └───────────────────────────────────┘        │
│         ┌───────────────────────────────────┐        │
│         │ Password                     👁    │        │
│         └───────────────────────────────────┘        │
│         [              Sign In              ]        │
│                  Connection status                   │
└─────────────────────────────────────────────────────┘
```

- Centered card, subtle elevation shadow, on a quiet dark background (no
  gradient noise).
- Floating labels, password-visibility toggle, inline validation, subtle
  shake animation on invalid submit.
- Loading state disables the button and shows a spinner during (simulated)
  authentication.
- No technical error strings exposed to the user (never show a raw
  exception).

## Main dashboard

**Top bar (navbar-style):**
```
Logo    Employee Monitoring              Team Name   [Username ▾]
```

Right-aligned Team Name, then a rounded, clickable username trigger with a
chevron dropdown:
```
┌────────────────────────┐
│ User Name               │
│ user@example.com        │
├────────────────────────┤
│ Account (future)        │
│ Logout                  │
└────────────────────────┘
```
Only Logout needs functional behavior in this phase.

**Center — before Check In:**
```
                Good Morning, User
              Ready to start your day?
                    00:00:00
                  [ Check In ]
```

**Center — after Check In (WORKING):**
```
                You're checked in
                    02:14:37
        [ Take a Break ]     [ Check Out ]
             ● Active    Last activity: now
```
"Take a Break" is styled as secondary/outline; "Check Out" is styled with
a subtle danger tint (not alarmingly red — this is a routine action, not
a destructive one). A slim status pill reflects real-time state: green
dot "Active" / amber dot "Idle" / gray dot "On Break", with a soft
(non-distracting) pulse.

**Center — BREAK:**
```
                   On Break
                    00:18:21
                  [ Resume ]
```

**Center — COMPLETED:**
```
               Workday Complete
                    07:42:18
                 Total Work Time
              Checked out at 18:04
```
A small breakdown row beneath the total (Active / Idle / Break minutes)
gives the employee transparency into how their time was classified. A
"Check In" button reappears below for a new session.

## Interaction & motion

- Button-state transitions animate as a crossfade + slight scale
  (150–250ms ease) — never an abrupt swap.
- Timer uses a monospaced numeral style so digit width doesn't jitter as
  it counts.
- Logout and Exit while working use a consistently styled in-app modal,
  never a native OS msgbox. Check Out proceeds immediately without a
  confirmation dialog.
- Animations stay short and functional — this is an accessibility
  requirement (see below), not just a style choice.

## Visual design system

| Token | Dark (default) |
|---|---|
| Background | `#0F1115` |
| Surface | `#171A21` |
| Elevated Surface | `#1E222B` |
| Primary Text | `#F5F7FA` |
| Secondary Text | `#9AA3B2` |
| Border | `#2A303B` |
| Primary Accent | `#6366F1` (indigo — extend to a subtle `#6366F1 → #8B5CF6` gradient for primary CTAs only) |
| Success | `#22C55E` |
| Warning | `#F59E0B` |
| Danger | `#EF4444` |

- All colors centralized in a theme token system (`theme.py` / QSS
  variables) — never hard-coded inline in component files.
- Light theme is a first-class second palette (off-white `#F7F7F8`
  background family, same accent, shadows instead of hard borders),
  toggle-able and persisted in `app_state`/`settings`.
- Typography: Inter (or Segoe UI Variable as a native Windows fallback).
  Hierarchy: Title 20px/Semibold, Body 14px/Regular, Caption 12px/Medium.
  Monospaced only for the timer.
- Consistent, reusable components: buttons, cards, inputs, status
  indicators, dropdowns, tooltips, toasts, modals. Avoid excessive
  shadow/gradient/animation noise — restraint is what makes it read
  "premium" rather than "busy."

## System tray

Tray states reflect session status via icon/badge:

```
● Checked In      ◐ On Break      ○ Checked Out      ⚠ Attention Required
```

Menu:
```
Employee Monitoring
────────────────────
Open Dashboard
Current Status: Checked In
────────────────────
Check In
Take a Break
Resume
Check Out
────────────────────
Logout
Exit
```

The agent stays active in the tray if the window is closed while a
session is running (see Window behavior above). Clicking the icon itself
(single or double click) opens the app — the menu is for actions, the icon
is for bringing the app back. A click burst is coalesced so one physical
double click opens the window once.

## Accessibility

Keyboard navigation throughout; visible focus states; sufficient text
contrast (WCAG AA minimum); clearly labeled buttons (avoid icon-only
controls where meaning is ambiguous); tooltips on tray/status icons;
clear success/error feedback; short, purposeful animations only.
