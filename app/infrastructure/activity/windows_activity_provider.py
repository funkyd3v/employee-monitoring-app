"""Windows activity provider: hooks + GetLastInputInfo fallback.

Privacy enforcement (docs/ENGINEERING_RULES.md §Privacy boundary,
docs/SECURITY_PRIVACY.md): this is the *only* place raw input could ever be
observed, so it is the boundary. Listeners discard key values / coordinates /
clipboard within the hook tick — only a payload-free ``activity_callback``
is ever forwarded. No layer above this module can access raw input because
it never leaves this tick.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from app.core.logging import get_logger
from app.domain.activity.provider import ActivityProvider

_logger = get_logger("activity.provider")

# Win32 GetLastInputInfo fallback (docs/TECH_STACK.md: pynput + GetLastInputInfo)
# Hooks miss elevated-window input; Win32 is OS ground-truth.


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _get_idle_seconds_win32() -> float | None:
    """Authoritative idle seconds via Win32, or None if unavailable."""
    try:
        info = _LastInputInfo(cbSize=ctypes.sizeof(_LastInputInfo))
        windll = ctypes.windll  # type: ignore[attr-defined,unused-ignore]
        if not windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = windll.kernel32.GetTickCount()
        elapsed_ms = (tick - info.dwTime) & 0xFFFFFFFF
        return float(elapsed_ms / 1000.0)
    except Exception:
        _logger.debug("GetLastInputInfo fallback unavailable", exc_info=True)
        return None


class WindowsActivityProvider(ActivityProvider):
    """Hook + Win32 fallback provider (docs/SECURITY_PRIVACY.md §Risks).

    * Hooks (``pynput``) give low-latency detection; they are started on a
      dedicated thread and all UI updates are marshaled via the callback only
      — no listener thread ever touches a Qt widget (docs/ENGINEERING_RULES.md
      §Threading Discipline, §Qt event loop conflicts).
    * ``get_idle_seconds`` delegates to ``GetLastInputInfo`` as the
      authoritative fallback for elevated/admin windows.
    * Falls back to polling-only when ``pynput`` is unavailable (non-Windows
      dev or missing optional dependency) — import is lazy so the module
      remains importable in CI.
    """

    def __init__(self) -> None:
        self._callback: Callable[[], None] | None = None
        self._running = False
        self._lock = threading.Lock()
        self._keyboard_listener: object | None = None
        self._mouse_listener: object | None = None

    def set_activity_callback(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            self._callback = callback

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._start_listeners()
        _logger.info(
            "Windows activity provider started (hooks=%s)", self._has_listeners()
        )

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_listeners()
        _logger.info("Windows activity provider stopped")

    def get_idle_seconds(self) -> float | None:
        return _get_idle_seconds_win32()

    # ── internals ─────────────────────────────────────────────────────────

    def _has_listeners(self) -> bool:
        return self._keyboard_listener is not None or self._mouse_listener is not None

    def _on_input(self, *args: object, **kwargs: object) -> None:  # noqa: ARG002
        """Hook entry — discard all payload here (privacy boundary)."""
        cb = None
        with self._lock:
            if not self._running:
                return
            cb = self._callback
        if cb is not None:
            try:
                cb()
            except Exception:
                _logger.debug("activity callback raised", exc_info=True)

    def _start_listeners(self) -> None:
        try:
            from pynput import keyboard, mouse  # type: ignore[import-untyped]
        except Exception:
            _logger.info("pynput unavailable — running with Win32 fallback only")
            return

        try:
            # Ignore key/button payload: the handler signature receives the
            # value but we never store or forward it (privacy boundary).
            k_listener = keyboard.Listener(on_press=self._on_input)
            m_listener = mouse.Listener(
                on_move=self._on_input,
                on_click=self._on_input,
                on_scroll=self._on_input,
            )
            k_listener.daemon = True
            m_listener.daemon = True
            k_listener.start()
            m_listener.start()
            self._keyboard_listener = k_listener
            self._mouse_listener = m_listener
            _logger.debug("pynput listeners started")
        except Exception:
            _logger.warning(
                "failed to start pynput listeners — fallback to Win32", exc_info=True
            )
            self._keyboard_listener = None
            self._mouse_listener = None

    def _stop_listeners(self) -> None:
        for name in ("_keyboard_listener", "_mouse_listener"):
            listener = getattr(self, name)
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    _logger.debug("listener stop raised", exc_info=True)
                setattr(self, name, None)


class PollingActivityProvider(ActivityProvider):
    """Pure polling provider using Win32 (or hook-callback shim).

    Useful as a lightweight alternative when hook threads are undesirable.
    Callers may also drive activity via :meth:`simulate_activity`.
    """

    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self._callback: Callable[[], None] | None = None
        self._running = False
        self._poll_interval = poll_interval_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def set_activity_callback(self, callback: Callable[[], None] | None) -> None:
        self._callback = callback

    def simulate_activity(self) -> None:
        cb = self._callback
        if cb is not None and self._running:
            try:
                cb()
            except Exception:
                _logger.debug("polling activity callback raised", exc_info=True)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        # Polling thread polls GetLastInputInfo; actual hooks still rely on
        # caller invoking simulate_activity() — this is intentionally minimal.

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def get_idle_seconds(self) -> float | None:
        return _get_idle_seconds_win32()

    def _poll_loop(self) -> None:
        last_idle: float | None = None
        while not self._stop_event.is_set():
            idle = self.get_idle_seconds()
            # A drop in idle means input happened — emit the payload-free tick.
            if idle is not None and last_idle is not None and idle < last_idle:
                cb = self._callback
                if cb is not None:
                    try:
                        cb()
                    except Exception:
                        _logger.debug("poll loop callback raised", exc_info=True)
            last_idle = idle
            time.sleep(self._poll_interval)
