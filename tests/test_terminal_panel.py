import sys

from PyQt6.QtCore import QMimeData, QUrl, Qt
from PyQt6.QtGui import QGuiApplication, QKeyEvent, QTextCursor

import ui.widgets.terminal_panel as terminal_panel
from services.terminal_refs import TERMINAL_REF_MIME
from ui.widgets.terminal_panel import IntegratedTerminalPanel, TerminalTextEdit, _terminal_input_from_mime


def _press(widget, key, text="", modifiers=Qt.KeyboardModifier.NoModifier):
    event = QKeyEvent(QKeyEvent.Type.KeyPress, key, modifiers, text)
    widget.keyPressEvent(event)


def test_terminal_text_edit_maps_basic_keys(qapp):
    edit = TerminalTextEdit()
    sent = []
    edit.input_requested.connect(sent.append)

    _press(edit, Qt.Key.Key_A, "a")
    _press(edit, Qt.Key.Key_Return)
    _press(edit, Qt.Key.Key_Backspace)
    _press(edit, Qt.Key.Key_Up)

    expected_backspace = "\b" if sys.platform == "win32" else "\x7f"
    assert sent == ["a", "\r\n", expected_backspace, "\x1b[A"]


def test_terminal_text_edit_keeps_tab_for_shell_completion(qapp):
    edit = TerminalTextEdit()
    sent = []
    edit.input_requested.connect(sent.append)

    assert not edit.focusNextPrevChild(True)
    _press(edit, Qt.Key.Key_Tab, "\t")

    assert sent == ["\t"]


def test_terminal_text_edit_renders_ansi_foreground_colors(qapp):
    edit = TerminalTextEdit()

    edit.append_output("\x1b[31mred\x1b[0m plain")

    assert edit.toPlainText() == "red plain"
    cursor = QTextCursor(edit.document())
    cursor.setPosition(1)
    assert cursor.charFormat().foreground().color().name() == "#e06c75"
    cursor.setPosition(5)
    assert cursor.charFormat().foreground().color().name() != "#e06c75"


def test_terminal_text_edit_keeps_split_ansi_sequences(qapp):
    edit = TerminalTextEdit()

    edit.append_output("\x1b[3")
    edit.append_output("2mgreen")

    assert edit.toPlainText() == "green"
    cursor = QTextCursor(edit.document())
    cursor.setPosition(1)
    assert cursor.charFormat().foreground().color().name() == "#98c379"


def test_terminal_text_edit_renders_native_terminal_frame(qapp):
    edit = TerminalTextEdit()
    edit.render_frame({
        "columns": 3,
        "lines": 2,
        "text": "RG \nok ",
        "cursor_row": 1,
        "cursor_column": 2,
        "spans": [
            {"start": 0, "length": 1, "foreground": {"kind": "named", "value": 1}, "background": {"kind": "named", "value": 257}, "flags": 2},
            {"start": 1, "length": 1, "foreground": {"kind": "rgb", "value": [10, 200, 30]}, "background": {"kind": "named", "value": 257}, "flags": 0},
        ],
    })

    assert edit.toPlainText() == "RG \nok "
    cursor = QTextCursor(edit.document())
    cursor.setPosition(0)
    assert cursor.charFormat().foreground().color().name() == "#e06c75"
    assert cursor.charFormat().fontWeight() == 700
    cursor.setPosition(2)
    assert cursor.charFormat().foreground().color().name() == "#0ac81e"


def test_terminal_text_edit_coalesces_native_frames(qapp):
    edit = TerminalTextEdit()
    edit.queue_frame({"columns": 1, "lines": 1, "text": "a", "spans": [], "cursor_row": 0, "cursor_column": 0})
    edit.queue_frame({"columns": 1, "lines": 1, "text": "b", "spans": [], "cursor_row": 0, "cursor_column": 0})

    edit._flush_frame()

    assert edit.toPlainText() == "b"


def test_terminal_text_edit_ctrl_c_sends_interrupt_without_selection(qapp):
    edit = TerminalTextEdit()
    sent = []
    edit.input_requested.connect(sent.append)

    _press(edit, Qt.Key.Key_C, "", Qt.KeyboardModifier.ControlModifier)

    assert sent == ["\x03"]


