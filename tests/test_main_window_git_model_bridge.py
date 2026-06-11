import os
import time

import pytest
from PyQt6.QtCore import QThreadPool, Qt
from PyQt6.QtWidgets import QMessageBox, QMenu

from services.workspace_snapshot import RecentWorkspace
from storage.repository import ConversationStore, register_workspace
from storage.repository import list_workspaces
from ui.main_window import (
    DEFAULT_ACTIVITY_WIDTH,
    MAX_ACTIVITY_WIDTH,
    MIN_ACTIVITY_WIDTH,
    MainWindow,
    _ExtensionReviewThread,
)
from ui.theme import palette
from ui.widgets.workspace_dashboard import WorkspaceDashboard


def _wait_until(qapp, predicate, timeout_s: float = 2.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate()


def _settle_file_viewer_workers(qapp):
    from ui.widgets.file_viewer import _TextFileTab

    qapp.processEvents()
    QThreadPool.globalInstance().waitForDone(1500)
    for widget in qapp.allWidgets():
        if isinstance(widget, _TextFileTab):
            widget._worker_pool.waitForDone(1500)
    qapp.processEvents()


@pytest.fixture
def quiet_file_language(monkeypatch):
    monkeypatch.setattr(
        "ui.widgets.file_viewer._TextFileTab._refresh_diagnostics",
        lambda self, delay_ms=None: None,
    )


@pytest.fixture(autouse=True)
def fast_app_theme(monkeypatch):
    monkeypatch.setattr("ui.main_window.apply_app_theme", lambda *_args, **_kwargs: None)


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


def test_main_window_drafts_git_help_request(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._left.git_help_requested.emit("Help me diagnose git push.", [])

        assert window._chat.composer.input.toPlainText() == "Help me diagnose git push."
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_constructor_defers_git_status_refresh(qapp, workspace, monkeypatch):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()

    def fail_git_snapshot(_repo_path):
        raise AssertionError("startup should not synchronously load git status")

    monkeypatch.setattr("ui.main_window.build_git_snapshot", fail_git_snapshot)
    monkeypatch.setattr("services.git_snapshot.build_git_snapshot", fail_git_snapshot)
    monkeypatch.setattr("services.workspace_snapshot.build_git_snapshot", fail_git_snapshot)

    window = MainWindow(startup_workspace=str(workspace))
    try:
        assert window._initial_git_changes is None
        assert window._initial_git_snapshot is None
        assert window._left._file_tree.topLevelItemCount() > 0
        assert window._left._git.log.count() == 0
        assert window._workspace_dashboard._git_status.text() == "Git pending"
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_uses_wider_default_activity_panel(qapp, workspace, monkeypatch):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        assert window._left.minimumWidth() == MIN_ACTIVITY_WIDTH
        assert window._left.maximumWidth() == MAX_ACTIVITY_WIDTH

        widths = []
        monkeypatch.setattr(window, "_set_activity_panel_width", widths.append)
        window._apply_default_activity_width()

        assert widths == [DEFAULT_ACTIVITY_WIDTH]
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_reveals_opened_file_in_left_panel(
    qapp, workspace, quiet_file_language
):
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


def test_main_window_git_open_reveals_file_without_switching_left_tab(
    qapp, workspace, quiet_file_language
):
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


def test_main_window_viewer_tab_change_reveals_file_in_left_panel(
    qapp, workspace, quiet_file_language
):
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


def test_main_window_close_last_file_hides_viewer_not_chat(
    qapp, workspace, quiet_file_language
):
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


def test_main_window_left_rail_search_and_extensions_actions(qapp, workspace, monkeypatch):
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

        menu_choices = ["File Search", "Text Search"]

        def choose_search_action(menu, _pos):
            wanted = menu_choices.pop(0)
            for action in menu.actions():
                if action.text() == wanted:
                    return action
            return None

        monkeypatch.setattr(QMenu, "exec", choose_search_action)
        assert "search" not in window._left._activity_buttons
        window._left._search_btn.click()
        window._left._search_btn.click()
        assert "extensions" not in window._left._activity_buttons
        window._left._extensions_btn.click()

        assert menu_choices == []
        assert window._left.active_activity() == "chats"
        assert calls == ["file", "text", "extensions"]
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_startup_disables_new_extensions_for_review(qapp, workspace, monkeypatch):
    from services.tool_registry import is_extension_disabled, is_extension_seen

    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    ext_dir = workspace / ".aichs" / "extensions"
    ext_dir.mkdir(parents=True)
    ext = ext_dir / "startup.py"
    ext.write_text("def register(registry): pass\n", encoding="utf-8")
    prompts = []
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.question",
        lambda *args, **kwargs: prompts.append(args) or QMessageBox.StandardButton.No,
    )
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window.show()
        _wait_until(qapp, lambda: bool(prompts))

        assert prompts
        assert is_extension_disabled(ext, str(workspace))
        assert is_extension_seen(ext, str(workspace))

        prompts.clear()
        window._review_new_extensions()
        assert prompts == []
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_extension_review_thread_emits_summaries(qapp, workspace, monkeypatch):
    summaries = [object()]
    monkeypatch.setattr(
        "ui.main_window.disable_unreviewed_extensions",
        lambda repo: summaries if repo == str(workspace) else [],
    )
    thread = _ExtensionReviewThread(5, str(workspace))
    done = []
    thread.done.connect(lambda *args: done.append(args))

    thread.run()

    assert done == [(5, str(workspace), summaries, "")]


def test_main_window_ignores_stale_extension_review_result(qapp, workspace, monkeypatch):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    prompts = []
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.question",
        lambda *args, **kwargs: prompts.append(args) or QMessageBox.StandardButton.No,
    )
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._extension_review_generation = 2

        window._on_extension_review_done(1, str(workspace), [object()], "")

        assert prompts == []
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


