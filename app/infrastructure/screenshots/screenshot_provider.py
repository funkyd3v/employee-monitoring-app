"""Screenshot providers: MSS (+ Pillow) for Windows, Dummy for tests/CI."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from PIL import Image as PILImage

from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.domain.screenshots.processing import encode_jpeg
from app.domain.screenshots.provider import ScreenshotProvider

if TYPE_CHECKING:
    from pathlib import Path

    from PIL.Image import Image

_logger = get_logger("screenshots.provider")


class MssScreenshotProvider(ScreenshotProvider):
    """Full-screen capture via ``mss`` (fast, no window handle needed).

    Pillow is used only for border/encode steps in the service layer;
    this provider just captures the raw screen.
    """

    def capture(self) -> Image:
        try:
            import mss
            import mss.tools  # ensure backend available
        except Exception as exc:
            raise ProviderUnavailableError(f"mss unavailable: {exc}") from exc

        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(monitor)
                # mss → PIL
                img = PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                return img
        except Exception as exc:
            raise ProviderUnavailableError(f"screenshot capture failed: {exc}") from exc

    def capture_to_file(self, dest: Path) -> Path:
        img = self.capture()
        encode_jpeg(img, dest)
        return dest


class DummyScreenshotProvider(ScreenshotProvider):
    """Deterministic 320×180 test image (no display required).

    ``fail_next`` flips a single ``ProviderUnavailableError`` for failure-mode
    tests.
    """

    def __init__(self, *, color: tuple[int, int, int] = (23, 26, 33)) -> None:
        self._color = color
        self._capture_count = 0
        self.fail_next = False
        self.last_capture_id: str | None = None

    @property
    def capture_count(self) -> int:
        return self._capture_count

    def capture(self) -> Image:
        if self.fail_next:
            self.fail_next = False
            raise ProviderUnavailableError("dummy capture failure (injected)")
        self._capture_count += 1
        img = PILImage.new("RGB", (320, 180), self._color)
        self.last_capture_id = uuid.uuid4().hex[:8]
        return img

    def capture_to_file(self, dest: Path) -> Path:
        img = self.capture()
        encode_jpeg(img, dest)
        return dest
