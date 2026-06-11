from PyQt6.QtWidgets import QFrame, QVBoxLayout, QListWidget, QListWidgetItem
from PyQt6.QtCore import Qt, pyqtSignal

from services.crew import CrewMember
from ui.theme import popover_frame_style, popover_list_style

_ROLE_REL = Qt.ItemDataRole.UserRole
_ROLE_ABS = Qt.ItemDataRole.UserRole + 1
_ROLE_KIND = Qt.ItemDataRole.UserRole + 2
_ROLE_TOKEN = Qt.ItemDataRole.UserRole + 3


class FileMentionPicker(QFrame):
    file_selected = pyqtSignal(str, str)  # relative path, absolute path
    crew_selected = pyqtSignal(str)       # crew mention token

    def __init__(
        self,
        files: list[tuple[str, str]],
        crew: list[CrewMember] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._all = files
        self._crew = crew or []
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.setStyleSheet(popover_frame_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setStyleSheet(popover_list_style())
        self._list.itemActivated.connect(self._on_activated)
        self._list.itemClicked.connect(self._on_activated)
        layout.addWidget(self._list)

        self.filter("@")

    def set_files(self, files: list[tuple[str, str]]):
        self._all = files

    def set_crew(self, crew: list[CrewMember]):
        self._crew = crew

    def filter(self, query: str):
        q = query.lstrip("@").lower().strip()
        self._list.clear()
        for member in self._crew:
            haystack = f"{member.name} {member.id} {member.title}".lower()
            if q and q not in haystack:
                continue
            item = QListWidgetItem(f"@{member.name}  ·  {member.title}")
            item.setData(_ROLE_KIND, "crew")
            item.setData(_ROLE_TOKEN, member.name)
            item.setToolTip(member.description)
            self._list.addItem(item)
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
            item.setData(_ROLE_KIND, "file")
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
        if item.data(_ROLE_KIND) == "crew":
            self.crew_selected.emit(item.data(_ROLE_TOKEN))
        else:
            self.file_selected.emit(item.data(_ROLE_REL), item.data(_ROLE_ABS))
