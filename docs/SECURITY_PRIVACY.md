# Security & Privacy

This app monitors employee activity, which makes privacy discipline a
correctness requirement, not a nice-to-have. See
`docs/ENGINEERING_RULES.md` §Privacy Boundary for the enforcement rule.

## Security & privacy checklist

- [ ] No plaintext passwords anywhere (dummy auth doesn't need to store
      one long-term either).
- [ ] No actual keystroke values, mouse coordinates, or clipboard
      contents ever captured or logged.
- [ ] Authentication/session tokens stored only via `keyring` (Windows
      Credential Manager) — never in SQLite or config files.
- [ ] Screenshot storage directory restricted to the current user via
      Windows filesystem permissions.
- [ ] Logs never contain credentials, tokens, or screenshot contents.
- [ ] Failed uploads never cause data loss (see data deletion rule).
- [ ] Successful synchronization is confirmed before any local deletion.
- [ ] The application clearly communicates monitoring state to the
      employee at all times — no hidden background monitoring, no
      ambiguity about whether they're being tracked right now (dashboard
      status pill, tray icon).
- [ ] Uninstaller behavior around retained local data is explicit and
      documented.

## Logging & error handling

Levels: `DEBUG, INFO, WARNING, ERROR, CRITICAL`, via `RotatingFileHandler`
(e.g. 5MB × 5 backups) at `%LOCALAPPDATA%\EmployeeMonitoring\logs\agent.log`.

```
INFO  Application started
INFO  User authenticated
INFO  Work session started
INFO  Activity state changed: ACTIVE → IDLE
INFO  Screenshot captured
WARNING Backend unavailable — retry scheduled
ERROR Screenshot capture failed
```

**Never logged:** passwords, tokens, screenshot contents, typed
characters, unnecessary PII.

## Failure isolation

No subsystem failure should crash the app.

| Failure | Response |
|---|---|
| Screenshot capture fails | Log error → retry per policy → activity monitoring continues uninterrupted |
| Database error | Log critical → block destructive state changes → notify user if necessary |
| Network failure | Keep data locally → continue normal monitoring → retry sync later |

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Antivirus/SmartScreen flags the PyInstaller executable | Code-sign with an Authenticode certificate before any real distribution; consider Microsoft reputation submission |
| Qt event loop conflicts with global input hooks | Run `pynput` listeners on dedicated threads; marshal all UI updates via Qt signals only |
| Elevated/admin windows not triggering `pynput` hooks | Win32 `GetLastInputInfo` as an authoritative, hook-independent fallback |
| High-frequency screenshots inflating local disk usage pre-backend | JPEG compression, configurable local retention cap with a visible warning |
| Timer/session drift after sleep, freeze, or crash | Never trust the in-memory timer; always recompute from persisted timestamps + monotonic clock |
| Screenshot file/DB record desync on crash | Atomic write pipeline + startup orphan-recovery pass |
| Future backend contract mismatch | Define JSON payload schemas now; treat them as the pseudo-contract the dummy layer already honors |
| Silent data loss on failed upload | Strict "confirm-then-delete" rule, never "attempt-then-delete" |
