from PyQt6.QtGui import QGuiApplication

from services.terminal_refs import TERMINAL_REF_MIME
from ui.widgets.terminal_card import TerminalCard


def test_terminal_card_copy_text_is_plain_output(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="!term[1:2]")

    assert card.copy_text() == "alpha\nbeta"
    assert card.copy_ref() == "!term[1:2]"


def test_terminal_output_copy_uses_selection_and_precise_hidden_reference(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="!term[1:2]")
    output = card._output
    cursor = output.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)

    output.copy()

    mime = QGuiApplication.clipboard().mimeData()
    assert QGuiApplication.clipboard().text() == "beta"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "!term[2:2]"


def test_terminal_output_partial_line_copy_has_no_hidden_reference(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="!term[1:2]")
    output = card._output
    cursor = output.textCursor()
    cursor.setPosition(7)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)

    output.copy()

    mime = QGuiApplication.clipboard().mimeData()
    assert QGuiApplication.clipboard().text() == "eta"
    assert not mime.hasFormat(TERMINAL_REF_MIME)


def test_terminal_output_copy_without_selection_copies_plain_text_and_hidden_reference(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="!term[1:2]")

    card._output.copy()

    mime = QGuiApplication.clipboard().mimeData()
    assert QGuiApplication.clipboard().text() == "alpha\nbeta"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "!term[1:2]"


def test_terminal_card_stream_skips_leading_blank_before_ref_lines(qapp):
    card = TerminalCard()
    card.append_line("")
    card.append_line("-a---          25/05/2026    13:53            223 pytest.ini")
    card.append_line("-a---          27/05/2026    23:02           3736 README.md")
    card.finish(0, detail="exit 0", ref="!term[1:2]")
    output = card._output
    text = output.toPlainText()
    start = text.index("README.md") - len("-a---          27/05/2026    23:02           3736 ")
    cursor = output.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(len(text), cursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)

    output.copy()

    mime = QGuiApplication.clipboard().mimeData()
    assert QGuiApplication.clipboard().text().endswith("README.md")
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "!term[2:2]"
