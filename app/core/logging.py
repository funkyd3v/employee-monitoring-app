"""Logging bootstrap with structural secret redaction.

Security invariant (docs/SECURITY_PRIVACY.md): logs must never contain
credentials, tokens, screenshot contents, or raw input values. This is
enforced *at the boundary* in two complementary ways:

1. :class:`RedactingFormatter` rewrites every rendered log line before it
   reaches the output — including exception text and nested payloads,
   because redaction runs on the fully formatted string.
2. Live secrets (auth tokens, session keys) registered via
   :func:`register_secret` are replaced verbatim wherever they appear.

Sensitive *values* must still never be placed in log calls — use
structured fields (IDs, error codes, state names). This filter is the last
line of defence, not a licence to log secrets.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import threading
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

REDACTED: Final = "[REDACTED]"

_APP_LOGGER_NAME: Final = "employee_monitoring_agent"

# Secret-bearing key names. Redaction keys off these so innocuous text like
# "reason: updated" or "user: janedoe" is never touched.
_KEY_NAMES: Final = (
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?key|auth(?:orization)?|proxy-authorization|"
    r"credential|client[_-]?secret)"
)
# Token value charset deliberately excludes "[", "]" so a "[REDACTED]" the
# formatter wrote itself can never be re-matched (idempotency).
_TOKEN_CHARS: Final = r"[A-Za-z0-9._~+/=\-]+"  # noqa: S105 -- regex charset name, not a credential

# Order matters and each rule is written so it can never re-match its own
# output: ordered from most specific to least, single-token values exclude
# "[", and the standalone-bearer rule avoids swallowing prose key words.

# 1) Authorization header lines — consume the whole remainder of the line,
#    including multi-part tokens (e.g. "Bearer <jwt> <sig>").
_AUTH_HEADER_PATTERN: Final = re.compile(
    r"(?im)(authorization|proxy-authorization)\s*:\s*(?:bearer\s+)?[^\r\n]*"
)
# 2) Quoted JSON/config values: {"password": "hunter2"}, 'token' = 'abc'.
_QUOTED_PAIR_PATTERN: Final = re.compile(
    rf"(?i)((['\"])({_KEY_NAMES})\2\s*[:=]\s*['\"])([^'\"]*)(['\"])"
)
# 3) Bare pairs, value may optionally be "Bearer <token>":
#    token=xyz, password: hunter2, token: Bearer e30.e30.
_BARE_PAIR_PATTERN: Final = re.compile(
    rf"(?i)({_KEY_NAMES})(\s*[:=]\s*)(['\"]?)(?:bearer\s+)?({_TOKEN_CHARS})"
)
# 4) Standalone tokens: "… Bearer eyJhbGci …". The negative lookahead keeps
#    prose like "the bearer token is" from swallowing the literal key words
#    or their plurals ("token", "secret", "credentials", …); the ≥8-char
#    minimum filters short prose nouns while still catching real OAuth/JWT
#    tokens. Bias is toward redaction, never toward leaking.
_BEARER_PATTERN: Final = re.compile(
    rf"(?i)\bbearer\s+(?!(?:{_KEY_NAMES})s?\b)([A-Za-z0-9._~+/=\-]{{8,}})"
)
# 5) Query-string pairs: ?token=abc&secret=def.
_QUERY_PATTERN: Final = re.compile(rf"(?i)([?&](?:{_KEY_NAMES})=)([^&#\s]*)")


def _redact_message(message: str) -> str:
    if not message:
        return message

    # Authorization headers first: they can hold multi-part tokens.
    message = _AUTH_HEADER_PATTERN.sub(rf"\1: {REDACTED}", message)
    # Quoted values: full value, including spaces, is consumed.
    message = _QUOTED_PAIR_PATTERN.sub(
        lambda m: f"{m.group(1)}{REDACTED}{m.group(5)}", message
    )
    # Bare pairs: value may carry a "Bearer " prefix.
    message = _BARE_PAIR_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", message
    )
    # Standalone "Bearer <token>".
    message = _BEARER_PATTERN.sub(rf"Bearer {REDACTED}", message)
    # Query-string pairs: ?token=value&secret=value.
    message = _QUERY_PATTERN.sub(rf"\1{REDACTED}", message)

    for secret in _registry.snapshot():
        if secret and secret != REDACTED and secret in message:
            message = message.replace(secret, REDACTED)
    return message


class _SecretRegistry:
    """Thread-safe registry of live secret values to redact verbatim."""

    def __init__(self, redacted: str) -> None:
        self._redacted = redacted
        self._lock = threading.Lock()
        self._secrets: set[str] = set()

    def add(self, secret: str) -> None:
        if secret and secret != self._redacted:
            with self._lock:
                self._secrets.add(secret)

    def discard(self, secret: str) -> None:
        with self._lock:
            self._secrets.discard(secret)

    def snapshot(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._secrets)


_registry = _SecretRegistry(REDACTED)


def register_secret(secret: str | None) -> None:
    """Register a live secret (token/password) for verbatim redaction.

    Call once at credential-load time; call :func:`discard_secret` on
    logout so it stops being replaced in future log lines.
    """
    if secret:
        _registry.add(secret)


def discard_secret(secret: str | None) -> None:
    """Stop redacting a previously registered secret."""
    if secret:
        _registry.discard(secret)


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts secrets from every rendered record."""

    def format(self, record: logging.LogRecord) -> str:
        return _redact_message(super().format(record))