def test_main_window_workspace_rail_shows_dashboard(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        assert window._center_stack.currentWidget() is window._workbench

        window._left._activity_buttons["workspace"].click()

        assert window._left.active_activity() == "workspace"
        assert window._left._activity_buttons["workspace"].text() == "Work"
        assert window._left._activity_buttons["workspace"].toolTip() == "Workspace"
        assert window._left.is_activity_panel_collapsed()
        assert window._center_stack.currentWidget() is window._workspace_dashboard
        dashboard_style = window._workspace_dashboard.styleSheet()
        assert "}}" not in dashboard_style
        assert "QWidget {" not in dashboard_style
        assert "QLabel { background:transparent; }" in dashboard_style
        assert "QPushButton#workspaceOpenFolder:hover" in dashboard_style
        assert window._is_context_collapsed()
        assert window._context_shell.isHidden()
        assert window._context_shell.maximumWidth() == 0

        window._left._activity_buttons["files"].click()

        assert window._center_stack.currentWidget() is window._workbench
        assert window._left.active_activity() == "files"
        assert not window._context_shell.isHidden()
        assert window._context_shell.maximumWidth() == 30
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_workspace_restores_expanded_context_rail(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._expand_context()
        expanded_width = window._root_splitter.sizes()[2]

        window._left._activity_buttons["workspace"].click()

        assert window._context_shell.isHidden()
        assert window._root_splitter.sizes()[2] == 0

        window._left._activity_buttons["chats"].click()

        assert window._center_stack.currentWidget() is window._workbench
        assert not window._context_shell.isHidden()
        assert not window._is_context_collapsed()
        assert window._context_shell.minimumWidth() >= 220
        assert window._root_splitter.sizes()[2] >= min(220, expanded_width)
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_workspace_dashboard_shows_home_context(qapp, workspace):
    (workspace / "README.md").write_text("# Project Readme\n\nUseful context.\n", encoding="utf-8")
    long_rule = "Keep the full instruction text visible. " * 40
    (workspace / "AGENTS.md").write_text(
        "# Rules\n\nAlways **run** the tests.\n\n" + long_rule,
        encoding="utf-8",
    )
    skills_dir = workspace / ".aichs" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "review.md").write_text(
        "---\nname: review\ndescription: Review code\n---\nReview carefully.\n",
        encoding="utf-8",
    )
    ext_dir = workspace / ".aichs" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "demo.py").write_text("def register(registry):\n    pass\n", encoding="utf-8")
    store = ConversationStore(str(workspace))
    path = store.save(
        "dash-chat",
        {
            "id": "dash-chat",
            "title": "Dashboard chat",
            "updated_at": "2026-02-03T04:05:00",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    dashboard = WorkspaceDashboard(str(workspace))
    try:
        _wait_until(qapp, lambda: dashboard._snapshot_applied)

        assert "Project Readme" in dashboard._readme_preview.toPlainText()
        assert "Always run the tests." in dashboard._instructions_preview.toPlainText()
        assert long_rule.strip() in dashboard._instructions_preview.toPlainText()
        assert "<h1" in dashboard._instructions_preview.toHtml().lower()
        assert dashboard._agents_status.text() == "AGENTS.md"
        assert dashboard._skills_status.text() == "1 skill"
        assert dashboard._extensions_status.text() == "1 extension"
        assert "QLabel#workspaceSectionLabel" in dashboard.styleSheet()
        assert "QLabel#workspaceStatusPill" in dashboard.styleSheet()
        assert "border-radius:6px" in dashboard.styleSheet()
        assert dashboard._recent_chats.item(0).toolTip() == str(path)
        row = dashboard._recent_chats.itemWidget(dashboard._recent_chats.item(0))
        assert row.title.text() == "Dashboard chat"
        assert row.details.text() == "Feb 03, 2026 04:05 - 1 message"
        assert palette()["TEXT_DIM"] in row.details.styleSheet()
    finally:
        dashboard.close()


def test_workspace_dashboard_actions(qapp, workspace):
    readme = workspace / "README.md"
    readme.write_text("# Readme\n", encoding="utf-8")
    dashboard = WorkspaceDashboard(str(workspace))
    calls = []
    try:
        dashboard.open_file_requested.connect(lambda path: calls.append(("open", path)))
        dashboard.new_chat_requested.connect(lambda: calls.append(("new", "")))
        dashboard.file_search_requested.connect(lambda: calls.append(("file-search", "")))
        dashboard.text_search_requested.connect(lambda: calls.append(("text-search", "")))

        dashboard._open_readme_btn.click()
        dashboard._new_chat_btn.click()
        dashboard._file_search_btn.click()
        dashboard._text_search_btn.click()

        assert ("open", str(readme)) in calls
        assert ("new", "") in calls
        assert ("file-search", "") in calls
        assert ("text-search", "") in calls
    finally:
        dashboard.close()


def test_main_window_recent_workspace_switch_retargets_panels(qapp, workspace, tmp_path):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("other\n", encoding="utf-8")
    register_workspace(other)
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._left._activity_buttons["workspace"].click()
        _wait_until(qapp, lambda: window._workspace_dashboard._recent.count() > 0)
        item = window._workspace_dashboard._recent.item(0)

        window._workspace_dashboard._recent.itemClicked.emit(item)
        qapp.processEvents()

        assert os.getcwd() == str(other.resolve())
        assert window._chat.cwd == str(other.resolve())
        assert window._left._file_tree.root_path == str(other.resolve())
        assert window._left._git.repo_path == str(other.resolve())
        assert window._chat.store.workspace == other.resolve()
        assert window._left._conv.store is window._chat.store
        assert window._center_stack.currentWidget() is window._workspace_dashboard
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_workspace_open_folder_switches(qapp, workspace, tmp_path, monkeypatch):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    other = tmp_path / "folder-choice"
    other.mkdir()
    monkeypatch.setattr(
        "ui.widgets.workspace_dashboard.QFileDialog.getExistingDirectory",
        lambda *_args, **_kwargs: str(other),
    )
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._left._activity_buttons["workspace"].click()

        window._workspace_dashboard._open_btn.click()
        qapp.processEvents()

        assert os.getcwd() == str(other.resolve())
        assert window._chat.cwd == str(other.resolve())
        assert window._settings.load()["workspace_path"] == str(other.resolve())
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_workspace_dashboard_missing_recent_workspace_is_disabled(qapp, workspace, tmp_path):
    cwd = os.getcwd()
    missing = tmp_path / "missing"
    register_workspace(missing)
    dashboard = WorkspaceDashboard(str(workspace))
    calls = []
    try:
        _wait_until(qapp, lambda: dashboard._recent.count() > 0)
        dashboard.switch_requested.connect(calls.append)
        item = dashboard._recent.item(0)

        row = dashboard._recent.itemWidget(item)
        assert "Missing folder" in row.details.text()
        assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
        dashboard._recent.itemClicked.emit(item)

        assert calls == []
        assert os.getcwd() == cwd
    finally:
        dashboard.close()
        os.chdir(cwd)


def test_workspace_dashboard_context_menu_removes_recent_workspace(qapp, workspace, tmp_path, monkeypatch):
    other = tmp_path / "old-work"
    other.mkdir()
    registered = register_workspace(other)
    dashboard = WorkspaceDashboard(str(workspace), defer_refresh=True)
    dashboard._recent.clear()
    dashboard._add_workspace_item(
        RecentWorkspace(
            path=registered["path"],
            name=registered["name"],
            updated_at=registered["updated_at"],
            exists=True,
        )
    )
    item = dashboard._recent.item(0)
    action_texts = []

    def choose_remove(menu, _pos):
        action_texts.extend(action.text() for action in menu.actions())
        return menu.actions()[0]

    try:
        monkeypatch.setattr(QMenu, "exec", choose_remove)
        monkeypatch.setattr(dashboard._recent, "itemAt", lambda _pos: item)

        dashboard._show_recent_menu(dashboard._recent.visualItemRect(item).center())

        assert action_texts == ["Remove from Recent"]
        assert list_workspaces() == []
        assert dashboard._recent.count() == 1
        row = dashboard._recent.itemWidget(dashboard._recent.item(0))
        assert row.title.text() == "No recent workspaces yet"
    finally:
        dashboard.close()


def test_workspace_dashboard_shutdown_clears_refresh_threads(qapp, workspace):
    dashboard = WorkspaceDashboard(str(workspace), defer_refresh=True)
    try:
        dashboard.refresh()

        dashboard.shutdown()

        assert dashboard._refresh_threads == []
        assert dashboard._refresh_generation == 2
    finally:
        dashboard.close()


def test_main_window_workspace_switch_cancelled_while_streaming(
    qapp, workspace, tmp_path, monkeypatch
):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    other = tmp_path / "other"
    other.mkdir()
    window = MainWindow(startup_workspace=str(workspace))
    stopped = []
    try:
        window._chat.is_streaming = lambda: True
        window._chat.stop_streaming = lambda: stopped.append(True)
        monkeypatch.setattr(
            "ui.main_window.QMessageBox.question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
        )

        assert window._switch_workspace(str(other)) is False

        assert os.getcwd() == str(workspace.resolve())
        assert stopped == []
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
        assert window._context._tool_activity.item(0).text() == "No run log for this chat"
        assert window._context._copy_btn.isEnabled() is False
        assert window._context._copy_details_btn.isEnabled() is False
        assert window._context._clear_btn.isEnabled() is False

        window._chat.conversation_changed.emit("chat-1")
        window._chat.run_log_activity.emit("Reading file 'src/main.py'", "chat-1")
        window._chat.run_log_activity.emit("Running command: pytest -q", "chat-2")

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
        assert "Conversation: chat-1" in details
        assert "Original:\nReading file 'src/main.py'" in details

        window._context._scope_combo.setCurrentIndex(1)

        assert window._context._tool_activity.count() == 2
        assert window._context._tool_activity.item(0).text() == "Run command - pytest -q"
        assert window._context._tool_activity.item(1).text() == "Read file - src/main.py"

        window._context._scope_combo.setCurrentIndex(0)

        window._context.clear_activity()

        assert window._context._tool_activity.item(0).text() == "No run log for this chat"
        assert window._context._copy_btn.isEnabled() is False
        assert window._context._copy_details_btn.isEnabled() is False
        assert window._context._clear_btn.isEnabled() is True

        window._context._scope_combo.setCurrentIndex(1)

        assert window._context._tool_activity.count() == 1
        assert window._context._tool_activity.item(0).text() == "Run command - pytest -q"
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_language_section_tracks_supported_active_file(qapp, workspace):
    from tests.conftest import write_extension

    write_extension(
        workspace,
        "python_lang.py",
        '''
        def diagnose(ctx):
            return [{
                "line": 1,
                "column": 0,
                "severity": "warning",
                "message": "demo warning",
                "source": "demo",
                "code": "W1",
            }]

        def actions(ctx):
            return [{
                "id": "fix-demo",
                "title": "Fix demo warning",
                "safe": True,
            }]

        def apply(ctx):
            if ctx.action_id == "fix-demo":
                return {
                    "content": "print('fixed')\\n",
                    "message": "Applied demo fix.",
                }
            return {}

        def format_doc(ctx):
            return {
                "content": "print('formatted')\\n",
                "message": "Formatted.",
            }

        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                diagnostics=diagnose,
                code_actions=actions,
                apply_code_action=apply,
                format_document=format_doc,
            )
        ''',
    )
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    opened = workspace / "src" / "main.py"
    unsupported = workspace / "notes.txt"
    unsupported.write_text("notes\n", encoding="utf-8")
    drafted = []
    try:
        assert not window._language_context_tab.isHidden()
        window._language_context_tab.click()

        assert window._context._active_panel == "language"
        assert window._context._language_type.text() == "No language"
        assert window._context._language_file.text() == "No supported file"
        assert window._context._language_summary.text() == "Open a file with registered language support."

        window._viewer.diagnostic_fix_requested.connect(
            lambda text, refs: drafted.append((text, refs))
        )
        window._open_file(str(opened))
        _settle_file_viewer_workers(qapp)
        _wait_until(qapp, lambda: window._context._language_type.text() == "Python")

        assert not window._language_context_tab.isHidden()
        window._language_context_tab.click()

        assert not window._is_context_collapsed()
        assert window._context._active_panel == "language"
        assert window._context._pages.currentIndex() == 1
        assert window._context._title.text() == "Language"
        assert window._context._tool_activity.item(0).text() == "No run log for this chat"
        assert window._context._language_icon.text() == "Py"
        assert window._context._language_type.text() == "Python"
        assert window._context._language_file.text() == "src/main.py"
        assert window._context._language_summary.text() == ""
        assert not window._context._language_summary.isVisible()
        assert not window._context._language_format_btn.isHidden()
        assert window._context._language_format_btn.isEnabled()
        assert not window._context._language_fix_safe_btn.isHidden()
        assert window._context._language_fix_safe_btn.isEnabled()
        assert not window._context._language_ask_file_btn.isHidden()
        assert window._context._language_ask_file_btn.isEnabled()
        assert not window._context._language_refresh_btn.isHidden()
        assert window._context._language_refresh_btn.isEnabled()
        assert not window._context._language_problems_label.isHidden()
        assert not window._context._language_diagnostics.isHidden()
        assert window._context._language_diagnostics.item(0).text().startswith(
            "1:1 warning [demo W1] - demo warning"
        )
        assert not window._context._language_quick_fix_btn.isHidden()
        assert window._context._language_quick_fix_btn.isEnabled()
        assert not window._context._language_ask_btn.isHidden()
        assert window._context._language_ask_btn.isEnabled()

        window._context._language_ask_file_btn.click()

        assert "Please review @src/main.py" in drafted[-1][0]
        assert drafted[-1][1] == ["src/main.py"]

        window._context._language_ask_btn.click()

        assert "Please fix this diagnostic in @src/main.py:1." in drafted[-1][0]
        assert drafted[-1][1] == ["src/main.py"]

        window._context._language_fix_safe_btn.click()
        _settle_file_viewer_workers(qapp)

        tab = window._viewer._tabs.currentWidget()
        _wait_until(qapp, lambda: tab._editor.toPlainText() == "print('fixed')\n")
        assert tab._editor.toPlainText() == "print('fixed')\n"

        window._context._language_format_btn.click()
        _settle_file_viewer_workers(qapp)

        _wait_until(qapp, lambda: tab._editor.toPlainText() == "print('formatted')\n")
        assert tab._editor.toPlainText() == "print('formatted')\n"

        window._open_file(str(unsupported))
        _settle_file_viewer_workers(qapp)
        _wait_until(
            qapp,
            lambda: window._context._language_summary.text() == "No language support for this file.",
        )

        assert not window._language_context_tab.isHidden()
        assert window._context._active_panel == "language"
        assert window._context._pages.currentIndex() == 1
        assert window._context._title.text() == "Language"
        assert window._context._language_icon.text() == "--"
        assert window._context._language_type.text() == "No language"
        assert window._context._language_file.text() == "notes.txt"
        assert window._context._language_summary.text() == "No language support for this file."
        assert window._context._language_actions_label.isHidden()
        assert window._context._language_problems_label.isHidden()

        window._context_tab.click()

        assert window._context._active_panel == "run_log"
        assert window._context._pages.currentIndex() == 0
        assert window._context._title.text() == "Run Log"
        assert window._context._tool_activity.item(0).text() == "No run log for this chat"
    finally:
        _settle_file_viewer_workers(qapp)
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
        assert window._context_tab.text() == ""
        assert window._context_tab.toolTip() == "Show run log"
        assert window._context_tab.accessibleName() == "Run Log"
        assert not window._context_tab.icon().isNull()
        assert window._language_context_tab.text() == ""
        assert window._language_context_tab.accessibleName() == "Language"
        assert not window._language_context_tab.icon().isNull()

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
