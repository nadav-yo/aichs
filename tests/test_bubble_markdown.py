from contextlib import contextmanager
from types import SimpleNamespace

from PyQt6.QtCore import QEvent, Qt

from services.file_ref_clipboard import AICHS_MESSAGE_COPY_MIME, parse_file_refs_payload
from ui.markdown_html import copy_code_url
import ui.widgets.bubble as bubble_module
from ui.widgets.bubble import MessageBubble, _ASYNC_MARKDOWN_RENDER_CHARS, _MarkdownRenderWorker, _to_html


def test_streamed_assistant_text_finalizes_to_rich_text(qapp):
    label = _FakeLabel()
    timer = _FakeTimer()
    bubble = SimpleNamespace(
        _is_user=False,
        _copy_text="## Title\n\n**bold**",
        _md_source=None,
        _stream_render_pending=False,
        label=label,
        _stream_render_timer=timer,
    )

    MessageBubble.finalize(bubble, bubble._copy_text)

    assert bubble._md_source is not None
    assert label.textFormat() == Qt.TextFormat.RichText
    assert "Title" in label.text()
    assert "bold" in label.text()
    assert timer.stopped is True


def test_assistant_stream_appends_coalesce_label_updates(qapp):
    label = _FakeLabel()
    stream_view = _FakeStreamView()
    stream_timer = _FakeTimer()
    typing_timer = _FakeTimer()
    bubble = SimpleNamespace(
        _typing=False,
        _timer=typing_timer,
        _copy_text="",
        _stream_render_pending=False,
        _stream_render_chunks=[],
        _stream_view=stream_view,
        label=label,
        _stream_render_timer=stream_timer,
    )
    bubble._render_stream_text = lambda: MessageBubble._render_stream_text(bubble)

    MessageBubble.append(bubble, "one")
    MessageBubble.append(bubble, " two")
    MessageBubble.append(bubble, " three")

    assert bubble._copy_text == "one two three"
    assert label.set_texts == []
    assert stream_view.appended == ["one"]
    assert stream_timer.start_count == 1
    assert bubble._stream_render_pending is True

    stream_timer.active = False
    MessageBubble._flush_stream_text(bubble)
    assert stream_view.text() == "one two three"
    assert stream_view.appended == ["one", " two three"]
    assert label.set_texts == []
    assert bubble._stream_render_pending is False


def test_single_stream_append_does_not_repaint_on_timer(qapp):
    label = _FakeLabel()
    stream_view = _FakeStreamView()
    stream_timer = _FakeTimer()
    bubble = SimpleNamespace(
        _typing=False,
        _timer=_FakeTimer(),
        _copy_text="",
        _stream_render_pending=False,
        _stream_render_chunks=[],
        _stream_view=stream_view,
        label=label,
        _stream_render_timer=stream_timer,
    )
    bubble._render_stream_text = lambda: MessageBubble._render_stream_text(bubble)

    MessageBubble.append(bubble, "one")
    MessageBubble._flush_stream_text(bubble)

    assert label.set_texts == []
    assert stream_view.appended == ["one"]


def test_finalize_stops_pending_stream_render(qapp):
    label = _FakeLabel()
    stream_view = _FakeStreamView()
    timer = _FakeTimer()
    timer.active = True
    bubble = SimpleNamespace(
        _is_user=False,
        _copy_text="draft",
        _md_source=None,
        _stream_render_pending=True,
        _stream_render_chunks=["draft"],
        _stream_view=stream_view,
        label=label,
        _stream_render_timer=timer,
    )

    MessageBubble.finalize(bubble, "**done**")

    assert timer.stopped is True
    assert bubble._stream_render_pending is False
    assert bubble._stream_render_chunks == []
    assert stream_view.cleared is True
    assert stream_view.hidden is True
    assert label.textFormat() == Qt.TextFormat.RichText
    assert "<strong>done</strong>" in label.text()


