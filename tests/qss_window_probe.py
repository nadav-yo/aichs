"""Isolated Settings + MainWindow QSS probe (one process, offscreen)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main(workspace: str | None = None) -> int:
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from storage.settings import SettingsStore
    from tests.qss_helpers import (
        assert_app_stylesheets_parse,
        capture_qt_stylesheet_warnings,
        parse_failures,
        reparse_widget_stylesheets,
    )
    from ui import theme
    from ui.widgets.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    failures: list[str] = []

    store = SettingsStore()
    dialog = SettingsDialog(store)
    for page_id in dialog._page_ids:
        dialog._ensure_page(dialog._page_ids.index(page_id))
    dialog.show()
    app.processEvents()
    failures.extend(reparse_widget_stylesheets(app, dialog, prefix="settings/"))
    dialog.close()
    app.processEvents()

    if workspace:
        from ui.main_window import MainWindow
        import ui.main_window as main_window_module
        import ui.win_caption as win_caption_module

        main_window_module.QMessageBox.question = (
            lambda *_args, **_kwargs: QMessageBox.StandardButton.No
        )
        win_caption_module.install_caption_sync = lambda _app: None
        win_caption_module.sync_all_windows_captions = lambda *_args: None

        original_style = app.styleSheet()
        window = None
        try:
            with capture_qt_stylesheet_warnings() as messages:
                app.setStyleSheet(theme.build_stylesheet("dark"))
                window = MainWindow(startup_workspace=workspace)
                window.show()
                app.processEvents()
                failures.extend(parse_failures(messages))
            failures.extend(reparse_widget_stylesheets(app, window, prefix="mainwindow/"))
        finally:
            app.setStyleSheet(original_style)
            if window is not None:
                window.close()
            app.processEvents()

    for theme_name in ("dark", "modern", "light"):
        assert_app_stylesheets_parse(
            app,
            [(f"window app {theme_name}", theme.build_stylesheet(theme_name))],
        )

    if failures:
        for line in failures:
            print(line, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(main(workspace))
