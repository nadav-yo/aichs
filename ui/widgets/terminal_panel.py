from __future__ import annotations

import sys

from PyQt6.QtCore import QSize, Qt, QMimeData, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPixmap, QTextCursor
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout

from services.terminal_refs import TERMINAL_REF_MIME, terminal_ref
from services.terminal_session import TerminalSession
from ui.theme import (
    code_text_edit_style,
    icon_button_style,
    mono_font,
    mono_font_pt,
    palette,
    surface_frame_style,
)

_KEY_SEQUENCES = {
    Qt.Key.Key_Backspace: "\b" if sys.platform == "win32" else "\x7f",
    Qt.Key.Key_Delete: "\x1b[3~",
    Qt.Key.Key_Tab: "\t",
    Qt.Key.Key_Left: "\x1b[D",
    Qt.Key.Key_Right: "\x1b[C",
    Qt.Key.Key_Up: "\x1b[A",
    Qt.Key.Key_Down: "\x1b[B",
    Qt.Key.Key_Home: "\x1b[H",
    Qt.Key.Key_End: "\x1b[F",
    Qt.Key.Key_PageUp: "\x1b[5~",
    Qt.Key.Key_PageDown: "\x1b[6~",
}


def _terminal_icon(kind: str, *, size: int = 14, color: str = "#c9d2e6") -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidth(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    if kind == "clear":
        painter.drawLine(3, 12, 12, 12)
        painter.drawLine(5, 9, 12, 9)
        painter.drawLine(5, 9, 10, 4)
        painter.drawLine(10, 4, 13, 7)
    else:
        painter.drawLine(4, 4, 10, 10)
        painter.drawLine(10, 4, 4, 10)
    painter.end()
    return QIcon(pix)


class TerminalTextEdit(QPlainTextEdit):
    input_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start_pos = None
        self._drag_start_in_selection = False
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def append_output(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        for ch in normalized:
            if ch == "\b":
                cursor = self.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.deletePreviousChar()
                self.setTextCursor(cursor)
                continue
            self.insertPlainText(ch)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def copy_mime(self) -> QMimeData:
        cursor = self.textCursor()
        mime = QMimeData()
        mime.setText(self._copied_plain_text(cursor))
        ref = self._copied_ref(cursor)
        if ref:
            mime.setData(TERMINAL_REF_MIME, ref.encode("utf-8"))
        return mime

    def copy(self):
        QGuiApplication.clipboard().setMimeData(self.copy_mime())

    def drag_mime(self) -> QMimeData | None:
        if not self.textCursor().hasSelection():
            return None
        ref = self._copied_ref(self.textCursor())
        if not ref:
            return None
        mime = QMimeData()
        mime.setText(ref)
        mime.setData(TERMINAL_REF_MIME, ref.encode("utf-8"))
        return mime

    def _copied_plain_text(self, cursor: QTextCursor) -> str:
        text = cursor.selectedText() if cursor.hasSelection() else self.toPlainText()
        return text.replace("\u2029", "\n").strip()

    def _copied_ref(self, cursor: QTextCursor) -> str:
        text = self.toPlainText()
        if not text.strip():
            return ""
        if cursor.hasSelection() and not _selection_covers_full_lines(text, cursor):
            return ""
        start, end = _cursor_line_range(text, cursor)
        return terminal_ref(start, end)

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
        if event.matches(QKeySequence.StandardKey.Copy) and self.textCursor().hasSelection():
            self.copy()
            return
        modifiers = event.modifiers()
        key = event.key()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_C:
                self.input_requested.emit("\x03")
                return
            if key == Qt.Key.Key_D:
                self.input_requested.emit("\x04")
                return
            if key == Qt.Key.Key_L:
                self.input_requested.emit("\x0c")
                return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.input_requested.emit("\r\n")
            return
        if key in _KEY_SEQUENCES:
            self.input_requested.emit(_KEY_SEQUENCES[key])
            return
        text = event.text()
        if text:
            self.input_requested.emit(text)
            return
        super().keyPressEvent(event)


class IntegratedTerminalPanel(QFrame):
    terminal_finished = pyqtSignal(object)
    close_requested = pyqtSignal()

    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd
        self._session: TerminalSession | None = None
        self._finished = False
        self.setObjectName("integratedTerminalPanel")
        self.setMinimumHeight(180)
        self.setMaximumHeight(360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = QFrame(self)
        self._header.setObjectName("integratedTerminalHeader")
        header = QHBoxLayout(self._header)
        header.setContentsMargins(10, 5, 8, 5)
        header.setSpacing(6)
        self._shell_label = QLabel("terminal", self._header)
        self._shell_label.setObjectName("integratedTerminalShell")
        self._clear_btn = QPushButton("", self._header)
        self._clear_btn.setFixedSize(30, 30)
        self._clear_btn.setIconSize(QSize(14, 14))
        self._clear_btn.setToolTip("Clear terminal")
        self._clear_btn.clicked.connect(self.clear_active)
        self._close_btn = QPushButton("", self._header)
        self._close_btn.setFixedSize(30, 30)
        self._close_btn.setIconSize(QSize(14, 14))
        self._close_btn.setToolTip("Hide terminal")
        self._close_btn.clicked.connect(self.close_active)
        header.addWidget(self._shell_label)
        header.addStretch(1)
        header.addWidget(self._clear_btn)
        header.addWidget(self._close_btn)
        root.addWidget(self._header)

        self.output = TerminalTextEdit(self)
        self.output.input_requested.connect(self.write_input)
        root.addWidget(self.output, 1)
        self.apply_appearance()

    def start(self, cwd: str | None = None) -> None:
        if cwd:
            self.cwd = cwd
        if self._session is not None and not self._finished:
            self.output.setFocus()
            return
        self._finished = False
        self.output.clear()
        self._session = TerminalSession(self.cwd, self)
        self._session.started.connect(self._on_started)
        self._session.output.connect(self.output.append_output)
        self._session.finished.connect(self._on_finished)
        self._session.start()
        self.output.setFocus()

    def is_running(self) -> bool:
        return self._session is not None and not self._finished

    def active_session(self) -> TerminalSession | None:
        return self._session

    def active_view(self) -> TerminalTextEdit:
        return self.output

    def write_input(self, text: str) -> None:
        if self._session is not None:
            self._session.write(text)

    def clear_active(self) -> None:
        self.clear()

    def close_active(self) -> None:
        self.close_requested.emit()

    def clear(self) -> None:
        self.output.clear()
        if self._session is not None and not self._finished:
            self._session.write("\x0c")

    def stop(self) -> None:
        if self._session is not None:
            self._session.terminate()

    def terminate(self) -> None:
        self.stop()

    def apply_appearance(self) -> None:
        p = palette()
        self.setStyleSheet(
            surface_frame_style(selector="QFrame#integratedTerminalPanel", border_radius=8)
            + f"QFrame#integratedTerminalHeader {{ background:{p['BG2']}; border:none; border-top-left-radius:8px; border-top-right-radius:8px; }}"
            + f"QLabel#integratedTerminalShell {{ color:{p['TEXT']}; background:{p['BG3']}; border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px; padding:3px 8px; font-weight:650; }}"
        )
        self.output.setFont(mono_font(mono_font_pt()))
        self.output.setStyleSheet(code_text_edit_style(selector="QPlainTextEdit", font_pt=mono_font_pt(), padding="8px 10px"))
        self._clear_btn.setIcon(_terminal_icon("clear", color=p["TEXT_DIM"]))
        self._close_btn.setIcon(_terminal_icon("close", color=p["TEXT_DIM"]))
        self._clear_btn.setStyleSheet(icon_button_style(30))
        self._close_btn.setStyleSheet(icon_button_style(30))

    def _on_started(self) -> None:
        label = self._session.label if self._session is not None else "terminal"
        self._shell_label.setText(label)
        self._shell_label.setToolTip("Terminal")

    def _on_finished(self, result: dict) -> None:
        self._finished = True
        exit_code = int(result.get("exit_code") or 0)
        self._shell_label.setToolTip(f"Terminal exited with code {exit_code}")
        self.terminal_finished.emit(result)


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


def _selection_covers_full_lines(text: str, cursor: QTextCursor) -> bool:
    start = min(cursor.selectionStart(), cursor.selectionEnd())
    end = max(cursor.selectionStart(), cursor.selectionEnd())
    if start == end:
        return False
    starts_on_line_boundary = start == 0 or text[start - 1] == "\n"
    ends_on_line_boundary = (
        end >= len(text)
        or text[end] == "\n"
        or text[end - 1] == "\n"
    )
    return starts_on_line_boundary and ends_on_line_boundary
