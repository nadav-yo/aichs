import sys
import argparse
import multiprocessing
from importlib import resources
from pathlib import Path

from services.performance import slowest_logged_operations

APP_USER_MODEL_ID = "studio.aichs.desktop"
APP_NAME = "Aichs"
APP_ORGANIZATION = "aichs"
APP_ORGANIZATION_DOMAIN = "aichs.studio"


def _assets_dir() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    source_assets = root / "assets"
    if source_assets.exists():
        return source_assets
    return Path(str(resources.files("assets")))


ASSETS = _assets_dir()
_APP_VALUE_OPTIONS = {"--workspace", "-w", "--performance-summary-limit"}
_APP_FLAG_OPTIONS = {"--last-workspace", "--performance-summary"}
_APP_VALUE_PREFIXES = tuple(f"{option}=" for option in _APP_VALUE_OPTIONS if option.startswith("--"))


def _parse_args(argv: list[str]) -> tuple[str | None, bool, bool, int, list[str]]:
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
    parser.add_argument(
        "--performance-summary",
        action="store_true",
        help="Print slow operation totals from performance.log and exit.",
    )
    parser.add_argument(
        "--performance-summary-limit",
        type=int,
        default=10,
        help="Maximum slow operations to print with --performance-summary.",
    )
    app_args, qt_args = _split_app_qt_args(argv)
    args = parser.parse_args(app_args)
    if args.workspace_arg and args.workspace:
        parser.error("pass either WORKSPACE or --workspace, not both")
    return (
        args.workspace or args.workspace_arg,
        bool(args.last_workspace),
        bool(args.performance_summary),
        max(1, int(args.performance_summary_limit)),
        qt_args,
    )


def _split_app_qt_args(argv: list[str]) -> tuple[list[str], list[str]]:
    app_args: list[str] = []
    qt_args: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg.startswith(_APP_VALUE_PREFIXES):
            app_args.append(arg)
            index += 1
            continue
        if arg in _APP_VALUE_OPTIONS:
            app_args.append(arg)
            if index + 1 < len(argv):
                app_args.append(argv[index + 1])
                index += 2
                continue
            index += 1
            continue
        if arg in _APP_FLAG_OPTIONS:
            app_args.append(arg)
            index += 1
            continue
        if arg.startswith("-"):
            qt_args.append(arg)
            if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                qt_args.append(argv[index + 1])
                index += 2
                continue
            index += 1
            continue
        app_args.append(arg)
        index += 1
    return app_args, qt_args


def _print_performance_summary(limit: int = 10) -> None:
    summaries = slowest_logged_operations(limit=max(1, int(limit)))
    if not summaries:
        print("No slow performance events found.")
        return
    print("Slow operations from performance.log:")
    print("operation\tcount\ttotal_ms\tmax_ms\tavg_ms\tlatest_detail")
    for item in summaries:
        print(
            f"{item.operation}\t{item.count}\t{item.total_ms:.3f}\t"
            f"{item.max_ms:.3f}\t{item.avg_ms:.3f}\t{item.latest_detail}"
        )


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


def _set_macos_process_name_cps(name: str) -> bool:
    """Undocumented CPSSetProcessName — used for Dock hover on non-bundled launches."""
    try:
        import ctypes
        from ctypes import Structure, byref, c_char_p, c_uint32, cdll
        from ctypes.util import find_library

        lib_name = find_library("ApplicationServices")
        if not lib_name:
            return False
        app_services = cdll.LoadLibrary(lib_name)

        class ProcessSerialNumber(Structure):
            _fields_ = [
                ("highLongOfPSN", c_uint32),
                ("lowLongOfPSN", c_uint32),
            ]

        psn = ProcessSerialNumber()
        if app_services.GetCurrentProcess(byref(psn)) != 0:
            return False
        app_services.CPSSetProcessName.argtypes = [
            ctypes.POINTER(ProcessSerialNumber),
            c_char_p,
        ]
        return app_services.CPSSetProcessName(byref(psn), name.encode("utf-8")) == 0
    except Exception:
        return False


def _set_macos_bundle_and_process_name(name: str) -> bool:
    """Prefer PyObjC when present; used for menu bar + Dock process identity."""
    try:
        from Foundation import NSBundle, NSProcessInfo
    except Exception:
        return False
    try:
        bundle = NSBundle.mainBundle()
        info = None
        if bundle is not None:
            info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            try:
                info.setObject_forKey_(name, "CFBundleName")
                info.setObject_forKey_(name, "CFBundleDisplayName")
            except Exception:
                info["CFBundleName"] = name
                info["CFBundleDisplayName"] = name
        NSProcessInfo.processInfo().setProcessName_(name)
        return True
    except Exception:
        return False


def _configure_macos_process_identity(name: str = APP_NAME) -> None:
    """Make Dock / menu bar say Aichs for pipx (non-.app) launches.

    Must run before QApplication: Qt's Cocoa plugin snapshots bundle + process
    name when it starts NSApplication.
    """
    if sys.platform != "darwin":
        return
    _set_macos_bundle_and_process_name(name)
    _set_macos_process_name_cps(name)


def _configure_application(app) -> None:
    """Set the process/app identity shown in OS chrome (Dock hover, taskbar, etc.)."""
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setOrganizationDomain(APP_ORGANIZATION_DOMAIN)
    if hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName("aichs")


def _app_icon():
    from PyQt6.QtGui import QIcon

    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addFile(str(ASSETS / "png" / f"icon-{size}.png"))
    return icon


def _start_gui(workspace: str | None, last_workspace: bool, qt_args: list[str]) -> int:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtWidgets import QApplication

    from storage.settings import SettingsStore
    from ui.main_window import MainWindow
    from ui.performance_monitor import EventLoopStallMonitor
    from ui.theme import apply_app_theme

    SettingsStore().apply()
    _set_windows_app_id()
    # Before QApplication: Cocoa reads CFBundleName / processName for Dock + menu.
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName(APP_ORGANIZATION)
    QCoreApplication.setOrganizationDomain(APP_ORGANIZATION_DOMAIN)
    _configure_macos_process_identity(APP_NAME)
    app = QApplication([sys.argv[0], *qt_args])
    _configure_application(app)
    icon = _app_icon()
    app.setWindowIcon(icon)
    app.setStyle("Fusion")
    apply_app_theme(app)
    app._aichs_stall_monitor = EventLoopStallMonitor(app)
    w = MainWindow(startup_workspace=workspace, prefer_saved_workspace=last_workspace)
    w.setWindowIcon(icon)
    w.show()
    return app.exec()


def main():
    multiprocessing.freeze_support()
    (
        workspace,
        last_workspace,
        performance_summary,
        performance_summary_limit,
        qt_args,
    ) = _parse_args(sys.argv[1:])
    if performance_summary:
        _print_performance_summary(performance_summary_limit)
        return
    if workspace:
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            print(f"aichs: workspace not found: {workspace}", file=sys.stderr)
            sys.exit(2)
        workspace = str(workspace_path)
    sys.exit(_start_gui(workspace, last_workspace, qt_args))


if __name__ == "__main__":
    main()
