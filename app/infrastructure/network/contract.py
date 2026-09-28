"""The wire contract, as data instead of as code.

Adding an endpoint to the client used to mean editing
:mod:`app.infrastructure.network.sync_adapter` and
:mod:`app.infrastructure.network.auth_adapter`: a new path in a dict, a new
field in a hand-written request body, a new branch in the uploader. Every one
of those edits is a place for the next release to forget about.

This module inverts that. The entire API surface the client speaks is declared
once, as :class:`Endpoint` records, and the providers are *generic drivers* that
look an endpoint up by name and execute whatever it declares. Two consequences:

* a new entity the backend starts accepting syncs with a line of data, and
* the same surface can be **replaced from outside the code** — see
  :func:`load_contract` — so pointing the agent at a differently-shaped
  backend is a JSON file, not a rebuild.

Nothing here performs I/O or knows about HTTP; it is a typed description that
the network layer executes. That is what keeps it verifiable: a contract is
data, so it can be asserted on directly in tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from app.core.exceptions import ConfigurationError

#: The one field every outbox payload carries: the agent's local row id, which
#: is also the server's idempotency key.
ID_FIELD: Final = "id"

#: Path parameter standing in for the agent's local row id.
CLIENT_ID: Final = "client_id"

#: ``AuthenticatedUser`` fields the client reads out of a user envelope.
USER_FIELDS: Final = ("external_user_id", "email", "display_name", "workspace_name")

#: A multipart field list: ``(wire name, payload key)``. The two are usually
#: identical; where they are not — the upload renames the local ``id`` to
#: ``client_id`` because that is what the endpoint's path parameter is called —
#: the rename is declared here rather than special-cased in the uploader. A JSON
#: contract may write either form: a list for identity mapping, or an object to
#: be explicit.
FieldPairs = tuple[tuple[str, str], ...]


def _names(value: object, *, owner: str) -> tuple[str, ...]:
    """Validate a declared list of wire field names."""
    if not isinstance(value, (list, tuple)):
        raise ConfigurationError(
            f"api_contract_file: endpoint '{owner}' body_fields must be a list or null"
        )
    return tuple(str(item) for item in value)


def _optional_names(value: object, *, owner: str) -> tuple[str, ...] | None:
    return None if value is None else _names(value, owner=owner)


def _field_pairs(value: object, *, owner: str) -> FieldPairs:
    """Normalise a declared multipart field list into name/key pairs."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple((str(item), str(item)) for item in value)
    if isinstance(value, dict):
        return tuple((str(wire), str(source)) for wire, source in value.items())

    raise ConfigurationError(
        f"api_contract_file: endpoint '{owner}' form_fields must be an array of names "
        "or an object mapping wire names to payload keys"
    )


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One HTTP operation, declared.

    ``body_fields`` and ``form_fields`` are the *wire* shape of the request,
    kept separate from whatever the local row happens to contain. The local
    payload builders emit bookkeeping columns the server has no use for
    (``sync_status``, for instance); sending only what is declared means a
    local column can never leak into a request by accident.

    ``body_fields`` are the wire names, which for a JSON body are the payload
    names too; ``form_fields`` are name pairs because a multipart body is the
    one place the server renames a field.

    ``body_fields=None`` means "send the payload untouched", which suits a
    backend that wants the whole object. The declared form is the default
    because it is the safer one.
    """

    name: str
    method: str
    path: str
    summary: str
    authenticated: bool = True
    body_fields: tuple[str, ...] | None = None
    form_fields: FieldPairs = ()
    file_field: str | None = None
    file_content_type: str | None = None
    #: False when the request carries no body at all (e.g. ``/me``).
    sends_body: bool = True

    def url(self, *, client_id: int) -> str:
        """Path with the agent's local row id substituted."""
        return self.path.format(**{CLIENT_ID: client_id})

    def body(self, payload: dict[str, Any], *, client_id: int) -> dict[str, Any]:
        """Project a local payload onto the declared wire fields.

        The local id is always included: the server requires it to agree with
        the path, which turns a client/server contract drift into a 422 rather
        than a silently mis-filed row.
        """
        if not self.sends_body:
            return {}

        if self.body_fields is None:
            projected = dict(payload)
        else:
            projected = {
                key: payload[key] for key in self.body_fields if key in payload
            }

        projected[ID_FIELD] = int(payload.get(ID_FIELD) or client_id)
        return projected

    def form(self, metadata: dict[str, Any]) -> dict[str, str]:
        """Multipart text fields, stringified for the wire.

        Renames are honoured (``client_id`` reads the local ``id``), and an
        absent value drops the field entirely rather than sending an empty
        string the server would have to interpret.
        """
        return {
            wire: str(metadata[source])
            for wire, source in self.form_fields
            if metadata.get(source) is not None
        }

    @property
    def uploads_file(self) -> bool:
        return self.file_field is not None

    def with_overrides(self, definition: dict[str, Any]) -> Endpoint:
        """A copy of this endpoint with fields taken from a JSON definition."""
        if not isinstance(definition, dict):
            raise ConfigurationError(
                f"api_contract_file: endpoint '{self.name}' must be an object"
            )

        path = definition.get("path")
        if path is not None and (not isinstance(path, str) or not path.startswith("/")):
            raise ConfigurationError(
                f"api_contract_file: endpoint '{self.name}' needs a 'path' starting with '/'"
            )

        body_fields = definition.get("body_fields")
        if body_fields is not None and not isinstance(body_fields, list):
            raise ConfigurationError(
                f"api_contract_file: endpoint '{self.name}' body_fields must be "
                "a list or null"
            )

        def as_tuple(key: str) -> tuple[str, ...]:
            value = definition.get(key, ())
            if not isinstance(value, (list, tuple)):
                raise ConfigurationError(
                    f"api_contract_file: endpoint '{self.name}' {key} must be a list"
                )
            return tuple(str(item) for item in value)

        return Endpoint(
            name=self.name,
            method=str(definition.get("method", self.method)).upper(),
            path=str(path) if path is not None else self.path,
            summary=str(definition.get("summary", self.summary)),
            authenticated=bool(definition.get("authenticated", self.authenticated)),
            body_fields=self.body_fields
            if "body_fields" not in definition
            else _optional_names(body_fields, owner=self.name),
            form_fields=_field_pairs(definition["form_fields"], owner=self.name)
            if "form_fields" in definition
            else self.form_fields,
            file_field=definition.get("file_field", self.file_field),
            file_content_type=definition.get(
                "file_content_type", self.file_content_type
            ),
            sends_body=bool(definition.get("sends_body", self.sends_body)),
        )


