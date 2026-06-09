import os

from PyQt6.QtCore import QThreadPool, Qt

from ui.main_window import MainWindow


def _settle_file_viewer_workers(qapp):
    QThreadPool.globalInstance().waitForDone(1500)
    qapp.processEvents()


def test_main_window_wires_current_chat_model_to_git_changes(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._chat.model_combo.addItem("bridge-model")
        window._chat.model_combo.setCurrentText("bridge-model")

        assert window._left._git._changes._current_model_getter() == "bridge-model"
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_reveals_opened_file_in_left_panel(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    opened = workspace / "src" / "main.py"
    revealed = []
    try:
        window._left.reveal_file = lambda path, **_kwargs: revealed.append(path) or True

        window._open_file(str(opened))

        assert revealed == [str(opened)]
    finally:
        _settle_file_viewer_workers(qapp)
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_git_open_reveals_file_without_switching_left_tab(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    opened = workspace / "src" / "main.py"
    try:
        window._left._tabs.setCurrentIndex(2)

        window._left._git.file_open.emit(str(opened))

        assert window._left._tabs.tabText(window._left._tabs.currentIndex()) == "Git"
        item = window._left._file_tree.currentItem()
        assert item is not None
        assert item.data(0, Qt.ItemDataRole.UserRole) == str(opened)
    finally:
        _settle_file_viewer_workers(qapp)
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_viewer_tab_change_reveals_file_in_left_panel(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    first = workspace / "src" / "main.py"
    second = workspace / "notes.txt"
    second.write_text("notes\n", encoding="utf-8")
    try:
        window._open_file(str(first))
        window._open_file(str(second))

        window._viewer._tabs.setCurrentIndex(0)

        item = window._left._file_tree.currentItem()
        assert item is not None
        assert item.data(0, Qt.ItemDataRole.UserRole) == str(first)
    finally:
        _settle_file_viewer_workers(qapp)
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)
