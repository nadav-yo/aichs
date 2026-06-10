from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QAction, QGuiApplication, QKeySequence, QTextCursor

from config import MAX_TERMINAL_BLOCKS
from services.terminal_refs import TERMINAL_REF_MIME, terminal_ref
from ui.theme import palette, card_frame_style, meta_font_pt, mono_font_pt, mono_font


class _TerminalOutput(QTextEdit):
    def __init__(self, ref_getter, parent=None):
        super().__init__(parent)
        self._ref_getter = ref_getter

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

    def _copied_plain_text(self, cursor: QTextCursor) -> str:
        text = cursor.selectedText() if cursor.hasSelection() else self.toPlainText()
        return text.replace("\u2029", "\n").strip()

    def _copied_ref(self, cursor: QTextCursor) -> str:
        if not self._ref_getter():
            return ""
        if cursor.hasSelection() and not _selection_covers_full_lines(self.toPlainText(), cursor):
            return ""
        start, end = _cursor_line_range(self.toPlainText(), cursor)
        return terminal_ref(start, end)

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

        self._ref = QLabel("")
        self._status = QLabel("Running…")
        self._ref.hide()
        footer_row.addWidget(self._ref)
        footer_row.addStretch()
        footer_row.addWidget(self._status)

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
            f"QTextEdit {{ background:transparent; color:{p['TEXT']}; border:none; padding:6px 10px; }}"
        )
        self._footer.setStyleSheet(
            "QFrame { background:transparent; border:none; }"
        )
        if self._exit_code is None:
            self._status.setStyleSheet(
                f"color:{p['TEXT_DIM']}; font-size:{meta}px; background:transparent; border:none;"
            )
            self._ref.setStyleSheet(
                f"color:{p['TEXT_DIM']}; font-size:{max(9, meta - 1)}px; background:transparent; border:none;"
            )
        elif self._exit_code == 0:
            self._status.setStyleSheet(
                f"color:{p['SUCCESS']}; font-size:{max(9, meta - 1)}px; background:transparent; border:none;"
            )
            self._ref.setStyleSheet(
                f"color:{p['TEXT_DIM']}; font-size:{max(9, meta - 1)}px; background:transparent; border:none;"
            )
        else:
            self._status.setStyleSheet(
                f"color:#f87171; font-size:{meta}px; background:transparent; border:none;"
            )
            self._ref.setStyleSheet(
                f"color:{p['TEXT_DIM']}; font-size:{max(9, meta - 1)}px; background:transparent; border:none;"
            )

    def append_line(self, line: str):
        if self._line_count == 0 and not str(line).strip():
            return
        self._line_count += 1
        height = min(150, max(38, self._line_count * 20 + 20))
        self._output.setFixedHeight(height)
        if height >= 150:
            self._output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._output.append(line)
        self._output.verticalScrollBar().setValue(
            self._output.verticalScrollBar().maximum()
        )

    def set_output(self, output: str):
        lines = str(output or "").splitlines()
        self._line_count = len(lines)
        self._output.setPlainText("\n".join(lines))
        height = min(150, max(38, self._line_count * 20 + 20))
        self._output.setFixedHeight(height)
        if height >= 150:
            self._output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self._output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._output.verticalScrollBar().setValue(
            self._output.verticalScrollBar().maximum()
        )

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

    def copy_text(self) -> str:
        return self._output.copy_text()

    def copy_ref(self) -> str:
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
