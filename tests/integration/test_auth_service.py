"""Integration tests for AuthService against a real (temporary) SQLite DB.

Covers the DOD acceptance "session persists across app restarts"
(docs/TESTING_AND_DOD.md) via a persisted token store + a fresh service
instance, plus the security invariant that tokens never reach logs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from app.core.clock import utc_now
from app.core.exceptions import AuthenticationError, SessionStateError
from app.core.logging import RedactingFormatter, _registry
from app.domain.auth.auth import DummyAuthConfig, LocalDummyAuthProvider
from app.domain.auth.credential_store import CredentialStoreUnavailableError
from app.domain.sessions.state_machine import AppState, SessionAction
from app.infrastructure.database.repositories import UserRepository
from app.infrastructure.security.credential_store import (
    DevFileCredentialStore,
    EphemeralCredentialStore,
    KeyringCredentialStore,
)
from app.services.auth_service import AuthService

if TYPE_CHECKING:
    from pathlib import Path

    from app.infrastructure.database.db import Database

EMAIL = "employee@example.com"
PASSWORD = "s3cret!"


class ThrowingStore:
    """A credential store whose backend exits mid-operation (outage)."""

    backend = "broken"

    def save(self, token: str) -> None:  # noqa: ARG002
        raise CredentialStoreUnavailableError("credential manager unreachable")

    def load(self) -> None:
        raise CredentialStoreUnavailableError("credential manager unreachable")

    def clear(self) -> None:
        raise CredentialStoreUnavailableError("credential manager unreachable")


def make_service(
    database: Database,
    *,
    store=None,
    provider: LocalDummyAuthProvider | None = None,
) -> AuthService:
    store = store or EphemeralCredentialStore()
    provider = provider or LocalDummyAuthProvider(
        DummyAuthConfig(
            email=EMAIL, password=PASSWORD, display_name="Jane", team_name="Eng"
        )
    )
    return AuthService(
        auth_provider=provider,
        credential_store=store,
        session_factory=database.session,
    )


def test_login_persists_user_and_reaches_ready(
    database: Database,
) -> None:
    service = make_service(database)
    user = service.login(EMAIL, PASSWORD)

    assert user.email == EMAIL
    assert service.is_authenticated()
    assert service.current_user_id() is not None
    assert service.state is AppState.READY

    row = UserRepository(database.session()).get_by_email(EMAIL)
    assert row is not None
    assert row.external_user_id == f"local:{EMAIL}"
    assert row.display_name == "Jane"


def test_failed_login_leaves_state_logged_out_and_no_token(
    database: Database,
) -> None:
    store = EphemeralCredentialStore()
    service = make_service(database, store=store)

    with pytest.raises(AuthenticationError, match="invalid credentials"):
        service.login(EMAIL, "wrong")

    assert service.state is AppState.LOGGED_OUT
    assert store.load() is None
    assert not service.is_authenticated()
    assert UserRepository(database.session()).get_by_email(EMAIL) is None


def test_unavailable_store_fails_login_loudly(database: Database) -> None:
    service = make_service(database, store=ThrowingStore())
    with pytest.raises(AuthenticationError, match="could not be persisted"):
        service.login(EMAIL, PASSWORD)
    assert service.state is AppState.LOGGED_OUT


def test_logout_clears_token_and_state(database: Database) -> None:
    store = EphemeralCredentialStore()
    service = make_service(database, store=store)
    service.login(EMAIL, PASSWORD)
    assert store.load() is not None

    service.logout()

    assert store.load() is None
    assert service.state is AppState.LOGGED_OUT
    assert not service.is_authenticated()
    assert service.current_user_id() is None


def test_logout_while_working_is_rejected_and_token_kept(
    database: Database,
) -> None:
    store = EphemeralCredentialStore()
    service = make_service(database, store=store)
    service.login(EMAIL, PASSWORD)

    service.machine.apply(
        SessionAction.CHECK_IN, at=utc_now(), user_id=service.current_user_id()
    )
    assert service.state is AppState.WORKING

    with pytest.raises(SessionStateError):
        service.logout()

    assert store.load() is not None
    assert service.is_authenticated()


def test_restore_after_restart_survives_with_fresh_service(
    database: Database, tmp_path: Path
) -> None:
    store = DevFileCredentialStore(tmp_path / "config" / "auth.token")
    first = make_service(database, store=store)
    first.login(EMAIL, PASSWORD)

    fresh = make_service(database, store=store)
    user = fresh.restore()

    assert user is not None
    assert user.email == EMAIL
    assert fresh.state is AppState.READY
    assert fresh.current_user_id() is not None


def test_restore_without_token_returns_none(database: Database) -> None:
    service = make_service(database, store=EphemeralCredentialStore())
    assert service.restore() is None
    assert service.state is AppState.LOGGED_OUT


def test_restore_with_corrupt_token_discards_it(database: Database) -> None:
    store = EphemeralCredentialStore()
    store.save("garbage-token")
    service = make_service(database, store=store)

    assert service.restore() is None
    assert store.load() is None
    assert service.state is AppState.LOGGED_OUT


def test_token_never_reaches_log_boundary(database: Database) -> None:
    store = EphemeralCredentialStore()
    service = make_service(database, store=store)
    service.login(EMAIL, PASSWORD)
    token = store.load()
    assert token is not None

    # The live token is registered with the redaction boundary on login…
    assert token in _registry.snapshot()

    # …and a hypothetical buggy log line is redacted at the formatter.
    record = logging.LogRecord(
        "employee_monitoring_agent",
        logging.WARNING,
        __file__,
        1,
        f"leak: {token}",
        None,
        None,
    )
    formatter = RedactingFormatter(fmt="%(message)s")
    rendered = formatter.format(record)
    assert token not in rendered
    assert "dv1." not in rendered.replace("[REDACTED]", "")

    # Logout drops the secret from the boundary.
    service.logout()
    assert token not in _registry.snapshot()


def test_restore_uses_keyring_backend_end_to_end(database: Database) -> None:
    from tests.unit.test_credential_store import FakeKeyring

    store = KeyringCredentialStore(FakeKeyring())
    first = make_service(database, store=store)
    first.login(EMAIL, PASSWORD)

    fresh = make_service(database, store=store)
    assert fresh.restore() is not None
