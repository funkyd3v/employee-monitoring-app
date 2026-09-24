"""Image processing helpers: idle border, checksum, validation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageOps
from PIL.Image import Image

# Red border for idle captures (docs/ENGINEERING_RULES.md, docs/UI_SPEC.md Danger)
IDLE_BORDER_COLOR = "#EF4444"
IDLE_BORDER_WIDTH = 4


def apply_idle_border(image: Image, *, idle: bool) -> Image:
    """Return image with red border if ``idle`` else unchanged.

    Does not mutate the input image.
    """
    if not idle:
        return image
    # Expand border — visible red frame per spec "red border on idle captures"
    return ImageOps.expand(image, border=IDLE_BORDER_WIDTH, fill=IDLE_BORDER_COLOR)


def compute_checksum(file_path: Path) -> str:
    """SHA256 checksum of file at ``file_path`` (hex)."""
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_image(file_path: Path) -> bool:
    """Return True if ``file_path`` is a readable JPEG."""
    try:
        with PILImage.open(file_path) as img:
            img.verify()
        return file_path.stat().st_size > 0
    except Exception:
        return False


def encode_jpeg(image: Image, dest: Path, *, quality: int = 85) -> None:
    """Encode ``image`` as JPEG to ``dest``."""
    # Ensure RGB for JPEG
    if image.mode in ("RGBA", "LA"):
        background = PILImage.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.save(dest, format="JPEG", quality=quality, optimize=True)
