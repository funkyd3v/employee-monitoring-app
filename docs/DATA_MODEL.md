# Data Model (SQLite)

| Table | Key Columns | Purpose |
|---|---|---|
| `users` | `id, external_user_id, email, display_name, team_name, created_at, updated_at` | Local cache of authenticated user (no password stored) |
| `work_sessions` | `id, user_id, started_at, ended_at, status, total_work_seconds, created_at, updated_at` | One row per Check-In → Check-Out cycle |
| `breaks` | `id, session_id, started_at, ended_at, duration_seconds, created_at` | Break intervals within a session |
| `activity_periods` | `id, session_id, started_at, ended_at, state (ACTIVE/IDLE), duration_seconds, created_at` | Timeline reconstruction |
| `screenshots` | `id, session_id, captured_at, activity_state, file_path, file_size, checksum, sync_status, attempt_count, last_attempt_at, synced_at, created_at` | Screenshot metadata; checksum enables orphan/corruption detection |
| `sync_queue` | `id, entity_type, entity_id, operation, status, attempt_count, last_attempt_at, last_error, created_at` | Outbox pattern for future API sync |
| `app_state` | `key, value, updated_at` | Theme, window position, misc runtime flags |
| `settings` | `key, value` | Local settings today; will also hold cached server-pushed policy later (screenshot interval, idle threshold, working hours) |

## Screenshot file naming

Deterministic, collision-resistant, never timestamp-only:

```
{session_id}_{timestamp}_{uuid}.jpg
e.g. sess_20260923_001_20260923T103000_8f2a.jpg
```

## Storage layout

Per-user app-data directory, **not** the install directory:

```
%LOCALAPPDATA%\EmployeeMonitoring\
├── database\agent.db
├── screenshots\
│   ├── pending\
│   └── processing\
├── logs\agent.log
├── cache\
└── config\
```

## Access rules

- All access goes through `app/infrastructure/database/repositories.py`
  (SQLAlchemy) — never raw SQL scattered through services or UI code.
- `screenshots` and `sync_queue` implement the outbox pattern described
  in `docs/ENGINEERING_RULES.md` §Atomic Screenshot Pipeline and §Data
  Deletion Rule — do not bypass it with a direct delete.
- Migrations live in `app/infrastructure/database/migrations.py`; schema
  changes should not be applied ad hoc.
