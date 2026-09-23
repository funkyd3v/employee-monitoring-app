# Application State Machine

```
LOGGED_OUT
    │ login success
    ▼
READY (CHECKED_OUT)  ── dashboard visible, "Check In" shown
    │ Check In
    ▼
WORKING (ACTIVE|IDLE sub-state)
    │                        │
    │ Take a Break           │ Check Out
    ▼                        ▼
BREAK                    COMPLETED (final time shown,
    │ Resume                 "Check In" reappears for new session)
    ▼
WORKING
```

## Transition rules

Invalid transitions are **explicitly rejected**, not just avoided by UI
flow (i.e. the state machine itself must reject them, not only the buttons
that would normally trigger them):

- Check Out before Check In → rejected.
- Take Break while already on break → rejected.
- Resume while already working → rejected.
- Check In immediately after Checkout → policy-configurable; default is a
  new, distinct work session.

## Sub-states and side effects

- `WORKING` has an internal **active/idle** sub-state driven by the
  activity engine. This never changes which buttons are shown — it only
  affects what's written to the activity timeline and whether the idle
  screenshot border applies.
- `BREAK`: no screenshots, no active/idle tracking; break duration
  accumulates separately from work duration.
- A working-hours policy (admin-configured, future) can force
  `WORKING → COMPLETED` with a UI notification. Not implemented in this
  phase, but the state machine should not preclude it.

## Implementation notes

- The state machine lives in `app/domain/sessions/`.
- State lives in persisted session rows, not in memory — see
  `docs/ENGINEERING_RULES.md` §Timer Correctness for why.
- Any new transition must be added to the explicit rejection list above if
  it is invalid, not left to be "prevented" by the UI alone.
