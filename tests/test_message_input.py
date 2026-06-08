from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QMimeData, QUrl, Qt
from PyQt6.QtGui import QDropEvent, QImage, QKeyEvent, QTextCursor

from services.file_ref_clipboard import AICHS_MESSAGE_COPY_MIME, file_refs_payload
from services.chat_drag import (
    AICHS_CHAT_DROP_MIME,
    AICHS_COMMIT_DROP_MIME,
    AICHS_FILE_DROP_MIME,
    chat_drop_payload,
    commit_drop_payload,
    file_drop_payload,
)
from services.file_editor_refs import AICHS_EDITOR_REF_MIME, editor_ref_payload
from services.terminal_refs import TERMINAL_REF_MIME
from ui.widgets.message_input import (
    ComposerWidget,
    _images_from_mime,
    _slash_has_args,
    _with_visible_file_mentions,
)


def _drop_event(mime: QMimeData) -> QDropEvent:
    return QDropEvent(
        QPointF(8, 8),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _move_cursor_to_end(composer: ComposerWidget):
    cursor = composer.input.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    composer.input.setTextCursor(cursor)
    composer.input._on_text_changed()


def test_images_from_mime_local_png(qapp, tmp_path):
    png = tmp_path / "sprite.png"
    QImage(8, 8, QImage.Format.Format_RGB32).save(str(png))

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(png))])

    images = _images_from_mime(mime)
    assert len(images) == 1
    assert images[0].width() == 8


def test_drop_image_url_does_not_insert_text(qapp, tmp_path):
    png = tmp_path / "food-sprites.png"
    QImage(12, 12, QImage.Format.Format_RGB32).save(str(png))

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(png))])

    composer = ComposerWidget()
    pasted: list[QImage] = []
    composer.input.image_pasted.connect(pasted.append)

    composer.input.dropEvent(_drop_event(mime))

    assert composer.input.toPlainText() == ""
    assert len(pasted) == 1
    assert composer.strip.has_images()


def test_drop_non_image_url_does_not_insert_text(qapp, tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("hello", encoding="utf-8")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(doc))])

    composer = ComposerWidget()
    composer.input.dropEvent(_drop_event(mime))

    assert composer.input.toPlainText() == ""
    assert not composer.strip.has_images()


def test_message_input_grows_after_two_lines(qapp):
    composer = ComposerWidget()
    composer.resize(420, composer.sizeHint().height())
    composer.show()
    qapp.processEvents()
    base_height = composer.input.height()

    composer.input.setPlainText("one\ntwo")
    qapp.processEvents()
    two_line_height = composer.input.height()

    composer.input.setPlainText("one\ntwo\nthree")
    qapp.processEvents()
    three_line_height = composer.input.height()

    composer.input.clear()
    qapp.processEvents()

    assert two_line_height == base_height
    assert three_line_height > base_height
    assert composer.input.height() == base_height


def test_paste_terminal_ref_prefers_hidden_reference(qapp):
    composer = ComposerWidget()
    mime = QMimeData()
    mime.setText("d----          27/05/2026    23:59                .aichs")
    mime.setData(TERMINAL_REF_MIME, b"!term[27:27]")

    composer.input.insertFromMimeData(mime)

    assert composer.input.toPlainText() == "!term[27:27]"


def test_paste_aichs_message_adds_visible_file_mention_and_records_refs(qapp):
    composer = ComposerWidget()
    mime = QMimeData()
    text = "services\\git_diff.py: 77%"
    mime.setText(text)
    mime.setData(AICHS_MESSAGE_COPY_MIME, file_refs_payload(text))

    composer.input.insertFromMimeData(mime)

    assert composer.input.toPlainText() == "@services\\git_diff.py: 77%"
    assert composer.take_pasted_file_refs() == ["services\\git_diff.py"]
    assert composer.take_pasted_file_refs() == []


def test_drop_file_ref_inserts_visible_mention(qapp):
    composer = ComposerWidget()
    mime = QMimeData()
    mime.setData(AICHS_FILE_DROP_MIME, file_drop_payload(["src/main.py"]))

    composer.input.dropEvent(_drop_event(mime))

    assert composer.input.toPlainText() == "@src/main.py "


