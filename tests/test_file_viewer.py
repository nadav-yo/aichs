from types import SimpleNamespace

from PyQt6.QtCore import QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QColor, QGuiApplication, QTextCursor, QTextDocument
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox, QWidget

from services.chat_drag import AICHS_FILE_DROP_MIME, parse_file_drop
from services.file_editor_refs import AICHS_EDITOR_REF_MIME, parse_editor_refs
from services.language_features import CodeActionResult
from storage.settings import FILE_EDITOR_AUTO_SAVE_KEY, FILE_EDITOR_TAB_SPACES_KEY
from tests.conftest import write_extension
from ui.theme import palette
from ui.widgets.file_viewer import (
    FileViewerPanel,
    _FileTextEdit,
    _TextFileTab,
    _diagnostic_details,
    _read_text_preview,
    _read_text_preview_details,
)
from ui.widgets.markdown_browser import (
    RemoteImageTextBrowser,
    image_from_markdown_image_data,
)


class _Settings:
    def __init__(self, data: dict):
        self._data = data

    def load(self) -> dict:
        return dict(self._data)


def _wait_until(qapp, predicate, timeout_ms: int = 1500):
    elapsed = 0
    while elapsed < timeout_ms:
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(25)
        elapsed += 25
    qapp.processEvents()
    assert predicate()


def test_read_text_preview_truncates(workspace):
    path = workspace / "big.txt"
    path.write_text("x" * 600_000, encoding="utf-8")
    text = _read_text_preview(str(path))
    assert "[Preview truncated" in text


def test_read_text_preview_blocks_archive_by_type(workspace):
    path = workspace / "source.tar.gz"
    path.write_bytes(b"\x1f\x8b\x08\x00archive bytes")
    text, truncated, decode_error, blocked_preview = _read_text_preview_details(str(path))
    assert "[Cannot preview binary or archive file: source.tar.gz]" in text
    assert "archive bytes" not in text
    assert truncated is False
    assert decode_error is False
    assert blocked_preview is True


def test_read_text_preview_blocks_binary_content(workspace):
    path = workspace / "payload"
    path.write_bytes(b"hello\x00world")
    text = _read_text_preview(str(path))
    assert "[Cannot preview binary or archive file: payload]" in text
    assert "hello" not in text


def test_markdown_preview_rasterizes_svg_badges(qapp):
    image = image_from_markdown_image_data(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="88" height="20">'
        b'<rect width="88" height="20" fill="#2563eb"/></svg>'
    )

    assert not image.isNull()
    assert image.width() == 88
    assert image.height() == 20


def test_remote_markdown_preview_uses_cached_remote_images(qapp):
    browser = RemoteImageTextBrowser()
    url = QUrl("https://img.shields.io/badge/python-3.11%2B-blue")
    image = image_from_markdown_image_data(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="88" height="20">'
        b'<rect width="88" height="20" fill="#2563eb"/></svg>'
    )
    browser._remote_images[url.toString()] = image

    loaded = browser.loadResource(QTextDocument.ResourceType.ImageResource, url)

    assert not loaded.isNull()
    assert loaded.size() == image.size()
    browser.close()


def test_file_viewer_saves_text_edits(workspace):
    path = workspace / "src" / "main.py"
    events = []
    tab = SimpleNamespace(
        _editable=True,
        _auto_save_timer=SimpleNamespace(stop=lambda: events.append(("timer", "stop"))),
        _path=str(path),
        _repo_root=str(workspace),
        _editor=SimpleNamespace(toPlainText=lambda: "print('bye')\n"),
        _edit_mode=True,
        _content="print('hi')\n",
        _markdown=False,
        _is_showing_diff=lambda: False,
        _set_dirty=lambda dirty: events.append(("dirty", dirty)),
        _set_status=lambda status: events.append(("status", status)),
        _schedule_diff_refresh=lambda delay_ms=None: events.append(("diff", delay_ms)),
        _render=lambda diagnostics_delay_ms=None: events.append(("render", diagnostics_delay_ms)),
    )

    _TextFileTab._save(tab)

    assert path.read_text(encoding="utf-8") == "print('bye')\n"
    assert tab._content == "print('bye')\n"
    assert tab._edit_mode is False
    assert ("timer", "stop") in events
    assert ("dirty", False) in events
    assert ("status", "Saved") in events
    assert ("diff", 0) in events
    assert ("render", 0) in events


