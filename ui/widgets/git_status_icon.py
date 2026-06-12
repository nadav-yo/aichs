from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from ui.theme import ACCENT, current_theme, palette

_CACHE: dict[tuple[str, str, str], QIcon] = {}


def git_status_description(code: str, label: str = "") -> str:
    kind, _symbol, _color = _git_status_kind(code, label)
    return {
        "added": "Added",
        "conflict": "Conflict",
        "deleted": "Deleted",
        "modified": "Modified",
        "renamed": "Renamed",
        "untracked": "Untracked",
    }.get(kind, "Changed")


def git_status_icon(code: str, label: str = "", theme: str | None = None) -> QIcon:
    theme_name = theme or current_theme()
    key = (theme_name, code or "", label or "")
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    paint_git_status_badge(painter, code, label, QRectF(2, 2, 12, 12), theme=theme)
    painter.end()
    icon = QIcon(pixmap)
    _CACHE[key] = icon
    return icon


def paint_git_status_badge(
    painter: QPainter,
    code: str,
    label: str = "",
    rect: QRectF | None = None,
    *,
    theme: str | None = None,
):
    kind, _symbol, color = _git_status_kind(code, label, theme=theme)
    rect = rect or QRectF(0, 0, 8, 8)
    fill = QColor(color)
    border = QColor("#10213f") if (theme or "") != "light" else QColor("#ffffff")

    painter.setPen(QPen(border, 1))
    painter.setBrush(fill)
    painter.drawEllipse(rect)


def _git_status_kind(code: str, label: str = "", theme: str | None = None) -> tuple[str, str, str]:
    p = palette(theme)
    raw = (code or "").ljust(2)[:2]
    mark = (label or raw.strip() or "").upper()
    if raw == "??" or mark == "?":
        return "untracked", "?", p["TEXT_DIM"]
    if "U" in raw or mark == "U":
        return "conflict", "!", "#f59e0b"
    if "D" in raw or mark == "D":
        return "deleted", "-", "#ef4444"
    if "A" in raw or mark == "A":
        return "added", "+", p["SUCCESS"]
    if "R" in raw or mark == "R":
        return "renamed", ">", p["LINK"]
    if "M" in raw or mark == "M":
        return "modified", "", ACCENT
    return "changed", "*", p["TEXT_DIM"]