def test_terminal_text_edit_pastes_clipboard_text_to_terminal(qapp):
    edit = TerminalTextEdit()
    sent = []
    edit.input_requested.connect(sent.append)
    mime = QMimeData()
    mime.setText("echo pasted")
    QGuiApplication.clipboard().setMimeData(mime)

    _press(edit, Qt.Key.Key_V, "v", Qt.KeyboardModifier.ControlModifier)

    assert sent == ["echo pasted"]


def test_terminal_input_mime_accepts_and_quotes_dropped_files(qapp):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("C:/my folder/file.txt")])

    assert _terminal_input_from_mime(mime) == '"C:/my folder/file.txt"'


def test_terminal_text_edit_copy_keeps_plain_text_and_composer_reference(qapp):
    edit = TerminalTextEdit()
    edit.set_terminal_id("a1b2c3d4")
    edit.append_output("alpha\nbeta")
    cursor = edit.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)

    mime = edit.copy_mime()

    assert mime.text() == "beta"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[a1b2c3d4:2:2]"


def test_terminal_text_edit_drag_mime_is_tab_specific_reference(qapp):
    edit = TerminalTextEdit()
    edit.set_terminal_id("a1b2c3d4")
    edit.append_output("alpha\nbeta")
    cursor = edit.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)

    mime = edit.drag_mime()

    assert mime is not None
    assert mime.text() == "#term[a1b2c3d4:2:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[a1b2c3d4:2:2]"


def test_terminal_text_edit_drag_requires_selection(qapp):
    edit = TerminalTextEdit()
    edit.append_output("alpha")

    assert edit.drag_mime() is None


def test_terminal_text_edit_partial_line_copy_uses_containing_line_reference(qapp):
    edit = TerminalTextEdit()
    edit.set_terminal_id("a1b2c3d4")
    edit.append_output("alpha\nbeta")
    cursor = edit.textCursor()
    cursor.setPosition(7)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)

    mime = edit.copy_mime()

    assert mime.text() == "eta"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[a1b2c3d4:2:2]"


def test_terminal_text_edit_partial_line_drag_uses_containing_line_reference(qapp):
    edit = TerminalTextEdit()
    edit.set_terminal_id("a1b2c3d4")
    edit.append_output("alpha\nbeta")
    cursor = edit.textCursor()
    cursor.setPosition(7)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)

    mime = edit.drag_mime()

    assert mime is not None
    assert mime.text() == "#term[a1b2c3d4:2:2]"


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _FakeSession:
    label = "pwsh"

    def __init__(self, *_args):
        self.writes = []
        self.started = _FakeSignal()
        self.output = _FakeSignal()
        self.finished = _FakeSignal()
        self.terminated = False

    def write(self, text):
        self.writes.append(text)

    def start(self):
        self.started.emit()

    def terminate(self):
        self.terminated = True


class _NativeFakeSession(_FakeSession):
    @staticmethod
    def available():
        return True

    def __init__(self, *_args):
        super().__init__()
        self.frame = _FakeSignal()
        self.size = None
        self.resizes = []

    def set_size(self, columns, lines):
        self.size = (columns, lines)

    def resize(self, columns, lines):
        self.resizes.append((columns, lines))


def _new_panel_with_fake_sessions(monkeypatch):
    monkeypatch.setattr(terminal_panel, "NativeTerminalSession", _FakeSession)
    return IntegratedTerminalPanel("C:/work")


def test_integrated_terminal_header_shows_shell_without_cwd(monkeypatch, qapp, tmp_path):
    panel = _new_panel_with_fake_sessions(monkeypatch)
    panel.cwd = str(tmp_path)
    panel.new_terminal()

    assert panel._tab_bar.tabText(0) == "pwsh"
    assert str(tmp_path) not in panel._tab_bar.tabText(0)


def test_integrated_terminal_uses_native_frame_without_losing_tab_ui(monkeypatch, qapp):
    monkeypatch.setattr(terminal_panel, "NativeTerminalSession", _NativeFakeSession)
    panel = IntegratedTerminalPanel("C:/work")

    panel.new_terminal()
    session = panel.active_session()
    session.output.emit("raw terminal bytes")
    session.frame.emit({
        "columns": 2, "lines": 1, "text": "ok", "spans": [], "cursor_row": 0, "cursor_column": 1,
    })
    panel.output._flush_frame()

    assert isinstance(session, _NativeFakeSession)
    assert session.size is not None
    assert panel.output.toPlainText() == "ok"

    _press(panel.output, Qt.Key.Key_Return)
    assert session.writes == ["\r"]