def test_text_file_tab_skips_diff_timer_outside_git_repo(workspace):
    ready = []
    tab = SimpleNamespace(
        _file_backed=True,
        _repo_root=str(workspace),
        _diff_generation=4,
        _on_diff_ready=lambda generation, diff_text: ready.append((generation, diff_text)),
    )

    _TextFileTab._schedule_diff_refresh(tab, delay_ms=0)

    assert tab._diff_generation == 5
    assert ready == [(5, None)]


def test_file_viewer_marks_dirty_tabs_across_files(qapp, workspace, monkeypatch):
    monkeypatch.setattr(_TextFileTab, "_refresh_diagnostics", lambda self, delay_ms=None: None)
    monkeypatch.setattr(_FileTextEdit, "configure_syntax", lambda self, path, content: None)
    first = workspace / "src" / "main.py"
    second = workspace / "notes.txt"
    second.write_text("notes\n", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(first), repo_root=str(workspace))
    first_tab = panel._tabs.widget(0)
    first_tab._editor.edit_requested.emit()
    first_tab._editor.setPlainText("print('dirty')\n")
    panel._add_tab_widget(str(second), "notes.txt", QWidget())

    assert panel._tabs.tabText(0) == "* main.py"
    assert panel._tabs.tabToolTip(0).startswith("Unsaved changes")
    assert panel._tabs.tabText(1) == "notes.txt"
    panel.close()


