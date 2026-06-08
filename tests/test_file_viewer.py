from PyQt6.QtCore import Qt

from storage.settings import FILE_EDITOR_AUTO_SAVE_KEY
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
    assert "color:" in tab._editor.toHtml()
    assert hasattr(tab, "_edit_btn") is False

    tab._editor.edit_requested.emit()
    assert tab._editor.isReadOnly() is False
    tab._editor.setPlainText("print('formatted save')\n")
    tab._save()

    assert path.read_text(encoding="utf-8") == "print('formatted save')\n"
    assert panel._tabs.tabText(0) == "main.py"
    assert tab._edit_mode is False
    assert tab._editor.isReadOnly() is True
    assert "color:" in tab._editor.toHtml()
    panel.close()


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