@dataclass(frozen=True, slots=True)
class ApiContract:
    """Everything the client needs to know about a backend's shape."""

    health: Endpoint
    login: Endpoint
    refresh: Endpoint
    logout: Endpoint
    identity: Endpoint
    workspace: Endpoint
    agent_config: Endpoint
    #: Outbox ``entity_type`` -> the endpoint that persists it.
    entities: dict[str, Endpoint] = field(default_factory=dict)
    #: ``entity_type`` -> the endpoint that carries its binary payload, for
    #: entities that have one.
    file_endpoints: dict[str, Endpoint] = field(default_factory=dict)
    #: ``AuthenticatedUser`` field -> key in the server's user envelope.
    user_fields: dict[str, str] = field(default_factory=dict)
    #: Set when the contract came from an operator-supplied file.
    source: str = "built-in"

    def entity(self, entity_type: str) -> Endpoint | None:
        """Endpoint that persists an outbox entity, or None if undeclared."""
        return self.entities.get(entity_type)

    def file_endpoint(self, entity_type: str) -> Endpoint | None:
        """Binary-upload endpoint for an entity, or None if it has none."""
        return self.file_endpoints.get(entity_type)

    def user_key(self, field_name: str) -> str:
        """Server-side key for a user field (identity mapping by default)."""
        return self.user_fields.get(field_name, field_name)

    def known_entities(self) -> tuple[str, ...]:
        return tuple(sorted(self.entities))

    def describe(self) -> str:
        """One-line summary of the surface, for the startup log."""
        origin = "" if self.source == "built-in" else f" from {self.source}"
        return f"{len(self.entities)} sync entities, identity at {self.identity.path}{origin}"


