import os
import subprocess

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QSplitter, QListWidget, QListWidgetItem, QLabel,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor

from ui.theme import palette, meta_font_pt, mono_font_pt, mono_font, ACCENT


class GitPanel(QWidget):
    file_open = pyqtSignal(str)

    def __init__(self, repo_path: str, parent=None):
        super().__init__(parent)
        self.repo_path = repo_path

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        changes_wrap = QWidget()
        cl = QVBoxLayout(changes_wrap)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(2)
        self._changes_lbl = self._label("Uncommitted changes")
        cl.addWidget(self._changes_lbl)
        self.changes = QListWidget()
        self.changes.itemDoubleClicked.connect(self._open_change)
        cl.addWidget(self.changes)

        log_wrap = QWidget()
        ll = QVBoxLayout(log_wrap)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(2)
        self._log_lbl = self._label("Git log")
        ll.addWidget(self._log_lbl)
        self.log = QListWidget()
        ll.addWidget(self.log)

        splitter.addWidget(changes_wrap)
        splitter.addWidget(log_wrap)
        splitter.setSizes([180, 320])
        root.addWidget(splitter, 1)

        self.apply_appearance()
        self.refresh()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(5000)

    def _label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        return lbl

    def apply_appearance(self):
        p = palette()
        meta = meta_font_pt()
        mono = mono_font_pt()
        font = mono_font(mono)

        self._changes_lbl.setStyleSheet(
            f"font-size:{meta}px; color:{p['TEXT_DIM']}; padding:2px 4px;"
        )
        self._log_lbl.setStyleSheet(
            f"font-size:{meta}px; color:{p['TEXT_DIM']}; padding:2px 4px;"
        )
        list_style = f"background:{p['BG2']}; border:none; color:{p['TEXT']};"
        self.changes.setFont(font)
        self.changes.setStyleSheet(list_style)
        self.log.setFont(font)
        self.log.setStyleSheet(list_style)

    def _run(self, cmd: list) -> str:
        try:
            r = subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True, timeout=5)
            return r.stdout.strip()
        except Exception:
            return ""

    def _status_color(self, code: str) -> QColor:
        p = palette()
        if code in ("??", "A ", "A"):
            return QColor(p["SUCCESS"] if code != "??" else p["TEXT_DIM"])
        if "D" in code:
            return QColor("#f87171")
        if "M" in code or "U" in code:
            return QColor(ACCENT)
        return QColor(p["TEXT"])

    def _parse_status(self, line: str) -> tuple[str, str, str]:
        if len(line) < 3:
            return "", "", line
        code = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        label = {"??": "?", " M": "M", "M ": "M", "A ": "A", " D": "D", "D ": "D"}.get(code, code.strip() or "·")
        return code, label, path

    def _open_change(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.file_open.emit(path)

    def refresh(self):
        self.changes.clear()
        status = self._run(["git", "status", "--short", "-uall"])
        if status == "" and not os.path.isdir(os.path.join(self.repo_path, ".git")):
            self._changes_lbl.setText("Uncommitted changes")
            item = QListWidgetItem("(not a git repository)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.changes.addItem(item)
        elif not status:
            self._changes_lbl.setText("Uncommitted changes — clean")
        else:
            lines = status.splitlines()
            count = 0
            for line in lines:
                code, label, path = self._parse_status(line)
                if path.endswith("/"):
                    continue
                abs_path = path if os.path.isabs(path) else os.path.join(self.repo_path, path)
                if os.path.isdir(abs_path):
                    continue
                item = QListWidgetItem(f"{label}  {path}")
                item.setData(Qt.ItemDataRole.UserRole, abs_path)
                item.setForeground(self._status_color(code))
                self.changes.addItem(item)
                count += 1
            self._changes_lbl.setText(f"Uncommitted changes ({count})")

        self.log.clear()
        for line in self._run(["git", "log", "--oneline", "-40"]).splitlines():
            self.log.addItem(line)

    def set_repo_path(self, path: str):
        self.repo_path = path
        self.refresh()
