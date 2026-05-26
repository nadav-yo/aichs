from PyQt6.QtCore import Qt

from ui.widgets.bubble import MessageBubble


def test_streamed_assistant_text_finalizes_to_rich_text(qapp):
    bubble = MessageBubble("", is_user=False, typing=True)
    bubble.append("## Title\n\n**bold**")
    bubble.finalize(bubble._copy_text)
    assert bubble._md_source is not None
    assert bubble.label.textFormat() == Qt.TextFormat.RichText
    assert "Title" in bubble.label.text()
    assert "bold" in bubble.label.text()