#: The v1 contract, matching docs/API_CONTRACT.md and the backend's
#: ``/api-docs``. Data, not code — changing it is a deliberate, reviewable act.
DEFAULT_CONTRACT: Final = ApiContract(
    health=Endpoint(
        name="health",
        method="GET",
        path="/health",
        summary="Unauthenticated liveness probe.",
        authenticated=False,
        sends_body=False,
    ),
    login=Endpoint(
        name="login",
        method="POST",
        path="/auth/login",
        summary="Exchange credentials for a bearer token.",
        authenticated=False,
        body_fields=("email", "password", "device_name", "platform", "agent_version"),
    ),
    refresh=Endpoint(
        name="refresh",
        method="POST",
        path="/auth/refresh",
        summary="Validate a stored token and extend its lifetime.",
        sends_body=False,
    ),
    logout=Endpoint(
        name="logout",
        method="POST",
        path="/auth/logout",
        summary="Revoke the presenting token.",
        sends_body=False,
    ),
    identity=Endpoint(
        name="me",
        method="GET",
        path="/me",
        summary="Read the authenticated identity.",
        sends_body=False,
    ),
    workspace=Endpoint(
        name="workspace",
        method="GET",
        path="/workspace",
        summary="Read the workspace name the app labels itself with.",
        sends_body=False,
    ),
    agent_config=Endpoint(
        name="agent_config",
        method="GET",
        path="/agent/config",
        summary="Policy the agent should run with.",
        sends_body=False,
    ),
    entities={
        # The identity record arrives with the login response, so the domain's
        # `user` outbox type has nowhere to be written. Declaring it as a
        # no-body request keeps the driver uniform: an undeclared type still
        # produces a deliberate, non-retryable failure rather than a silently
        # dropped item the outbox would keep retrying.
        "user": Endpoint(
            name="user",
            method="GET",
            path="/me",
            summary="The authenticated user, delivered with the login response.",
            sends_body=False,
        ),
        "work_session": Endpoint(
            name="work_session",
            method="PUT",
            path="/work-sessions/{client_id}",
            summary="Record a work session.",
            body_fields=(
                ID_FIELD,
                "started_at",
                "ended_at",
                "status",
                "total_work_seconds",
            ),
        ),
        "break": Endpoint(
            name="break",
            method="PUT",
            path="/breaks/{client_id}",
            summary="Record a break inside a session.",
            body_fields=(
                ID_FIELD,
                "session_id",
                "started_at",
                "ended_at",
                "duration_seconds",
            ),
        ),
        "activity_period": Endpoint(
            name="activity_period",
            method="PUT",
            path="/activity-periods/{client_id}",
            summary="Record active vs idle time.",
            body_fields=(
                ID_FIELD,
                "session_id",
                "started_at",
                "ended_at",
                "state",
                "duration_seconds",
            ),
        ),
        "screenshot": Endpoint(
            name="screenshot",
            method="PUT",
            path="/screenshots/{client_id}",
            summary="Register screenshot metadata.",
            body_fields=(
                ID_FIELD,
                "session_id",
                "captured_at",
                "activity_state",
                "file_size",
                "checksum",
            ),
        ),
    },
    file_endpoints={
        "screenshot": Endpoint(
            name="screenshot_file",
            method="POST",
            path="/screenshots/{client_id}/file",
            summary="Upload the image bytes.",
            # The path parameter is called `client_id`; the local payload calls
            # the same column `id`. That rename is declared here rather than
            # hardcoded in the uploader.
            form_fields=(
                ("client_id", "id"),
                ("session_id", "session_id"),
                ("captured_at", "captured_at"),
                ("activity_state", "activity_state"),
                ("checksum", "checksum"),
            ),
            file_field="file",
            file_content_type="image/jpeg",
        ),
    },
    user_fields={name: name for name in USER_FIELDS},
)

