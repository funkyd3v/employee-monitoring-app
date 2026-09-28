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
Cloud storage, server-side screenshot processing, admin web dashboard,
team/employee management, cloud reporting/analytics, billing, mobile apps,
macOS/Linux support.

### Delivered since the client was written
- **Reference backend** (`~/projects/empolee-monitoring-backend`): Sanctum
  bearer auth, idempotent outbox upserts, screenshot upload with SHA-256
  verification, and a small single-employee dashboard. It is a *sample*
  implementation of `docs/API_CONTRACT.md`, not a production service.
- **API mode** (`EM_MODE=api`): `ApiAuthProvider` + `ApiSyncProvider` replace
  the local dummies. No service, repository, state machine or UI code
  changed — the swap is still a single flag, which is the point.

The app must still expose clean interfaces so the rest of the above can be
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
AuthProvider          WorkspaceProvider        ConfigProvider
    ├── LocalDummy          ├── Local              ├── LocalConfigProvider
    └── ApiAuthProvider     └── ApiWorkspaceProvider└── ApiConfigProvider

SyncProvider
    ├── DummySyncProvider
    └── ApiSyncProvider
```

- The UI and domain layer never know which implementation is active — it
  is swapped via a single `mode: "local" | "api"` flag.
- **Contract-first:** JSON payload shapes for activity batches, screenshot
  metadata, and session events are defined now, matching what a realistic
  REST API would expect, so the eventual backend contract is "implement
  what the client already sends," not a renegotiation.
- The wire format is pinned in `docs/API_CONTRACT.md`. Two decisions there
  are load-bearing and must survive any future backend:
  - the authenticated user always comes from the token, never the body (the
    payload's `user_id` is a *local* row id and is ignored);
  - one entity per request, because the outbox may only delete a local row
    after an unambiguous confirmation.
- Dependency inversion is enforced throughout:
  `ScreenshotService → ScreenshotRepository` (never SQLite directly),
  `SyncService → SyncProvider` interface (never HTTP directly).

### The contract is data, not code

The interface alone is not enough to make a backend pluggable. If every
provider hardcodes its own paths and field lists, adding an endpoint still
means editing provider code in four places — and the next release forgets one.

So the surface itself is declared once, in
`app/infrastructure/network/contract.py`: one `Endpoint` per operation, with
its path template, method, and the exact wire fields it carries. The providers
are then *generic drivers* — `ApiSyncProvider` contains no per-entity branch,
`ApiAuthProvider` no hardcoded `/auth/login`.

Two things follow, and both are tested:

- **A new entity is a line of data.** `Entity.body_fields` also acts as a
  filter, so a local bookkeeping column can never leak into a request.
- **A different backend is a file.** `EM_API_CONTRACT_PATH` overlays paths,
  field lists, multipart field names and user-envelope keys without touching
  Python. A malformed overlay is a startup error, because the alternative is
  an agent that looks configured and syncs to the wrong URLs.

The same seam is what new features use. `workspace` and `agent/config` were
integrated without editing a service, the UI or the domain: the sync tick
(which is already a worker thread talking to the backend) reads them, and a
Qt signal carries the one value the UI needs — so the threading rule holds
too. A future endpoint costs a record in the contract plus, at most, one call
in `SyncWorker._refresh_remote_settings`.

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
