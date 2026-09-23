"""Unit tests for structural secret redaction in the logging layer.

These guard the security invariant from docs/SECURITY_PRIVACY.md: logs must
never contain credentials, tokens, or raw input. If any of these tests are
relaxed, treat it as a security regression, not a lint clean-up.
"""

from __future__ import annotations

import logging

import pytest
from app.core.logging import (
    REDACTED,
    RedactingFormatter,
    discard_secret,
    register_secret,
)


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []
        self.setFormatter(RedactingFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _render(message: str) -> str:
    handler = _CaptureHandler()
    logger = logging.getLogger("test.redaction")
    logger.propagate = False
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.info("%s", message)
    return handler.messages[0]


@pytest.mark.parametrize(
    ("message", "forbidden"),
    [
        ("password=hunter2", "hunter2"),
        ("password: hunter2", "hunter2"),
        ('{"password": "hunter2"}', "hunter2"),
        ('{"token": "abc123def456"}', "abc123def456"),
        ("token=superlongvaluetokenoperator", "superlongvaluetokenoperator"),
        ("api_key=sk-123456-abcdef", "sk-123456-abcdef"),
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc-def_ghi",
            "eyJhbGciOiJIUzI1NiJ9.abc-def_ghi",
        ),
        ("proxy-authorization: basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("sent a bearer token: Bearer e30.e30", "e30.e30"),
        ("oauth refresh_token in query ?refresh_token=xyz789&continue=1", "xyz789"),
    ],
)
def test_secret_patterns_are_redacted(message: str, forbidden: str) -> None:
    assert forbidden not in _render(message)
    assert REDACTED in _render(message)


@pytest.mark.parametrize(
    "message",
    [
        "user: janedoe logged in",
        "reason: updated the record",
        "mode=api id=42",
        "capture failed: error_5",
        "the bearer token was rejected by the policy",
        "bearer credentials are never logged",
    ],
)
def test_non_secret_text_is_untouched(message: str) -> None:
    assert _render(message) == message


def test_registered_secret_is_redacted_verbatim() -> None:
    live_token = "LIVE-TOKEN-C0mpLet3-StaTic"
    register_secret(live_token)
    try:
        rendered = _render(f"handshake ok token={live_token} in flow")
        assert live_token not in rendered
        assert REDACTED in rendered
    finally:
        discard_secret(live_token)


def test_discarded_secret_stops_being_redacted() -> None:
    live_token = "temp-secret-123"
    register_secret(live_token)
    discard_secret(live_token)
    rendered = _render(f"contains {live_token} now plain")
    assert live_token in rendered


def test_reserved_marker_is_never_registered() -> None:
    register_secret(REDACTED)
    rendered = _render(f"literal {REDACTED} marker")
    assert rendered.count(REDACTED) == 1


def test_redaction_is_idempotent() -> None:
    first = _render("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9")
    second = _render("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9")
    # No doubled markers, stable output across repeated formatting.
    assert first == second
    assert first.count(REDACTED) == 1


def test_bearer_after_pair_value_is_fully_consumed() -> None:
    rendered = _render("sent a bearer token: Bearer e30.e30")
    assert "e30.e30" not in rendered
    assert REDACTED in rendered


def test_multi_part_authorization_token_fully_consumed() -> None:
    rendered = _render("Authorization: Bearer header.payload.sig extra-part")
    assert "header.payload.sig" not in rendered
    assert "extra-part" not in rendered
    assert rendered.count(REDACTED) == 1


def test_exception_text_is_redacted() -> None:
    handler = _CaptureHandler()
    logger = logging.getLogger("test.redaction.exc")
    logger.propagate = False
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    try:
        raise ValueError("password=hunter2 leaked inside exception")
    except ValueError:
        logger.info("boom", exc_info=True)
    rendered = handler.messages[0]
    assert "hunter2" not in rendered
    assert "password=" in rendered
