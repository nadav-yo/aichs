from __future__ import annotations

import sys
import re
from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt, QMimeData, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QFont, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPixmap, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
)

from services.native_terminal import NativeTerminalSession
from services.terminal_refs import TERMINAL_REF_MIME, terminal_ref
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

_ANSI_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_INCOMPLETE_ANSI_SGR_RE = re.compile(r"\x1b(?:\[[0-9;]*)?")
_ANSI_FOREGROUND_COLORS = {
    30: "#5c6370", 31: "#e06c75", 32: "#98c379", 33: "#e5c07b",
    34: "#61afef", 35: "#c678dd", 36: "#56b6c2", 37: "#c9d2e6",
    90: "#7f8799", 91: "#ff7b88", 92: "#b4dc8e", 93: "#f4d27a",
    94: "#81b8ff", 95: "#df9cff", 96: "#75d9e8", 97: "#f2f5fb",
}


def _trailing_ansi_escape(text: str) -> str:
    """Retain an ANSI SGR sequence split between process output chunks."""
    start = text.rfind("\x1b")
    if start < 0:
        return ""
    suffix = text[start:]
    return suffix if _INCOMPLETE_ANSI_SGR_RE.fullmatch(suffix) else ""


def _ansi_256_color(index: int) -> QColor:
    index = max(0, min(int(index), 255))
    if index < 16:
        code = (30 + index) if index < 8 else (90 + index - 8)
        return QColor(_ANSI_FOREGROUND_COLORS[code])
    if index < 232:
        value = index - 16
        levels = (0, 95, 135, 175, 215, 255)
        return QColor(levels[value // 36], levels[(value // 6) % 6], levels[value % 6])
    level = 8 + (index - 232) * 10
    return QColor(level, level, level)


def _frame_color(color: object, *, background: bool = False) -> QColor | None:
    if not isinstance(color, dict):
        return None
    kind = color.get("kind")
    value = color.get("value")
    if kind == "rgb" and isinstance(value, list) and len(value) == 3:
        return QColor(*[max(0, min(int(component), 255)) for component in value])
    if kind == "indexed":
        return _ansi_256_color(int(value))
    if kind != "named":
        return None
    named = int(value)
    # Alacritty's first sixteen named colors are the standard ANSI palette.
    # Foreground/background defaults intentionally inherit the app palette.
    if named < 16:
        return _ansi_256_color(named)
    if background and named == 257:
        return None
    if not background and named == 256:
        return None
    return None


def _frame_format(cell: dict) -> QTextCharFormat:
    format_ = QTextCharFormat()
    foreground = _frame_color(cell.get("foreground"))
    background = _frame_color(cell.get("background"), background=True)
    flags = int(cell.get("flags") or 0)
    if flags & 1:
        foreground, background = background, foreground
    # QTextCharFormat defaults to Qt's black document brush rather than the
    # QPlainTextEdit stylesheet color.  Native default-color cells must set
    # the app foreground explicitly or they disappear on our dark canvas.
    format_.setForeground(foreground or QColor(palette()["TEXT"]))
    if background is not None:
        format_.setBackground(background)
    if flags & 2:
        format_.setFontWeight(QFont.Weight.Bold)
    if flags & 4:
        format_.setFontItalic(True)
    if flags & 8:
        format_.setFontUnderline(True)
    return format_


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
    terminal_size_changed = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start_pos = None
        self._drag_start_in_selection = False
        self._ansi_format = QTextCharFormat()
        self._ansi_pending = ""
        self._terminal_id = ""
        self._enter_sequence = "\r\n"
        self._pending_frame: dict | None = None
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setInterval(16)
        self._frame_timer.timeout.connect(self._flush_frame)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setAcceptDrops(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def append_output(self, text: str) -> None:
        text = self._ansi_pending + str(text or "")
        self._ansi_pending = _trailing_ansi_escape(text)
        if self._ansi_pending:
            text = text[:-len(self._ansi_pending)]
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        position = 0
        for match in _ANSI_SGR_RE.finditer(normalized):
            self._insert_terminal_text(cursor, normalized[position:match.start()])
            self._apply_sgr(match.group(1))
            position = match.end()
        self._insert_terminal_text(cursor, normalized[position:])
        self.setTextCursor(cursor)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def record_output(self, _text: str) -> None:
        """Keep native-terminal transcript ownership in the session, not the view."""

    def set_terminal_id(self, terminal_id: str) -> None:
        self._terminal_id = str(terminal_id or "").strip()

    def set_pseudo_terminal_input(self, enabled: bool) -> None:
        self._enter_sequence = "\r" if enabled else "\r\n"

    def render_frame(self, frame: dict) -> None:
        """Render the native engine's authoritative screen grid."""
        columns = max(1, int(frame.get("columns") or 1))
        lines = max(1, int(frame.get("lines") or 1))
        text = frame.get("text")
        if not isinstance(text, str):
            return
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text, _frame_format({}))
        for span in frame.get("spans") or []:
            if not isinstance(span, dict):
                continue
            start = max(0, int(span.get("start") or 0))
            end = min(len(text), start + max(0, int(span.get("length") or 0)))
            if start >= end:
                continue
            styled = QTextCursor(self.document())
            styled.setPosition(start)
            styled.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            styled.mergeCharFormat(_frame_format(span))
        row = max(0, min(int(frame.get("cursor_row") or 0), lines - 1))
        column = max(0, min(int(frame.get("cursor_column") or 0), columns - 1))
        cursor_position = min(len(text), row * (columns + 1) + column)
        cursor.setPosition(cursor_position)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def queue_frame(self, frame: dict) -> None:
        """Render at most once per display frame, always using the newest grid."""
        self._pending_frame = frame
        if not self._frame_timer.isActive():
            self._frame_timer.start()

    def _flush_frame(self) -> None:
        frame, self._pending_frame = self._pending_frame, None
        if frame is not None:
            self.render_frame(frame)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        metrics = self.fontMetrics()
        columns = max(10, self.viewport().width() // max(1, metrics.horizontalAdvance("M")))
        lines = max(2, self.viewport().height() // max(1, metrics.height()))
        self.terminal_size_changed.emit(columns, lines)

    def focusNextPrevChild(self, _next: bool) -> bool:
        """Keep Tab inside the terminal instead of using Qt's focus traversal."""
        return False

    def _insert_terminal_text(self, cursor: QTextCursor, text: str) -> None:
        for ch in text:
            if ch == "\b":
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.deletePreviousChar()
                continue
            cursor.insertText(ch, self._ansi_format)

    def _apply_sgr(self, params: str) -> None:
        codes = [int(value) if value else 0 for value in params.split(";")]
        index = 0
        while index < len(codes):
            code = codes[index]
            if code == 0:
                self._ansi_format = QTextCharFormat()
            elif code == 1:
                self._ansi_format.setFontWeight(QFont.Weight.Bold)
            elif code == 22:
                self._ansi_format.setFontWeight(QFont.Weight.Normal)
            elif code == 4:
                self._ansi_format.setFontUnderline(True)
            elif code == 24:
                self._ansi_format.setFontUnderline(False)
            elif code in _ANSI_FOREGROUND_COLORS:
                self._ansi_format.setForeground(QColor(_ANSI_FOREGROUND_COLORS[code]))
            elif code == 39:
                self._ansi_format.clearForeground()
            elif code == 38 and index + 1 < len(codes):
                mode = codes[index + 1]
                if mode == 5 and index + 2 < len(codes):
                    self._ansi_format.setForeground(_ansi_256_color(codes[index + 2]))
                    index += 2
                elif mode == 2 and index + 4 < len(codes):
                    self._ansi_format.setForeground(QColor(codes[index + 2], codes[index + 3], codes[index + 4]))
                    index += 4
            index += 1

    def copy_mime(self) -> QMimeData:
        cursor = self.textCursor()
        mime = QMimeData()
        mime.setText(self._copied_plain_text(cursor))
        if ref := self._copied_ref(cursor):
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
        if cursor.hasSelection():
            return cursor.selectedText().replace("\u2029", "\n")
        return self.toPlainText().rstrip()

    def _copied_ref(self, cursor: QTextCursor) -> str:
        if not self._terminal_id:
            return ""
        text = self.toPlainText()
        start, end = _cursor_line_range(text, cursor)
        return terminal_ref(start, end, self._terminal_id)

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

    def dragEnterEvent(self, event):
        if _terminal_input_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        if _terminal_input_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        text = _terminal_input_from_mime(event.mimeData())
        if not text:
            event.ignore()
            return
        self.input_requested.emit(text)
        event.acceptProposedAction()

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
        if event.matches(QKeySequence.StandardKey.Paste):
            text = _terminal_input_from_mime(QGuiApplication.clipboard().mimeData())
            if text:
                self.input_requested.emit(text)
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
            self.input_requested.emit(self._enter_sequence)
            return
        if key in _KEY_SEQUENCES:
            self.input_requested.emit(_KEY_SEQUENCES[key])
            return
        text = event.text()
        if text:
            self.input_requested.emit(text)
            return
        super().keyPressEvent(event)


@dataclass
class _TerminalTab:
    session: object
    output: TerminalTextEdit
    name: str = "terminal"
    finished: bool = False
    closed: bool = False


class IntegratedTerminalPanel(QFrame):
    terminal_finished = pyqtSignal(object)
    close_requested = pyqtSignal()

    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self.cwd = cwd
        self._tabs: list[_TerminalTab] = []
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
        self._tab_bar = QTabBar(self._header)
        self._tab_bar.setObjectName("integratedTerminalTabs")
        self._tab_bar.setDrawBase(False)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self._tab_bar.setTabsClosable(True)
        self._tab_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._tab_bar.setMinimumWidth(140)
        self._tab_bar.currentChanged.connect(self._set_active_tab)
        self._tab_bar.tabCloseRequested.connect(self.close_terminal)
        self._tab_bar.tabBarDoubleClicked.connect(self.rename_terminal)
        self._new_btn = QPushButton("+", self._header)
        self._new_btn.setFixedSize(30, 30)
        self._new_btn.setToolTip("New terminal")
        self._new_btn.clicked.connect(self.new_terminal)
        self._clear_btn = QPushButton("", self._header)
        self._clear_btn.setFixedSize(30, 30)
        self._clear_btn.setIconSize(QSize(14, 14))
        self._clear_btn.setToolTip("Clear terminal")
        self._clear_btn.clicked.connect(self.clear_active)
        header.addWidget(self._tab_bar, 1)
        header.addWidget(self._new_btn)
        header.addWidget(self._clear_btn)
        root.addWidget(self._header)

        self._output_stack = QStackedWidget(self)
        self.output = self._make_output()
        self._output_stack.addWidget(self.output)
        root.addWidget(self._output_stack, 1)
        self.apply_appearance()

    def start(self, cwd: str | None = None) -> None:
        if cwd:
            self.cwd = cwd
        if self._tabs:
            self.active_view().setFocus()
            return
        self.new_terminal()

    def new_terminal(self) -> None:
        output = self.output if not self._tabs else self._make_output()
        self._apply_output_appearance(output)
        if output.parent() is not self._output_stack:
            self._output_stack.addWidget(output)
        session = self._make_session()
        output.set_terminal_id(getattr(session, "terminal_id", ""))
        tab = _TerminalTab(session=session, output=output)
        if hasattr(session, "set_size"):
            session.set_size(*self._output_size(output))
        session.started.connect(lambda tab=tab: self._on_started(tab))
        if hasattr(session, "frame"):
            output.set_pseudo_terminal_input(True)
            session.output.connect(output.record_output)
            session.frame.connect(output.queue_frame)
            output.terminal_size_changed.connect(session.resize)
        else:
            session.output.connect(output.append_output)
        session.finished.connect(lambda result, tab=tab: self._on_finished(tab, result))
        self._tabs.append(tab)
        index = self._tab_bar.addTab(tab.name)
        self._tab_bar.setCurrentIndex(index)
        self._output_stack.setCurrentWidget(output)
        session.start()
        output.setFocus()

    def is_running(self) -> bool:
        tab = self._active_tab()
        return tab is not None and not tab.finished and not tab.closed

    def active_session(self) -> object | None:
        tab = self._active_tab()
        return tab.session if tab is not None else None

    def session_for_terminal_id(self, terminal_id: str) -> object | None:
        for tab in self._tabs:
            if not tab.closed and str(getattr(tab.session, "terminal_id", "")) == str(terminal_id):
                return tab.session
        return None

    def active_view(self) -> TerminalTextEdit:
        return self.output

    def write_input(self, text: str) -> None:
        session = self.active_session()
        if session is not None:
            session.write(text)

    def clear_active(self) -> None:
        self.clear()

    def clear(self) -> None:
        tab = self._active_tab()
        if tab is None:
            return
        tab.output.clear()
        if not tab.finished:
            tab.session.write("\x0c")

    def stop(self) -> None:
        tab = self._active_tab()
        if tab is not None:
            tab.session.terminate()

    def terminate(self) -> None:
        for tab in list(self._tabs):
            tab.closed = True
            tab.session.terminate()
        self._tabs.clear()
        while self._tab_bar.count():
            self._tab_bar.removeTab(0)
        self._reset_idle_output()

    def close_terminal(self, index: int | None = None) -> None:
        if index is None:
            index = self._tab_bar.currentIndex()
        if index < 0 or index >= len(self._tabs):
            return
        tab = self._tabs.pop(index)
        tab.closed = True
        tab.session.terminate()
        self._tab_bar.removeTab(index)
        self._output_stack.removeWidget(tab.output)
        if tab.output is not self.output:
            tab.output.deleteLater()
        if self._tabs:
            self._tab_bar.setCurrentIndex(min(index, len(self._tabs) - 1))
            return
        self._reset_idle_output()
        self.close_requested.emit()

    def rename_terminal(self, index: int) -> None:
        if index < 0 or index >= len(self._tabs):
            return
        tab = self._tabs[index]
        name, accepted = QInputDialog.getText(self, "Rename terminal", "Name", text=tab.name)
        name = name.strip()
        if accepted and name:
            tab.name = name
            self._tab_bar.setTabText(index, name)

    def apply_appearance(self) -> None:
        p = palette()
        self.setStyleSheet(
            surface_frame_style(selector="QFrame#integratedTerminalPanel", border_radius=8)
            + f"QFrame#integratedTerminalHeader {{ background:{p['BG2']}; border:none; border-top-left-radius:8px; border-top-right-radius:8px; }}"
            + f"QTabBar#integratedTerminalTabs::tab {{ color:{p['TEXT_DIM']}; background:transparent; border:none; min-width:110px; padding:6px 10px; margin-right:2px; }}"
            + f"QTabBar#integratedTerminalTabs::tab:selected {{ color:{p['TEXT']}; background:{p['BG3']}; border-radius:6px; }}"
        )
        for tab in self._tabs:
            self._apply_output_appearance(tab.output)
        self._apply_output_appearance(self.output)
        self._new_btn.setStyleSheet(icon_button_style(30))
        self._clear_btn.setIcon(_terminal_icon("clear", color=p["TEXT_DIM"]))
        self._clear_btn.setStyleSheet(icon_button_style(30))

    def _make_output(self) -> TerminalTextEdit:
        output = TerminalTextEdit(self)
        output.input_requested.connect(self.write_input)
        return output

    def _make_session(self):
        return NativeTerminalSession(self.cwd, self)

    @staticmethod
    def _output_size(output: TerminalTextEdit) -> tuple[int, int]:
        metrics = output.fontMetrics()
        return (
            max(10, output.viewport().width() // max(1, metrics.horizontalAdvance("M"))),
            max(2, output.viewport().height() // max(1, metrics.height())),
        )

    def _reset_idle_output(self) -> None:
        while self._output_stack.count():
            widget = self._output_stack.widget(0)
            self._output_stack.removeWidget(widget)
            widget.deleteLater()
        self.output = self._make_output()
        self._apply_output_appearance(self.output)
        self._output_stack.addWidget(self.output)

    def _apply_output_appearance(self, output: TerminalTextEdit) -> None:
        output.setFont(mono_font(mono_font_pt()))
        output.setStyleSheet(code_text_edit_style(selector="QPlainTextEdit", font_pt=mono_font_pt(), padding="8px 10px"))

    def _active_tab(self) -> _TerminalTab | None:
        index = self._tab_bar.currentIndex()
        return self._tabs[index] if 0 <= index < len(self._tabs) else None

    def _set_active_tab(self, index: int) -> None:
        if 0 <= index < len(self._tabs):
            self.output = self._tabs[index].output
            self._output_stack.setCurrentWidget(self.output)

    def _on_started(self, tab: _TerminalTab) -> None:
        if tab.closed:
            return
        tab.name = self._default_tab_name(tab.session.label)
        index = self._tabs.index(tab)
        self._tab_bar.setTabText(index, tab.name)
        self._tab_bar.setTabToolTip(index, "Double-click to rename")

    def _on_finished(self, tab: _TerminalTab, result: dict) -> None:
        tab.finished = True
        # Closing a tab is deliberate UI cleanup, not terminal output worth
        # adding to the conversation.  Natural shell exits remain recorded.
        if tab.closed:
            return
        if not tab.closed and tab in self._tabs:
            index = self._tabs.index(tab)
            exit_code = int(result.get("exit_code") or 0)
            self._tab_bar.setTabToolTip(index, f"Terminal exited with code {exit_code}")
        self.terminal_finished.emit(result)

    def _default_tab_name(self, shell: str) -> str:
        titles = {tab.name for tab in self._tabs}
        if shell not in titles:
            return shell
        suffix = 2
        while f"{shell} {suffix}" in titles:
            suffix += 1
        return f"{shell} {suffix}"


def _terminal_input_from_mime(mime: QMimeData | None) -> str:
    if mime is None:
        return ""
    paths = []
    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile():
                paths.append(_quote_terminal_path(url.toLocalFile()))
    if paths:
        return " ".join(paths)
    return mime.text() if mime.hasText() else ""


def _quote_terminal_path(path: str) -> str:
    path = str(path or "")
    if not path or not any(char.isspace() for char in path):
        return path
    if sys.platform == "win32":
        return f'"{path.replace(chr(34), chr(34) * 2)}"'
    return "'" + path.replace("'", "'\"'\"'") + "'"


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
    return max(1, min(start_line, line_count)), max(1, min(end_line, line_count))
