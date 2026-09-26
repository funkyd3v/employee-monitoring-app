"""Second-instance activation (docs/UI_SPEC.md §Window behavior).

The agent is a background tray app: the window is closed far more often than
it is used, so two user gestures must always surface it again:

* a click on the tray icon — handled in :mod:`app.ui.tray.tray_manager`;
* launching the app a second time (double-clicking the desktop shortcut or
  the pinned taskbar icon) while the tray instance is still running.

The second launch must **not** open a second window: two writers on one
SQLite file is a corruption risk, which is why the single-instance guard
exists. But exiting silently would leave the user staring at a shortcut that
"does nothing". So the copy that loses the guard sends a one-line *show
yourself* request over a local socket to the copy that won it; the winner
raises its window and the loser exits quietly. The user-visible result is
identical to a normal launch.

The channel is a per-data-dir named pipe (``QLocalServer``): no network port
is opened, and two separate data dirs (e.g. a dev checkout next to the
installed agent) never signal each other.
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from app.config.constants import APP_NAME_SHORT
from app.core.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from PySide6.QtWidgets import QWidget

_logger = get_logger("system.activation")

# The only message on the wire. It is a command, not user data: no payload,
# no path, nothing that could leak into a log line.
_SHOW_MESSAGE = "show"

_CONNECT_TIMEOUT_MS = 400
_CONNECT_ATTEMPTS = 3
# The winner binds its endpoint in the same step that takes the data-dir
# lock, so the window is sub-millisecond — but a cold, loaded machine can be
# slower. A short pause between attempts covers it; the caller is a process
# with no window yet, so waiting briefly costs the user nothing.
_CONNECT_RETRY_DELAY_S = 0.15
_MAX_MESSAGE_BYTES = 256

# Win32 constants (see activation docs in this module).
_SW_RESTORE = 9
_ASFW_ANY = -1


def activation_server_name(data_dir: Path) -> str:
    """Channel name for ``data_dir`` — stable, short, and pipe-safe.

    The data dir is already per user (docs/DATA_MODEL.md §Storage layout),
    so hashing it makes the channel per user *and* per install: a second
    checkout with its own ``EM_DATA_DIR`` gets its own window instead of
    hijacking the installed agent.
    """
    digest = hashlib.sha256(str(data_dir).lower().encode("utf-8")).hexdigest()[:16]
    return f"{APP_NAME_SHORT}.activation.{digest}"


class InstanceActivator(QObject):
    """Listens for "another copy of me was launched" requests.

    :attr:`show_requested` is the whole contract: main.py connects it to the
    UI's "surface the window" entry point. The emitted token is the request
    kind, so a later build can route to a specific screen without changing
    the transport.
    """

    show_requested = Signal(str)

    def __init__(self, server_name: str, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server_name = server_name
        self._server: QLocalServer | None = None
        self._sockets: set[QLocalSocket] = set()

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def listening(self) -> bool:
        return self._server is not None and self._server.isListening()

    def start(self) -> bool:
        """Start listening. Idempotent, and never raises.

        Activation is a convenience: a failure here degrades to the old
        "second launch exits silently" behavior, so it must not take the
        agent down.
        """
        if self.listening:
            return True

        server = QLocalServer(self)
        # Owner-only endpoint: another local user must not be able to raise
        # someone else's monitoring window.
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._listen(server):
            # A hard-killed instance can leave a stale endpoint behind (a
            # Unix socket file). We only get here while holding the
            # single-instance guard, so nobody else can be listening on it:
            # drop the stale endpoint and try once more.
            QLocalServer.removeServer(self._server_name)
            if not self._listen(server):
                _logger.warning("activation channel unavailable: %s", self._server_name)
                return False

        server.newConnection.connect(self._on_new_connection)
        self._server = server
        _logger.info("activation channel listening on %s", self._server_name)
        return True

    def stop(self) -> None:
        """Stop listening and release the endpoint (idempotent)."""
        for socket in tuple(self._sockets):
            self._sockets.discard(socket)
            socket.close()

        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        try:
            QLocalServer.removeServer(self._server_name)
        except Exception:
            _logger.debug("activation endpoint cleanup failed", exc_info=True)

    def _listen(self, server: QLocalServer) -> bool:
        try:
            return bool(server.listen(self._server_name))
        except Exception:
            _logger.debug("listen(%s) raised", self._server_name, exc_info=True)
            return False

    @Slot()
    def _on_new_connection(self) -> None:
        server = self._server
        if server is None:
            return
        while True:
            socket = server.nextPendingConnection()
            if socket is None:
                return
            # No ``deleteLater`` anywhere on this path: the socket is a child
            # of the server, so it is freed with it, and a deferred-delete
            # event that outlives its object is a use-after-free waiting for
            # the next event loop to run.
            self._sockets.add(socket)
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        # Bounded read: the channel carries a single fixed command, so a
        # longer payload is not ours — decode defensively and ignore noise.
        raw = socket.read(_MAX_MESSAGE_BYTES)
        if raw.isEmpty():
            return
        payload = bytes(raw.data()).decode("utf-8", "replace")
        if not payload:
            return
        _logger.info("activation request received: %s", payload)
        self.show_requested.emit(payload)

    def _on_disconnected(self, socket: QLocalSocket) -> None:
        # Drop our reference only — the (closed) socket stays owned by the
        # server. One idle socket per launch is a few hundred bytes; a
        # correctness hazard is not worth reclaiming.
        self._sockets.discard(socket)


def request_show(
    server_name: str,
    *,
    attempts: int = _CONNECT_ATTEMPTS,
    timeout_ms: int = _CONNECT_TIMEOUT_MS,
) -> bool:
    """Ask the instance owning ``server_name`` to surface its window.

    Blocking on purpose: the caller is a second copy of the agent that is
    about to exit, so it has no UI to keep responsive, and the delivery
    result is the only thing it needs. Retried a few times to cover a
    primary that has taken the lock but not finished binding the socket.

    Returns ``True`` when the request was written to the running instance.
    """
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        # The socket is owned by this scope and freed with it. Do not
        # ``deleteLater()`` a socket whose event loop may never run again:
        # the queued delete event outlives the object and corrupts the next
        # loop that does.
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(timeout_ms):
            # The launching copy holds the foreground (the user just
            # activated the shortcut); hand that permission to the running
            # instance so its window is allowed to come to the front.
            _allow_foreground_requests()
            socket.write(_SHOW_MESSAGE.encode("utf-8"))
            delivered = socket.waitForBytesWritten(timeout_ms)
            socket.close()
            if delivered:
                _logger.info("activation request delivered to %s", server_name)
                return True
        else:
            socket.abort()
        _logger.debug(
            "activation attempt %s/%s to %s failed", attempt, attempts, server_name
        )
        if attempt < attempts:
            time.sleep(_CONNECT_RETRY_DELAY_S)
    return False


def bring_to_front(window: QWidget) -> None:
    """Best-effort Win32 foreground activation for a window the OS buried.

    Windows only lets the foreground process change the foreground, so a
    window raised from a tray click — or from another process that just handed
    us focus — can land *behind* the windows the user was already looking at.
    Qt's own ``activateWindow()`` handles the common case; this is the extra
    nudge for the rest. Every step is best effort: on other platforms, and on
    any Win32 error, the Qt-level raise/activate stands on its own.
    """
    if sys.platform != "win32":
        return
    try:
        user32 = _user32()
        hwnd = int(window.winId())
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)
        user32.SetActiveWindow(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        _logger.debug("foreground activation failed", exc_info=True)


def _user32() -> Any:
    """``user32`` handle, resolved lazily so the module stays importable.

    ``getattr`` rather than ``ctypes.windll``: typeshed only declares
    ``windll`` for Windows targets, so a plain attribute access would fail
    type checking on the Linux/macOS dev machines this repo is linted on.
    """
    return getattr(ctypes, "windll").user32  # noqa: B009 — see docstring


def _allow_foreground_requests() -> None:
    """Let any process take the foreground away from us (``ASFW_ANY``)."""
    if sys.platform != "win32":
        return
    try:
        _user32().AllowSetForegroundWindow(_ASFW_ANY)
    except Exception:
        _logger.debug("AllowSetForegroundWindow failed", exc_info=True)


__all__ = [
    "InstanceActivator",
    "activation_server_name",
    "bring_to_front",
    "request_show",
]
