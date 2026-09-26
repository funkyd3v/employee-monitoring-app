# Testing Strategy & Definition of Done

## Test categories

| Category | Covers |
|---|---|
| Unit | Session/break/idle calculations, state transitions, screenshot scheduling math, retry backoff calculation, sync queue logic, cleanup rules, time/clock calculations, recovery logic |
| Integration | SQLite repositories, screenshot storage, queue persistence, restart recovery, dummy auth, dummy sync |
| UI (`pytest-qt`) | Login, logout, Check In/Break/Resume/Check Out flow, timer display accuracy, user menu, tray interaction |
| Failure tests | Network unavailable, database unavailable, screenshot capture failure, app crash, Windows restart, system sleep/resume, corrupted screenshot file, duplicate sync attempt |

Any new piece of `domain/` or `services/` logic needs a corresponding
unit test. Any new worker needs at least one failure-mode test.

## Acceptance criteria / Definition of Done

- [ ] User can log in with dummy credentials; session persists across app
      restart; logout works.
- [ ] Dashboard shows Team Name and user menu correctly.
- [ ] Check In starts a session with an accurate, drift-free timer.
- [ ] Break/Resume transitions correctly; break time excluded from work
      time.
- [ ] Check Out ends the session; total time remains visible in the
      summary state.
- [ ] Keyboard/mouse activity is detected; idle triggers after the
      configured threshold; no actual input values are ever stored.
- [ ] Screenshots capture at the configured interval (1-minute interval
      verified specifically), stop during Break and after Checkout.
- [ ] Idle-captured screenshots visibly show the red border; metadata
      correctly links state to image.
- [ ] App functions fully offline; all data queues locally; nothing is
      lost.
- [ ] App survives forced termination and Windows restart without
      corrupting the active session (recovery pass verified).
- [ ] Window cannot be resized or maximized; only minimize/close are
      available; close-while-working minimizes to tray.
- [ ] With the app in the tray, a single click on the tray icon, a double
      click on the tray icon, and a double click on the desktop shortcut
      all bring the window back (hidden *and* minimized states).
- [ ] Launching the app twice never yields two windows or two database
      writers; the second launch opens the first instance's window.
- [ ] Dark/light theme toggle applies instantly and persists.
- [ ] App installs and uninstalls cleanly via Windows "Apps & Features,"
      with documented behavior for local data retention on uninstall.

## Dummy development configuration

```
Dummy user:          employee@example.com / configurable dummy password
Screenshot interval: 1 minute (default)
Idle threshold:      5 minutes (default)
```

Keep this clearly separated from production code paths — it must never
ship as a real fallback.

## Indicative timeline (20–25 working days)

| Phase | Days | Deliverables |
|---|---|---|
| 1. Architecture & project setup | 1–2 | Repo scaffold, dependency mgmt, project structure, lint/type-check config, pytest setup, bootstrap, config system, logging |
| 2. Database & domain layer | 2–3 | SQLite schema, SQLAlchemy models, repositories, migrations, domain entities, state machine |
| 3. Authentication | 1–2 | Login UI, dummy auth provider, secure session persistence (keyring), logout, auth abstraction |
| 4. Premium UI/UX | 3–4 | Dark theme + design tokens, login screen, dashboard, all work-session states, user dropdown, status indicators, system tray |
| 5. Work-session engine | 2–3 | Check In/Break/Resume/Check Out, accurate elapsed-time calculation, persisted state, restart recovery |
| 6. Activity monitoring | 2–3 | Windows activity provider (hooks + Win32 fallback), active/idle engine, configurable threshold, persisted activity periods, session integration |
| 7. Screenshot engine | 2–3 | Capture pipeline, drift-resistant scheduler, idle red-border, atomic file writes, metadata storage |
| 8. Offline queue & recovery | 2 | Sync queue, dummy sync provider, retry system, cleanup worker, orphan-file/record recovery |
| 9. Windows integration & reliability | 1–2 | Startup behavior, tray lifecycle, sleep/resume handling, crash recovery, logging hardening |
| 10. Testing, packaging & QA | 3 | Unit/integratio/failure tests, PyInstaller build, Inno Setup installer, code signing, uninstaller verification, final QA pass |

Phases 4 and 5–7 can run partially in parallel across a small team.
