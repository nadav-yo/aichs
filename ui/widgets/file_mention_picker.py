from PyQt6.QtWidgets import QFrame, QVBoxLayout, QListWidget, QListWidgetItem
from PyQt6.QtCore import Qt, pyqtSignal

from ui.theme import palette, ACCENT

_ROLE_REL = Qt.ItemDataRole.UserRole
_ROLE_ABS = Qt.ItemDataRole.UserRole + 1


class FileMentionPicker(QFrame):
    file_selected = pyqtSignal(str, str)  # relative path, absolute path

    def __init__(self, files: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._all = files
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        p = palette()
        self.setStyleSheet(
            f"QFrame {{ background:{p['BG2']}; border:1px solid {p['BORDER']};"
            "border-radius:8px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setStyleSheet(
            f"QListWidget {{ background:transparent; border:none; }}"
            f"QListWidget::item {{ padding:8px 10px; border-radius:4px; }}"
            f"QListWidget::item:hover {{ background:{p['BG3']}; }}"
            f"QListWidget::item:selected {{ background:{ACCENT}; color:white; }}"
        )
        self._list.itemActivated.connect(self._on_activated)
        self._list.itemClicked.connect(self._on_activated)
        layout.addWidget(self._list)

        self.filter("@")

    def set_files(self, files: list[tuple[str, str]]):
        self._all = files

    def filter(self, query: str):
        q = query.lstrip("@").lower().strip()
        self._list.clear()
        matches = []
        for rel, abs_path in self._all:
            name = rel.rsplit("/", 1)[-1].lower()
            haystack = rel.lower()
            if not q or q in haystack or q in name:
                matches.append((rel, abs_path))
            if len(matches) >= 80:
                break
        for rel, abs_path in matches:
            item = QListWidgetItem(f"@{rel}")
            item.setData(_ROLE_REL, rel)
            item.setData(_ROLE_ABS, abs_path)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        h = min(self._list.count() * 36 + 8, 240)
        self.setFixedHeight(max(h, 44))

    def select_next(self):
        row = self._list.currentRow()
        if row < self._list.count() - 1:
            self._list.setCurrentRow(row + 1)

    def select_prev(self):
        row = self._list.currentRow()
        if row > 0:
            self._list.setCurrentRow(row - 1)

    def confirm(self):
        item = self._list.currentItem()
        if item:
            self._on_activated(item)

    def count(self) -> int:
        return self._list.count()

    def _on_activated(self, item: QListWidgetItem):
        self.hide()
        self.file_selected.emit(item.data(_ROLE_REL), item.data(_ROLE_ABS))
