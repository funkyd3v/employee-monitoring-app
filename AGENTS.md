# AGENTS.md — Employee Monitoring Desktop Agent

This file is the entry point for any AI agent (or human) working in this
repository. Read this fully before making changes. It links out to the
detailed docs in `docs/`, which are the source of truth for their topics —
this file only orients you and states the rules that apply everywhere.

## What this project is

A Windows desktop agent, built in Python, that runs in the background and
records an employee's work-session activity: check-in/out, breaks, idle
vs. active periods, and admin-configured screenshots. It is a **client-side
agent only** for this phase — no backend, no cloud sync, no admin dashboard.
Those are future work, and the codebase must be architected so they can be
added later without redesigning the core app.

Full scope: see `docs/ARCHITECTURE.md` §Scope.

## Non-negotiable rules (apply to every change)

These are the rules most likely to be violated under time pressure. If a
change conflicts with one of these, stop and flag it rather than proceeding.

1. **Never capture actual input.** Keyboard/mouse monitoring records that
   activity occurred, never what was typed, clicked, or copied. No key
   values, no typed characters, no mouse coordinates, no clipboard content.
   This must be structurally enforced at the provider level — see
   `docs/SECURITY_PRIVACY.md`.
2. **The UI timer is never the source of truth.** All elapsed time is
   recomputed from persisted timestamps + accumulated break time, never
   accumulated in memory. See `docs/ENGINEERING_RULES.md` §Timer Correctness.
3. **Never delete local data on a failed or attempted upload.** Data is
   deleted only after the server confirms persistence. See
   `docs/ENGINEERING_RULES.md` §Data Deletion Rule.
4. **Screenshot scheduling must be drift-resistant.** Never
   `sleep(interval)` in a loop — compute `next_capture_at` from the
   scheduled start plus `n × interval`. See
   `docs/ENGINEERING_RULES.md` §Screenshot Scheduling.
5. **Everything that will eventually talk to a backend sits behind an
   interface** (`AuthProvider`, `SyncProvider`, `ConfigProvider`), with a
   local/dummy implementation today. UI and domain code never import a
   concrete provider directly. See `docs/ARCHITECTURE.md` §Backend-Readiness.
6. **Qt UI thread is never blocked.** Screenshot capture, DB access, and
   network I/O all run on worker threads; UI updates flow through Qt
   signals only, never direct widget access from a worker. See
   `docs/ENGINEERING_RULES.md` §Threading Discipline.
7. **Persisted state drives recovery, not memory.** The app must survive
   crash, forced termination, sleep/lock, and Windows restart without
   corrupting an active session. When recovery is ambiguous, fail safe and
   ask the user rather than guessing.
8. **No fullscreen, no maximize.** The window is fixed-size, frameless,
   with only minimize and close. This is a product requirement, not a
   default to "improve."

## Doc index

| File | Covers |
|---|---|
| `docs/ARCHITECTURE.md` | Scope, layered architecture, backend-readiness strategy |
| `docs/TECH_STACK.md` | Language, libraries, and why each was chosen |
| `docs/PROJECT_STRUCTURE.md` | Directory layout and where new code belongs |
| `docs/STATE_MACHINE.md` | Application session state machine and transition rules |
| `docs/ENGINEERING_RULES.md` | The detailed, non-negotiable engineering rules (§8 of the plan) |
| `docs/DATA_MODEL.md` | SQLite schema, storage layout, file naming |
| `docs/UI_SPEC.md` | Window behavior, screens, design tokens, tray |
| `docs/SECURITY_PRIVACY.md` | Privacy boundary, credential handling, security checklist |
| `docs/TESTING_AND_DOD.md` | Test strategy, acceptance criteria, known risks |

## Working conventions

- **Language:** Python 3.12+, typed (mypy or pyright clean), linted with Ruff.
- **Layering:** UI → Services → Providers/Repositories → SQLite/Filesystem.
  Never skip a layer (e.g. UI must not touch SQLAlchemy directly; a
  service must not touch SQLite directly — it goes through a repository).
- **Dependency injection:** wire concrete implementations in
  `app/core/container.py`, not inline in business logic.
- **Tests:** every new unit of logic in `domain/` or `services/` needs a
  corresponding unit test. New workers need a failure-mode test (crash,
  restart, offline). See `docs/TESTING_AND_DOD.md`.
- **Logging:** use the existing `logging` setup; never log credentials,
  tokens, screenshot contents, or any raw input value.
- **Out of scope for this phase:** backend API, cloud storage, admin
  dashboard, team management, billing, mobile, macOS/Linux. Don't build
  these — but don't make choices that would require rewriting the client
  to support them later either.

## When in doubt

Prefer the interpretation that keeps the client fully functional with the
backend completely unavailable — that is the default operating mode of
this application, not a degraded fallback.
