# Architecture

## Scope

### In scope (this phase)
- Authentication: login UI, dummy/local auth, secure persistent session,
  logout, session restoration — architected for a real backend later.
- Work sessions: Check In, live timer, Pause/Resume (Break), Check Out,
  daily total, session persistence, recovery of unfinished sessions.
- Activity monitoring: keyboard/mouse activity detection at the event
  level only, active/idle calculation, configurable idle threshold,
  activity timeline, break-aware and checkout-aware monitoring.
- Screenshots: full-screen capture, admin-defined interval (min. 1 min),
  drift-resistant scheduled timing, active/idle classification at capture
  time, red border on idle captures, local storage, queue-based sync
  architecture.
- Offline operation: SQLite local DB, local screenshot queue,
  pending-sync state, retry with backoff, recovery after connectivity
  loss, background cleanup after confirmed sync.
- Windows integration: background agent, tray/status, start-on-login,
  graceful shutdown, logging, error recovery, packaging, installer.

### Explicitly out of scope (this phase)
Backend API, backend database, cloud storage, server-side screenshot
processing, real production authentication, admin web dashboard,
team/employee management, cloud reporting/analytics, production upload
implementation, billing, mobile apps, macOS/Linux support.

The app must still expose clean interfaces so all of the above can be
added later without touching the core client.

## Layered architecture

```
Windows Desktop
      │
PySide6 UI Layer            (Login / Dashboard / Tray / Status)
      │  Qt signals only — no direct widget access from workers
Application Layer           (Auth, Session, Activity, Screenshot,
                              Sync, Cleanup, Configuration services)
      │
   ┌──┴───────────────┬──────────────────┐
Activity Provider  Screenshot Provider  Sync Provider
(Windows)          (mss/PIL)            (Dummy → API)
   └──────────────────┴──────────────────┘
                    │
          Repository Layer (SQLAlchemy)
                    │
           SQLite + Filesystem
                    │
             Future Backend API
```

**Core principle — "local-first, API-ready":** every feature that will
eventually talk to a server sits behind an interface (`AuthProvider`,
`SyncProvider`, `ConfigProvider`) with a dummy/local implementation today.
Swapping to a real backend later is an infrastructure task, not a
redesign — the UI, state machine, activity engine, screenshot engine, and
SQLite repositories must not change as a result.

## Backend-readiness strategy

```
AuthProvider
    ├── LocalDummyAuthProvider   (today)
    └── ApiAuthProvider          (future)

SyncProvider
    ├── DummySyncProvider        (today)
    └── ApiSyncProvider          (future)

ConfigProvider
    ├── LocalConfigStore         (today)
    └── RemoteConfigProvider     (future)
```

- The UI and domain layer never know which implementation is active — it
  is swapped via a single `mode: "local" | "api"` flag.
- **Contract-first:** JSON payload shapes for activity batches, screenshot
  metadata, and session events are defined now, matching what a realistic
  REST API would expect, so the eventual backend contract is "implement
  what the client already sends," not a renegotiation.
- Illustrative future endpoints (owned by the backend team, not built now):
  `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`,
  `GET /me`, `GET /team`, `POST /work-sessions`, `PATCH /work-sessions/{id}`,
  `POST /activities/batch`, `POST /screenshots`, `POST /sync/batch`,
  `GET /agent/config`.
- Dependency inversion is enforced throughout:
  `ScreenshotService → ScreenshotRepository` (never SQLite directly),
  `SyncService → SyncProvider` interface (never HTTP directly).

## Application lifecycle

**Startup:**
```
Load config → init logging → init database → recover incomplete local state
  → init services → restore authentication → restore work session if valid
  → start background workers → show dashboard/tray
```

**Shutdown:**
```
Stop screenshot scheduler → stop activity worker → flush pending local state
  → stop sync/cleanup workers → close database → exit
```

**Crash recovery:** the app must survive Windows restart, power loss,
crash, or forced termination without corrupting a work session. If
recovery is ambiguous (e.g. unclear how long the app was closed), fail
safe — require explicit user confirmation rather than silently inventing
working time.

## Performance & resource management

- UI remains responsive during monitoring; screenshot/DB/network work
  never blocks the Qt main thread.
- Database operations are short and indexed; workers idle (no
  busy-polling) when there's no work to do.
- Logs, screenshots, database, and queue sizes are actively monitored and
  rotated/capped.
- Low disk space handling: detect → stop creating new screenshots if
  necessary → preserve activity/session metadata → show a clear in-app
  warning → log the event. Retention thresholds are configurable.
