from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from storage.settings import FILE_EDITOR_AUTO_SAVE_KEY
from ui.theme import palette
from ui.widgets.file_viewer import (
    FileViewerPanel,
    _TextFileTab,
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
