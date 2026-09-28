# Agent API Contract (v1)

The reference implementation of this contract is the Laravel backend in
`~/projects/empolee-monitoring-backend`. This document exists so the client
side can be read without the server, and so a future backend that replaces
the sample one has an exact specification to implement.

The client that speaks it is `app/infrastructure/network/` in this repo:
`ApiAuthProvider` (auth), `ApiSyncProvider` (outbox drain),
`ApiWorkspaceProvider` (the label) and `ApiConfigProvider` (operator policy).
None of them hardcodes a path or a field name: every one reads them from
`app/infrastructure/network/contract.py`, and the request bodies are declared
there rather than assembled in code. See § Adapting the client without code
changes.

The reference server serves this same contract as a browsable reference at
`/api-docs`, generated from its own route table.

## Transport

| Property | Value |
|---|---|
| Base URL | `http://127.0.0.1:8000/api/v1` (`EM_API_BASE_URL`) |
| Auth | `Authorization: Bearer <token>` on everything except login/health |
| Accept | `application/json` |
| Encoding | `application/json`, except the screenshot binary (`multipart/form-data`) |
| Timestamps | ISO-8601 UTC with offset, e.g. `2026-09-28T09:00:00+00:00` |
| Error shape | Laravel's standard JSON: `{"message": "...", "errors": {...}}` |

## Two rules that shape everything

1. **Identity comes from the token, never the body.** Payloads carry a
   `user_id`, but that is the *agent's local* SQLite row id. The server
   ignores it and derives the user from the bearer token. A bug or a forgery
   in the body therefore cannot move data between employees.

2. **One entity per request.** The client deletes a local row only after an
   unambiguous confirmation. A bulk endpoint with a per-item result list would
   make "did this item land?" ambiguous, so there is no batch endpoint and no
   `POST /sync/batch` — the contract from `docs/ARCHITECTURE.md` is narrowed
   deliberately here.

## Authentication

### `POST /auth/login`

```json
{
  "email": "employee@example.com",
  "password": "secret",
  "device_name": "DESKTOP-ABC",     // optional
  "platform": "windows",             // optional
  "agent_version": "0.1.0"           // optional
}
```

`200`:

```json
{
  "token": "1|xxxxxxxxxxxxxxxxxxxxxxxx",
  "token_type": "Bearer",
  "expires_at": "2026-10-28T09:00:00+00:00",
  "user": {
    "id": 1,
    "external_user_id": "1",
    "email": "employee@example.com",
    "display_name": "Employee",
    "name": "Employee",
    "workspace_name": "Engineering"
  }
}
```

`422` for bad credentials — the same message for "no such user" and "wrong
password", so the endpoint cannot enumerate accounts. Rate limited to 5
attempts per minute per (email, IP).

How the client reads a login failure decides what the employee is told, so
the mapping is explicit rather than incidental:

| Response | Reported to the employee as |
|---|---|
| `401` / `403` / `422` | "Check your email and password" |
| `404` / `405` | "Could not reach the monitoring server" — the route is absent, which means the base URL is wrong, not the password |
| `429` / `5xx` / connect or timeout | "Could not reach the monitoring server" |
| `2xx` with a non-JSON body, or no `token`, or no `user` | "Could not reach the monitoring server" |

Only the first row is ever about the password. Everything else is a
deployment or connectivity problem, and telling the employee to retype a
correct password sends them to fix the one thing that is already right.

The client stores the token in the OS keyring and keeps it for the life of
the login. Re-logging in on the same device revokes the previous token.

### `POST /auth/refresh`

Bearer token required. Validates it and extends the expiry, returning the
**same** token. The client re-reads one long-lived credential from the
keyring on every start and never re-persists, so rotating the value here
would require a mid-boot write the client does not do. Expiry is the
revocation mechanism instead.

`401` when the token is unknown, expired or revoked. The client then wipes
the stored token and shows the login screen.

### `POST /auth/logout`

Bearer token required. Deletes the presenting token. `200 {"status":
"logged_out"}`. The client treats failure here as non-fatal — the employee
must always be able to log out.

### `GET /me`