#: Overlay keys that replace a single named endpoint of the default contract.
_SINGLE_ENDPOINTS: Final = {
    "health": "health",
    "login": "login",
    "refresh": "refresh",
    "logout": "logout",
    "me": "identity",
    "workspace": "workspace",
    "agent_config": "agent_config",
}


def load_contract(overlay: Path | str | None) -> ApiContract:
    """Build the contract, optionally overlaying an operator-supplied file.

    The overlay is JSON with optional keys: the singles above (``health``,
    ``login``, ``refresh``, ``logout``, ``me``, ``workspace``,
    ``agent_config``), ``entities`` (any outbox ``entity_type``),
    ``file_endpoints`` (binary uploads) and ``user_fields`` (how to read a
    user envelope). It exists so a differently-shaped backend can be integrated
    by dropping a file next to the agent, and so a new entity can be taught to
    the client without shipping a new build.

    A malformed overlay raises :class:`ConfigurationError` at startup. Silently
    ignoring it would be the worst outcome available: the agent would look
    configured and quietly sync to the wrong URLs.
    """
    if overlay is None or overlay == "":
        return DEFAULT_CONTRACT

    path = Path(overlay)
    if not path.is_file():
        raise ConfigurationError(f"api_contract_file does not exist: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"api_contract_file is not readable JSON: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigurationError("api_contract_file must contain a JSON object")

    contract = DEFAULT_CONTRACT
    overrides: dict[str, Any] = {}

    for key, attribute in _SINGLE_ENDPOINTS.items():
        if key in raw:
            overrides[attribute] = getattr(contract, attribute).with_overrides(raw[key])

    entities = dict(contract.entities)
    for entity_type, definition in _mapping(raw, "entities").items():
        entities[str(entity_type)] = _declared_endpoint(
            str(entity_type), definition, fallback_path=f"/{entity_type}/{{client_id}}"
        )

    file_endpoints = dict(contract.file_endpoints)
    for entity_type, definition in _mapping(raw, "file_endpoints").items():
        file_endpoints[str(entity_type)] = _declared_endpoint(
            str(entity_type),
            definition,
            fallback_path=f"/{entity_type}/{{client_id}}/file",
        )

    declared_user_fields = _mapping(raw, "user_fields")

    return replace(
        contract,
        **overrides,
        entities=entities,
        file_endpoints=file_endpoints,
        user_fields={
            **contract.user_fields,
            **{str(k): str(v) for k, v in declared_user_fields.items()},
        },
        source=str(path),
    )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"api_contract_file: '{key}' must be an object")
    return value


def _declared_endpoint(name: str, definition: Any, *, fallback_path: str) -> Endpoint:
    """Build a brand-new endpoint declaration (a type this build never saw).

    Validated as strictly as an override: a typo in a hand-written contract
    file must be a startup error, because the alternative is an agent that
    looks configured and quietly syncs to the wrong place.
    """
    if not isinstance(definition, dict):
        raise ConfigurationError(f"api_contract_file: '{name}' must be an object")

    path = definition.get("path", fallback_path)
    if not isinstance(path, str) or not path.startswith("/"):
        raise ConfigurationError(
            f"api_contract_file: endpoint '{name}' needs a 'path' starting with '/'"
        )

    return Endpoint(
        name=name,
        method=str(definition.get("method", "PUT")).upper(),
        path=path,
        summary=str(definition.get("summary", "")),
        authenticated=bool(definition.get("authenticated", True)),
        body_fields=_optional_names(definition.get("body_fields"), owner=name),
        form_fields=_field_pairs(definition.get("form_fields", ()), owner=name),
        file_field=definition.get("file_field"),
        file_content_type=definition.get("file_content_type"),
        sends_body=bool(definition.get("sends_body", True)),
    )


__all__ = [
    "CLIENT_ID",
    "DEFAULT_CONTRACT",
    "ID_FIELD",
    "USER_FIELDS",
    "ApiContract",
    "Endpoint",
    "FieldPairs",
    "load_contract",
]
