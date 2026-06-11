import sys
import argparse
import multiprocessing
from importlib import resources
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from ui.theme import apply_app_theme
from ui.main_window import MainWindow
from ui.performance_monitor import EventLoopStallMonitor
from storage.settings import SettingsStore

APP_USER_MODEL_ID = "studio.aichs.desktop"


def _assets_dir() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    source_assets = root / "assets"
    if source_assets.exists():
        return source_assets
    return Path(str(resources.files("assets")))


ASSETS = _assets_dir()


def _parse_args(argv: list[str]) -> tuple[str | None, bool, list[str]]:
    parser = argparse.ArgumentParser(
        prog="aichs",
        description="Start aichs in a local repository workspace.",
    )
    parser.add_argument(
        "workspace_arg",
        nargs="?",
        help="Workspace directory to open.",
    )
    parser.add_argument(
        "-w",
        "--workspace",
        help="Workspace directory to open.",
    )
    parser.add_argument(
        "--last-workspace",
        action="store_true",
        help="Open the last saved workspace instead of the launch directory.",
    )
    args, qt_args = parser.parse_known_args(argv)
    if args.workspace_arg and args.workspace:
        parser.error("pass either WORKSPACE or --workspace, not both")
    return args.workspace or args.workspace_arg, bool(args.last_workspace), qt_args


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
    workspace, last_workspace, qt_args = _parse_args(sys.argv[1:])
    if workspace:
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            print(f"aichs: workspace not found: {workspace}", file=sys.stderr)
            sys.exit(2)
        workspace = str(workspace_path)
    SettingsStore().apply()
    _set_windows_app_id()
    app = QApplication([sys.argv[0], *qt_args])
    icon = _app_icon()
    app.setWindowIcon(icon)
    app.setStyle("Fusion")
    apply_app_theme(app)
    app._aichs_stall_monitor = EventLoopStallMonitor(app)
    w = MainWindow(startup_workspace=workspace, prefer_saved_workspace=last_workspace)
    w.setWindowIcon(icon)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
