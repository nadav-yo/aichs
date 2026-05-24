import re

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QDragEnterEvent, QDropEvent, QTextCursor

from config import MAX_INLINE_IMAGE_DIMENSION
from services.content import encode_image
from ui.theme import composer_style, chat_font_pt, palette, ACCENT

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class MessageInput(QTextEdit):
    send_requested      = pyqtSignal()
    edit_last_requested = pyqtSignal()
    image_pasted        = pyqtSignal(QImage)
    slash_changed       = pyqtSignal(str)   # "/" + typed text, or "" when leaving slash mode
    mention_changed     = pyqtSignal(str)   # "@" + typed text, or "" when leaving file mention mode
    picker_next         = pyqtSignal()
    picker_prev         = pyqtSignal()
    picker_confirm      = pyqtSignal()
    mention_next        = pyqtSignal()
    mention_prev        = pyqtSignal()
    mention_confirm     = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Message…")
        self.setAcceptRichText(False)
        self.setFixedHeight(72)
        self._in_slash_mode = False
        self._in_mention_mode = False
        self._mention_start = -1
        self._apply_style()
        self.textChanged.connect(self._on_text_changed)

    def _apply_style(self):
        self.setStyleSheet(composer_style())

    def apply_font_size(self, font_pt: int | None = None):
        self.setStyleSheet(composer_style(font_pt))

    def apply_appearance(self):
        self.setStyleSheet(composer_style())

    def _on_text_changed(self):
        text = self.toPlainText()
        if text.startswith("/"):
            self._in_slash_mode = True
            self.slash_changed.emit(text)
        elif self._in_slash_mode:
            self._in_slash_mode = False
            self.slash_changed.emit("")

        query = "" if self._in_slash_mode else self._current_mention_query(text)
        if query:
            self._in_mention_mode = True
            self.mention_changed.emit(query)
        elif self._in_mention_mode:
            self._in_mention_mode = False
            self._mention_start = -1
            self.mention_changed.emit("")

    def _current_mention_query(self, text: str) -> str:
        pos = self.textCursor().position()
        before = text[:pos]
        match = re.search(r"(^|[\s(])@([^\s@]*)$", before)
        if not match:
            return ""
        self._mention_start = match.start(2) - 1
        return "@" + match.group(2)

    def exit_slash_mode(self):
        self._in_slash_mode = False

    def exit_mention_mode(self):
        self._in_mention_mode = False
        self._mention_start = -1

    def insert_file_mention(self, rel_path: str):
        token = f'@"{rel_path}"' if any(ch.isspace() for ch in rel_path) else f"@{rel_path}"
        cursor = self.textCursor()
        end = cursor.position()
        start = self._mention_start if self._mention_start >= 0 else end
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(token + " ")
        self.setTextCursor(cursor)
        self.exit_mention_mode()
        self.mention_changed.emit("")

    def add_file_mention(self, rel_path: str):
        token = f'@"{rel_path}"' if any(ch.isspace() for ch in rel_path) else f"@{rel_path}"
        cursor = self.textCursor()
        text = self.toPlainText()
        pos = cursor.position()
        prefix = "" if pos == 0 or text[pos - 1].isspace() else " "
        suffix = "" if pos < len(text) and text[pos].isspace() else " "
        cursor.insertText(prefix + token + suffix)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event):
        if self._in_mention_mode:
            key = event.key()
            if key == Qt.Key.Key_Up:
                self.mention_prev.emit()
                event.accept()
                return
            if key == Qt.Key.Key_Down:
                self.mention_next.emit()
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            ):
                self.mention_confirm.emit()
                event.accept()
                return
            if key == Qt.Key.Key_Escape:
                self.exit_mention_mode()
                self.mention_changed.emit("")
                event.accept()
                return

        if self._in_slash_mode:
            key = event.key()
            if key == Qt.Key.Key_Up:
                self.picker_prev.emit()
                event.accept()
                return
            if key == Qt.Key.Key_Down:
                self.picker_next.emit()
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            ):
                self.picker_confirm.emit()
                event.accept()
                return
            if key == Qt.Key.Key_Escape:
                self._in_slash_mode = False
                self.slash_changed.emit("")
                event.accept()
                return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                event.accept()
                self.send_requested.emit()
                return
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Up and not self.toPlainText():
            self.edit_last_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage):
                self.image_pasted.emit(image)
                return
        super().insertFromMimeData(source)


