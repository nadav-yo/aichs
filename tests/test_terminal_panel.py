import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

from services.terminal_refs import TERMINAL_REF_MIME
import ui.widgets.terminal_panel as terminal_panel
from ui.widgets.terminal_panel import IntegratedTerminalPanel, TerminalTextEdit


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


def test_terminal_text_edit_ctrl_c_sends_interrupt_without_selection(qapp):
    edit = TerminalTextEdit()
    sent = []
    edit.input_requested.connect(sent.append)

    _press(edit, Qt.Key.Key_C, "", Qt.KeyboardModifier.ControlModifier)

    assert sent == ["\x03"]


def test_terminal_text_edit_copy_adds_hidden_reference_for_full_line_selection(qapp):
    edit = TerminalTextEdit()
    edit.append_output("alpha\nbeta")
    cursor = edit.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)

    mime = edit.copy_mime()

    assert mime.text() == "beta"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[2:2]"


def test_terminal_text_edit_drag_mime_is_reference_link(qapp):
    edit = TerminalTextEdit()
    edit.append_output("alpha\nbeta")
    cursor = edit.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)

    mime = edit.drag_mime()

    assert mime is not None
    assert mime.text() == "#term[2:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[2:2]"


def test_terminal_text_edit_drag_requires_selection(qapp):
    edit = TerminalTextEdit()
    edit.append_output("alpha")

    assert edit.drag_mime() is None


def test_terminal_text_edit_partial_line_copy_has_no_hidden_reference(qapp):
    edit = TerminalTextEdit()
    edit.append_output("alpha\nbeta")
    cursor = edit.textCursor()
    cursor.setPosition(7)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    edit.setTextCursor(cursor)

    mime = edit.copy_mime()

    assert mime.text() == "eta"
    assert not mime.hasFormat(TERMINAL_REF_MIME)


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


def _new_panel_with_fake_sessions(monkeypatch):
    monkeypatch.setattr(terminal_panel, "TerminalSession", _FakeSession)
    return IntegratedTerminalPanel("C:/work")


def test_integrated_terminal_header_shows_shell_without_cwd(monkeypatch, qapp, tmp_path):
    panel = _new_panel_with_fake_sessions(monkeypatch)
    panel.cwd = str(tmp_path)
    panel.new_terminal()

    assert panel._tab_bar.tabText(0) == "pwsh"
    assert str(tmp_path) not in panel._tab_bar.tabText(0)


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