Bearer token + the `sync` ability. Returns `{"user": {...}}` in the same
envelope as login. This is the client's connectivity probe: reachable *and*
authenticated, which is a different question from "is the port open".

### `GET /health`

No auth. `200 {"status": "ok", "time": "..."}`. Separates "backend is down"
from "our token is bad" — a 401 here would still mean a reachable server.

## Outbox writes

All four are `PUT /{entity}/{client_id}`, authenticated, ability `sync`, and
all are **upserts keyed on `(user_id, client_id)`**. Replaying any of them
converges on the same row.

`client_id` is the agent's local row id. It appears in the path *and* in the
body as `id`; a mismatch is a `422`, which turns a client/server contract
drift into a visible error instead of a silently mis-filed row.

`operation` (`CREATE` / `UPDATE`) is not sent — both converge on the same
state, which is exactly what makes a retried `CREATE` safe.

### `PUT /work-sessions/{client_id}`

```json
{
  "id": 12,
  "user_id": 1,
  "started_at": "2026-09-28T09:00:00+00:00",
  "ended_at": null,
  "status": "WORKING",
  "total_work_seconds": 0
}
```

`status` ∈ `WORKING | BREAK | COMPLETED`.

### `PUT /breaks/{client_id}`

```json
{
  "id": 5,
  "session_id": 12,
  "started_at": "2026-09-28T10:00:00+00:00",
  "ended_at": "2026-09-28T10:15:00+00:00",
  "duration_seconds": 900
}
```

### `PUT /activity-periods/{client_id}`

```json
{
  "id": 7,
  "session_id": 12,
  "started_at": "2026-09-28T09:00:00+00:00",
  "ended_at": null,
  "state": "ACTIVE",
  "duration_seconds": 0
}
```

`state` ∈ `ACTIVE | IDLE`. This is the *entire* activity vocabulary. The
agent records that input happened, never what was typed — no keystroke value,
mouse coordinate or clipboard content is collected by the client, so the
server has nothing of the sort to accept or store.

### `PUT /screenshots/{client_id}` (metadata only)

```json
{
  "id": 3,
  "session_id": 12,
  "captured_at": "2026-09-28T09:31:00+00:00",
  "activity_state": "ACTIVE",
  "file_size": 2536,
  "checksum": "<64 hex chars>"
}
```

The local row also carries `sync_status`, which is the agent's own bookkeeping
and is **not sent**: the contract declares which fields are wire fields, so a
local column cannot leak into a request by accident.

### `POST /screenshots/{client_id}/file` (the binary)

`multipart/form-data`:

| Field | Notes |
|---|---|
| `file` | JPEG or PNG, max 8 MiB (configurable) |
| `client_id` | Must match the path |
| `session_id` | The agent's local work-session row id |
| `captured_at` | ISO-8601 UTC |
| `activity_state` | `ACTIVE` or `IDLE` |
| `checksum` | SHA-256 the agent computed locally |

The server hashes the received bytes and compares them with `checksum`. A
mismatch is a `422` and **changes nothing** — no file written, no metadata
touched — so the agent's local copy stays intact and the capture is not lost
to a truncated upload.

Metadata and binary are separate calls because the agent records a capture
twice locally (an outbox row and a pending upload). Either can arrive first,
and both may be replayed. A metadata row with no file is a normal, resumable
state.

## `GET /agent/config`

Authenticated. Returns the agent's `ServerPolicy` field for field:

```json
{
  "screenshot_interval_seconds": 60,
  "idle_threshold_seconds": 300,
  "local_retention_megabytes": 512,
  "sync_poll_seconds": 30,
  "cleanup_poll_seconds": 300,
  "sync_batch_limit": 20,
  "retention_days": 7
}
```

The client consumes this through `ConfigProvider`
(`app/domain/policy/provider.py`), with `ApiConfigProvider` as the api-mode
implementation. The sync tick re-reads it and `PolicyService` applies what a
running agent can adopt:

| Field | Applied |
|---|---|
| `screenshot_interval_seconds` | immediately (`ScreenshotService.set_interval`) |
| `idle_threshold_seconds` | immediately (`ActivityService.set_idle_threshold`) |
| everything else | next start — read once at construction |

