# mypy: ignore-errors
"""Windows power / session event handling.

Engineering rules (docs/ENGINEERING_RULES.md § Sleep / lock / resume):
* Never silently classify system sleep as active work.
* Recalculate activity state on resume; don't generate false activity.
* Preserve timestamps; keep timeline consistent — no gap-filling guess.
* Work is performed via queued Qt slots — the worker never touches UI.

Windows delivers these as native messages:
* ``WM_POWERBROADCAST`` (``PBT_APMSUSPEND`` / ``PBT_APMRESUMESUSPEND``)
* ``WM_WTSSESSION_CHANGE`` (``WTS_SESSION_LOCK`` / ``WTS_SESSION_UNLOCK``)
* ``TaskbarCreated`` (registered window message — Explorer restarted)

Qt does not surface these as high-level signals, so we install a
``QAbstractNativeEventFilter``. On non-Windows or when ``pywin32`` is absent
the filter degrades to a no-op — the :class:`SystemPowerManager` remains
usable headless and tests drive it via ``emit_*`` shims.

The manager is a ``QObject`` with Qt signals; ``main.py`` connects those
signals to the worker supervisors' existing
``handle_system_suspend`` / ``handle_system_resume`` slots with
``QueuedConnection``, preserving threading discipline.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from app.core.logging import get_logger

_logger = get_logger("system.power")

if TYPE_CHECKING:
    from PySide6.QtCore import QAbstractNativeEventFilter  # noqa: F401


class SystemPowerManager(QObject):
    """Emits power/session transitions as Qt signals.

    Signals are the public API — connect them to any worker/service slot.
    The ``emit_*`` helpers are public so tests (and the native filter) can
    drive transitions without a real Windows message.
    """

    # Session / power
    system_suspend = Signal()
    system_resume = Signal()
    session_locked = Signal()
    session_unlocked = Signal()
    # Explorer
    taskbar_created = Signal()

    def __init__(self, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._filter: object | None = None
        self._taskbar_msg_id: int | None = None
        self._installed = False

    # ── Public shims (tests / filter call these) ────────────────────────

    def emit_suspend(self) -> None:
        _logger.info("system suspend detected")
        self.system_suspend.emit()

    def emit_resume(self) -> None:
        _logger.info("system resume detected")
        self.system_resume.emit()

    def emit_locked(self) -> None:
        _logger.info("session locked")
        self.session_locked.emit()

    def emit_unlocked(self) -> None:
        _logger.info("session unlocked")
        self.session_unlocked.emit()

    def emit_taskbar_created(self) -> None:
        _logger.info("taskbar created — tray will be re-shown")
        self.taskbar_created.emit()

    # ── Installation ────────────────────────────────────────────────────

    def install(self, app: object | None = None) -> bool:
        """Install the native event filter for this ``app``.

        Returns True if a real Windows filter was installed. No-op on non-Windows
        or when Qt/Win32 is unavailable — the manager stays usable via shims.
        """
        if self._installed:
            return True

        if sys.platform != "win32":
            _logger.debug("power filter not installed — not Windows")
            return False

        try:
            # Resolve imported win32 constants lazily so module stays importable
            # without pywin32 on CI.
            filt = _PowerEventFilter(self)
            # ``app`` may be QApplication instance; if None, use QCoreApplication.instance()
            if app is None:
                from PySide6.QtCore import QCoreApplication

                app = QCoreApplication.instance()
            if app is not None and hasattr(app, "installNativeEventFilter"):
                app.installNativeEventFilter(filt)  # type: ignore[attr-defined]
                self._filter = filt
                self._installed = True
                # Resolve TaskbarCreated msg id for fast path compare
                try:
                    import ctypes

                    ctypes.windll.user32.RegisterWindowMessageW.argtypes = [
                        ctypes.c_wchar_p
                    ]
                    ctypes.windll.user32.RegisterWindowMessageW.restype = ctypes.c_uint
                    self._taskbar_msg_id = int(
                        ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")
                    )
                    # Give filter access
                    if isinstance(filt, _PowerEventFilter):
                        filt.set_taskbar_msg(self._taskbar_msg_id)
                except Exception:
                    _logger.debug("TaskbarCreated registration failed", exc_info=True)
                _logger.info("power native event filter installed")
                return True
        except Exception:
            _logger.debug("power filter install failed", exc_info=True)
        return False

    def uninstall(self, app: object | None = None) -> None:
        if not self._installed or self._filter is None:
            return
        try:
            if app is None:
                from PySide6.QtCore import QCoreApplication

                app = QCoreApplication.instance()
            if app is not None and hasattr(app, "removeNativeEventFilter"):
                app.removeNativeEventFilter(self._filter)  # type: ignore[attr-defined]
        except Exception:
            _logger.debug("power filter uninstall failed", exc_info=True)
        self._filter = None
        self._installed = False

    @property
    def is_installed(self) -> bool:
        return self._installed


# ── Native filter ──────────────────────────────────────────────────────


class _PowerEventFilter:  # type: ignore[no-redef]
    """QAbstractNativeEventFilter shim that translates native msgs."""

    def __init__(self, manager: SystemPowerManager) -> None:
        # Import here to keep module importable headless
        from PySide6.QtCore import QAbstractNativeEventFilter

        class _Impl(QAbstractNativeEventFilter):
            def __init__(
                self, mgr: SystemPowerManager, outer: _PowerEventFilter
            ) -> None:
                super().__init__()
                self._mgr = mgr
                self._outer = outer

            def nativeEventFilter(
                self, event_type: bytes, message: object
            ) -> tuple[bool, int]:  # noqa: N802
                try:
                    return self._outer._handle(event_type, message)
                except Exception:
                    _logger.debug("nativeEventFilter raised", exc_info=True)
                    return (False, 0)

        self._impl = _Impl(manager, self)
        self._manager = manager
        self._taskbar_msg: int | None = None

    def set_taskbar_msg(self, msg_id: int) -> None:
        self._taskbar_msg = msg_id

    # QAbstractNativeEventFilter duck: delegate
    def nativeEventFilter(self, event_type: bytes, message: object) -> tuple[bool, int]:  # noqa: N802
        return self._impl.nativeEventFilter(event_type, message)

    # Qt will call via impl; this is the real handler
    def _handle(self, event_type: bytes, message: object) -> tuple[bool, int]:
        # Only handle Windows messages
        if not event_type.startswith(b"windows"):
            return (False, 0)
        try:
            import ctypes

            # message is a pointer to MSG struct
            # We can cast to MSG and inspect message id / wParam
            # This avoids pulling in win32gui for the hot path.
            msg_id = None
            wparam = None
            lparam = None
            try:
                # MSG layout: HWND, UINT msg, WPARAM, LPARAM, DWORD time, POINT
                # We only need msg/wParam; parse via ctypes.
                # message is int pointer on Python
                class _MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", ctypes.c_void_p),
                        ("message", ctypes.c_uint),
                        ("wParam", ctypes.c_void_p),
                        ("lParam", ctypes.c_void_p),
                        ("time", ctypes.c_ulong),
                        ("pt_x", ctypes.c_long),
                        ("pt_y", ctypes.c_long),
                    ]

                m = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents  # type: ignore[arg-type]
                msg_id = int(m.message)
                wparam = int(m.wParam) if m.wParam is not None else 0
                lparam = int(m.lParam) if m.lParam is not None else 0
            except Exception:
                return (False, 0)

            # TaskbarCreated — must be checked before other ids as it's dynamic
            if self._taskbar_msg is not None and msg_id == self._taskbar_msg:
                self._manager.emit_taskbar_created()
                return (False, 0)

            # WM_POWERBROADCAST = 0x0218
            WM_POWERBROADCAST = 0x0218
            PBT_APMSUSPEND = 0x0004
            PBT_APMRESUMESUSPEND = 0x0007

            # WM_WTSSESSION_CHANGE = 0x02B1
            WM_WTSSESSION_CHANGE = 0x02B1
            WTS_SESSION_LOCK = 0x7
            WTS_SESSION_UNLOCK = 0x8

            if msg_id == WM_POWERBROADCAST:
                if wparam == PBT_APMSUSPEND:
                    self._manager.emit_suspend()
                elif wparam == PBT_APMRESUMESUSPEND:
                    self._manager.emit_resume()
                return (False, 0)

            if msg_id == WM_WTSSESSION_CHANGE:
                if wparam == WTS_SESSION_LOCK:
                    self._manager.emit_locked()
                elif wparam == WTS_SESSION_UNLOCK:
                    self._manager.emit_unlocked()
                return (False, 0)

        except Exception:
            _logger.debug("power handle failed", exc_info=True)
        return (False, 0)

    # Make this object look like a QAbstractNativeEventFilter to Qt
    def __getattr__(self, name: str) -> object:
        return getattr(self._impl, name)


# Fallback no-op filter type when Qt's QAbstractNativeEventFilter isn't needed for typing
try:
    from PySide6.QtCore import QAbstractNativeEventFilter as _RealFilter  # noqa: F401

    _HAS_QT_FILTER = True
except Exception:
    _HAS_QT_FILTER = False


__all__ = ["SystemPowerManager"]
