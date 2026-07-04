from services.terminal_refs import TERMINAL_REF_MIME
from ui.widgets.terminal_card import TerminalCard


def test_terminal_card_copy_text_is_plain_output(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    assert card.copy_text() == "alpha\nbeta"
    assert card.copy_ref() == "#term[1:2]"


def test_terminal_output_copy_uses_selection_and_precise_hidden_reference(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="#term[1:2]")
    output = card._output
    cursor = output.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)

    mime = output.copy_mime()
    assert mime.text() == "beta"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[2:2]"


def test_terminal_output_partial_line_copy_has_no_hidden_reference(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="#term[1:2]")
    output = card._output
    cursor = output.textCursor()
    cursor.setPosition(7)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)

    mime = output.copy_mime()
    assert mime.text() == "eta"
    assert not mime.hasFormat(TERMINAL_REF_MIME)


def test_terminal_output_copy_without_selection_copies_plain_text_and_hidden_reference(qapp, monkeypatch):
    import ui.widgets.terminal_card as terminal_card

    clipboard = _FakeClipboard()
    monkeypatch.setattr(terminal_card.QGuiApplication, "clipboard", lambda: clipboard)
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    card._output.copy()

    mime = clipboard.mimeData()
    assert clipboard.text() == "alpha\nbeta"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[1:2]"


def test_terminal_card_stream_skips_leading_blank_before_ref_lines(qapp):
    card = TerminalCard()
    card.append_line("")
    card.append_line("-a---          25/05/2026    13:53            223 pytest.ini")
    card.append_line("-a---          27/05/2026    23:02           3736 README.md")
    card.finish(0, detail="exit 0", ref="#term[1:2]")
    output = card._output
    text = output.toPlainText()
    start = text.index("README.md") - len("-a---          27/05/2026    23:02           3736 ")
    cursor = output.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(len(text), cursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)

    mime = output.copy_mime()
    assert mime.text().endswith("README.md")
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[2:2]"


def test_terminal_output_drag_mime_is_reference_link(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="#term[1:2]")
    output = card._output
    cursor = output.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)

    mime = output.drag_mime()

    assert mime is not None
    assert mime.text() == "#term[2:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[2:2]"


def test_terminal_output_partial_line_drag_has_no_payload(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta")
    card.finish(0, detail="exit 0", ref="#term[1:2]")
    output = card._output
    cursor = output.textCursor()
    cursor.setPosition(7)
    cursor.setPosition(10, cursor.MoveMode.KeepAnchor)
    output.setTextCursor(cursor)

    assert output.drag_mime() is None


def test_terminal_output_drag_requires_selection(qapp):
    card = TerminalCard()
    card.set_output("alpha")

    assert card._output.drag_mime() is None


class _FakeClipboard:
    def __init__(self):
        self._mime = None

    def setMimeData(self, mime):
        self._mime = mime

    def mimeData(self):
        return self._mime

    def text(self):
        return self._mime.text() if self._mime is not None else ""


def test_terminal_card_can_start_collapsed_and_expand(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta", collapsed=True)
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    assert card._collapsed is True
    assert card._output.isHidden()
    assert card._output.toPlainText() == ""
    assert card.copy_text() == "alpha\nbeta"
    assert card.copy_ref() == "#term[1:2]"

    card.expand_output()

    assert card._collapsed is False
    assert not card._output.isHidden()
    assert card._output.toPlainText() == "alpha\nbeta"


def test_terminal_card_large_output_uses_bounded_preview(qapp, monkeypatch):
    import ui.widgets.terminal_card as terminal_card

    monkeypatch.setattr(terminal_card, "MAX_TERMINAL_CARD_PREVIEW_CHARS", 8)
    card = TerminalCard()

    card.set_output("1234567890")

    assert card._output.toPlainText() == "12345678"
    assert card._preview_truncated is True
    assert not card._preview.isHidden()


def test_collapsed_terminal_card_copy_mime_is_reference_only(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta", collapsed=True)
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    mime = card.copy_mime()

    assert mime.text() == "#term[1:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[1:2]"


def test_collapsed_terminal_ref_label_copy_mime_has_hidden_reference(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta", collapsed=True)
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    mime = card._ref.copy_mime()

    assert mime.text() == "#term[1:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[1:2]"


def test_collapsed_terminal_card_drag_mime_is_reference_link(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta", collapsed=True)
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    mime = card.drag_mime()

    assert mime is not None
    assert mime.text() == "#term[1:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[1:2]"


def test_collapsed_terminal_ref_label_drag_mime_is_reference_link(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta", collapsed=True)
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    mime = card._ref.drag_mime()

    assert mime is not None
    assert mime.text() == "#term[1:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[1:2]"


def test_collapsed_terminal_status_label_copy_mime_has_hidden_reference(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta", collapsed=True)
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    mime = card._status.copy_mime()

    assert mime.text() == "#term[1:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[1:2]"


def test_collapsed_terminal_status_label_drag_mime_has_hidden_reference(qapp):
    card = TerminalCard()
    card.set_output("alpha\nbeta", collapsed=True)
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    mime = card._status.drag_mime()

    assert mime is not None
    assert mime.text() == "#term[1:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[1:2]"


def test_collapsed_terminal_card_copy_puts_hidden_reference_on_clipboard(qapp, monkeypatch):
    import ui.widgets.terminal_card as terminal_card

    clipboard = _FakeClipboard()
    monkeypatch.setattr(terminal_card.QGuiApplication, "clipboard", lambda: clipboard)
    card = TerminalCard()
    card.set_output("alpha\nbeta", collapsed=True)
    card.finish(0, detail="exit 0", ref="#term[1:2]")

    card.copy()

    mime = clipboard.mimeData()
    assert clipboard.text() == "#term[1:2]"
    assert bytes(mime.data(TERMINAL_REF_MIME)).decode("utf-8") == "#term[1:2]"
