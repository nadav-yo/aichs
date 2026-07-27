from PyQt6.QtWidgets import QApplication, QFrame, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QMenu, QPushButton, QSizePolicy
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QAction, QDrag, QGuiApplication, QKeySequence, QTextCursor

from config import MAX_TERMINAL_BLOCKS

MAX_TERMINAL_CARD_PREVIEW_CHARS = 256 * 1024
from services.terminal_refs import TERMINAL_REF_MIME, terminal_ref, terminal_ref_id
from ui.theme import (
    palette,
    card_frame_style,
    code_text_edit_style,
    hint_label_style,
    meta_font_pt,
    mono_font_pt,
    mono_font,
    secondary_button_style,
)


def _terminal_ref_mime(ref: str) -> QMimeData:
    mime = QMimeData()
    ref = str(ref or "").strip()
    mime.setText(ref)
    if ref:
        mime.setData(TERMINAL_REF_MIME, ref.encode("utf-8"))
    return mime


class _TerminalRefLabel(QLabel):
    def __init__(self, ref_getter=None, parent=None):
        super().__init__(parent)
        self._ref_getter = ref_getter or (lambda: self.text())
        self._drag_start_pos = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def copy_mime(self) -> QMimeData:
        return _terminal_ref_mime(self._ref_getter())

    def copy(self) -> None:
        QGuiApplication.clipboard().setMimeData(self.copy_mime())

    def drag_mime(self) -> QMimeData | None:
        ref = str(self._ref_getter() or "").strip()
        return _terminal_ref_mime(ref) if ref else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._drag_start_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            event.accept()
            return
        self._drag_start_pos = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (
            self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (pos - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            mime = self.drag_mime()
            if mime is not None:
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.CopyAction)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        copy_ref = QAction("Copy terminal reference", self)
        copy_ref.triggered.connect(self.copy)
        menu.addAction(copy_ref)
        menu.exec(event.globalPos())


class _TerminalOutput(QTextEdit):
    def __init__(self, ref_getter, parent=None):
        super().__init__(parent)
        self._ref_getter = ref_getter
        self._drag_start_pos = None
        self._drag_start_in_selection = False

    def copy(self):
        QGuiApplication.clipboard().setMimeData(self.copy_mime())

    def copy_mime(self) -> QMimeData:
        cursor = self.textCursor()
        text = self._copied_plain_text(cursor)
        ref = self._copied_ref(cursor)
        mime = QMimeData()
        mime.setText(text)
        if ref:
            mime.setData(TERMINAL_REF_MIME, ref.encode("utf-8"))
        return mime

    def copy_text(self) -> str:
        return self._copied_plain_text(self.textCursor())

    def copy_ref(self) -> str:
        return self._copied_ref(self.textCursor())

    def drag_mime(self) -> QMimeData | None:
        if not self.textCursor().hasSelection():
            return None
        ref = self._copied_ref(self.textCursor())
        if not ref:
            return None
        return _terminal_ref_mime(ref)

    def _copied_plain_text(self, cursor: QTextCursor) -> str:
        text = cursor.selectedText() if cursor.hasSelection() else self.toPlainText()
        return text.replace("\u2029", "\n").strip()

    def _copied_ref(self, cursor: QTextCursor) -> str:
        source_ref = str(self._ref_getter() or "")
        if not source_ref:
            return ""
        start, end = _cursor_line_range(self.toPlainText(), cursor)
        return terminal_ref(start, end, terminal_ref_id(source_ref))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self._drag_start_pos = pos
            self._drag_start_in_selection = self._pos_in_selection(pos)
            if self._drag_start_in_selection:
                event.accept()
                return
        else:
            self._drag_start_pos = None
            self._drag_start_in_selection = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (
            self._drag_start_in_selection
            and self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (pos - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            mime = self.drag_mime()
            if mime is not None:
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.CopyAction)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        self._drag_start_in_selection = False
        super().mouseReleaseEvent(event)

    def _pos_in_selection(self, pos) -> bool:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        hit = self.cursorForPosition(pos).position()
        start = min(cursor.selectionStart(), cursor.selectionEnd())
        end = max(cursor.selectionStart(), cursor.selectionEnd())
        return start <= hit <= end

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        copy_ref = QAction("Copy with reference", self)
        copy_ref.triggered.connect(self.copy)
        menu.addSeparator()
        menu.addAction(copy_ref)
        menu.exec(event.globalPos())


class TerminalCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumWidth(680)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._exit_code: int | None = None
        self._line_count = 0
        self._ref_text = ""
        self._raw_output = ""
        self._collapsed = False
        self._preview_truncated = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._output = _TerminalOutput(lambda: self._ref_text)
        self._output.setReadOnly(True)
        self._output.setFrameShape(QFrame.Shape.NoFrame)
        self._output.setMinimumHeight(30)
        self._output.setMaximumHeight(38)
        self._output.setFixedHeight(38)
        self._output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._output.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._output.document().setMaximumBlockCount(MAX_TERMINAL_BLOCKS)

        self._footer = QFrame()
        footer_row = QHBoxLayout(self._footer)
        footer_row.setContentsMargins(10, 0, 10, 5)

        self._drag_start_pos = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._ref = _TerminalRefLabel(lambda: self._ref_text)
        self._status = _TerminalRefLabel(lambda: self._ref_text)
        self._preview = QLabel("")
        self._toggle = QPushButton("Hide output")
        self._toggle.setFixedHeight(24)
        self._toggle.clicked.connect(self.toggle_output)
        self._ref.hide()
        self._preview.hide()
        footer_row.addWidget(self._ref)
        footer_row.addWidget(self._preview)
        footer_row.addStretch()
        footer_row.addWidget(self._status)
        footer_row.addWidget(self._toggle)

        root.addWidget(self._output, 0)
        root.addWidget(self._footer)

        self.apply_appearance()

    def apply_appearance(self):
        p = palette()
        mono = mono_font_pt()
        meta = meta_font_pt()
        self._output.setFont(mono_font(mono))
        self.setStyleSheet(card_frame_style())
        self._output.setStyleSheet(
            code_text_edit_style(selector="QTextEdit", font_pt=mono, padding="6px 10px")
        )
        self._footer.setStyleSheet(
            "QFrame { background:transparent; border:none; }"
        )
        if self._exit_code is None:
            self._status.setStyleSheet(hint_label_style(font_pt=meta))
            self._ref.setStyleSheet(hint_label_style(font_pt=max(9, meta - 1)))
            self._preview.setStyleSheet(hint_label_style(font_pt=max(9, meta - 1)))
        elif self._exit_code == 0:
            self._status.setStyleSheet(
                hint_label_style(text_color=p["SUCCESS"], font_pt=max(9, meta - 1))
            )
            self._ref.setStyleSheet(hint_label_style(font_pt=max(9, meta - 1)))
            self._preview.setStyleSheet(hint_label_style(font_pt=max(9, meta - 1)))
        else:
            self._status.setStyleSheet(
                hint_label_style(text_color="#f87171", font_pt=meta)
            )
            self._ref.setStyleSheet(hint_label_style(font_pt=max(9, meta - 1)))
            self._preview.setStyleSheet(hint_label_style(font_pt=max(9, meta - 1)))
        self._toggle.setStyleSheet(secondary_button_style(border_radius=6, padding="2px 8px", font_weight="500"))

    def append_line(self, line: str):
        if self._line_count == 0 and not str(line).strip():
            return
        self._collapsed = False
        self._output.show()
        self._toggle.setText("Hide output")
        text = str(line)
        self._raw_output += ("" if not self._raw_output else "\n") + text.rstrip("\n")
        self._line_count += 1
        self._sync_output_geometry()
        self._output.append(line)
        self._output.verticalScrollBar().setValue(
            self._output.verticalScrollBar().maximum()
        )

    def set_output(self, output: str, *, collapsed: bool = False):
        self._raw_output = str(output or "")
        self._line_count = len(self._raw_output.splitlines())
        self._collapsed = bool(collapsed)
        if self._collapsed:
            self._output.clear()
            self._output.hide()
            self._toggle.setText("Show output")
            self._update_preview_state(preview_truncated=False)
            return
        self._load_output_preview()

    def toggle_output(self) -> None:
        if self._collapsed:
            self.expand_output()
        else:
            self.collapse_output()

    def collapse_output(self) -> None:
        self._collapsed = True
        self._output.clear()
        self._output.hide()
        self._toggle.setText("Show output")
        self._update_preview_state(preview_truncated=False)

    def expand_output(self) -> None:
        self._collapsed = False
        self._load_output_preview()

    def _load_output_preview(self) -> None:
        preview = self._raw_output[:MAX_TERMINAL_CARD_PREVIEW_CHARS]
        preview_truncated = len(self._raw_output) > len(preview)
        self._output.setPlainText(preview.rstrip("\n"))
        self._output.show()
        self._toggle.setText("Hide output")
        self._sync_output_geometry()
        self._update_preview_state(preview_truncated=preview_truncated)
        self._output.verticalScrollBar().setValue(
            self._output.verticalScrollBar().maximum()
        )

    def _sync_output_geometry(self) -> None:
        visible_lines = len(self._output.toPlainText().splitlines())
        height = min(150, max(38, max(1, visible_lines) * 20 + 20))
        self._output.setFixedHeight(height)
        if height >= 150:
            self._output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self._output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _update_preview_state(self, *, preview_truncated: bool) -> None:
        self._preview_truncated = bool(preview_truncated)
        if self._preview_truncated:
            self._preview.setText("preview")
            self._preview.setToolTip(f"Showing first {MAX_TERMINAL_CARD_PREVIEW_CHARS} characters.")
            self._preview.show()
        else:
            self._preview.hide()

    def finish(self, exit_code: int = 0, detail: str | None = None, ref: str = ""):
        self._exit_code = exit_code
        if detail:
            self._status.setText(detail)
        elif exit_code == 0:
            self._status.setText("done")
        else:
            self._status.setText(f"exit {exit_code}")
        if ref:
            self._ref_text = ref
            self._ref.setText(ref)
            self._ref.setToolTip("Reference this terminal output in chat.")
            self._ref.show()
        else:
            self._ref_text = ""
            self._ref.hide()
        self.apply_appearance()

    def copy_mime(self) -> QMimeData:
        if self._collapsed and self._ref_text:
            return _terminal_ref_mime(self._ref_text)
        return self._output.copy_mime()

    def copy(self) -> None:
        QGuiApplication.clipboard().setMimeData(self.copy_mime())

    def drag_mime(self) -> QMimeData | None:
        if self._collapsed and self._ref_text:
            return _terminal_ref_mime(self._ref_text)
        return self._output.drag_mime()

    def mousePressEvent(self, event):
        if self._collapsed and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._drag_start_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            event.accept()
            return
        self._drag_start_pos = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (
            self._collapsed
            and self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (pos - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            mime = self.drag_mime()
            if mime is not None:
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.CopyAction)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self._collapsed and event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        if not self._collapsed or not self._ref_text:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        copy_ref = QAction("Copy terminal reference", self)
        copy_ref.triggered.connect(self.copy)
        menu.addAction(copy_ref)
        menu.exec(event.globalPos())

    def copy_text(self) -> str:
        return self._raw_output.strip() if self._collapsed else self._output.copy_text()

    def copy_ref(self) -> str:
        if self._collapsed and self._ref_text:
            return self._ref_text
        return self._output.copy_ref()


def _cursor_line_range(text: str, cursor: QTextCursor) -> tuple[int, int]:
    lines = text.splitlines()
    line_count = max(1, len(lines))
    if not cursor.hasSelection():
        return 1, line_count
    start = min(cursor.selectionStart(), cursor.selectionEnd())
    end = max(cursor.selectionStart(), cursor.selectionEnd())
    if end > start and end <= len(text) and text[end - 1] == "\n":
        end -= 1
    start_line = text.count("\n", 0, start) + 1
    end_line = text.count("\n", 0, max(start, end)) + 1
    start_line = max(1, min(start_line, line_count))
    end_line = max(start_line, min(end_line, line_count))
    return start_line, end_line
