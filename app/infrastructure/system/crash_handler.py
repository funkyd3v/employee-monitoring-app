# mypy: ignore-errors
"""Crash recovery — global exception handling and DB integrity guard.

Phase 9 deliverables:
* ``install_global_handlers`` — route *all* uncaught exceptions (main thread,
  worker threads, Qt messages) to the hardened logger instead of crashing
  silently or spamming stderr.
* ``verify_database`` — lightweight SQLite ``PRAGMA integrity_check`` on
  startup; on corruption the file is quarantined and the app recreates it
  (persisted-state drives recovery, not memory — but a corrupt file must be
  fail-safe, not fatal).

Both are best-effort and never raise — the app must survive their failure.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from app.core.logging import get_logger

_logger = get_logger("system.crash")


def install_global_handlers() -> None:
    """Install sys/threading/Qt message handlers — idempotent."""

    # ── sys.excepthook (main thread) ────────────────────────────────────
    _orig_excepthook = sys.excepthook

    def _excepthook(
        exc_type: type[BaseException], exc: BaseException, tb: object
    ) -> None:
        # KeyboardInterrupt / SystemExit are intentional — don't log as crash
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            _orig_excepthook(exc_type, exc, tb)
            return
        _logger.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        # Still call original so stderr in dev consoles sees it
        try:
            _orig_excepthook(exc_type, exc, tb)
        except Exception:  # noqa: S110
            pass

    sys.excepthook = _excepthook

    # ── threading.excepthook (worker threads, Python 3.8+) ──────────────
    _orig_thread_hook = getattr(threading, "excepthook", None)

    def _thread_hook(args: threading.ExceptHookArgs) -> None:  # type: ignore[no-untyped-def]
        if isinstance(args.exc_value, (KeyboardInterrupt, SystemExit)):
            if _orig_thread_hook is not None:
                _orig_thread_hook(args)
            return
        _logger.critical(
            "uncaught thread exception in %s: %s",
            args.thread.name if args.thread else "unknown",
            args.exc_value,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        if _orig_thread_hook is not None:
            try:
                _orig_thread_hook(args)
            except Exception:  # noqa: S110
                pass

    try:
        threading.excepthook = _thread_hook  # type: ignore[assignment]
    except Exception:  # noqa: S110
        pass

    # ── Qt message handler — route qWarning/qCritical to logging ────────
    try:
        from PySide6.QtCore import qInstallMessageHandler

        def _qt_handler(msg_type: object, ctx: object, msg: str) -> None:  # type: ignore[no-untyped-def]
            # Map QtMsgType (0=Debug,1=Warning,2=Critical,3=Fatal,4=Info)
            try:
                level = int(msg_type) if msg_type is not None else 1  # type: ignore[arg-type]
            except Exception:
                level = 1
            if level == 0:
                _logger.debug("Qt: %s", msg)
            elif level == 1:
                _logger.warning("Qt: %s", msg)
            elif level == 2:
                _logger.error("Qt: %s", msg)
            elif level == 3:
                _logger.critical("Qt fatal: %s", msg)
            else:
                _logger.info("Qt: %s", msg)

        qInstallMessageHandler(_qt_handler)
    except Exception:
        _logger.debug("qInstallMessageHandler not installed", exc_info=True)


def verify_database(db_path: Path, *, quarantine_dir: Path | None = None) -> bool:
    """Run ``PRAGMA integrity_check`` on ``db_path``.

    If the check fails and ``db_path`` exists and is non-empty, the file (and
    its WAL/SHM sidecars) is moved to ``quarantine_dir`` (or next to the DB
    with a timestamp suffix) so the app can recreate a clean DB on next
    ``migrate``. Returns True when the DB is healthy or absent, False when it
    was quarantined.
    """
    if not db_path.exists() or db_path.stat().st_size == 0:
        return True

    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            row = cur.fetchone()
            ok = row is not None and row[0] == "ok"
            if ok:
                _logger.debug("database integrity ok: %s", db_path)
                return True
            _logger.error("database integrity failed: %s result=%s", db_path, row)
        finally:
            conn.close()
    except Exception as exc:
        _logger.warning(
            "integrity check raised for %s: %s", db_path, exc, exc_info=True
        )
        # Treat check failure as corruption — quarantine.
        ok = False

    if not ok:
        _quarantine(db_path, quarantine_dir)
        return False
    return True


def _quarantine(db_path: Path, quarantine_dir: Path | None) -> None:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if quarantine_dir is not None:
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        dest = quarantine_dir / f"{db_path.stem}.corrupt.{stamp}{db_path.suffix}"
    else:
        dest = db_path.with_name(f"{db_path.stem}.corrupt.{stamp}{db_path.suffix}")
    try:
        db_path.rename(dest)
        _logger.critical("quarantined corrupt database %s -> %s", db_path, dest)
    except Exception as exc:
        _logger.error(
            "failed to quarantine corrupt DB %s: %s", db_path, exc, exc_info=True
        )
        # Fallback: try to remove so next migrate can recreate
        try:
            db_path.unlink(missing_ok=True)
        except Exception:
            _logger.debug("failed to unlink corrupt DB", exc_info=True)
    # Also quarantine WAL/SHM sidecars
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        q_dest = Path(str(dest) + suffix)
        if sidecar.exists():
            try:
                sidecar.rename(q_dest)
            except Exception:
                try:
                    sidecar.unlink(missing_ok=True)
                except Exception:
                    pass


__all__ = ["install_global_handlers", "verify_database"]
