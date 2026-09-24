# Employee Monitoring — Desktop Agent

A Windows desktop agent, built in Python, that records an employee's
work-session activity: check-in/out, breaks, idle vs. active periods, and
admin-configured screenshots. **Client-side agent only** for this phase —
no backend, no cloud sync, no admin dashboard.

> For an agent (AI or human) working in this repository, start at
> `AGENTS.md`; the docs in `docs/` are the source of truth.

## Status

Phases 1–10 complete (Phase 10: packaging & QA). See `docs/TESTING_AND_DOD.md` § Indicative timeline for the full plan.

## Getting started (development)

Requirements: Python 3.12+ and `uv` (or `pip`).

```bash
uv sync --group dev
.venv/bin/ruff check app tests        # lint (ruff)
.venv/bin/ruff format --check app tests
.venv/bin/mypy app                     # types (strict)
.venv/bin/pytest -q
.venv/bin/employee-monitoring-agent --version
```

## Building a fully self-contained Windows bundle (Phase 10)

No Python, SQLite, or external runtime is required on the target machine — the bundle embeds the interpreter, stdlib (`_sqlite3`), PySide6/Qt6, SQLAlchemy, Pillow, mss, keyring, APScheduler, httpx and the Windows backends (`pywin32`, `pynput` when built on Windows).

**On Windows (required for the real artefact; Linux can only do a dry smoke):**

```powershell
uv sync --group dev
uv run pyinstaller installer/build.spec --noconfirm --clean
# -> dist/EmployeeMonitoring/EmployeeMonitoring.exe  (onedir, windowed, no console)

# Optional: build the per-user installer (Inno Setup 6 required, iscc on PATH)
iscc installer/installer.iss
# -> installer/dist/EmployeeMonitoring-Setup-0.1.0.exe

# Install and smoke-test
installer\dist\EmployeeMonitoring-Setup-0.1.0.exe  /SILENT
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1
```

Details: `installer/build.spec` (`--onedir` per `docs/TECH_STACK.md:17`, `UPX=False` to avoid AV churn per `docs/SECURITY_PRIVACY.md:59`) and `installer/installer.iss` (per-user `PrivilegesRequired=lowest`, `HKCU\...\Run` autostart `--minimized`, Start Menu icon, close-apps on upgrade).

**Code signing (Authenticode):** unsigned dev builds show a SmartScreen warning — expected. For a signed build set `SignTool` in `installer.iss` and pass `/Ssigntool="signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f cert.pfx /p pass $f"` on the `iscc` command line (see `installer/installer.iss` header).

**Uninstall & data retention:** the installer never deletes `%LOCALAPPDATA%\EmployeeMonitoring` (DB `agent.db`, `screenshots/{pending,processing}`, `logs/agent.log`). Apps & Features removes only `{app}`; delete the data folder manually for a full wipe — the installer tells the user where it is (`docs/DATA_MODEL.md` layout).

**Placeholder icon:** `assets/icons/app.ico` (gradient `6366F1→8B5CF6`, `docs/UI_SPEC.md:130`) is used for the exe and installer until replaced with a final brand asset.

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