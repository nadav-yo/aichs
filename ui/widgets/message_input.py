import math
import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton, QFrame,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QImage, QPixmap, QDragEnterEvent, QDropEvent, QTextCursor,
    QSyntaxHighlighter, QTextCharFormat, QColor, QFont,
)

from config import MAX_INLINE_IMAGE_DIMENSION
from services.chat_drag import (
    AICHS_CHAT_DROP_MIME,
    AICHS_COMMIT_DROP_MIME,
    AICHS_FILE_DROP_MIME,
    chat_drop_text,
    commit_drop_text,
    file_drop_text,
    parse_chat_drop,
    parse_commit_drop,
    parse_file_drop,
)
from services.content import encode_image
from services.file_editor_refs import (
    AICHS_EDITOR_REF_MIME,
    editor_ref_paths,
    editor_ref_text,
    parse_editor_refs,
)
from services.file_ref_clipboard import AICHS_MESSAGE_COPY_MIME, parse_file_refs_payload
from services.terminal_refs import TERMINAL_REF_MIME
from ui.theme import (
    attachment_remove_button_style,
    attachment_thumbnail_style,
    code_surface_colors,
    composer_reference_colors,
    composer_shell_style,
    composer_style,
    skill_chip_style,
)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_REFERENCE_RE = re.compile(r'(?<!\S)@(?:"[^"]+"|[^\s@]*[^\s@.,:;!?)\]}])')
_INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_INPUT_MIN_HEIGHT = 46
_INPUT_MAX_LINES = 8


class _ReferenceHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self._reference_fmt = QTextCharFormat()
        self._code_fmt = QTextCharFormat()
        self.apply_appearance()

    def apply_appearance(self):
        colors = composer_reference_colors()
        self._reference_fmt = QTextCharFormat()
        self._reference_fmt.setForeground(QColor(colors["fg"]))
        self._reference_fmt.setBackground(QColor(colors["bg"]))
        self._reference_fmt.setFontWeight(QFont.Weight.DemiBold)

        code_colors = code_surface_colors()
        self._code_fmt = QTextCharFormat()
        self._code_fmt.setForeground(QColor(code_colors["foreground"]))
        self._code_fmt.setBackground(QColor(code_colors["background"]))
        self._code_fmt.setFontFamily("Cascadia Code")
        self._code_fmt.setFontWeight(QFont.Weight.DemiBold)
        self.rehighlight()

    def highlightBlock(self, text: str):
        code_spans: list[tuple[int, int]] = []
        for match in _INLINE_CODE_RE.finditer(text):
            start = match.start()
            length = match.end() - start
            code_spans.append((start, match.end()))
            self.setFormat(start, length, self._code_fmt)
        for match in _REFERENCE_RE.finditer(text):
            if any(start <= match.start() < end for start, end in code_spans):
                continue
            self.setFormat(match.start(), match.end() - match.start(), self._reference_fmt)


def _path_is_image(path: str) -> bool:
    return Path(path).suffix.lower() in _IMAGE_EXTS


def _mime_has_attachments(mime) -> bool:
    if mime.hasImage():
        return True
    if mime.hasUrls():
        return any(url.toLocalFile() for url in mime.urls())
    return False


def _mime_has_chat_refs(mime) -> bool:
    return (
        mime.hasFormat(AICHS_EDITOR_REF_MIME)
        or mime.hasFormat(AICHS_FILE_DROP_MIME)
        or mime.hasFormat(AICHS_COMMIT_DROP_MIME)
        or mime.hasFormat(AICHS_CHAT_DROP_MIME)
    )


def _images_from_mime(mime) -> list[QImage]:
    images: list[QImage] = []
    if mime.hasImage():
        image = mime.imageData()
        if isinstance(image, QImage) and not image.isNull():
            images.append(image)
    if mime.hasUrls():
        for url in mime.urls():
            path = url.toLocalFile()
            if path and _path_is_image(path):
                image = QImage(path)
                if not image.isNull():
                    images.append(image)
    return images


