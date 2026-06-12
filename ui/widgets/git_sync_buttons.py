"""Compact pull/push controls shared by the git header and Sync tab."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QWidget

from ui.theme import ACCENT, git_action_button_style, palette


def git_action_button_text(symbol: str, count: int) -> str:
    count = max(0, int(count or 0))
    return symbol if count == 0 else f"{symbol}{count}"


def fit_git_action_button(button: QPushButton, *, compact: bool = True) -> None:
    metrics = QFontMetrics(button.font())
    width = max(28 if compact else 36, metrics.horizontalAdvance(button.text()) + (14 if compact else 20))
    button.setFixedWidth(width)
    button.setFixedHeight(24 if compact else 32)


class GitSyncButtons(QWidget):
    pull_clicked = pyqtSignal()
    push_clicked = pyqtSignal()

    def __init__(self, parent=None, *, compact: bool = True):
        super().__init__(parent)
        self._compact = compact
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4 if compact else 8)

        self.pull_btn = QPushButton("↓")
        self.pull_btn.setAccessibleName("Pull")
        self.pull_btn.setToolTip("Pull from the upstream branch")
        self.pull_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pull_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.pull_btn.clicked.connect(self.pull_clicked.emit)
        row.addWidget(self.pull_btn)

        self.push_btn = QPushButton("↑")
        self.push_btn.setAccessibleName("Push")
        self.push_btn.setToolTip("No local commits to push")
        self.push_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.push_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.push_btn.clicked.connect(self.push_clicked.emit)
        row.addWidget(self.push_btn)

        if not compact:
            row.addStretch()
        self.apply_appearance()

    def apply_appearance(self):
        p = palette()
        self.pull_btn.setStyleSheet(git_action_button_style(ACCENT))
        self.push_btn.setStyleSheet(git_action_button_style(p["SUCCESS"]))
        fit_git_action_button(self.pull_btn, compact=self._compact)
        fit_git_action_button(self.push_btn, compact=self._compact)
