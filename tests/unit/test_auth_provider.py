"""Unit tests for the auth domain: local dummy provider and token format."""

from __future__ import annotations

import pytest
from app.core.exceptions import AuthenticationError
from app.domain.auth.auth import (
    AuthSession,
    DummyAuthConfig,
    LocalDummyAuthProvider,
)

EMAIL = "employee@example.com"
PASSWORD = "s3cret!"
DISPLAY_NAME = "Jane Doe"
TEAM = "Engineering"


def make_provider(
    *, password: str = PASSWORD, email: str = EMAIL
) -> LocalDummyAuthProvider:
    return LocalDummyAuthProvider(
        DummyAuthConfig(
            email=email,
            password=password,
            display_name=DISPLAY_NAME,
            team_name=TEAM,
        )
    )


def test_login_success_returns_user_and_session() -> None:
    session = make_provider().login(EMAIL, PASSWORD)
    assert isinstance(session, AuthSession)
    assert session.user.email == EMAIL
    assert session.user.display_name == DISPLAY_NAME
    assert session.user.team_name == TEAM
    assert session.user.external_user_id == f"local:{EMAIL}"
    assert session.token.startswith("dv1.")


def test_login_rejects_wrong_email() -> None:
    with pytest.raises(AuthenticationError, match="invalid credentials"):
        make_provider().login("someone-else@example.com", PASSWORD)


def test_login_rejects_wrong_password() -> None:
    with pytest.raises(AuthenticationError, match="invalid credentials"):
        make_provider().login(EMAIL, "not-the-password")


def test_login_rejects_empty_password() -> None:
    with pytest.raises(AuthenticationError, match="invalid credentials"):
        make_provider().login(EMAIL, "")


def test_login_rejects_empty_email() -> None:
    with pytest.raises(AuthenticationError, match="invalid credentials"):
        make_provider().login("", PASSWORD)


def test_unset_config_password_accepts_any_nonempty_for_dev() -> None:
    provider = make_provider(password="")
    session = provider.login(EMAIL, "whatever-dev-password")
    assert session.user.email == EMAIL


def test_token_never_contains_password_or_email_plaintext() -> None:
    session = make_provider().login(EMAIL, PASSWORD)
    assert PASSWORD not in session.token


def test_refresh_roundtrips_stored_token() -> None:
    provider = make_provider()
    session = provider.login(EMAIL, PASSWORD)
    refreshed = provider.refresh(session.token)
    assert refreshed.user.email == EMAIL
    assert refreshed.user.display_name == DISPLAY_NAME
    assert refreshed.token == session.token


def test_refresh_rejects_malformed_token() -> None:
    with pytest.raises(AuthenticationError, match="invalid local session token"):
        make_provider().refresh("not-a-token")


def test_refresh_rejects_token_with_wrong_prefix() -> None:
    token = make_provider().login(EMAIL, PASSWORD).token
    with pytest.raises(AuthenticationError, match="invalid local session token"):
        make_provider().refresh(f"bogus.{token}")
    with pytest.raises(AuthenticationError, match="invalid local session token"):
        make_provider().refresh(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_refresh_rejects_empty_token() -> None:
    with pytest.raises(AuthenticationError, match="invalid local session token"):
        make_provider().refresh("")


def test_logout_is_best_effort_noop() -> None:
    assert make_provider().logout("anything") is None