def test_file_viewer_emits_dirty_file_state(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(str(workspace))
    changes = []
    panel.dirty_file_changed.connect(lambda p, dirty: changes.append((p, dirty)))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._editor.edit_requested.emit()
    tab._editor.setPlainText("print('dirty')\n")
    tab._save()

    assert changes == [(str(path), True), (str(path), False)]
    panel.close()


def test_file_viewer_clears_dirty_state_when_dirty_tab_closes(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(str(workspace))
    changes = []
    panel.dirty_file_changed.connect(lambda p, dirty: changes.append((p, dirty)))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._editor.edit_requested.emit()
    tab._editor.setPlainText("print('dirty')\n")
    panel._confirm_close_tab = lambda _widget: True
    panel.close_current_tab()

    assert changes == [(str(path), True), (str(path), False)]
    panel.close()


def test_file_viewer_cancel_keeps_dirty_tab_open(qapp, workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    original = path.read_text(encoding="utf-8")
    panel = FileViewerPanel(str(workspace))
    monkeypatch.setattr(
        "ui.widgets.file_viewer.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._editor.edit_requested.emit()
    tab._editor.setPlainText("print('dirty')\n")

    assert panel.close_current_tab() is False

    assert panel._tabs.count() == 1
    assert tab._dirty is True
    assert path.read_text(encoding="utf-8") == original
    panel.close()


def test_file_viewer_ok_reverts_dirty_tab_and_closes(qapp, workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    original = path.read_text(encoding="utf-8")
    changes = []
    panel = FileViewerPanel(str(workspace))
    panel.dirty_file_changed.connect(lambda p, dirty: changes.append((p, dirty)))
    monkeypatch.setattr(
        "ui.widgets.file_viewer.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok,
    )

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._editor.edit_requested.emit()
    tab._editor.setPlainText("print('dirty')\n")

    assert panel.close_current_tab() is True

    assert panel._tabs.count() == 0
    assert path.read_text(encoding="utf-8") == original
    assert changes == [(str(path), True), (str(path), False)]
    panel.close()


def test_file_viewer_reopens_recently_closed_file(qapp, workspace):
    first = workspace / "src" / "main.py"
    second = workspace / "notes.txt"
    second.write_text("one\ntwo\nthree\n", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(first), repo_root=str(workspace))
    panel.open_file(str(second), repo_root=str(workspace), line_no=3)

    assert panel.close_current_tab() is True
    assert panel.open_paths() == [str(first)]

    assert panel.reopen_recent_closed_file(repo_root=str(workspace)) == str(second)

    assert panel.open_paths() == [str(first), str(second)]
    assert panel.active_path() == str(second)
    tab = panel._tabs.currentWidget()
    assert tab._editor.textCursor().blockNumber() == 2
    panel.close()


def test_file_viewer_auto_saves_when_enabled(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(
        str(workspace),
        settings=_Settings({FILE_EDITOR_AUTO_SAVE_KEY: True}),
    )

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._editor.edit_requested.emit()
    tab._editor.setPlainText("print('auto')\n")

    assert tab._dirty is True
    assert tab._auto_save_timer.isActive()
    tab._auto_save_timer.stop()
    tab._save(auto=True)
    assert path.read_text(encoding="utf-8") == "print('auto')\n"
    assert tab._dirty is False
    assert tab._edit_mode is True
    assert panel._tabs.tabText(0) == "main.py"
    assert tab._save_btn.isEnabled() is False
    panel.close()


def test_file_viewer_opens_formatted_and_save_returns_to_view(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)

    assert tab._editor.isReadOnly() is True
    assert tab._status.text() == "Formatted view"
    assert "print" in tab._editor.toPlainText()
    assert tab._editor._syntax_highlighter is not None
    assert hasattr(tab, "_edit_btn") is False

    tab._editor.edit_requested.emit()
    assert tab._editor.isReadOnly() is False
    assert tab._editor._syntax_highlighter is not None
    tab._editor.setPlainText("print('formatted save')\n")
    tab._save()

    assert path.read_text(encoding="utf-8") == "print('formatted save')\n"
    assert panel._tabs.tabText(0) == "main.py"
    assert tab._edit_mode is False
    assert tab._editor.isReadOnly() is True
    assert tab._editor.toPlainText() == "print('formatted save')\n"
    assert tab._editor._syntax_highlighter is not None
    panel.close()


def test_file_viewer_open_file_can_jump_to_line(qapp, workspace):
    path = workspace / "src" / "main.py"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace), line_no=3)
    tab = panel._tabs.widget(0)

    assert tab._editor.textCursor().blockNumber() == 2
    panel.close()


def test_file_viewer_file_tabs_drag_as_chat_file_refs(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))

    mime = panel._tabs.tabBar().mime_data_for_tab(0)

    assert mime is not None
    assert mime.hasFormat(AICHS_FILE_DROP_MIME)
    assert parse_file_drop(mime.data(AICHS_FILE_DROP_MIME)) == ["src/main.py"]
    assert mime.text() == "@src/main.py"

    panel.open_content("scratch", "Scratch")

    assert panel._tabs.tabBar().mime_data_for_tab(1) is None
    panel.close()


def test_file_viewer_runs_diagnostics_immediately_on_open(qapp, workspace, monkeypatch):
    calls = []

    def record_refresh(self, delay_ms=None):
        calls.append(delay_ms)
        self._set_diagnostics([])

    monkeypatch.setattr(_TextFileTab, "_refresh_diagnostics", record_refresh)
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))

    assert 0 in calls
    panel.close()


def test_file_viewer_refresh_file_updates_open_editor(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    path.write_text("print('agent edit')\n", encoding="utf-8")

    assert panel.refresh_file("src/main.py", repo_root=str(workspace)) is True
    assert tab._editor.toPlainText() == "print('agent edit')\n"
    assert tab._dirty is False
    panel.close()


def test_text_file_tab_local_find_selects_match(qapp):
    tab = _TextFileTab("demo.py", "one\nneedle\ntwo needle\n", "", None)

    tab._show_find()
    tab._find_query.setText("needle")

    assert tab._find_bar.isHidden() is False
    assert tab._editor.textCursor().selectedText() == "needle"
    assert tab._find_status.text() == "1 of 2"

    tab._find_next()
    assert tab._find_status.text() == "2 of 2"
    assert tab._find_shortcut.key().toString() == "Ctrl+F"
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_text_file_tab_local_find_keeps_focus_while_typing(qapp):
    tab = _TextFileTab("demo.py", "API docs\napplication\n", "", None)
    tab.show()
    qapp.processEvents()

    tab._show_find()
    tab._find_query.setText("A")
    qapp.processEvents()
    tab._find_query.insert("PI")

    assert tab._find_query.text() == "API"
    assert tab._find_query.hasFocus()
    assert tab._editor.toPlainText() == "API docs\napplication\n"
    assert tab._editor.textCursor().selectedText() == "API"
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_file_text_edit_populates_local_completions(qapp):
    editor = _FileTextEdit()
    editor.configure_completion("demo.js")
    editor.setPlainText("const renderer = renderScene;\nren")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    editor._show_completion(manual=False)

    completions = editor._completion_model.stringList()
    assert "renderer" in completions
    assert "renderScene" in completions
    assert "return" not in completions
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_text_edit_inserts_completion_over_prefix(qapp):
    editor = _FileTextEdit()
    editor.configure_completion("demo.py")
    editor.setPlainText("def render_scene():\n    ret")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    editor._show_completion(manual=False)
    editor._insert_completion("return")

    assert editor.toPlainText().endswith("    return")
    assert editor._completion_model.stringList() == []
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_text_edit_enter_preserves_current_line_indent(qapp):
    editor = _FileTextEdit()
    editor.setPlainText("\t  print('hi')")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    editor.show()
    editor.setFocus()
    qapp.processEvents()

    QTest.keyClick(editor, Qt.Key.Key_Return)

    assert editor.toPlainText() == "\t  print('hi')\n\t  "
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_text_edit_shift_tab_outdents_current_line(qapp):
    editor = _FileTextEdit()
    editor.setPlainText("    print('hi')")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    editor.show()
    editor.setFocus()
    qapp.processEvents()

    QTest.keyClick(editor, Qt.Key.Key_Backtab)

    assert editor.toPlainText() == "print('hi')"
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_text_edit_tab_stop_is_four_spaces(qapp):
    editor = _FileTextEdit()
    editor.apply_appearance()

    assert editor.tabStopDistance() == editor.fontMetrics().horizontalAdvance(" ") * 4
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_viewer_uses_tab_spaces_setting(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(
        str(workspace),
        settings=_Settings({FILE_EDITOR_TAB_SPACES_KEY: 2}),
    )

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)

    assert tab._editor.tabStopDistance() == tab._editor.fontMetrics().horizontalAdvance(" ") * 2
    panel.close()


def test_file_text_edit_does_not_complete_read_only(qapp):
    editor = _FileTextEdit()
    editor.configure_completion("demo.py")
    editor.setPlainText("return")
    editor.setReadOnly(True)

    editor._show_completion(manual=True)

    assert editor._completion_model.stringList() == []
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_text_edit_selection_mime_includes_editor_ref(qapp, workspace):
    editor = _FileTextEdit()
    path = workspace / "src" / "main.py"
    editor.configure_reference(str(path), str(workspace))
    editor.setPlainText("one\ntwo\nthree\n")
    cursor = editor.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)

    mime = editor._mime_data_for_selection()

    assert mime is not None
    assert mime.text() == "two\nthree"
    assert parse_editor_refs(mime.data(AICHS_EDITOR_REF_MIME)) == [{
        "path": "src/main.py",
        "start_line": 2,
        "end_line": 3,
        "text": "two\nthree",
    }]

    editor.copy()
    clipboard = QGuiApplication.clipboard().mimeData()
    assert clipboard.text() == "two\nthree"
    assert parse_editor_refs(clipboard.data(AICHS_EDITOR_REF_MIME))[0]["path"] == "src/main.py"
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_text_edit_drag_mime_uses_lightweight_reference_text(qapp, workspace):
    editor = _FileTextEdit()
    path = workspace / "src" / "main.py"
    editor.configure_reference(str(path), str(workspace))
    editor.setPlainText("one\ntwo\nthree\n")
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len(editor.toPlainText()), QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)

    mime = editor._mime_data_for_drag()

    assert mime is not None
    assert mime.text() == "@src/main.py:1-3"
    assert parse_editor_refs(mime.data(AICHS_EDITOR_REF_MIME)) == [{
        "path": "src/main.py",
        "start_line": 1,
        "end_line": 3,
        "text": "",
    }]
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_text_edit_diagnostic_markers_expand_gutter(qapp):
    from services.language_features import Diagnostic

    editor = _FileTextEdit()
    editor.setPlainText("one\ntwo\n")
    before = editor.line_number_area_width()

    editor.set_diagnostics([
        Diagnostic(path="demo.py", line=2, column=0, severity="warning", message="careful"),
    ])

    assert editor.line_number_area_width() > before
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_file_text_edit_diagnostic_marker_hover_shows_details(qapp):
    from services.language_features import Diagnostic

    class _HoverEvent:
        def __init__(self, widget, pos: QPoint):
            self._widget = widget
            self._pos = pos

        def position(self):
            return QPointF(self._pos)

        def globalPosition(self):
            return QPointF(self._widget.mapToGlobal(self._pos))

    editor = _FileTextEdit()
    editor.resize(260, 120)
    editor.setPlainText("one\ntwo\n")
    editor.show()
    qapp.processEvents()
    editor.set_diagnostics([
        Diagnostic(
            path="demo.py",
            line=2,
            column=4,
            severity="warning",
            source="ruff",
            code="F841",
            message="Local variable app is assigned to but never used",
        ),
    ])

    block = editor.document().findBlockByNumber(1)
    top = int(editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top())
    marker_y = top + max(2, (editor.fontMetrics().height() - 7) // 2)
    pos = QPoint(7, marker_y + 3)

    assert editor._diagnostic_line_at_gutter_pos(pos) == 2

    editor.line_number_area_mouse_move_event(_HoverEvent(editor._line_number_area, pos))

    assert editor._hovered_diagnostic_line == 2
    assert editor._line_number_area.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert "line 2:5 warning [ruff F841]" in editor._line_number_area.toolTip()
    assert "Local variable app" in editor._line_number_area.toolTip()

    editor.line_number_area_leave_event(None)

    assert editor._hovered_diagnostic_line is None
    assert editor._line_number_area.toolTip() == ""
    editor.close()
    editor.deleteLater()
    qapp.processEvents()


def test_text_file_tab_right_click_diagnostic_marker_drafts_fix_request(qapp, workspace):
    from services.language_features import Diagnostic

    class _MouseEvent:
        def __init__(self, widget, pos: QPoint, button):
            self._widget = widget
            self._pos = pos
            self._button = button
            self.accepted = False

        def button(self):
            return self._button

        def position(self):
            return QPointF(self._pos)

        def globalPosition(self):
            return QPointF(self._widget.mapToGlobal(self._pos))

        def accept(self):
            self.accepted = True

    path = workspace / "src" / "main.py"
    tab = _TextFileTab(str(path), "one\ntwo\n", str(workspace), None)
    tab.resize(320, 180)
    tab.show()
    qapp.processEvents()
    tab._set_diagnostics([
        Diagnostic(
            path=str(path),
            line=2,
            column=4,
            severity="warning",
            source="ruff",
            code="F841",
            message="Local variable app is assigned to but never used",
        ),
    ])
    drafted = []
    tab.diagnostic_fix_requested.connect(lambda text, refs: drafted.append((text, refs)))

    block = tab._editor.document().findBlockByNumber(1)
    top = int(tab._editor.blockBoundingGeometry(block).translated(tab._editor.contentOffset()).top())
    marker_y = top + max(2, (tab._editor.fontMetrics().height() - 7) // 2)
    event = _MouseEvent(
        tab._editor._line_number_area,
        QPoint(7, marker_y + 3),
        Qt.MouseButton.RightButton,
    )

    tab._editor.line_number_area_mouse_press_event(event)

    assert event.accepted is True
    assert drafted
    assert "Please fix this diagnostic in @src/main.py:2." in drafted[0][0]
    assert "Diagnostic tool: ruff F841" in drafted[0][0]
    assert "Local variable app is assigned to but never used" in drafted[0][0]
    assert drafted[0][1] == ["src/main.py"]
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_text_file_tab_uses_configured_chat_prompt_templates(qapp, workspace):
    from services.language_features import Diagnostic

    path = workspace / "src" / "main.py"
    tab = _TextFileTab(
        str(path),
        "one\ntwo\n",
        str(workspace),
        None,
        file_review_prompt="Inspect {mention} from {path}.",
        diagnostic_fix_prompt="Resolve {mention} in {path} line {line}.",
    )
    drafted = []
    tab.diagnostic_fix_requested.connect(lambda text, refs: drafted.append((text, refs)))

    tab._draft_file_question()

    assert drafted[-1][0].startswith("Inspect @src/main.py from src/main.py.")
    assert "Summarize what this file does" in drafted[-1][0]
    assert drafted[-1][1] == ["src/main.py"]

    tab._draft_diagnostic_fix([
        Diagnostic(
            path=str(path),
            line=2,
            column=4,
            severity="warning",
            source="ruff",
            code="F841",
            message="Local variable app is assigned to but never used",
        ),
    ])

    assert drafted[-1][0].startswith("Resolve @src/main.py:2 in src/main.py line 2.")
    assert "Diagnostic tool: ruff F841" in drafted[-1][0]
    assert "Diagnostic output:" in drafted[-1][0]
    assert drafted[-1][1] == ["src/main.py"]
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_text_file_tab_code_action_marks_buffer_dirty_without_saving(qapp, workspace):
    path = workspace / "src" / "main.py"
    path.write_text("print('old')\n", encoding="utf-8")
    tab = _TextFileTab(str(path), "print('old')\n", str(workspace), None)

    tab._apply_code_action_content("print('new')\n", "Applied test fix.")

    assert tab._dirty is True
    assert tab._edit_mode is True
    assert tab._editor.toPlainText() == "print('new')\n"
    assert path.read_text(encoding="utf-8") == "print('old')\n"
    assert tab._status.text() == "Applied test fix."
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_text_file_tab_code_action_normalizes_formatter_newlines(qapp, workspace):
    path = workspace / "scripts" / "render_icons.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('old')\n", encoding="utf-8")
    tab = _TextFileTab(str(path), "print('old')\n", str(workspace), None)

    tab._apply_code_action_content(
        "def render():\r\r\n    return 1\r\r\n\r\r\ndef main():\r\n    render()\r\n",
        "Formatted.",
    )

    assert tab._editor.toPlainText() == (
        "def render():\n"
        "    return 1\n"
        "\n"
        "def main():\n"
        "    render()\n"
    )
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_text_file_tab_code_action_no_content_keeps_buffer(qapp, workspace):
    path = workspace / "src" / "main.py"
    tab = _TextFileTab(str(path), "print('old')\n", str(workspace), None)

    tab._on_code_action_ready(0, CodeActionResult(message="Nothing changed."), [])

    assert tab._dirty is False
    assert tab._editor.toPlainText() == "print('old')\n"
    assert tab._status.text() == "Nothing changed."
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_diagnostic_details_limits_output():
    from services.language_features import Diagnostic

    details = _diagnostic_details([
        Diagnostic(
            path="demo.py",
            line=i + 1,
            column=0,
            severity="warning",
            source="ruff",
            code=f"R{i}",
            message=f"issue {i}",
        )
        for i in range(10)
    ])

    assert "line 1:1 warning [ruff R0]: issue 0" in details
    assert "... and 2 more" in details


def test_file_viewer_applies_extension_diagnostics(qapp, workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "language.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                diagnostics=lambda ctx: [{
                    "line": 1,
                    "column": 0,
                    "severity": "error",
                    "message": "syntax problem",
                }],
            )
        """,
    )
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    _wait_until(qapp, lambda: bool(tab._diagnostics))

    assert tab._diagnostics[0].message == "syntax problem"
    assert tab._editor._diagnostics == tab._diagnostics
    assert tab._status.text() == "1 problem (1 error)"
    panel.close()


def test_file_viewer_clears_diagnostics_in_diff_mode(qapp, workspace):
    path = workspace / "src" / "main.py"
    write_extension(
        workspace,
        "language.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["*.py"],
                diagnostics=lambda ctx: [{"line": 1, "message": "hidden in diff"}],
            )
        """,
    )
    panel = FileViewerPanel(str(workspace))

    panel.open_file(
        str(path),
        repo_root=str(workspace),
        diff_text="--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-print('hi')\n+print('bye')\n",
    )
    tab = panel._tabs.widget(0)

    assert tab._is_showing_diff() is True
    assert tab._diagnostics == []
    assert tab._editor._diagnostics == []
    panel.close()