class _Thumb(QWidget):
    remove_requested = pyqtSignal()

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._img = QLabel()
        self._img.setPixmap(pixmap.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))

        self._remove = QPushButton("✕")
        self._remove.setFixedSize(18, 18)
        self._remove.clicked.connect(self.remove_requested.emit)

        row.addWidget(self._img)
        row.addWidget(self._remove, 0, Qt.AlignmentFlag.AlignTop)
        self.apply_appearance()

    def apply_appearance(self):
        p = palette()
        self._img.setStyleSheet(f"border:1px solid {p['BORDER']}; border-radius:6px;")
        self._remove.setStyleSheet(
            f"QPushButton {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER']}; border-radius:9px; font-size:10px; padding:0; }}"
            f"QPushButton:hover {{ color:#ff5555; }}"
        )


class ImageStrip(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 4)
        self._layout.setSpacing(8)
        self._layout.addStretch()
        self.hide()

    def add_image(self, image: QImage):
        image = _scaled_for_inline(image)
        media_type, data_b64, raw = encode_image(image)
        pixmap = QPixmap()
        pixmap.loadFromData(raw)

        thumb = _Thumb(pixmap)
        thumb.remove_requested.connect(lambda t=thumb: self._remove_thumb(t))
        self._items.append({
            "media_type": media_type,
            "data": data_b64,
            "widget": thumb,
        })
        self._layout.insertWidget(self._layout.count() - 1, thumb)
        self.show()
        self.changed.emit()

    def _remove_thumb(self, thumb: _Thumb):
        idx = next((i for i, item in enumerate(self._items) if item["widget"] is thumb), None)
        if idx is None:
            return
        self._layout.removeWidget(thumb)
        thumb.deleteLater()
        self._items.pop(idx)
        if not self._items:
            self.hide()
        self.changed.emit()

    def clear(self):
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._items.clear()
        self.hide()

    def images(self) -> list[dict]:
        return [{"media_type": i["media_type"], "data": i["data"]} for i in self._items]

    def has_images(self) -> bool:
        return bool(self._items)


class ComposerWidget(QWidget):
    send_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._active_skill = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # skill chip row
        self._skill_row = QWidget()
        chip_layout = QHBoxLayout(self._skill_row)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(0)
        self._skill_chip = QPushButton()
        self._skill_chip.setFixedHeight(22)
        self._skill_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skill_chip.clicked.connect(self.clear_skill)
        chip_layout.addWidget(self._skill_chip)
        chip_layout.addStretch()
        self._skill_row.hide()

        self.strip = ImageStrip()
        self.input = MessageInput()
        self.input.send_requested.connect(self.send_requested.emit)
        self.input.image_pasted.connect(self.strip.add_image)

        root.addWidget(self._skill_row)
        root.addWidget(self.strip)
        root.addWidget(self.input)

    def text(self) -> str:
        return self.input.toPlainText().strip()

    def clear(self):
        self.input.clear()
        self.strip.clear()

    def set_enabled(self, enabled: bool):
        self.input.setEnabled(enabled)
        self.strip.setEnabled(enabled)

    def focus_input(self):
        self.input.setFocus()

    def apply_font_size(self, font_pt: int | None = None):
        self.input.apply_font_size(font_pt)

    def set_skill(self, skill) -> None:
        self._active_skill = skill
        self._skill_chip.setText(f"  /{skill.name}  ×  ")
        self._skill_chip.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}22; color:{ACCENT}; border:1px solid {ACCENT}55;"
            "border-radius:10px; font-size:11px; padding:0 4px; }}"
            f"QPushButton:hover {{ background:{ACCENT}44; }}"
        )
        self._skill_row.show()

    def clear_skill(self) -> None:
        self._active_skill = None
        self._skill_row.hide()

    def active_skill(self):
        return self._active_skill

    def apply_appearance(self):
        self.input.apply_appearance()
        for item in self.strip._items:
            item["widget"].apply_appearance()
        if self._active_skill:
            self.set_skill(self._active_skill)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        mime = event.mimeData()
        if mime.hasImage():
            image = mime.imageData()
            if isinstance(image, QImage):
                self.strip.add_image(image)
        for url in mime.urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(tuple(_IMAGE_EXTS)):
                image = QImage(path)
                if not image.isNull():
                    self.strip.add_image(image)
        event.acceptProposedAction()


def _scaled_for_inline(image: QImage) -> QImage:
    longest = max(image.width(), image.height())
    if longest <= MAX_INLINE_IMAGE_DIMENSION:
        return image
    return image.scaled(
        MAX_INLINE_IMAGE_DIMENSION,
        MAX_INLINE_IMAGE_DIMENSION,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
