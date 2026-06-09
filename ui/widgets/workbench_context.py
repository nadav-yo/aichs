from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import ACCENT, chat_font_pt, meta_font_pt, palette


class WorkbenchContextPanel(QWidget):
    collapse_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tool_items: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._title = QLabel("Activity")
        self._title.setObjectName("contextPanelTitle")
        header.addWidget(self._title, 1)

        self._collapse_btn = QPushButton("Hide")
        self._collapse_btn.setToolTip("Collapse activity shelf")
        self._collapse_btn.clicked.connect(self.collapse_requested.emit)
        header.addWidget(self._collapse_btn)
        layout.addLayout(header)

        layout.addWidget(_section_label("Recent"))
        self._tool_activity = QListWidget()
        layout.addWidget(self._tool_activity, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setToolTip("Copy selected activity")
        self._copy_btn.clicked.connect(self.copy_selected_activity)
        actions.addWidget(self._copy_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setToolTip("Clear activity")
        self._clear_btn.clicked.connect(self.clear_activity)
        actions.addWidget(self._clear_btn)
        layout.addLayout(actions)

        self.apply_appearance()
        self._sync_empty_state()

    def add_tool_activity(self, text: str):
        compact = " ".join(str(text or "").split())
        if not compact:
            return
        self._tool_items.insert(0, compact)
        self._tool_items = self._tool_items[:8]
        self._render_activity()

    def copy_selected_activity(self):
        item = self._tool_activity.currentItem()
        if item is None or item.text() == "No recent activity":
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(item.text())

    def clear_activity(self):
        self._tool_items.clear()
        self._render_activity()

    def _render_activity(self):
        self._tool_activity.clear()
        for item_text in self._tool_items:
            item = QListWidgetItem(item_text)
            item.setToolTip(item_text)
            self._tool_activity.addItem(item)
        self._sync_empty_state()

    def _sync_empty_state(self):
        if self._tool_activity.count():
            self._copy_btn.setEnabled(True)
            self._clear_btn.setEnabled(True)
            return
        item = QListWidgetItem("No recent activity")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._tool_activity.addItem(item)
        self._copy_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)

    def apply_appearance(self):
        p = palette()
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
        self._collapse_btn.setStyleSheet(
            f"background-color:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
            "padding:4px 8px;"
        )
        action_style = (
            f"background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
            "padding:5px 8px;"
        )
        self._copy_btn.setStyleSheet(action_style)
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


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("contextPanelSection")
    return label