def test_file_viewer_diff_mode_marks_changed_lines(qapp, workspace):
    path = workspace / "src" / "main.py"
    content = "print('hi')\nprint('there')\n"
    path.write_text(content, encoding="utf-8")
    diff_text = (
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,2 +1,2 @@\n"
        " print('hi')\n"
        "+print('there')\n"
    )
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace), diff_text=diff_text)
    tab = panel._tabs.widget(0)

    assert tab._editor.isReadOnly() is True
    assert tab._editor.toPlainText() == content
    assert tab._editor._changed_lines == {2}
    assert tab._status.text() == "Diff preview"
    panel.close()


def test_file_viewer_markdown_uses_preview_until_edit(qapp, workspace, monkeypatch):
    monkeypatch.setattr(_TextFileTab, "_refresh_diagnostics", lambda self, delay_ms=None: None)
    path = workspace / "README.md"
    path.write_text("# Hello\n\n| A | B |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._apply_markdown_preview()

    assert tab._status.text() == "Markdown preview"
    assert tab._preview.isVisibleTo(tab)
    assert tab._editor.isHidden()
    assert tab._preview_toggle.isChecked() is True
    assert "<h1" in tab._preview.toHtml().lower()
    assert "<table" in tab._preview.toHtml().lower()

    tab._preview.edit_requested.emit()
    assert tab._editor.isVisibleTo(tab)
    assert tab._preview.isHidden()
    assert tab._editor.isReadOnly() is False
    assert tab._preview_toggle.isChecked() is False

    tab._editor.setPlainText("# Saved\n")
    tab._save()
    tab._apply_markdown_preview()

    assert path.read_text(encoding="utf-8") == "# Saved\n"
    assert tab._status.text() == "Markdown preview"
    assert tab._preview.isVisibleTo(tab)
    assert tab._editor.isHidden()
    assert tab._preview_toggle.isChecked() is True
    assert "Saved" in tab._preview.toPlainText()
    panel.close()


def test_file_viewer_preview_checkbox_toggles_markdown_source(qapp, workspace):
    path = workspace / "README.md"
    path.write_text("# Preview\n", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._preview_toggle.setChecked(False)

    assert tab._editor.isVisibleTo(tab)
    assert tab._preview.isHidden()
    assert tab._edit_mode is True

    tab._preview_toggle.setChecked(True)

    assert tab._preview.isVisibleTo(tab)
    assert tab._editor.isHidden()
    panel.close()


def test_file_viewer_escape_key_returns_clean_markdown_edit_to_preview(qapp, workspace):
    path = workspace / "README.md"
    path.write_text("# Original\n", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._preview.edit_requested.emit()
    tab._editor.setFocus()
    qapp.processEvents()

    QTest.keyClick(tab._editor, Qt.Key.Key_Escape)

    assert tab._dirty is False
    assert tab._edit_mode is False
    assert tab._preview_toggle.isChecked() is True
    assert tab._preview.isVisibleTo(tab)
    assert tab._editor.isHidden()
    panel.close()


def test_file_viewer_escape_discards_markdown_edit_and_returns_to_preview(qapp, workspace):
    path = workspace / "README.md"
    original = "# Original\n"
    path.write_text(original, encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._preview.edit_requested.emit()
    tab._editor.setPlainText("# Unsaved\n")
    assert tab._dirty is True

    tab._editor.setFocus()
    qapp.processEvents()
    QTest.keyClick(tab._editor, Qt.Key.Key_Escape)

    assert path.read_text(encoding="utf-8") == original
    assert tab._dirty is False
    assert panel._tabs.tabText(0) == "README.md"
    assert tab._edit_mode is False
    assert tab._status.text() == "Reverted"
    assert tab._preview_toggle.isChecked() is True
    assert tab._preview.isVisibleTo(tab)
    assert tab._editor.isHidden()
    _wait_until(qapp, lambda: "Original" in tab._preview.toPlainText())
    assert "Original" in tab._preview.toPlainText()
    panel.close()


def test_file_viewer_revert_only_enabled_for_unsaved_changes(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)

    assert tab._revert_btn.isEnabled() is False

    tab._editor.edit_requested.emit()
    tab._editor.setPlainText("print('dirty')\n")

    assert tab._revert_btn.isEnabled() is True

    tab._save()

    assert tab._revert_btn.isEnabled() is False
    panel.close()


def test_file_viewer_line_number_gutter_expands(qapp):
    short = _TextFileTab("short.py", "x\n", "", None)
    long = _TextFileTab("long.py", "\n".join(str(i) for i in range(120)), "", None)

    assert long._editor.line_number_area_width() > short._editor.line_number_area_width()

    short.close()
    long.close()
    short.deleteLater()
    long.deleteLater()
    qapp.processEvents()


def test_file_viewer_keeps_truncated_preview_read_only(qapp, workspace, monkeypatch):
    monkeypatch.setattr("ui.widgets.file_viewer.MAX_FILE_PREVIEW_BYTES", 8)
    path = workspace / "big.txt"
    path.write_text("0123456789abcdef", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._editor.setPlainText("changed")
    tab._save()

    assert tab._editor.isReadOnly() is True
    assert tab._save_btn.isEnabled() is False
    assert path.read_text(encoding="utf-8") == "0123456789abcdef"
    panel.close()


def test_text_file_tab_minimap_scrolls_editor(qapp):
    tab = _TextFileTab("demo.py", "\n".join(f"line {i}" for i in range(200)), "", None)
    tab._minimap.resize(86, 240)
    qapp.processEvents()

    assert tab._minimap.width() == 86
    assert tab._editor.toPlainText().startswith("line 0")

    scroll = tab._editor.verticalScrollBar()
    scroll.setRange(0, 100)
    scroll.setPageStep(20)
    scroll.setValue(scroll.minimum())
    tab._minimap._scroll_to_y(tab._minimap.height())

    assert scroll.value() > scroll.minimum()
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_text_file_tab_minimap_uses_syntax_colors(qapp):
    tab = _TextFileTab("demo.py", "def hello():\n    return 'world'\n", "", None)
    p = palette()

    syntax_color = tab._minimap._line_color("def hello():", p, 1)
    fallback_color = tab._minimap._line_color("plain text", p, 1)
    tab._editor.set_changed_lines({1})
    changed_color = tab._minimap._line_color("def hello():", p, 1)

    assert syntax_color.name() != QColor(p["TEXT_DIM"]).name()
    assert syntax_color.alpha() == 130
    assert fallback_color.alpha() == 130
    assert changed_color.name() == QColor(p["SUCCESS"]).name()
    assert changed_color.alpha() == 150
    tab.close()
    tab.deleteLater()
    qapp.processEvents()


def test_text_file_tab_minimap_wheel_scrolls_editor(qapp):
    tab = _TextFileTab("demo.py", "\n".join(f"line {i}" for i in range(200)), "", None)
    tab._minimap.resize(86, 240)
    qapp.processEvents()

    scroll = tab._editor.verticalScrollBar()
    scroll.setRange(0, 100)
    scroll.setValue(scroll.minimum())
    event = type(
        "WheelEvent",
        (),
        {
            "angleDelta": lambda self: type("Delta", (), {"y": lambda self: -120})(),
            "accept": lambda self: None,
        },
    )()
    tab._minimap.wheelEvent(event)

    assert scroll.value() > scroll.minimum()
    assert tab._minimap.cursor().shape() == Qt.CursorShape.PointingHandCursor
    tab.close()
    tab.deleteLater()
    qapp.processEvents()
