from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QWidget, QLabel, QFrame,
)
from PyQt6.QtCore import Qt, QSize, QEvent
from PyQt6.QtGui import QKeyEvent

from services.palette import PaletteItem, filter_items
from ui.theme import (
    chat_font_pt,
    hint_label_style,
    overlay_dialog_style,
    overlay_results_list_style,
    overlay_search_input_style,
    overlay_separator_style,
    palette,
    title_label_style,
)


class _QueryInput(QLineEdit):
    def __init__(self, palette: "CommandPalette", parent=None):
        super().__init__(parent)
        self._palette = palette

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Down and self._palette._list.count():
            row = max(0, self._palette._list.currentRow())
            self._palette._list.setCurrentRow(row)
            self._palette._list.setFocus()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Up and self._palette._list.count():
            row = self._palette._list.currentRow()
            if row <= 0:
                event.accept()
                return
            self._palette._list.setCurrentRow(row - 1)
            self._palette._list.setFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class _ResultRow(QWidget):
    def __init__(self, item: PaletteItem, parent=None):
        super().__init__(parent)
        palette()
        fs = chat_font_pt()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        title = QLabel(item.label)
        title.setStyleSheet(title_label_style(font_pt=fs))
        layout.addWidget(title)

        if item.subtitle:
            sub = QLabel(item.subtitle)
            sub.setStyleSheet(hint_label_style())
            layout.addWidget(sub)


class CommandPalette(QDialog):
    def __init__(self, all_items: list[PaletteItem], parent=None):
        super().__init__(parent)
        self._all_items = all_items
        self._filtered: list[PaletteItem] = []

        self.setWindowTitle("Command palette")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(560, 360)

        self.setStyleSheet(overlay_dialog_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._query = _QueryInput(self)
        self._query.setPlaceholderText("Search conversations, files, /commands…")
        self._query.setClearButtonEnabled(True)
        self._query.setStyleSheet(overlay_search_input_style())
        self._query.textChanged.connect(self._refilter)
        self._query.returnPressed.connect(self._activate_current)
        root.addWidget(self._query)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(overlay_separator_style())
        root.addWidget(sep)

        self._list = QListWidget()
        self._list.setStyleSheet(overlay_results_list_style())
        self._list.itemActivated.connect(self._on_activated)
        self._list.installEventFilter(self)
        root.addWidget(self._list, 1)

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
        self._filtered = filter_items(self._all_items, text)
        self._list.clear()
        for item in self._filtered[:50]:
            row = QListWidgetItem()
            row.setSizeHint(QSize(0, 52 if item.subtitle else 40))
            row.setData(Qt.ItemDataRole.UserRole, item)
            self._list.addItem(row)
            self._list.setItemWidget(row, _ResultRow(item))
        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_activated(self, row: QListWidgetItem):
        item = row.data(Qt.ItemDataRole.UserRole)
        if isinstance(item, PaletteItem):
            self._run(item)

    def _activate_current(self):
        row = self._list.currentItem()
        if not row:
            return
        item = row.data(Qt.ItemDataRole.UserRole)
        if isinstance(item, PaletteItem):
            self._run(item)

    def _run(self, item: PaletteItem):
        self.accept()
        item.run()
