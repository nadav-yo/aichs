import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from ui.theme import apply_app_theme
from ui.main_window import MainWindow
from storage.settings import SettingsStore

ASSETS = Path(__file__).resolve().parent / "assets"


def _app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addFile(str(ASSETS / "png" / f"icon-{size}.png"))
    return icon


def main():
    SettingsStore().apply()
    app = QApplication(sys.argv)
    app.setWindowIcon(_app_icon())
    app.setStyle("Fusion")
    apply_app_theme(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