def test_large_assistant_markdown_finalizes_asynchronously(qapp):
    label = _FakeLabel()
    timer = _FakeTimer()
    pool = _FakePool()
    source = "# Large\n\n" + ("body text\n" * (_ASYNC_MARKDOWN_RENDER_CHARS // 8))
    bubble = SimpleNamespace(
        _is_user=False,
        _copy_text=source,
        _md_source=None,
        _md_html=None,
        _markdown_render_generation=0,
        _markdown_render_pool=pool,
        _stream_render_pending=False,
        label=label,
        _stream_render_timer=timer,
    )
    bubble._on_markdown_render_done = lambda *args: MessageBubble._on_markdown_render_done(bubble, *args)

    MessageBubble.finalize(bubble, source)

    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == source.strip()
    assert bubble._md_html is None
    assert len(pool.workers) == 1

    worker = pool.workers[0]
    MessageBubble._on_markdown_render_done(
        bubble,
        worker._generation,
        worker._source,
        _to_html(worker._source),
    )

    assert label.textFormat() == Qt.TextFormat.RichText
    assert "<h1>Large</h1>" in label.text()
    assert bubble._md_html == label.text()


def test_markdown_render_worker_records_operation(monkeypatch):
    operations = []

    @contextmanager
    def fake_time_operation(operation, *, detail="", slow_ms=100.0):
        operations.append((operation, detail))
        yield

    monkeypatch.setattr(bubble_module, "time_operation", fake_time_operation)
    worker = _MarkdownRenderWorker(7, "# Timed")
    done = []
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert operations == [("markdown.render", "chars=7")]
    assert done[0][0:2] == (7, "# Timed")
    assert "<h1>Timed</h1>" in done[0][2]


def test_stale_markdown_render_result_is_ignored(qapp):
    label = _FakeLabel()
    bubble = SimpleNamespace(
        _markdown_render_generation=2,
        _md_source="new source",
        _md_html=None,
        label=label,
    )

    MessageBubble._on_markdown_render_done(bubble, 1, "old source", "<p>old</p>")
    MessageBubble._on_markdown_render_done(bubble, 2, "old source", "<p>old</p>")

    assert label.text() == ""
    assert bubble._md_html is None


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


def test_bubble_copy_code_link_copies_code(qapp, monkeypatch):
    import ui.widgets.bubble as bubble_module

    clipboard = _FakeClipboard()
    monkeypatch.setattr(bubble_module.QGuiApplication, "clipboard", lambda: clipboard)
    bubble = SimpleNamespace(file_clicked=SimpleNamespace(emit=lambda _path: None))

    MessageBubble._on_link(bubble, copy_code_url("print('hi')\n"))

    assert clipboard.text() == "print('hi')\n"


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
        self._text = ""

    def setText(self, text):
        self._text = text

    def text(self):
        return self._text

    def setMimeData(self, mime):
        self._mime = mime

    def mimeData(self):
        return self._mime


class _FakeLabel:
    def __init__(self):
        self._format = None
        self._text = ""
        self.set_texts = []
        self.hidden = False

    def setTextFormat(self, text_format):
        self._format = text_format

    def textFormat(self):
        return self._format

    def setText(self, text):
        self.set_texts.append(text)
        self._text = text

    def text(self):
        return self._text

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False


class _FakeStreamView:
    def __init__(self):
        self.appended = []
        self._text = ""
        self.hidden = True
        self.cleared = False

    def append_text(self, text):
        self.appended.append(text)
        self._text += text

    def text(self):
        return self._text

    def show(self):
        self.hidden = False

    def hide(self):
        self.hidden = True

    def clear_text(self):
        self.cleared = True
        self._text = ""


class _FakeTimer:
    def __init__(self):
        self.active = False
        self.start_count = 0
        self.stopped = False

    def isActive(self):
        return self.active

    def start(self):
        self.active = True
        self.start_count += 1

    def stop(self):
        self.active = False
        self.stopped = True


class _FakePool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


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
