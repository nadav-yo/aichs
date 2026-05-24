from PyQt6.QtWidgets import QFrame, QVBoxLayout, QListWidget, QListWidgetItem
from PyQt6.QtCore import Qt, pyqtSignal

from services.skills import Skill
from services.slash_commands import SlashCommand
from ui.theme import palette, ACCENT

_ROLE_KIND = Qt.ItemDataRole.UserRole
_ROLE_DATA = Qt.ItemDataRole.UserRole + 1


class SkillPicker(QFrame):
    skill_selected = pyqtSignal(object)   # Skill
    command_selected = pyqtSignal(str)    # built-in command name
    dismissed      = pyqtSignal()

    def __init__(self, skills: list[Skill], commands: list[SlashCommand] | None = None, parent=None):
        super().__init__(parent)
        self._all = skills
        self._commands = commands or []
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

        self.filter("")

    def filter(self, query: str):
        q = query.lstrip("/").lower().strip()
        self._list.clear()
        for cmd in self._commands:
            if not q or q in cmd.name or q in cmd.description.lower():
                item = QListWidgetItem(f"/{cmd.name}  —  {cmd.description}")
                item.setData(_ROLE_KIND, "command")
                item.setData(_ROLE_DATA, cmd.name)
                self._list.addItem(item)
        for skill in self._all:
            if not q or q in skill.name or q in skill.description.lower():
                item = QListWidgetItem(f"/{skill.name}  —  {skill.description}")
                item.setData(_ROLE_KIND, "skill")
                item.setData(_ROLE_DATA, skill)
                self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        h = min(self._list.count() * 44 + 8, 240)
        self.setFixedHeight(h)

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
        kind = item.data(_ROLE_KIND)
        data = item.data(_ROLE_DATA)
        self.hide()
        if kind == "command":
            self.command_selected.emit(data)
        else:
            self.skill_selected.emit(data)
