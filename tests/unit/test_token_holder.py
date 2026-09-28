"""Unit tests for the in-memory auth token holder.

The holder is the only bridge between the auth service and the sync provider.
Its two jobs: never leak the token into anything durable, and never serve a
stale one to a worker thread after logout.
"""

from __future__ import annotations

import threading

from app.domain.auth.token_holder import AuthTokenHolder

TOKEN = "1|super-secret-token"


def test_starts_empty() -> None:
    holder = AuthTokenHolder()
    assert holder.get() is None
    assert holder.has_token() is False


def test_set_then_get() -> None:
    holder = AuthTokenHolder()
    holder.set(TOKEN)
    assert holder.get() == TOKEN
    assert holder.has_token() is True


def test_clear_is_idempotent() -> None:
    holder = AuthTokenHolder()
    holder.set(TOKEN)
    holder.clear()
    holder.clear()
    assert holder.get() is None


def test_replacement_overwrites_the_previous_token() -> None:
    holder = AuthTokenHolder()
    holder.set("old")
    holder.set("new")
    assert holder.get() == "new"


def test_is_safe_under_concurrent_reads_and_writes() -> None:
    """The login thread writes while the sync worker reads."""
    holder = AuthTokenHolder()
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def writer(index: int) -> None:
        try:
            start.wait(timeout=5)
            for _ in range(200):
                holder.set(f"token-{index}")
        except BaseException as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    def reader() -> None:
        try:
            start.wait(timeout=5)
            for _ in range(200):
                value = holder.get()
                assert value is None or isinstance(value, str)
        except BaseException as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    holder.clear()
    assert holder.get() is None
