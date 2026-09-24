# mypy: ignore-errors
"""Startup behavior — autostart on Windows login.

Spec (docs/TESTING_AND_DOD.md Phase 9, docs/TECH_STACK.md Installer):
* The agent should be able to register itself to start when the user logs in
  via ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``.
* This module never raises on failure — it logs and returns ``False`` so the
  app remains functional when the registry is unavailable (CI, limited user).

The abstraction is intentionally small: UI/settings code talks to
:class:`StartupManager`, never to ``winreg`` directly (layering rule).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

from app.core.logging import get_logger

_logger = get_logger("system.startup")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "EmployeeMonitoring"


class StartupManager(Protocol):
    """Whether the agent starts with Windows."""

    def is_enabled(self) -> bool: ...
    def enable(self, executable: str | None = None) -> bool: ...
    def disable(self) -> bool: ...


class WindowsStartupManager:
    """Registry-backed implementation for Windows.

    ``executable`` is the command written to the Run value. When ``None``
    the current ``sys.executable`` is used (packaged build) or a quoted
    ``python -m`` fallback in development.
    """

    def __init__(
        self,
        value_name: str = _VALUE_NAME,
        key_path: str = _RUN_KEY,
    ) -> None:
        self._value_name = value_name
        self._key_path = key_path

    def is_enabled(self) -> bool:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key_path) as key:
                try:
                    winreg.QueryValueEx(key, self._value_name)
                    return True
                except FileNotFoundError:
                    return False
        except Exception:
            _logger.debug("is_enabled check failed", exc_info=True)
            return False

    def enable(self, executable: str | None = None) -> bool:
        try:
            import winreg

            cmd = executable or _default_command()
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, self._value_name, 0, winreg.REG_SZ, cmd)
            _logger.info("autostart enabled: %s", self._value_name)
            return True
        except Exception as exc:
            _logger.warning("failed to enable autostart: %s", exc, exc_info=True)
            return False

    def disable(self) -> bool:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                try:
                    winreg.DeleteValue(key, self._value_name)
                except FileNotFoundError:
                    pass
            _logger.info("autostart disabled: %s", self._value_name)
            return True
        except Exception as exc:
            _logger.warning("failed to disable autostart: %s", exc, exc_info=True)
            return False


class DummyStartupManager:
    """No-op for non-Windows / tests. Always reports disabled."""

    def is_enabled(self) -> bool:
        return False

    def enable(self, executable: str | None = None) -> bool:  # noqa: ARG002
        _logger.debug("DummyStartupManager.enable — no-op on this platform")
        return False

    def disable(self) -> bool:
        _logger.debug("DummyStartupManager.disable — no-op on this platform")
        return False


def build_startup_manager() -> StartupManager:
    """Factory used by the container — platform-selects the implementation."""
    if sys.platform == "win32":
        try:
            import winreg  # noqa: F401

            return WindowsStartupManager()
        except Exception:  # noqa: S110
            pass
    return DummyStartupManager()


def _default_command() -> str:
    """Build the registry command for the current launch.

    Packaged exe: quoted path. Dev run: ``python -m app.main``-style.
    """
    exe = Path(sys.executable)
    # When running from a venv python, quote the full exe + module launch.
    # The installer will overwrite this with the real installed path on Windows.
    if exe.name.lower().endswith("python.exe") or exe.name == "python":
        # Development: use the python executable + module.
        # Keep it quoted in case the venv path contains spaces.
        return f'"{exe}" -m app.main --minimized'
    return f'"{exe}" --minimized'


__all__ = [
    "DummyStartupManager",
    "StartupManager",
    "WindowsStartupManager",
    "build_startup_manager",
]
