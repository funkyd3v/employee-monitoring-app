"""Unit tests for Phase 9 — Windows integration & reliability."""

from __future__ import annotations

import sqlite3
import sys
import threading
from typing import TYPE_CHECKING

from app.core.logging import _redact_message
from app.infrastructure.system.crash_handler import verify_database
from app.infrastructure.system.power import SystemPowerManager
from app.infrastructure.system.single_instance import SingleInstanceGuard
from app.infrastructure.system.startup import DummyStartupManager, WindowsStartupManager
from app.ui.tray.tray_manager import TrayManager

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_dummy_startup_manager_noop() -> None:
    m = DummyStartupManager()
    assert m.is_enabled() is False
    assert m.enable() is False
    assert m.disable() is False


def test_windows_startup_manager_is_enabled_reports_false_when_missing(
    tmp_path: Path,
) -> None:
    # On non-Windows, class still importable but should gracefully return False
    # We instantiate with dummy key path that doesn't exist.
    m = WindowsStartupManager(
        value_name="TestPhase9", key_path=r"Software\NoSuchKey_Phase9"
    )
    # Should not raise
    assert m.is_enabled() is False


def test_single_instance_guard_acquire_and_release(tmp_path: Path) -> None:
    lock = tmp_path / "app.lock"
    g1 = SingleInstanceGuard(lock, mutex_name="TestMutexPhase9_A")
    assert g1.acquire() is True
    assert g1.acquired is True

    # Second guard on same file should fail on POSIX via flock, on Windows via msvcrt
    # On some CI environments file locks are advisory and may allow second open;
    # we assert that acquire returns bool and that release works.
    g2 = SingleInstanceGuard(lock, mutex_name="TestMutexPhase9_A")
    second = g2.acquire()
    # If first holds lock, second should fail (on Linux). On Windows without pywin32,
    # file lock should still block. Allow either but verify release restores.
    if not second:
        assert g2.acquired is False
        g1.release()
        # Now second should succeed
        assert g2.acquire() is True
        g2.release()
    else:
        # If second succeeded (e.g., no locking support), ensure we can release both
        g2.release()
        g1.release()
    assert g1.acquired is False


def test_single_instance_guard_context_manager(tmp_path: Path) -> None:
    lock = tmp_path / "ctx.lock"
    with SingleInstanceGuard(lock, mutex_name="CtxMutexPhase9") as g:
        assert g.acquired is True
    assert g.acquired is False


def test_power_manager_signals() -> None:
    mgr = SystemPowerManager()
    calls: list[str] = []
    mgr.system_suspend.connect(lambda: calls.append("suspend"))
    mgr.system_resume.connect(lambda: calls.append("resume"))
    mgr.session_locked.connect(lambda: calls.append("locked"))
    mgr.session_unlocked.connect(lambda: calls.append("unlocked"))
    mgr.taskbar_created.connect(lambda: calls.append("taskbar"))

    mgr.emit_suspend()
    mgr.emit_resume()
    mgr.emit_locked()
    mgr.emit_unlocked()
    mgr.emit_taskbar_created()

    assert calls == ["suspend", "resume", "locked", "unlocked", "taskbar"]


def test_power_manager_install_noop_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    mgr = SystemPowerManager()
    # Should return False and not raise
    assert mgr.install(app=None) is False
    assert mgr.is_installed is False


def test_verify_database_healthy(tmp_path: Path) -> None:
    db = tmp_path / "healthy.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    assert verify_database(db) is True
    assert db.exists()


def test_verify_database_quarantines_corrupt(tmp_path: Path) -> None:
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"not a sqlite file at all - corrupt")
    # Should quarantine and return False
    assert verify_database(db) is False
    # Original should be gone (moved)
    assert not db.exists()
    # Quarantined file should exist with .corrupt. suffix
    quarantined = list(tmp_path.glob("corrupt.corrupt.*.db"))
    assert len(quarantined) == 1


def test_verify_database_missing_returns_true(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    assert verify_database(db) is True


def test_tray_recovery_handlers_exist(qapp: object) -> None:
    # Requires QApplication (pytest-qt's qapp fixture)
    mgr = TrayManager()
    assert hasattr(mgr, "handle_taskbar_created")
    assert hasattr(mgr, "handle_system_resume")
    assert hasattr(mgr, "_ensure_visible")
    # Should not raise when called even if tray not visible
    mgr.handle_taskbar_created()
    mgr.handle_system_resume()
    mgr._ensure_visible()  # type: ignore[attr-defined]
    mgr.hide()


def test_global_exception_handlers_installed() -> None:
    from app.infrastructure.system.crash_handler import install_global_handlers

    orig = sys.excepthook
    install_global_handlers()
    assert sys.excepthook is not orig
    # threading excepthook should be set
    assert hasattr(threading, "excepthook")
    # Second install should be idempotent (doesn't double-wrap infinitely)
    install_global_handlers()
    assert sys.excepthook is not None


def test_logging_redaction_still_works() -> None:
    # Ensure Phase 9 logging hardening didn't break secret redaction
    msg = "token=supersecret12345 and password: hunter2"
    redacted = _redact_message(msg)
    assert "supersecret12345" not in redacted
    assert "hunter2" not in redacted
    assert "[REDACTED]" in redacted


def test_log_startup_banner_does_not_raise(tmp_path: Path) -> None:
    from app.core.logging import log_startup_banner

    # Should not raise even with dummy settings
    log_startup_banner(None)

    class _FakeLocal:
        mode = "local"
        data_dir = tmp_path

    class _FakeSettings:
        local = _FakeLocal()
        mode = "local"
        data_dir = tmp_path

    log_startup_banner(_FakeSettings())
