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
        assert not window._viewer.isHidden()
        assert window._workbench.orientation() == Qt.Orientation.Horizontal
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
        window._left.set_active_activity("git")

        window._left._git.file_open.emit(str(opened))

        assert window._left.active_activity() == "git"
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


def test_main_window_close_last_file_hides_viewer_not_chat(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    opened = workspace / "src" / "main.py"
    try:
        window._open_file(str(opened))

        assert not window._viewer.isHidden()
        assert not window._chat.isHidden()

        assert window._viewer.close_current_tab() is True

        assert window._viewer.isHidden()
        assert not window._chat.isHidden()
    finally:
        _settle_file_viewer_workers(qapp)
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_left_rail_search_and_extensions_actions(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        calls = []
        window._left.file_search_requested.disconnect()
        window._left.text_search_requested.disconnect()
        window._left.extensions_requested.disconnect()
        window._open_file_search = lambda: calls.append("file")
        window._open_text_search = lambda: calls.append("text")
        window._chat.show_extensions = lambda: calls.append("extensions")
        window._left.file_search_requested.connect(window._open_file_search)
        window._left.text_search_requested.connect(window._open_text_search)
        window._left.extensions_requested.connect(window._chat.show_extensions)

        window._left.set_active_activity("search")
        window._left._search_page.file_search_requested.emit()
        window._left._search_page.text_search_requested.emit()
        assert "extensions" not in window._left._activity_buttons
        window._left._extensions_btn.click()

        assert window._left.active_activity() == "search"
        assert calls == ["file", "text", "extensions"]
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_left_rail_clicks_toggle_activity_drawer(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    opened = workspace / "src" / "main.py"
    try:
        assert window._left.active_activity() == "chats"
        assert not window._left.is_activity_panel_collapsed()

        window._left._activity_buttons["chats"].click()

        assert window._left.active_activity() == "chats"
        assert window._left.is_activity_panel_collapsed()
        assert window._root_splitter.sizes()[0] <= 80

        window._left._activity_buttons["files"].click()

        assert window._left.active_activity() == "files"
        assert not window._left.is_activity_panel_collapsed()
        assert window._root_splitter.sizes()[0] >= 200

        window._left._activity_buttons["files"].click()

        assert window._left.is_activity_panel_collapsed()
        assert window._root_splitter.sizes()[0] <= 80

        window._left.reveal_file(str(opened), activate=True)

        assert window._left.active_activity() == "files"
        assert not window._left.is_activity_panel_collapsed()
        assert window._root_splitter.sizes()[0] >= 200
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_activity_shelf_tracks_tool_activity(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        assert window._is_context_collapsed()
        assert window._context._tool_activity.item(0).text() == "No run log yet"
        assert window._context._copy_btn.isEnabled() is False
        assert window._context._copy_details_btn.isEnabled() is False
        assert window._context._clear_btn.isEnabled() is False

        window._chat.tool_activity.emit("Reading file 'src/main.py'")

        assert window._context._tool_activity.count() == 1
        item = window._context._tool_activity.item(0)
        assert item.text() == "Read file - src/main.py"
        assert not item.icon().isNull()
        assert "Original:\nReading file 'src/main.py'" in item.toolTip()
        assert window._context._copy_btn.isEnabled() is True
        assert window._context._copy_details_btn.isEnabled() is True
        assert window._context._clear_btn.isEnabled() is True

        window._context._tool_activity.setCurrentRow(0)
        window._context.copy_selected_activity()

        assert qapp.clipboard().text() == "Read file - src/main.py"

        window._context.copy_selected_activity_details()

        details = qapp.clipboard().text()
        assert "Type: Read file" in details
        assert "Target: src/main.py" in details
        assert "Original:\nReading file 'src/main.py'" in details

        window._context.clear_activity()

        assert window._context._tool_activity.item(0).text() == "No run log yet"
        assert window._context._copy_btn.isEnabled() is False
        assert window._context._copy_details_btn.isEnabled() is False
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_context_panel_can_collapse_and_reopen(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        assert window._is_context_collapsed()
        assert window._context_shell.maximumWidth() == 30
        assert window._root_splitter.sizes()[2] <= 36
        assert window._context_tab.text() == "R\nu\nn\n\nL\no\ng"

        window._expand_context()

        assert not window._is_context_collapsed()
        assert window._context_shell.minimumWidth() >= 220
        assert window._root_splitter.sizes()[2] >= 200
        assert window._context._collapse_btn.text() == ">"
        assert window._context._collapse_btn.accessibleName() == "Collapse run log"
        assert window._context._collapse_btn.toolTip() == "Collapse run log"

        window._context._collapse_btn.click()

        assert window._is_context_collapsed()
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)
