import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPainter, QPainterPath, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QLabel

from config import AVATARS_DIR
from storage.settings import SettingsStore

AVATAR_SIZE = 36
_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "avatars"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_cache: dict[str, QPixmap] = {}


def list_builtin_avatars() -> list[str]:
    return sorted(p.stem for p in _ASSETS.glob("*.svg"))


def portrait_source(role: str) -> str:
    data = SettingsStore().load()
    return data.get(f"avatar_{role}", role)


def clear_cache() -> None:
    _cache.clear()


def persist_portrait(source: str, role: str) -> str:
    """Return the settings value for a portrait (built-in name or copied custom path)."""
    if not source or source in list_builtin_avatars():
        return source or role
    src = Path(source)
    if not src.is_file():
        return role
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    dest = AVATARS_DIR / f"{role}{src.suffix.lower()}"
    shutil.copy2(src, dest)
    return str(dest)


def avatar_pixmap(source: str, size: int = AVATAR_SIZE) -> QPixmap:
    key = f"{source}:{size}"
    if key in _cache:
        return _cache[key]

    path = Path(source)
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(0, 0, size, size)
    painter.setClipPath(clip)

    if path.is_file():
        if path.suffix.lower() == ".svg":
            QSvgRenderer(str(path)).render(painter)
        else:
            pix = QPixmap(str(path)).scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, pix)
    else:
        svg = _ASSETS / f"{source}.svg"
        if not svg.exists():
            svg = _ASSETS / "human.svg"
        QSvgRenderer(str(svg)).render(painter)

    painter.end()
    _cache[key] = image
    return image


def avatar_label(role: str, size: int = AVATAR_SIZE) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(avatar_pixmap(portrait_source(role), size))
    lbl.setFixedSize(QSize(size, size))
    lbl.setStyleSheet("background:transparent;")
    return lbl
