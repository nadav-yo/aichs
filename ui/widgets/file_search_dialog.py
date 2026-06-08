import html
import os

from PyQt6.QtCore import QEvent, QSize, Qt
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

from services.file_search import FileSearchIndex, FileSearchMatch
from ui.theme import ACCENT, chat_font_pt, palette, separator_color


class _SearchInput(QLineEdit):
    def __init__(self, dialog: "FileSearchDialog", parent=None):
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


class _FileResultRow(QWidget):
    def __init__(self, match: FileSearchMatch, parent=None):
        super().__init__(parent)
        p = palette()
        self.setMinimumHeight(42)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        label = QLabel(_match_path_html(match))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        label.setStyleSheet(
            f"color:{p['TEXT']}; font-size:{chat_font_pt()}px;"
            "background:transparent; padding:9px 12px;"
        )
        layout.addWidget(label)


class FileSearchDialog(QDialog):
    def __init__(self, root: str, on_open_file, parent=None):
        super().__init__(parent)
        self._root = os.path.abspath(root)
        self._on_open_file = on_open_file
        self._index = FileSearchIndex.from_root(self._root)
        self._filtered: list[FileSearchMatch] = []

        self.setWindowTitle("Open file")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(580, 420)

        p = palette()
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; border:1px solid {p['BORDER']}; border-radius:12px; }}"
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        self._query = _SearchInput(self)
        self._query.setObjectName("fileSearchQuery")
        self._query.setPlaceholderText("Search files by name")
        self._query.setClearButtonEnabled(True)
        self._query.setStyleSheet(
            f"QLineEdit {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:10px;"
            f"padding:10px 14px; font-size:{chat_font_pt()}px; }}"
            f"QLineEdit:focus {{ border:1px solid {ACCENT}; }}"
        )
        self._query.textChanged.connect(self._refilter)
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
        self._list.setObjectName("fileSearchResults")
        self._list.setStyleSheet(
            f"QListWidget {{ background:{p['BG2']}; border:none; outline:none; }}"
            f"QListWidget::item {{ border:none; }}"
            f"QListWidget::item:selected {{ background:{p['BG3']}; border-left:3px solid {ACCENT}; }}"
        )
        self._list.itemClicked.connect(self._on_activated)
        self._list.itemActivated.connect(self._on_activated)
        self._list.installEventFilter(self)
        root_layout.addWidget(self._list, 1)

        self._refilter("")

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

    def _refilter(self, text: str):
        self._filtered = self._index.search(text)
        self._list.clear()
        for match in self._filtered:
            row = QListWidgetItem()
            row.setSizeHint(QSize(0, 42))
            row.setData(Qt.ItemDataRole.UserRole, match)
            self._list.addItem(row)
            self._list.setItemWidget(row, _FileResultRow(match))
        if self._list.count():
            self._list.setCurrentRow(0)

    def _activate_current(self):
        row = self._list.currentItem()
        if row:
            self._on_activated(row)

    def _on_activated(self, row: QListWidgetItem):
        match = row.data(Qt.ItemDataRole.UserRole)
        if isinstance(match, FileSearchMatch):
            self.accept()
            self._on_open_file(match.path)


def _match_path_html(match: FileSearchMatch) -> str:
    name_start = match.rel_path.rfind(match.name)
    if name_start < 0:
        return _highlight_html(match.rel_path, ())
    indices = tuple(name_start + index for index in match.indices)
    return _highlight_html(match.rel_path, indices)


def _highlight_html(text: str, indices: tuple[int, ...]) -> str:
    highlighted = set(indices)
    out: list[str] = []
    for index, char in enumerate(text):
        escaped = html.escape(char)
        if index in highlighted:
            out.append(
                f"<span style=\"color:{ACCENT}; font-weight:700;\">"
                f"{escaped}</span>"
            )
        else:
            out.append(escaped)
    return "".join(out)