def _file_formatter() -> logging.Formatter:
    fmt = (
        "%(asctime)s | %(levelname)-8s | %(name)s | "
        "%(funcName)s:%(lineno)d | %(message)s"
    )
    return RedactingFormatter(fmt=fmt)


def _console_formatter() -> logging.Formatter:
    return RedactingFormatter(fmt="%(levelname)s | %(name)s | %(message)s")


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = True,
) -> logging.Logger:
    """Configure the application logger.

    Returns the application root logger. Never raises: if the log directory
    can't be created the app falls back to console-only logging rather than
    failing bootstrap (failure isolation).
    """
    root = logging.getLogger(_APP_LOGGER_NAME)
    root.setLevel(level.upper())
    root.propagate = False

    handlers: list[logging.Handler] = []
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "agent.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(_file_formatter())
        handlers.append(file_handler)
    except OSError:
        root.warning(
            "Could not create log directory %s — falling back to console only",
            log_dir,
            exc_info=True,
        )

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(_console_formatter())
        handlers.append(stream_handler)

    for handler in handlers:
        handler.setLevel(root.level)
        root.addHandler(handler)

    # Quiet high-volume third-party loggers: debug output here is noise and
    # can echo request internals (headers/URLs). WARNING keeps errors visible.
    for name in ("httpx", "httpcore", "urllib3", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger of the application root."""
    return logging.getLogger(f"{_APP_LOGGER_NAME}.{name}")


# ── Phase 9: hardening helpers ────────────────────────────────────────


def install_global_handlers() -> None:
    """Install global exception + Qt handlers (Phase 9 logging hardening).

    Delegates to :mod:`app.infrastructure.system.crash_handler` while keeping
    the logging module as the public surface for callers that already import
    from ``app.core.logging``.
    """
    try:
        from app.infrastructure.system.crash_handler import (
            install_global_handlers as _install,
        )

        _install()
    except Exception:
        get_logger("logging").debug("global handler install failed", exc_info=True)


def log_startup_banner(settings: object | None = None) -> None:
    """Emit a single structured banner at startup (version, mode, data dir).

    Helpful for post-crash diagnostics: the log always opens with an
    identifiable header even when the app later crashes early.
    """
    try:
        from app.config.constants import APP_VERSION

        logger = get_logger("lifecycle")
        mode = getattr(getattr(settings, "local", None), "mode", None) if settings else None
        data_dir = getattr(getattr(settings, "local", None), "data_dir", None) if settings else None
        if mode is None and settings is not None and hasattr(settings, "mode"):
            mode = settings.mode
        if data_dir is None and settings is not None and hasattr(settings, "data_dir"):
            data_dir = settings.data_dir
        logger.info(
            "=== Employee Monitoring Agent v%s starting (mode=%s data_dir=%s) ===",
            APP_VERSION,
            mode or "local",
            data_dir or "?",
        )
    except Exception:  # noqa: S110
        pass


def reset_logging() -> None:
    """Remove all handlers from the app logger — for tests only."""
    root = logging.getLogger(_APP_LOGGER_NAME)
    for h in list(root.handlers):
        try:
            root.removeHandler(h)
            h.close()
        except Exception:  # noqa: S110
            pass
    # Also clear cached global-handler flag so reinstall is observable in tests
    root.setLevel(logging.NOTSET)
