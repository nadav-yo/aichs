from types import SimpleNamespace

from PyQt6.QtCore import QEvent, Qt

from services.file_ref_clipboard import AICHS_MESSAGE_COPY_MIME, parse_file_refs_payload
from ui.widgets.bubble import MessageBubble, _to_html


def test_streamed_assistant_text_finalizes_to_rich_text(qapp):
    label = _FakeLabel()
    bubble = SimpleNamespace(
        _is_user=False,
        _copy_text="## Title\n\n**bold**",
        _md_source=None,
        label=label,
    )

    MessageBubble.finalize(bubble, bubble._copy_text)

    assert bubble._md_source is not None
    assert label.textFormat() == Qt.TextFormat.RichText
    assert "Title" in label.text()
    assert "bold" in label.text()


def test_assistant_markdown_linkifies_plain_file_paths():
    html = _to_html("The coverage gap in services\\git_diff.py comes from branches.")

    assert 'href="aichs-file:services\\git_diff.py"' in html
    assert ">services\\git_diff.py</a>" in html


def test_assistant_markdown_linkifies_file_paths_in_lists():
    html = _to_html(
        "- services\\chat.py: 79%\n"
        "- services\\git_diff.py: 77%\n"
        "- storage\\repository.py: 88%"
    )

    assert 'href="aichs-file:services\\chat.py"' in html
    assert 'href="aichs-file:services\\git_diff.py"' in html
    assert 'href="aichs-file:storage\\repository.py"' in html


def test_assistant_markdown_does_not_relink_existing_links():
    html = _to_html("[services/chat.py](aichs-file:services/chat.py)")

    assert html.count("aichs-file:services/chat.py") == 1


def test_bubble_copy_adds_aichs_file_ref_metadata(qapp):
    bubble = SimpleNamespace(
        _copy_text="Coverage mentions services\\git_diff.py: 77%",
        label=SimpleNamespace(hasSelectedText=lambda: False),
    )
    bubble._selected_or_copy_text = lambda: MessageBubble._selected_or_copy_text(bubble)

    mime = MessageBubble._copy_mime(bubble)
    assert mime.text() == "Coverage mentions services\\git_diff.py: 77%"
    assert parse_file_refs_payload(mime.data(AICHS_MESSAGE_COPY_MIME)) == [
        "services\\git_diff.py"
    ]


def test_bubble_keyboard_copy_adds_aichs_file_ref_metadata(qapp, monkeypatch):
    import ui.widgets.bubble as bubble_module

    clipboard = _FakeClipboard()
    monkeypatch.setattr(bubble_module.QGuiApplication, "clipboard", lambda: clipboard)
    label = SimpleNamespace(hasSelectedText=lambda: False)
    bubble = SimpleNamespace(
        label=label,
        _copy_text="The file you just provided is services/chat.py.",
    )
    bubble._selected_or_copy_text = lambda: MessageBubble._selected_or_copy_text(bubble)
    bubble._copy_mime = lambda: MessageBubble._copy_mime(bubble)
    bubble._copy_to_clipboard = lambda: MessageBubble._copy_to_clipboard(bubble)
    event = _FakeKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_C,
        Qt.KeyboardModifier.ControlModifier,
    )
    handled = MessageBubble.eventFilter(bubble, label, event)

    mime = clipboard.mimeData()
    assert handled is True
    assert event.isAccepted()
    assert mime.text() == "The file you just provided is services/chat.py."
    assert parse_file_refs_payload(mime.data(AICHS_MESSAGE_COPY_MIME)) == [
        "services/chat.py"
    ]


class _FakeClipboard:
    def __init__(self):
        self._mime = None

    def setMimeData(self, mime):
        self._mime = mime

    def mimeData(self):
        return self._mime


class _FakeLabel:
    def __init__(self):
        self._format = None
        self._text = ""
        self.hidden = False

    def setTextFormat(self, text_format):
        self._format = text_format

    def textFormat(self):
        return self._format

    def setText(self, text):
        self._text = text

    def text(self):
        return self._text

    def hide(self):
        self.hidden = True


class _FakeKeyEvent:
    def __init__(self, event_type, key, modifiers):
        self._type = event_type
        self._key = key
        self._modifiers = modifiers
        self._accepted = False

    def type(self):
        return self._type

    def key(self):
        return self._key

    def modifiers(self):
        return self._modifiers

    def accept(self):
        self._accepted = True

    def isAccepted(self):
        return self._accepted
