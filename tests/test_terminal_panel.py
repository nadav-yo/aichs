import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

from services.terminal_refs import TERMINAL_REF_MIME
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


class _FakeSession:
    label = "pwsh"

    def __init__(self):
        self.writes = []

    def write(self, text):
        self.writes.append(text)


def test_integrated_terminal_header_shows_shell_without_cwd(qapp, tmp_path):
    panel = IntegratedTerminalPanel(str(tmp_path))
    panel._session = _FakeSession()

    panel._on_started()

    labels = [label.text() for label in panel.findChildren(type(panel._shell_label))]
    assert panel._shell_label.text() == "pwsh"
    assert str(tmp_path) not in labels


def test_integrated_terminal_clear_redraws_active_prompt(qapp):
    panel = IntegratedTerminalPanel("C:/work")
    session = _FakeSession()
    panel._session = session
    panel._finished = False
    panel.output.append_output("PS C:/work> command")

    panel.clear()

    assert panel.output.toPlainText() == ""
    assert session.writes == ["\x0c"]


def test_integrated_terminal_chrome_uses_icon_buttons(qapp):
    panel = IntegratedTerminalPanel("C:/work")

    assert panel._clear_btn.text() == ""
    assert panel._close_btn.text() == ""
    assert not panel._clear_btn.icon().isNull()
    assert not panel._close_btn.icon().isNull()
    assert panel._clear_btn.toolTip() == "Clear terminal"
    assert panel._close_btn.toolTip() == "Hide terminal"


def test_integrated_terminal_single_session_accessors(qapp):
    panel = IntegratedTerminalPanel("C:/work")

    assert panel.active_session() is None
    assert panel.active_view() is panel.output


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
