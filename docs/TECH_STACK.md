# Technology Stack

| Area | Technology | Why |
|---|---|---|
| Language | Python 3.12+ | Required |
| Desktop UI | PySide6 (Qt for Python) | Only framework capable of a premium, frameless, custom-themed UI at production quality |
| Local database | SQLite | Embeddable, zero-install, offline-first |
| ORM / data access | SQLAlchemy | Mature, testable repository pattern |
| Screenshot capture | `mss` + Pillow | Fast capture; Pillow for idle-border compositing/compression |
| Windows activity/idle detection | `pynput` (hooks) + Win32 `GetLastInputInfo` via `ctypes` (authoritative fallback) | Hooks alone miss elevated-window input; Win32 API is OS ground-truth |
| Active window detection | `pywin32` (`win32gui`, `win32process`) | Window/process metadata for screenshot context |
| HTTP abstraction | `httpx` | Modern, async-capable, clean interface for the future API client |
| Configuration | `pydantic-settings` | Typed, validated, cleanly split into local vs. future server-controlled settings |
| Secure credential storage | `keyring` (Windows Credential Manager) | Tokens/sessions never stored in plaintext |
| Background scheduling | APScheduler + explicit drift-correction logic | Interval jobs that don't accumulate drift — see `docs/ENGINEERING_RULES.md` |
| Logging | `logging` + `RotatingFileHandler` | Local diagnostics, size-capped, never logs sensitive data |
| Packaging | PyInstaller (`--onedir`, windowed) | No Python/SQLite install required; `--onedir` avoids slow re-extraction and reduces AV false-positive churn vs `--onefile` |
| Installer | Inno Setup | Proper Windows installer, autostart registry entry, clean uninstall |
| Code signing | Authenticode certificate | Required before real rollout — unsigned PyInstaller executables are routinely flagged by SmartScreen/AV, which is especially damaging for monitoring software |
| Testing | `pytest`, `pytest-qt` | Unit, integration, and UI testing |
| Static analysis / types | Ruff, mypy or pyright | Code quality gate |
| Dependency management | `uv` or Poetry | Reproducible builds |

## Rules of thumb when adding a dependency

- Prefer libraries already in this table over introducing a new one for
  overlapping functionality.
- Anything that talks to the network goes through `httpx`, behind the
  `SyncProvider` / `AuthProvider` interfaces — never called directly from
  UI or domain code.
- Anything security-sensitive (tokens, session secrets) goes through
  `keyring`, never into SQLite or plain config files.
