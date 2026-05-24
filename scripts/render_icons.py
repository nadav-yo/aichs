"""Render SVG assets to PNG sizes for QIcon and packaging."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZES = (16, 32, 64, 128, 256, 512)


def render_svg(svg_path: Path, width: int, height: int) -> QImage:
    renderer = QSvgRenderer(str(svg_path))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return image


def render_square(svg_path: Path, size: int) -> QImage:
    return render_svg(svg_path, size, size)


def main() -> int:
    app = QApplication(sys.argv)

    icon_svg = ASSETS / "icon.svg"
    out_dir = ASSETS / "png"
    out_dir.mkdir(parents=True, exist_ok=True)

    for size in SIZES:
        png = out_dir / f"icon-{size}.png"
        render_square(icon_svg, size).save(str(png))
        print(f"wrote {png.relative_to(ROOT)}")

    logo_svg = ASSETS / "logo.svg"
    logo_png = ASSETS / "logo.png"
    render_svg(logo_svg, 1440, 320).save(str(logo_png))
    print(f"wrote {logo_png.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
