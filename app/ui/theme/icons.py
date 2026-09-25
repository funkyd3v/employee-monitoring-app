"""Theme-aware rendering for the bundled Lucide SVG icons."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app.ui.theme.assets import package_asset_path


@lru_cache(maxsize=256)
def _render_icon(name: str, color: str, size: int) -> QPixmap:
    path = package_asset_path("icons", "lucide", f"{name}.svg")
    if not path.is_file():
        return QPixmap()
    color_name = QColor(color).name()
    svg = path.read_bytes().replace(b"currentColor", color_name.encode("ascii"))
    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        return QPixmap()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def themed_icon(name: str, color: str, size: int = 16) -> QIcon:
    """Return a local SVG icon recolored for the active palette."""
    pixmap = _render_icon(name, QColor(color).name(), max(1, size))
    return QIcon(pixmap) if not pixmap.isNull() else QIcon()
