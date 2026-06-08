import html
import os

from PyQt6.QtCore import QEvent, QSize, QTimer, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.text_search import TextSearchMatch, search_file_contents
from ui.theme import ACCENT, chat_font_pt, meta_font_pt, palette, separator_color


class _SearchInput(QLineEdit):
    def __init__(self, dialog: "TextSearchDialog", parent=None):
        super().__init__(parent)
        self._dialog = dialog

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Down and self._dialog._list.count():
            row = max(0, self._dialog._list.currentRow())
            self._dialog._list.setCurrentRow(row)
            self._dialog._list.setFocus()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Up and self._dialog._list.count():
            row = self._dialog._list.currentRow()
            if row <= 0:
                event.accept()
                return
            self._dialog._list.setCurrentRow(row - 1)
            self._dialog._list.setFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class _TextSearchRow(QWidget):
    def __init__(self, match: TextSearchMatch, parent=None):
        super().__init__(parent)
        p = palette()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(2)

        path = QLabel(f"{html.escape(match.rel_path)}:{match.line_no}")
        path.setStyleSheet(
            f"color:{p['TEXT']}; font-size:{chat_font_pt()}px; background:transparent;"
        )
        layout.addWidget(path)

        snippet = QLabel(_highlight_line_html(match))
        snippet.setTextFormat(Qt.TextFormat.RichText)
        snippet.setStyleSheet(
            f"color:{p['TEXT_DIM']}; font-size:{meta_font_pt()}px; background:transparent;"
        )
        layout.addWidget(snippet)


class TextSearchDialog(QDialog):
    def __init__(self, root: str, on_open_file, parent=None):
        super().__init__(parent)
        self._root = os.path.abspath(root)
        self._on_open_file = on_open_file
        self._filtered: list[TextSearchMatch] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._run_search)

        self.setWindowTitle("Search files")
        self.setModal(True)
        self.setMinimumWidth(620)
        self.resize(680, 460)

        p = palette()
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; border:1px solid {p['BORDER']}; border-radius:12px; }}"
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        self._query = _SearchInput(self)
        self._query.setObjectName("textSearchQuery")
        self._query.setPlaceholderText("Search text in files")
        self._query.setClearButtonEnabled(True)
        self._query.setStyleSheet(
            f"QLineEdit {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:10px;"
            f"padding:10px 14px; font-size:{chat_font_pt()}px; }}"
            f"QLineEdit:focus {{ border:1px solid {ACCENT}; }}"
        )
        self._query.textChanged.connect(self._schedule_search)
        self._query.returnPressed.connect(self._activate_current)
        root_layout.addWidget(self._query)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep_color = separator_color()
        sep.setStyleSheet(
            f"background:{sep_color}; color:{sep_color}; border:none; max-height:1px;"
        )
        root_layout.addWidget(sep)

        self._list = QListWidget()
        self._list.setObjectName("textSearchResults")
        self._list.setStyleSheet(
            f"QListWidget {{ background:{p['BG2']}; border:none; outline:none; }}"
            f"QListWidget::item {{ border:none; }}"
            f"QListWidget::item:selected {{ background:{p['BG3']}; border-left:3px solid {ACCENT}; }}"
        )
        self._list.itemClicked.connect(self._on_activated)
        self._list.itemActivated.connect(self._on_activated)
        self._list.installEventFilter(self)
        root_layout.addWidget(self._list, 1)

    def showEvent(self, event):
        super().showEvent(event)
        self._query.setFocus()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self._list and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_current()
                return True
            if key == Qt.Key.Key_Up and self._list.currentRow() <= 0:
                self._query.setFocus()
                return True
        return super().eventFilter(obj, event)

    def _schedule_search(self, _text: str):
        self._timer.start()

    def _run_search(self):
        self._filtered = search_file_contents(self._root, self._query.text())
        self._list.clear()
        for match in self._filtered:
            row = QListWidgetItem()
            row.setSizeHint(QSize(0, 58))
            row.setData(Qt.ItemDataRole.UserRole, match)
            self._list.addItem(row)
            self._list.setItemWidget(row, _TextSearchRow(match))
        if self._list.count():
            self._list.setCurrentRow(0)

    def _activate_current(self):
        if self._timer.isActive():
            self._timer.stop()
            self._run_search()
        row = self._list.currentItem()
        if row:
            self._on_activated(row)

    def _on_activated(self, row: QListWidgetItem):
        match = row.data(Qt.ItemDataRole.UserRole)
        if isinstance(match, TextSearchMatch):
            self.accept()
            self._on_open_file(match.path, match.line_no)


def _highlight_line_html(match: TextSearchMatch) -> str:
    line = match.line_text
    start = max(0, min(match.start, len(line)))
    end = max(start, min(match.end, len(line)))
    return (
        html.escape(line[:start])
        + f"<span style=\"color:{ACCENT}; font-weight:700;\">"
        + html.escape(line[start:end])
        + "</span>"
        + html.escape(line[end:])
    )
