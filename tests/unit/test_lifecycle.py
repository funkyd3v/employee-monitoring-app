"""Unit tests for the lifecycle start/shutdown sequencing."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from app.core.exceptions import ConfigurationError
from app.core.lifecycle import Lifecycle, LifecycleContext

Start = Callable[[LifecycleContext], None]


def _mark(key: str, log: list[str]) -> Start:
    def start(ctx: LifecycleContext) -> None:
        log.append(key)
        ctx.set(key, True)

    return start


def _shutdown(key: str, log: list[str]) -> Start:
    def shutdown(_ctx: LifecycleContext) -> None:
        log.append(f"shutdown:{key}")

    return shutdown


def test_startup_runs_in_registration_order() -> None:
    log: list[str] = []
    lc = Lifecycle()
    for name in ("a", "b", "c"):
        lc.add(name, start=_mark(name, log), shutdown=_shutdown(name, log))
    lc.start()
    assert log == ["a", "b", "c"]
    assert lc.context.get("a") is True
    assert lc.context.get("c") is True


def test_shutdown_runs_in_reverse_order() -> None:
    log: list[str] = []
    lc = Lifecycle()
    for name in ("a", "b", "c"):
        lc.add(name, start=_mark(name, log), shutdown=_shutdown(name, log))
    lc.start()
    log.clear()
    lc.shutdown()
    assert log == ["shutdown:c", "shutdown:b", "shutdown:a"]


def test_failed_startup_unwinds_started_steps() -> None:
    log: list[str] = []

    def explode(ctx: LifecycleContext) -> None:  # noqa: ARG001
        raise RuntimeError("boom")

    lc = Lifecycle()
    lc.add("ok1", start=_mark("ok1", log), shutdown=_shutdown("ok1", log))
    lc.add("bad", start=explode, shutdown=_shutdown("bad", log))
    lc.add("ok2", start=_mark("ok2", log), shutdown=_shutdown("ok2", log))

    with pytest.raises(ConfigurationError, match="Startup failed at bad"):
        lc.start()

    # ok1 started and was unwound; bad never started; ok2 never started.
    assert "ok2" not in log
    assert log == ["ok1", "shutdown:ok1"]


def test_shutdown_is_best_effort() -> None:
    log: list[str] = []

    def bad_shutdown(ctx: LifecycleContext) -> None:  # noqa: ARG001
        raise RuntimeError("teardown failed")

    lc = Lifecycle()
    lc.add("a", start=_mark("a", log), shutdown=bad_shutdown)
    lc.add("b", start=_mark("b", log), shutdown=_shutdown("b", log))
    lc.start()
    lc.shutdown()  # must not raise even though "a" teardown raises
    assert log == ["a", "b", "shutdown:b"]
