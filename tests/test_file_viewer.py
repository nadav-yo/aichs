from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QTextCursor

from services.file_editor_refs import AICHS_EDITOR_REF_MIME, parse_editor_refs
from storage.settings import FILE_EDITOR_AUTO_SAVE_KEY
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


class _Settings:
    def __init__(self, data: dict):
        self._data = data

    def load(self) -> dict:
        return dict(self._data)


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


def test_file_viewer_saves_text_edits(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)
    tab._editor.edit_requested.emit()
    tab._editor.setPlainText("print('bye')\n")
    tab._save()

    assert path.read_text(encoding="utf-8") == "print('bye')\n"
    assert tab._dirty is False
    assert tab._edit_mode is False
    assert tab._editor.isReadOnly() is True
    assert tab._save_btn.isEnabled() is False
    panel.close()


def test_file_viewer_marks_dirty_tabs_across_files(qapp, workspace):
    first = workspace / "src" / "main.py"
    second = workspace / "notes.txt"
    second.write_text("notes\n", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(first), repo_root=str(workspace))
    first_tab = panel._tabs.widget(0)
    first_tab._editor.edit_requested.emit()
    first_tab._editor.setPlainText("print('dirty')\n")
    panel.open_file(str(second), repo_root=str(workspace))

    assert panel._tabs.tabText(0) == "* main.py"
    assert panel._tabs.tabToolTip(0).startswith("Unsaved changes")
    assert panel._tabs.tabText(1) == "notes.txt"
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


def test_file_viewer_markdown_uses_preview_until_edit(qapp, workspace):
    path = workspace / "README.md"
    path.write_text("# Hello\n\n| A | B |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    panel = FileViewerPanel(str(workspace))

    panel.open_file(str(path), repo_root=str(workspace))
    tab = panel._tabs.widget(0)

    assert tab._status.text() == "Markdown preview"
    assert tab._preview.isVisibleTo(tab)
    assert tab._editor.isHidden()
    assert "<h1" in tab._preview.toHtml().lower()
    assert "<table" in tab._preview.toHtml().lower()

    tab._preview.edit_requested.emit()
    assert tab._editor.isVisibleTo(tab)
    assert tab._preview.isHidden()
    assert tab._editor.isReadOnly() is False

    tab._editor.setPlainText("# Saved\n")
    tab._save()

    assert path.read_text(encoding="utf-8") == "# Saved\n"
    assert tab._status.text() == "Markdown preview"
    assert tab._preview.isVisibleTo(tab)
    assert tab._editor.isHidden()
    assert "Saved" in tab._preview.toPlainText()
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

    tab._cancel_shortcut.activated.emit()

    assert path.read_text(encoding="utf-8") == original
    assert tab._dirty is False
    assert panel._tabs.tabText(0) == "README.md"
    assert tab._edit_mode is False
    assert tab._status.text() == "Reverted"
    assert tab._preview.isVisibleTo(tab)
    assert tab._editor.isHidden()
    assert "Original" in tab._preview.toPlainText()
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
