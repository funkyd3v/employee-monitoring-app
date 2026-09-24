# mypy: ignore-errors
"""Single-instance guard — prevents concurrent DB corruption.

Phase 9 crash-recovery / reliability: two concurrent agents writing to the same
SQLite file can corrupt it. The guard uses a file lock (portable) plus a
named Windows mutex when ``pywin32`` is present. It is advisory — failure to
acquire never crashes the app; the caller decides whether to exit or warn.

Usage (in ``main.py`` lifecycle):

    guard = SingleInstanceGuard(data_dir / "app.lock")
    if not guard.acquire():
        logger.error("Another instance is already running — exiting.")
        return 1
    # ... run app ...
    guard.release()

The lock file lives in the per-user data dir, not the install dir
(docs/DATA_MODEL.md §Storage layout).
"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.logging import get_logger

_logger = get_logger("system.single_instance")


class SingleInstanceGuard:
    """File-lock + optional named mutex guard."""

    def __init__(self, lock_path: Path, mutex_name: str = "EmployeeMonitoringAgent") -> None:
        self._lock_path = lock_path
        self._mutex_name = mutex_name
        self._file: object | None = None
        self._mutex: object | None = None
        self._acquired = False

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @property
    def acquired(self) -> bool:
        return self._acquired

    def acquire(self) -> bool:
        """Try to acquire the guard. Returns True if we are the sole instance."""
        if self._acquired:
            return True

        # 1) File lock — portable. On Windows use msvcrt.locking.
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            # Open without truncating existing content atomically
            f = open(self._lock_path, "a+")  # noqa: SIM115, PTH123
            self._file = f
            if os.name == "nt":
                try:
                    import msvcrt

                    f.seek(0)
                    # _locking with LK_NBLCK — non-blocking
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    _logger.warning("single-instance file lock held: %s", exc)
                    try:
                        f.close()
                    except Exception:
                        pass
                    self._file = None
                    return False
            else:
                try:
                    import fcntl

                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    _logger.warning("single-instance file lock held: %s", exc)
                    try:
                        f.close()
                    except Exception:
                        pass
                    self._file = None
                    return False
            # Write pid for diagnostics (best-effort)
            try:
                f.seek(0)
                f.truncate()
                f.write(str(os.getpid()))
                f.flush()
            except Exception:
                _logger.debug("failed to write pid to lock file", exc_info=True)
        except Exception as exc:
            _logger.warning("failed to create lock file %s: %s", self._lock_path, exc, exc_info=True)
            # Don't block startup if we can't create the lock file — log and allow.
            self._file = None
            # Still try mutex; if no mutex either, treat as acquired (can't guard)
            # but log.
            _logger.info("proceeding without file lock — single-instance not enforced")
            self._acquired = True
            return True

        # 2) Named mutex on Windows — stronger cross-session check
        if os.name == "nt":
            try:
                import win32api  # type: ignore[import-untyped]
                import win32event  # type: ignore[import-untyped]
                import winerror  # type: ignore[import-untyped]

                mutex = win32event.CreateMutex(None, False, self._mutex_name)
                last_err = win32api.GetLastError()
                if last_err == winerror.ERROR_ALREADY_EXISTS:
                    _logger.warning("single-instance mutex already held")
                    # Release file lock we just took
                    self.release()
                    return False
                self._mutex = mutex
            except Exception:
                _logger.debug("named mutex unavailable — file lock is sole guard", exc_info=True)
                self._mutex = None

        self._acquired = True
        _logger.info("single-instance guard acquired %s", self._lock_path)
        return True

    def release(self) -> None:
        """Release the guard (idempotent)."""
        if not self._acquired and self._file is None and self._mutex is None:
            return

        # Release file lock
        if self._file is not None:
            f = self._file
            self._file = None
            try:
                if os.name == "nt":
                    try:
                        import msvcrt

                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                    except Exception:
                        _logger.debug("msvcrt unlock failed", exc_info=True)
                else:
                    try:
                        import fcntl

                        fcntl.flock(f, fcntl.LOCK_UN)  # type: ignore[attr-defined]
                    except Exception:
                        _logger.debug("fcntl unlock failed", exc_info=True)
                try:
                    f.close()
                except Exception:
                    pass
                # Keep the file on disk for next run, or remove? Keep it.
            except Exception:
                _logger.debug("file release failed", exc_info=True)

        # Release mutex
        if self._mutex is not None:
            try:
                import win32event  # type: ignore[import-untyped]

                win32event.ReleaseMutex(self._mutex)  # type: ignore[attr-defined]
            except Exception:
                _logger.debug("mutex release failed", exc_info=True)
            try:
                import win32api  # type: ignore[import-untyped]

                win32api.CloseHandle(self._mutex)
            except Exception:
                _logger.debug("mutex close failed", exc_info=True)
            self._mutex = None

        self._acquired = False
        _logger.info("single-instance guard released")

    def __enter__(self) -> SingleInstanceGuard:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


__all__ = ["SingleInstanceGuard"]
