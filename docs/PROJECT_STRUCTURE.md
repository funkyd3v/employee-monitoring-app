# Project Structure

```
employee-monitoring-agent/
│
├── app/
│   ├── main.py
│   │
│   ├── config/
│   │   ├── settings.py              # local + server-controlled split
│   │   └── constants.py
│   │
│   ├── core/
│   │   ├── container.py             # dependency injection wiring
│   │   ├── events.py
│   │   ├── exceptions.py
│   │   └── lifecycle.py             # startup/shutdown sequencing
│   │
│   ├── domain/
│   │   ├── auth/
│   │   ├── activity/
│   │   ├── sessions/                # state machine lives here
│   │   ├── screenshots/
│   │   └── sync/
│   │
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── session_service.py
│   │   ├── activity_service.py
│   │   ├── screenshot_service.py
│   │   ├── sync_service.py
│   │   └── cleanup_service.py
│   │
│   ├── infrastructure/
│   │   ├── database/
│   │   │   ├── models.py
│   │   │   ├── repositories.py
│   │   │   └── migrations.py
│   │   ├── activity/
│   │   │   └── windows_activity_provider.py
│   │   ├── screenshots/
│   │   │   └── screenshot_provider.py
│   │   ├── network/
│   │   │   ├── api_client.py        # future backend client
│   │   │   └── sync_adapter.py
│   │   └── security/
│   │       └── credential_store.py  # keyring wrapper
│   │
│   ├── ui/
│   │   ├── windows/
│   │   │   ├── login_window.py
│   │   │   ├── dashboard_window.py
│   │   │   └── components/          # timer widget, status pill, modal
│   │   ├── tray/
│   │   │   └── tray_manager.py
│   │   ├── theme/
│   │   │   ├── dark_theme.py
│   │   │   └── light_theme.py
│   │   └── dialogs/
│   │
│   └── workers/
│       ├── activity_worker.py
│       ├── screenshot_worker.py
│       ├── sync_worker.py
│       └── cleanup_worker.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── ui/
│
├── assets/
│   ├── icons/
│   └── branding/
│
├── scripts/
├── installer/
│   ├── build.spec                   # PyInstaller spec
│   └── installer.iss                # Inno Setup script
├── docs/
│
├── .env.example
├── pyproject.toml
├── README.md
└── LICENSE
```

## Where new code goes

- **Pure business rules with no I/O** (state transitions, elapsed-time
  math, retry backoff calculation) → `app/domain/`.
- **Orchestration that calls providers/repositories** → `app/services/`.
- **Anything that touches Windows APIs, the filesystem, the network, or
  the OS keychain** → `app/infrastructure/`, behind an interface defined
  in `app/domain/` or `app/services/`.
- **Anything that touches a Qt widget** → `app/ui/`. UI code never
  imports `infrastructure/` directly; it goes through a service and Qt
  signals.
- **Long-running background work** → `app/workers/`, each wrapped in
  supervisor logic (see `docs/ENGINEERING_RULES.md` §Threading Discipline).
- **Wiring concrete implementations to interfaces** → `app/core/container.py`
  only. Business logic should never `import` a concrete provider class
  directly (e.g. never `from infrastructure.network.api_client import
  ApiAuthProvider` inside `services/`).