def test_integrated_terminal_clear_redraws_active_prompt(monkeypatch, qapp):
    panel = _new_panel_with_fake_sessions(monkeypatch)
    panel.new_terminal()
    session = panel.active_session()
    panel.output.append_output("PS C:/work> command")

    panel.clear()

    assert panel.output.toPlainText() == ""
    assert session.writes == ["\x0c"]


def test_integrated_terminal_chrome_uses_icon_buttons(qapp):
    panel = IntegratedTerminalPanel("C:/work")

    assert panel._clear_btn.text() == ""
    assert not panel._clear_btn.icon().isNull()
    assert panel._clear_btn.toolTip() == "Clear terminal"
    assert panel._new_btn.toolTip() == "New terminal"
    assert panel._tab_bar.tabsClosable() is True
    assert panel._tab_bar.minimumWidth() == 140


def test_integrated_terminal_single_session_accessors(qapp):
    panel = IntegratedTerminalPanel("C:/work")

    assert panel.active_session() is None
    assert panel.active_view() is panel.output


def test_integrated_terminal_creates_switches_renames_and_closes_tabs(monkeypatch, qapp):
    panel = _new_panel_with_fake_sessions(monkeypatch)
    panel.new_terminal()
    first = panel.active_session()
    panel.new_terminal()
    second = panel.active_session()

    assert panel._tab_bar.count() == 2
    assert panel.active_view().styleSheet() == panel._tabs[0].output.styleSheet()
    assert [panel._tab_bar.tabText(index) for index in range(2)] == ["pwsh", "pwsh 2"]
    assert panel.active_session() is second

    monkeypatch.setattr(terminal_panel.QInputDialog, "getText", lambda *_args, **_kwargs: ("Build", True))
    panel.rename_terminal(0)
    panel._tab_bar.setCurrentIndex(0)
    assert panel._tab_bar.tabText(0) == "Build"
    assert panel.active_session() is first

    panel.close_terminal(0)
    assert first.terminated is True
    assert panel._tab_bar.count() == 1
    assert panel.active_session() is second


def test_integrated_terminal_closing_last_tab_hides_panel(monkeypatch, qapp):
    panel = _new_panel_with_fake_sessions(monkeypatch)
    panel.new_terminal()
    closed = []
    panel.close_requested.connect(lambda: closed.append(True))

    panel.close_terminal()

    assert panel._tab_bar.count() == 0
    assert closed == [True]


def test_integrated_terminal_terminate_closes_every_tab(monkeypatch, qapp):
    panel = _new_panel_with_fake_sessions(monkeypatch)
    panel.new_terminal()
    first = panel.active_session()
    panel.new_terminal()
    second = panel.active_session()

    panel.terminate()

    assert first.terminated is True
    assert second.terminated is True
    assert panel._tab_bar.count() == 0
    assert panel.active_session() is None


def test_integrated_terminal_does_not_emit_a_chat_result_for_closed_tab(monkeypatch, qapp):
    panel = _new_panel_with_fake_sessions(monkeypatch)
    panel.new_terminal()
    tab = panel._tabs[0]
    results = []
    panel.terminal_finished.connect(results.append)

    tab.closed = True
    panel._on_finished(tab, {"exit_code": 0})

    assert results == []


def test_terminal_text_edit_applies_backspace_echo(qapp):
    edit = TerminalTextEdit()

    edit.append_output("Write-Output ab\bc")

    assert edit.toPlainText() == "Write-Output ac"


def test_terminal_text_edit_applies_backspace_space_backspace_echo(qapp):
    edit = TerminalTextEdit()

    edit.append_output("ab\b \bc")

    assert edit.toPlainText() == "ac"


def test_terminal_text_edit_normalizes_crlf_without_extra_blank_lines(qapp):
    edit = TerminalTextEdit()

    edit.append_output("one\r\ntwo\r\n")

    assert edit.toPlainText() == "one\ntwo\n"
