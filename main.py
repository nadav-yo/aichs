import sys
import multiprocessing
from importlib import resources
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from ui.theme import apply_app_theme
from ui.main_window import MainWindow
from storage.settings import SettingsStore

APP_USER_MODEL_ID = "studio.aichs.desktop"


def _assets_dir() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    source_assets = root / "assets"
    if source_assets.exists():
        return source_assets
    return Path(str(resources.files("assets")))


ASSETS = _assets_dir()


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID,
        )
    except Exception:
        pass


def _app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addFile(str(ASSETS / "png" / f"icon-{size}.png"))
    return icon


def main():
    multiprocessing.freeze_support()
    SettingsStore().apply()
    _set_windows_app_id()
    app = QApplication(sys.argv)
    icon = _app_icon()
    app.setWindowIcon(icon)
    app.setStyle("Fusion")
    apply_app_theme(app)
    w = MainWindow()
    w.setWindowIcon(icon)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