def test_drop_editor_ref_inserts_line_mention_and_records_hidden_file(qapp):
    composer = ComposerWidget()
    mime = QMimeData()
    mime.setText("def main():\n    pass")
    mime.setData(
        AICHS_EDITOR_REF_MIME,
        editor_ref_payload([{
            "path": "src/main.py",
            "start_line": 10,
            "end_line": 11,
            "text": "def main():\n    pass",
        }]),
    )

    composer.input.dropEvent(_drop_event(mime))

    assert composer.input.toPlainText() == "@src/main.py:10-11 "
    assert composer.take_pasted_file_refs() == ["src/main.py"]
    assert composer.take_pasted_file_refs() == []


def test_drop_commit_ref_inserts_commit_text(qapp):
    composer = ComposerWidget()
    mime = QMimeData()
    mime.setData(
        AICHS_COMMIT_DROP_MIME,
        commit_drop_payload([{"hash": "abc1234", "subject": "initial commit"}]),
    )

    composer.input.dropEvent(_drop_event(mime))

    assert composer.input.toPlainText() == "commit abc1234 (initial commit) "


def test_drop_chat_ref_summons_archivist_and_records_hidden_ref(qapp):
    composer = ComposerWidget()
    mime = QMimeData()
    mime.setData(
        AICHS_CHAT_DROP_MIME,
        chat_drop_payload([{"id": "conv1", "title": "Viewport picking"}]),
    )

    composer.input.dropEvent(_drop_event(mime))

    assert composer.input.toPlainText() == '@Archivist using chat "Viewport picking", '
    assert composer.take_pasted_chat_refs() == [{"id": "conv1", "title": "Viewport picking"}]
    assert composer.take_pasted_chat_refs() == []


def test_visible_file_mentions_do_not_absorb_punctuation():
    text = "I read services\\git_diff.py."

    enriched = _with_visible_file_mentions(text, ["services\\git_diff.py"])

    assert enriched == "I read @services\\git_diff.py."


def test_visible_file_mentions_leave_external_text_without_refs_unchanged():
    assert _with_visible_file_mentions("I read services\\git_diff.py.", []) == "I read services\\git_diff.py."


def test_slash_has_args():
    assert _slash_has_args("/continue status")
    assert _slash_has_args("  /guard   status  ")
    assert not _slash_has_args("/continue")
    assert not _slash_has_args("/")
    assert not _slash_has_args("hello /continue status")


def test_tab_completes_slash_picker_without_inserting_tab(qapp):
    composer = ComposerWidget()
    completed = []
    composer.input.picker_complete.connect(lambda: completed.append(True))
    composer.input.setPlainText("/conti")

    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Tab,
        Qt.KeyboardModifier.NoModifier,
    )
    composer.input.keyPressEvent(event)

    assert completed == [True]
    assert composer.input.toPlainText() == "/conti"
    assert event.isAccepted()


def test_bang_shows_terminal_picker_and_tab_completes(qapp):
    composer = ComposerWidget()
    changed = []
    completed = []
    composer.input.terminal_changed.connect(changed.append)
    composer.input.picker_complete.connect(lambda: completed.append(True))
    composer.input.setPlainText("!")

    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Tab,
        Qt.KeyboardModifier.NoModifier,
    )
    composer.input.keyPressEvent(event)
    composer.input.complete_terminal_command()

    assert changed[0] == "!"
    assert completed == [True]
    assert composer.input.toPlainText() == "! "
    assert changed[-1] == ""


def test_terminal_picker_hides_after_command_text(qapp):
    composer = ComposerWidget()
    changed = []
    composer.input.terminal_changed.connect(changed.append)

    composer.input.setPlainText("!")
    composer.input.setPlainText("!dir")

    assert changed == ["!", ""]


def test_tab_completes_mention_picker(qapp):
    composer = ComposerWidget()
    completed = []
    composer.input.mention_confirm.connect(lambda: completed.append(True))
    composer.input.setPlainText("@mai")
    _move_cursor_to_end(composer)

    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Tab,
        Qt.KeyboardModifier.NoModifier,
    )
    composer.input.keyPressEvent(event)

    assert completed == [True]
    assert composer.input.toPlainText() == "@mai"
    assert event.isAccepted()


def test_complete_slash_command_replaces_partial_token(qapp):
    composer = ComposerWidget()
    composer.input.setPlainText("/conti")

    composer.input.complete_slash_command("continue")

    assert composer.input.toPlainText() == "/continue "


def test_complete_slash_command_preserves_args(qapp):
    composer = ComposerWidget()
    composer.input.setPlainText("/conti status")

    composer.input.complete_slash_command("continue")

    assert composer.input.toPlainText() == "/continue status"
