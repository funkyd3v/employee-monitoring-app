"""Resource resolution for bundled fonts, icons, and application artwork."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon


def _bundle_root() -> Path | None:
    value = getattr(sys, "_MEIPASS", None)
    return Path(value) if isinstance(value, str) else None


def package_asset_path(*parts: str) -> Path:
    package_root = Path(__file__).resolve().parents[1] / "assets"
    bundle_root = _bundle_root()
    candidates = [package_root.joinpath(*parts)]
    if bundle_root is not None:
        candidates.insert(0, bundle_root.joinpath("app", "ui", "assets", *parts))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return package_root.joinpath(*parts)


def application_asset_path(relative_path: str) -> Path:
    bundle_root = _bundle_root()
    project_root = Path(__file__).resolve().parents[3]
    candidates = [
        project_root / "assets" / relative_path,
        package_asset_path(relative_path),
    ]
    if bundle_root is not None:
        candidates.insert(0, bundle_root / "assets" / relative_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def font_asset_paths() -> tuple[Path, ...]:
    filenames = (
        "Inter-Regular.ttf",
        "Inter-Medium.ttf",
        "Inter-SemiBold.ttf",
        "Inter-Bold.ttf",
    )
    return tuple(package_asset_path("fonts", filename) for filename in filenames)


@lru_cache(maxsize=1)
def application_icon() -> QIcon:
    path = application_asset_path("icons/app.ico")
    if not path.is_file():
        return QIcon()
    icon = QIcon(str(path))
    return icon if not icon.isNull() else QIcon()
