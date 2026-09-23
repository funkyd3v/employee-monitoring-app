# Employee Monitoring — Desktop Agent

A Windows desktop agent, built in Python, that records an employee's
work-session activity: check-in/out, breaks, idle vs. active periods, and
admin-configured screenshots. **Client-side agent only** for this phase —
no backend, no cloud sync, no admin dashboard.

> For an agent (AI or human) working in this repository, start at
> `AGENTS.md`; the docs in `docs/` are the source of truth.

## Status

Phase 1 (architecture & project setup) — scaffold, config system, logging
with secret redaction, lifecycle management, DI container skeleton. See
`docs/TESTING_AND_DOD.md` § Indicative timeline for the full plan.

## Getting started (development)

Requirements: Python 3.12+ and `uv` (or `pip`).

```bash
uv sync --group dev   # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/mypy app
.venv/bin/pytest
.venv/bin/employee-monitoring-agent --version
```

Windows-only runtime dependencies (`pynput`, `pywin32`) are gated behind
`sys_platform == "win32"` markers so development and non-UI tests run on
any OS; they install automatically when building on Windows.

## Architecture

See `docs/ARCHITECTURE.md`. Key invariants (non-negotiable):

- Monitoring is **event-level only** — never keystrokes, coordinates, or
  clipboard content (enforced at the provider boundary).
- The UI timer is never the source of truth; elapsed time is always
  recomputed from persisted timestamps + accumulated breaks.
- Local data is deleted only after a server confirms persistence.
- Everything that will talk to a future backend sits behind an interface
  (`AuthProvider`, `SyncProvider`, `ConfigProvider`).
- The Qt UI thread is never blocked; workers update via Qt signals only.

## Security & privacy

`docs/SECURITY_PRIVACY.md` is the full checklist. Highlights:

- All tokens/credentials go through `keyring` (Windows Credential Manager).
- The logging layer applies **structural secret redaction** and never logs
  credentials, tokens, or screenshot contents.
- Screenshot storage is per-user and permission-restricted on Windows.