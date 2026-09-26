# Core Engineering Rules (Non-Negotiable)

These are the rules most likely to be silently violated under time
pressure, so they're stated explicitly rather than left implicit in the
architecture. Any change that touches these areas should be checked
against this doc before merging.

## Privacy boundary

The activity engine detects **that** input occurred, never **what** was
input.

- ✅ Allowed events: `INPUT_ACTIVITY_DETECTED`, `IDLE_STARTED`,
  `IDLE_CONTINUED`, `ACTIVE_RESUMED`.
- ❌ Never store: keystroke values, typed characters, passwords, mouse
  coordinates, clipboard contents, message content.

This boundary must be enforced **at the provider level**
(`windows_activity_provider.py`) so it is structurally impossible for a
higher layer to accidentally log raw input. The provider should never
even hold a keystroke value in memory beyond the current event tick.

## Timer correctness

Never treat the UI timer as the source of truth.

```
❌  timer += 1   # per tick, accumulated in memory

✅  elapsed = current_time - session_start - accumulated_break_time
```

The displayed timer is a pure visual projection of persisted session
state, recomputed from stored timestamps — never a counter that can
drift, freeze, or desync after a system sleep, app freeze, or restart.

## Time & clock handling

- Persisted timestamps (DB rows, events): UTC, timezone-aware datetimes.
- Elapsed-duration calculations (session length, idle duration):
  monotonic clock, immune to wall-clock adjustments (NTP sync, DST,
  manual clock changes).
- UI display: convert UTC → local Windows timezone at render time only.

## Screenshot scheduling (drift-resistant)

Do **not** schedule with `sleep(interval); capture()` in a loop — this
accumulates drift under system load.

```
next_capture_at = scheduled_start + (n × interval)
```

Compare against wall-clock time each tick and capture when
reached/passed, so captures land close to `10:01:00, 10:02:00, 10:03:00…`
rather than drifting to `10:01:00, 10:02:07, 10:03:14…`. A small
tolerance (±1–2s) is acceptable given Windows scheduler granularity.

## Atomic screenshot pipeline

```
Capture → apply idle border (if applicable) → compress/encode
   → write to TEMP file → validate → atomically move to pending/
   → create SQLite metadata record → create sync_queue record
```

Writing atomically (temp file + move, not a direct write to the final
path) ensures a crash mid-capture never leaves a corrupt file that's
already referenced by a DB row, or a DB row pointing at nothing. A
recovery pass on startup scans for orphaned files (no matching DB record)
and orphaned records (no matching file) and reconciles or discards them.

## Data deletion rule

Never delete local data merely because an upload was **attempted**.

```
Local data → Upload → Server confirms persistence → Mark synchronized → Delete local file/record
```

On failure: keep the data, increment `attempt_count`, retry per the
backoff policy below. **This is the single most important rule for
preventing silent data loss.**

## Retry strategy (exponential backoff)

| Attempt | Delay |
|---|---|
| 1 | immediate |
| 2 | 30s |
| 3 | 2 min |
| 4 | 5 min |
| 5 | 15 min (cap) |

Never hammer a backend that's down; cap the maximum interval.

## Connectivity model

Network-interface-up ≠ backend-reachable. Track distinct states:

`ONLINE | OFFLINE | BACKEND_UNAVAILABLE | AUTHENTICATION_REQUIRED | SYNCING`

The sync service determines real API reachability (not just "is there a
network adapter") once a real backend exists; today the dummy provider
simulates all of these states for testability.

## Sleep / lock / resume handling

Explicit rules for Windows workstation events (`WORKSTATION_LOCKED` /
`UNLOCKED`, `SYSTEM_SUSPEND` / `RESUME`):

- Never silently classify system sleep as active work time.
- Recalculate activity state on resume; don't generate false activity
  events from the resume action itself.
- Preserve all timestamps; keep the timeline consistent rather than
  inserting a gap-filling guess.
- The exact business rule (e.g. "sleep during WORKING pauses the
  session" vs. "sleep counts as idle") is a product policy decision —
  implement as a single configurable rule, not hardcoded, since this will
  likely need tuning once real usage data exists.

## Threading discipline

Workers (`ActivityWorker`, `ScreenshotWorker`, `SyncWorker`,
`CleanupWorker`) run off the Qt main thread and never touch UI widgets
directly — all UI updates flow through Qt signals. Each worker is wrapped
in supervisor logic: an uncaught exception is logged and the specific
worker restarts with backoff, rather than crashing the whole app.

A one-shot worker (`UiController`'s login worker) owns its own lifetime: it
deletes itself *in its own thread*, then the thread quits and is deleted.
Never leave a `QObject` whose thread affinity is a finished thread to be
destroyed from another thread, and never `deleteLater()` a short-lived
object whose event loop may never run again — the queued delete event
outlives the object and takes the next event loop down with it.

## Single instance & window activation

Only one process may own the data directory, but a second launch must never
be a silent no-op: the copy that loses the single-instance guard sends a
"show yourself" request over a per-data-dir local named pipe to the winner
and exits with code 0. A refused request is an error worth logging, never a
crash and never a second window.

Window presentation is owned by the UI (`UiController.show_main_window`);
OS-level foreground activation is wired in the composition root, because UI
code never talks to the OS directly.

## Key engineering decisions (summary)

- Monitoring logic lives in `services/`/`infrastructure/` providers,
  never inside UI code.
- The UI timer is a projection of persisted state, never the source of
  truth.
- No actual keyboard input is ever stored — activity events only.
- Data is deleted only after confirmed sync, never on upload attempt.
- The application is not coupled directly to a future API — everything
  goes through interfaces.
- The Qt UI thread is never blocked by screenshots, DB, or network I/O.
- Persisted state (not memory) drives crash/restart recovery.
- A second launch opens the running instance's window; it never opens a
  second window and never fails silently.
- UTC for persisted timestamps; monotonic clock for elapsed durations.
- Auth, sync, screenshot capture, activity detection, and UI are
  separate, independently testable modules.
- Screenshots are treated as sensitive local data and protected
  accordingly (filesystem permissions, no logging of contents).
- Screenshot scheduling is deterministic and drift-resistant, not
  `sleep()`-based.
- The desktop app remains fully usable with the backend completely
  unavailable — that's the default operating mode until further notice.
