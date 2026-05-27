from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QMimeData, QUrl, Qt
from PyQt6.QtGui import QDropEvent, QImage, QKeyEvent, QTextCursor

from ui.widgets.message_input import ComposerWidget, _images_from_mime, _slash_has_args


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