class MessageInput(QTextEdit):
    send_requested      = pyqtSignal()
    edit_last_requested = pyqtSignal()
    image_pasted        = pyqtSignal(QImage)
    focus_changed       = pyqtSignal(bool)
    slash_changed       = pyqtSignal(str)   # "/" + typed text, or "" when leaving slash mode
    terminal_changed    = pyqtSignal(str)   # "!" when showing terminal command help, or "" when leaving
    mention_changed     = pyqtSignal(str)   # "@" + typed text, or "" when leaving file mention mode
    picker_next         = pyqtSignal()
    picker_prev         = pyqtSignal()
    picker_confirm      = pyqtSignal()
    picker_complete     = pyqtSignal()
    mention_next        = pyqtSignal()
    mention_prev        = pyqtSignal()
    mention_confirm     = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Message…")
        self.setAcceptRichText(False)
        self.setMinimumHeight(_INPUT_MIN_HEIGHT)
        self.setFixedHeight(_INPUT_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._in_slash_mode = False
        self._in_terminal_mode = False
        self._in_mention_mode = False
        self._mention_start = -1
        self._enter_to_send = False
        self._pasted_file_refs: list[str] = []
        self._pasted_chat_refs: list[dict] = []
        self.setAcceptDrops(True)
        self._reference_highlighter = _ReferenceHighlighter(self.document())
        self._apply_style()
        self.textChanged.connect(self._on_text_changed)
        self.document().documentLayout().documentSizeChanged.connect(self._update_height)
        self._update_height()

    def _apply_style(self):
        self.setStyleSheet(composer_style())

    def apply_font_size(self, font_pt: int | None = None):
        self.setStyleSheet(composer_style(font_pt))
        self._update_height()

    def apply_appearance(self):
        self.setStyleSheet(composer_style())
        self._reference_highlighter.apply_appearance()
        self._update_height()

    def set_enter_to_send(self, enabled: bool):
        self._enter_to_send = enabled

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focus_changed.emit(False)

    def _on_text_changed(self):
        text = self.toPlainText()
        if text.startswith("/"):
            self._in_slash_mode = True
            if self._in_terminal_mode:
                self._in_terminal_mode = False
                self.terminal_changed.emit("")
            self.slash_changed.emit(text)
        elif self._in_slash_mode:
            self._in_slash_mode = False
            self.slash_changed.emit("")

        if text == "!":
            self._in_terminal_mode = True
            self.terminal_changed.emit(text)
        elif self._in_terminal_mode:
            self._in_terminal_mode = False
            self.terminal_changed.emit("")

        query = "" if self._in_slash_mode or self._in_terminal_mode else self._current_mention_query(text)
        if query:
            self._in_mention_mode = True
            self.mention_changed.emit(query)
        elif self._in_mention_mode:
            self._in_mention_mode = False
            self._mention_start = -1
            self.mention_changed.emit("")

    def _update_height(self, *_args):
        line_height = max(1, self.fontMetrics().lineSpacing())
        frame = self.frameWidth() * 2
        max_height = max(
            _INPUT_MIN_HEIGHT,
            _INPUT_MIN_HEIGHT + line_height * (_INPUT_MAX_LINES - 2),
        )
        explicit_lines = self.document().blockCount()
        visual_lines = self._visual_line_count()
        doc_height = math.ceil(self.document().documentLayout().documentSize().height()) + frame
        target = (
            _INPUT_MIN_HEIGHT
            if explicit_lines <= 2 and visual_lines <= 3
            else max(_INPUT_MIN_HEIGHT, min(max_height, doc_height))
        )
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if visual_lines > _INPUT_MAX_LINES or doc_height > max_height
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if self.height() != target:
            self.setFixedHeight(target)
            self.updateGeometry()

    def _visual_line_count(self) -> int:
        lines = 0
        block = self.document().begin()
        while block.isValid():
            layout = block.layout()
            lines += max(1, layout.lineCount() if layout else 0)
            block = block.next()
        return max(1, lines)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_height()

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

    def exit_terminal_mode(self):
        self._in_terminal_mode = False

    def exit_mention_mode(self):
        self._in_mention_mode = False
        self._mention_start = -1

    def take_pasted_file_refs(self) -> list[str]:
        refs = list(self._pasted_file_refs)
        self._pasted_file_refs.clear()
        return refs

    def take_pasted_chat_refs(self) -> list[dict]:
        refs = list(self._pasted_chat_refs)
        self._pasted_chat_refs.clear()
        return refs

    def clear_pasted_file_refs(self):
        self._pasted_file_refs.clear()
        self._pasted_chat_refs.clear()

    def remember_file_refs(self, refs: list[str]):
        self._remember_file_refs(refs)

    def insert_file_mention(self, rel_path: str):
        token = f'@"{rel_path}"' if any(ch.isspace() for ch in rel_path) else f"@{rel_path}"
        self._insert_mention_token(token)

    def insert_crew_mention(self, name: str):
        self._insert_mention_token(f"@{name}")

    def _insert_mention_token(self, token: str):
        cursor = self.textCursor()
        end = cursor.position()
        start = self._mention_start if self._mention_start >= 0 else end
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(token + " ")
        self.setTextCursor(cursor)
        self.exit_mention_mode()
        self.mention_changed.emit("")

    def insert_reference_text(self, text: str):
        text = str(text or "").strip()
        if not text:
            return
        cursor = self.textCursor()
        current = self.toPlainText()
        pos = cursor.position()
        prefix = "" if pos == 0 or current[pos - 1].isspace() else " "
        suffix = "" if pos < len(current) and current[pos].isspace() else " "
        cursor.insertText(prefix + text + suffix)
        self.setTextCursor(cursor)

    def insert_refs_from_mime(self, mime) -> bool:
        if mime.hasFormat(AICHS_EDITOR_REF_MIME):
            refs = parse_editor_refs(mime.data(AICHS_EDITOR_REF_MIME))
            text = editor_ref_text(refs)
            if text:
                self._remember_file_refs(editor_ref_paths(refs))
                self.insert_reference_text(text)
                return True
        if mime.hasFormat(AICHS_FILE_DROP_MIME):
            text = file_drop_text(parse_file_drop(mime.data(AICHS_FILE_DROP_MIME)))
            if text:
                self.insert_reference_text(text)
                return True
        if mime.hasFormat(AICHS_COMMIT_DROP_MIME):
            text = commit_drop_text(parse_commit_drop(mime.data(AICHS_COMMIT_DROP_MIME)))
            if text:
                self.insert_reference_text(text)
                return True
        if mime.hasFormat(AICHS_CHAT_DROP_MIME):
            refs = parse_chat_drop(mime.data(AICHS_CHAT_DROP_MIME))
            text = chat_drop_text(refs)
            if text:
                self._remember_chat_refs(refs)
                self.insert_reference_text(text)
                return True
        return False

    def _remember_file_refs(self, refs: list[str]):
        for ref in refs:
            if ref and ref not in self._pasted_file_refs:
                self._pasted_file_refs.append(ref)

    def _remember_chat_refs(self, refs: list[dict]):
        seen = {ref.get("id") for ref in self._pasted_chat_refs}
        for ref in refs:
            conv_id = str(ref.get("id") or "").strip()
            if conv_id and conv_id not in seen:
                seen.add(conv_id)
                self._pasted_chat_refs.append({
                    "id": conv_id,
                    "title": str(ref.get("title") or "Untitled").strip() or "Untitled",
                })

    def add_file_mention(self, rel_path: str):
        token = f'@"{rel_path}"' if any(ch.isspace() for ch in rel_path) else f"@{rel_path}"
        cursor = self.textCursor()
        text = self.toPlainText()
        pos = cursor.position()
        prefix = "" if pos == 0 or text[pos - 1].isspace() else " "
        suffix = "" if pos < len(text) and text[pos].isspace() else " "
        cursor.insertText(prefix + token + suffix)
        self.setTextCursor(cursor)

    def complete_slash_command(self, name: str):
        text = self.toPlainText()
        leading = len(text) - len(text.lstrip())
        body = text[leading:]
        if not body.startswith("/"):
            return
        parts = body[1:].split(maxsplit=1)
        suffix = f" {parts[1]}" if len(parts) > 1 else " "
        self.setPlainText(f"{text[:leading]}/{name}{suffix}")
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def complete_terminal_command(self):
        if self.toPlainText() != "!":
            return
        self.setPlainText("! ")
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        self.exit_terminal_mode()
        self.terminal_changed.emit("")

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
            if key == Qt.Key.Key_Tab:
                self.mention_confirm.emit()
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
            if key == Qt.Key.Key_Tab:
                self.picker_complete.emit()
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            ):
                if _slash_has_args(self.toPlainText()):
                    event.accept()
                    self.send_requested.emit()
                    return
                self.picker_confirm.emit()
                event.accept()
                return
            if key == Qt.Key.Key_Escape:
                self._in_slash_mode = False
                self.slash_changed.emit("")
                event.accept()
                return

        if self._in_terminal_mode:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                event.accept()
                return
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.picker_complete.emit()
                event.accept()
                return
            if key == Qt.Key.Key_Escape:
                self._in_terminal_mode = False
                self.terminal_changed.emit("")
                event.accept()
                return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            if self._enter_to_send:
                should_send = not bool(mods & Qt.KeyboardModifier.ShiftModifier)
            else:
                should_send = bool(
                    mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
                )
            if should_send:
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
        if self.insert_refs_from_mime(source):
            return
        if source.hasFormat(TERMINAL_REF_MIME):
            ref = bytes(source.data(TERMINAL_REF_MIME)).decode("utf-8", errors="replace").strip()
            if ref:
                self.textCursor().insertText(ref)
                return
        if source.hasFormat(AICHS_MESSAGE_COPY_MIME):
            refs = parse_file_refs_payload(source.data(AICHS_MESSAGE_COPY_MIME))
            self._remember_file_refs(refs)
            if refs and source.hasText():
                self.textCursor().insertText(_with_visible_file_mentions(source.text(), refs))
                return
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage):
                self.image_pasted.emit(image)
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if _mime_has_chat_refs(event.mimeData()) or _mime_has_attachments(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if _mime_has_chat_refs(event.mimeData()) or _mime_has_attachments(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        mime = event.mimeData()
        if _mime_has_chat_refs(mime):
            self._move_cursor_to_drop(event)
            if self.insert_refs_from_mime(mime):
                event.acceptProposedAction()
                return
        images = _images_from_mime(mime)
        if images:
            for image in images:
                self.image_pasted.emit(image)
            event.acceptProposedAction()
            return
        if mime.hasUrls():
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _move_cursor_to_drop(self, event: QDropEvent):
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        self.setTextCursor(self.cursorForPosition(pos))


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
        self._img.setStyleSheet(attachment_thumbnail_style())
        self._remove.setStyleSheet(attachment_remove_button_style())


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

        self._shell = QFrame()
        self._shell.setObjectName("composerShell")
        self._shell.setProperty("composerFocused", "false")
        shell_layout = QHBoxLayout(self._shell)
        shell_layout.setContentsMargins(10, 6, 10, 6)
        shell_layout.setSpacing(10)

        field_col = QVBoxLayout()
        field_col.setContentsMargins(0, 0, 0, 0)
        field_col.setSpacing(4)

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
        self.input.focus_changed.connect(self._set_shell_focused)

        field_col.addWidget(self._skill_row)
        field_col.addWidget(self.strip)
        field_col.addWidget(self.input)

        self.action_row = QHBoxLayout()
        self.action_row.setContentsMargins(0, 0, 0, 0)
        self.action_row.setSpacing(6)

        shell_layout.addLayout(field_col, 1)
        shell_layout.addLayout(self.action_row, 0)
        root.addWidget(self._shell)

        self.apply_appearance()

    def text(self) -> str:
        return self.input.toPlainText().strip()

    def clear(self):
        self.input.clear()
        self.input.clear_pasted_file_refs()
        self.strip.clear()

    def set_enabled(self, enabled: bool):
        self.input.setEnabled(enabled)
        self.strip.setEnabled(enabled)

    def focus_input(self):
        self.input.setFocus()

    def apply_font_size(self, font_pt: int | None = None):
        self.input.apply_font_size(font_pt)

    def set_enter_to_send(self, enabled: bool):
        self.input.set_enter_to_send(enabled)

    def set_skill(self, skill) -> None:
        self._active_skill = skill
        self._skill_chip.setText(f"Mode: /{skill.name}  x")
        self._skill_chip.setStyleSheet(skill_chip_style())
        self._skill_chip.setToolTip("Click to clear the active slash mode")
        self._skill_row.show()

    def clear_skill(self) -> None:
        self._active_skill = None
        self._skill_row.hide()

    def active_skill(self):
        return self._active_skill

    def take_pasted_file_refs(self) -> list[str]:
        return self.input.take_pasted_file_refs()

    def take_pasted_chat_refs(self) -> list[dict]:
        return self.input.take_pasted_chat_refs()

    def remember_file_refs(self, refs: list[str]):
        self.input.remember_file_refs(refs)

    def apply_appearance(self):
        self._shell.setStyleSheet(composer_shell_style())
        self._set_shell_focused(self.input.hasFocus())
        self.input.apply_appearance()
        for item in self.strip._items:
            item["widget"].apply_appearance()
        if self._active_skill:
            self.set_skill(self._active_skill)

    def _set_shell_focused(self, focused: bool) -> None:
        self._shell.setProperty("composerFocused", "true" if focused else "false")
        style = self._shell.style()
        style.unpolish(self._shell)
        style.polish(self._shell)
        self._shell.update()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if _mime_has_chat_refs(event.mimeData()) or _mime_has_attachments(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        if _mime_has_chat_refs(event.mimeData()):
            if self.input.insert_refs_from_mime(event.mimeData()):
                event.acceptProposedAction()
                self.focus_input()
                return
        for image in _images_from_mime(event.mimeData()):
            self.strip.add_image(image)
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
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


def _slash_has_args(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return False
    parts = stripped[1:].split(maxsplit=1)
    return len(parts) > 1 and bool(parts[1].strip())


def _with_visible_file_mentions(text: str, refs: list[str]) -> str:
    enriched = str(text or "")
    for ref in sorted((r for r in refs if r), key=len, reverse=True):
        mention = f'@"{ref}"' if any(ch.isspace() for ch in ref) else f"@{ref}"
        pattern = re.compile(rf"(?<!@)(?<![\w/\\-]){re.escape(ref)}(?![\w/\\-])")
        enriched = pattern.sub(lambda _match, value=mention: value, enriched)
    return enriched
