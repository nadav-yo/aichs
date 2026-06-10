from dataclasses import dataclass
from datetime import datetime
import re

from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import ACCENT, chat_font_pt, icon_button_style, meta_font_pt, palette


@dataclass(frozen=True)
class _RunLogEntry:
    kind: str
    target: str
    raw: str
    status: str = "Logged"
    detail: str = ""
    timestamp: str = ""

    @property
    def summary(self) -> str:
        return self.kind if not self.target else f"{self.kind} - {self.target}"

    @property
    def details(self) -> str:
        lines = [
            f"Type: {self.kind}",
            f"Target: {self.target or '(none)'}",
            f"Status: {self.status}",
        ]
        if self.timestamp:
            lines.append(f"When: {self.timestamp}")
        if self.detail and self.detail != self.target:
            lines.extend(["", self.detail])
        lines.extend(["", "Original:", self.raw])
        return "\n".join(lines)


class WorkbenchContextPanel(QWidget):
    collapse_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tool_items: list[_RunLogEntry] = []
        self._icon_cache: dict[tuple[str, str], QIcon] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._title = QLabel("Run Log")
        self._title.setObjectName("contextPanelTitle")
        header.addWidget(self._title, 1)

        self._collapse_btn = QPushButton(">")
        self._collapse_btn.setAccessibleName("Collapse run log")
        self._collapse_btn.setToolTip("Collapse run log")
        self._collapse_btn.setFixedSize(28, 28)
        self._collapse_btn.clicked.connect(self.collapse_requested.emit)
        header.addWidget(self._collapse_btn)
        layout.addLayout(header)

        layout.addWidget(_section_label("Recent Run"))
        self._tool_activity = QListWidget()
        self._tool_activity.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tool_activity.customContextMenuRequested.connect(self._show_activity_menu)
        layout.addWidget(self._tool_activity, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setToolTip("Copy selected row")
        self._copy_btn.clicked.connect(self.copy_selected_activity)
        actions.addWidget(self._copy_btn)

        self._copy_details_btn = QPushButton("Details")
        self._copy_details_btn.setToolTip("Copy selected row details")
        self._copy_details_btn.clicked.connect(self.copy_selected_activity_details)
        actions.addWidget(self._copy_details_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setToolTip("Clear run log")
        self._clear_btn.clicked.connect(self.clear_activity)
        actions.addWidget(self._clear_btn)
        layout.addLayout(actions)

        self.apply_appearance()
        self._sync_empty_state()

    def add_tool_activity(self, text: str):
        compact = " ".join(str(text or "").split())
        if not compact:
            return
        self._tool_items.insert(0, _parse_activity(compact))
        self._tool_items = self._tool_items[:12]
        self._render_activity()

    def copy_selected_activity(self):
        entry = self._selected_entry()
        if entry is None:
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(entry.summary)

    def copy_selected_activity_details(self):
        entry = self._selected_entry()
        if entry is None:
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(entry.details)

    def clear_activity(self):
        self._tool_items.clear()
        self._render_activity()

    def _render_activity(self):
        self._tool_activity.clear()
        for entry in self._tool_items:
            item = QListWidgetItem(entry.summary)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setIcon(self._entry_icon(entry))
            if entry.status == "Error":
                item.setForeground(QColor("#d94b4b"))
            item.setToolTip(entry.details)
            self._tool_activity.addItem(item)
        self._sync_empty_state()

    def _sync_empty_state(self):
        if self._tool_activity.count():
            self._copy_btn.setEnabled(True)
            self._copy_details_btn.setEnabled(True)
            self._clear_btn.setEnabled(True)
            return
        item = QListWidgetItem("No run log yet")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._tool_activity.addItem(item)
        self._copy_btn.setEnabled(False)
        self._copy_details_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)

    def _selected_entry(self) -> _RunLogEntry | None:
        item = self._tool_activity.currentItem()
        if item is None:
            return None
        entry = item.data(Qt.ItemDataRole.UserRole)
        return entry if isinstance(entry, _RunLogEntry) else None

    def _show_activity_menu(self, pos):
        item = self._tool_activity.itemAt(pos)
        entry = None
        if item is not None:
            self._tool_activity.setCurrentItem(item)
            entry = self._selected_entry()

        menu = QMenu(self)
        copy_row = menu.addAction("Copy row")
        copy_details = menu.addAction("Copy details")
        copy_row.setEnabled(entry is not None)
        copy_details.setEnabled(entry is not None)
        menu.addSeparator()
        clear_log = menu.addAction("Clear run log")
        clear_log.setEnabled(bool(self._tool_items))

        chosen = menu.exec(self._tool_activity.mapToGlobal(pos))
        if chosen == copy_row:
            self.copy_selected_activity()
        elif chosen == copy_details:
            self.copy_selected_activity_details()
        elif chosen == clear_log:
            self.clear_activity()

    def apply_appearance(self):
        p = palette()
        self._icon_cache.clear()
        self.setStyleSheet(
            f"background-color:{p['BG2']}; color:{p['TEXT']};"
        )
        list_style = (
            f"QListWidget {{ background-color:{p['BG2']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:7px; }}"
            "QListWidget::item { padding:4px 6px; }"
            f"QListWidget::item:selected {{ background-color:{p['SELECTION']};"
            f"color:{p['SELECTION_TEXT']}; }}"
        )
        self._tool_activity.setStyleSheet(list_style)
        self._collapse_btn.setStyleSheet(icon_button_style(28))
        action_style = (
            f"background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
            "padding:5px 8px;"
        )
        self._copy_btn.setStyleSheet(action_style)
        self._copy_details_btn.setStyleSheet(action_style)
        self._clear_btn.setStyleSheet(action_style)
        self._title.setStyleSheet(
            f"color:{p['TEXT']}; font-size:{max(13, chat_font_pt())}px;"
            "font-weight:700; padding-bottom:2px;"
        )
        for label in self.findChildren(QLabel, "contextPanelSection"):
            label.setStyleSheet(
                f"color:{ACCENT}; font-size:{meta_font_pt()}px;"
                "font-weight:700; padding-top:4px;"
            )
        if self._tool_items:
            self._render_activity()

    def _entry_icon(self, entry: _RunLogEntry) -> QIcon:
        p = palette()
        key = (entry.kind, p["BG2"])
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        icon = _run_log_icon(entry.kind)
        self._icon_cache[key] = icon
        return icon


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("contextPanelSection")
    return label


def _parse_activity(text: str) -> _RunLogEntry:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    match = re.fullmatch(r"Reading file '(.+)'(?: \((.+)\))?", text)
    if match:
        target = match.group(1)
        detail = f"Path: {target}"
        if match.group(2):
            detail = f"{detail}\nNote: {match.group(2)}"
        return _RunLogEntry("Read file", target, text, detail=detail, timestamp=timestamp)

    match = re.fullmatch(r"Searching files for '(.+)' in '(.+)'", text)
    if match:
        pattern, directory = match.groups()
        return _RunLogEntry(
            "Search files",
            pattern,
            text,
            detail=f"Pattern: {pattern}\nDirectory: {directory}",
            timestamp=timestamp,
        )

    match = re.fullmatch(r"Searching files in '(.+)'", text)
    if match:
        directory = match.group(1)
        return _RunLogEntry(
            "Search files",
            directory,
            text,
            detail=f"Directory: {directory}",
            timestamp=timestamp,
        )

    match = re.fullmatch(r"Searching project chat history(?: for '(.+)')?", text)
    if match:
        query = match.group(1) or "all chats"
        return _RunLogEntry(
            "Search chats",
            query,
            text,
            detail=f"Query: {query}",
            timestamp=timestamp,
        )

    if text.startswith("Running command: "):
        command = text[len("Running command: "):].strip()
        return _RunLogEntry(
            "Run command",
            command or "(empty command)",
            text,
            detail=f"Command: {command or '(empty command)'}",
            timestamp=timestamp,
        )

    if text == "Running command":
        return _RunLogEntry("Run command", "", text, timestamp=timestamp)

    if text.startswith("Tool error: "):
        message = text[len("Tool error: "):].strip()
        return _RunLogEntry(
            "Tool error",
            message,
            text,
            status="Error",
            detail=f"Error: {message}",
            timestamp=timestamp,
        )

    return _RunLogEntry("Tool notice", text, text, timestamp=timestamp)


def _run_log_icon(kind: str) -> QIcon:
    p = palette()
    color, symbol = _icon_style(kind)
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(QPen(QColor(p["BORDER_SUBTLE"]), 1))
    painter.drawRoundedRect(1, 1, 16, 16, 5, 5)
    painter.setPen(QColor("white"))
    font = QFont()
    font.setBold(True)
    font.setPointSize(8)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()
    return QIcon(pixmap)


def _icon_style(kind: str) -> tuple[str, str]:
    if kind == "Read file":
        return "#4f8cff", "R"
    if kind == "Search files":
        return "#2aa876", "S"
    if kind == "Search chats":
        return "#7d6bff", "C"
    if kind == "Run command":
        return "#c27a24", ">"
    if kind == "Tool error":
        return "#d94b4b", "!"
    return ACCENT, "i"