An operator can therefore retune an installed agent by changing server
configuration. Two rules keep that safe: a value the client's own validators
refuse (a 0-second interval, say) rejects the whole response and keeps the
local defaults, and a field the client does not recognise is ignored, so a
newer server can never break an older agent.

## `GET /workspace`

Bearer token + the `sync` ability.

```json
{"workspace": {"id": 1, "email": "...", "workspace_name": "Acme Support"}}
```

The employee names their own workspace from the dashboard; this endpoint is
how the running desktop app finds out. Polling it on the sync tick (it is one
cheap row read) means a rename in the browser relabels the app within a sync
cycle instead of at the next sign-in.

`PUT /workspace` with `{"workspace_name": "..."}` sets it, sharing its
validation with the dashboard form — one definition, two front doors. The
client never writes it: this is display-only context the employee owns.

## Adapting the client without code changes

The wire surface is data, in `app/infrastructure/network/contract.py`:
`ApiContract` holds one `Endpoint` per operation, and the providers are
generic drivers that look an endpoint up and execute whatever it declares.
`Endpoint.body_fields` is also a filter — a payload is projected onto the
declared wire fields, so a local column can never be sent by accident.

`EM_API_CONTRACT_PATH` points at a JSON overlay that replaces or extends the
built-in contract:

```json
{
  "me": {"path": "/v2/whoami"},
  "entities": {
    "focus_session": {
      "method": "PUT",
      "path": "/v2/focus-sessions/{client_id}",
      "body_fields": ["id", "started_at", "duration_seconds"]
    }
  },
  "file_endpoints": {
    "screenshot": {"path": "/v2/screenshots/{client_id}/binary", "file_field": "blob"}
  },
  "user_fields": {"workspace_name": "team"}
}
```

Everything not mentioned keeps the built-in value. A malformed or missing file
is a **startup error**, never a silent fallback: the worst outcome available
would be an agent that looks configured and quietly syncs to the wrong URLs.

The same seam is what a genuinely new feature uses. `workspace` and
`agent_config` were added without touching a service, the UI, or the domain —
the sync tick reads them, and a Qt signal carries the one the UI needs. A
future endpoint costs a record in the contract plus, at most, a call in
`SyncWorker._refresh_remote_settings`.

## How the client reacts to failures

Classification lives in one place (`app/infrastructure/network/http_client.py`)
because the outbox's behaviour depends entirely on getting it right.

| Response | `SyncResult` | Local data |
|---|---|---|
| 2xx | success | **deleted** (confirm-then-delete) |
| 401 / 403 | not retryable, `AUTHENTICATION_REQUIRED` | kept; one silent `refresh` + retry first |
| 5xx, 429, 408 | retryable, `BACKEND_UNAVAILABLE` | kept |
| 400 / 404 / 409 / 422 | **not retryable** | kept |
| DNS / connect / TLS / timeout | retryable, `BACKEND_UNAVAILABLE` | kept |
| 2xx with a non-JSON body | retryable, `BACKEND_UNAVAILABLE` | kept |

Non-retryable does not mean "discard". It means "stop retrying on a timer" —
the row stays in the outbox, visible and recoverable, instead of turning into
a hot loop against a backend that is working correctly.

## Privacy boundary

The API is designed so that the client's privacy rules are structural rather
than aspirational:

- No endpoint accepts keystroke content, mouse coordinates or clipboard data.
  `activity_periods.state` is an enum of two values and nothing else.
- Screenshots are stored on a private disk with no public URL. The only way to
  read one back is a controller that checks ownership.
- The password is used for one request and never stored. The token is hashed
  server-side and lives in the OS keyring on the client.
- Agent tokens carry the `sync` ability and dashboard access is a session
  cookie, so a copied agent token cannot browse the dashboard and a dashboard
  session cannot push data.

## Status codes

| Code | Meaning to the client |
|---|---|
| 200 / 201 | persisted |
| 401 / 403 | token problem → refresh once, then `AUTHENTICATION_REQUIRED` |
| 422 | contract violation or checksum mismatch → non-retryable, keep local data |
| 429 | rate limited → retryable |
| 5xx | server problem → retryable |
