"""Screenshot provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from PIL.Image import Image


class ScreenshotProvider(ABC):
    """Contract for platform screenshot capture.

    Backend-readiness: UI/services never import a concrete provider;
    container wires ``MssScreenshotProvider`` vs ``DummyScreenshotProvider``.
    """

    @abstractmethod
    def capture(self) -> Image:
        """Capture the full primary screen as a PIL Image.

        Raises ``ProviderUnavailableError`` if capture is impossible
        (e.g. headless CI, permission denied).
        """

    @abstractmethod
    def capture_to_file(self, dest: Path) -> Path:
        """Capture and encode to ``dest`` (JPEG) — convenience for pipeline."""
