from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QMimeData

from services.chat_drag import AICHS_COMMIT_DROP_MIME, commit_drop_payload, commit_drop_text
from services.git_status import run_git
from ui.theme import (
    git_changes_list_style,
    mono_font,
    mono_font_pt,
    sidebar_section_label_style,
)
from ui.widgets.git_changes_list import GitChangesList

_ROLE_HASH = Qt.ItemDataRole.UserRole
_ROLE_SUBJECT = Qt.ItemDataRole.UserRole + 1
_LOG_SEP = "\x1f"


class _CommitLogList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:
        commits = []
        for item in items:
            sha = str(item.data(_ROLE_HASH) or "").strip()
            if not sha:
                continue
            commits.append({
                "hash": sha,
                "subject": str(item.data(_ROLE_SUBJECT) or "").strip(),
            })
        mime = QMimeData()
        if commits:
            mime.setData(AICHS_COMMIT_DROP_MIME, commit_drop_payload(commits))
            mime.setText(commit_drop_text(commits))
        return mime


class GitPanel(QWidget):
    file_open = pyqtSignal(str)

    def __init__(self, repo_path: str, parent=None):
        super().__init__(parent)
        self.repo_path = repo_path

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._changes = GitChangesList(repo_path)
        self._changes.file_open.connect(self.file_open.emit)

        log_wrap = QWidget()
        ll = QVBoxLayout(log_wrap)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(2)
        self._log_lbl = QLabel("Git log")
        ll.addWidget(self._log_lbl)

        self.log = _CommitLogList()
        ll.addWidget(self.log)

        splitter.addWidget(self._changes)
        splitter.addWidget(log_wrap)
        splitter.setSizes([180, 320])
        root.addWidget(splitter, 1)

        self.apply_appearance()
        self._refresh_log()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(5000)

    def apply_appearance(self):
        mono = mono_font_pt()
        font = mono_font(mono)

        self._log_lbl.setStyleSheet(sidebar_section_label_style())
        list_style = git_changes_list_style()
        self.log.setFont(font)
        self.log.setStyleSheet(list_style)
        self._changes.apply_appearance()

    def refresh(self):
        self._changes.refresh()
        self._refresh_log()

    def _refresh_log(self):
        self.log.clear()
        for raw in run_git(
            ["git", "log", "--format=%H%x1f%h%x1f%s", "-40"],
            self.repo_path,
        ).splitlines():
            parsed = _parse_commit_log_line(raw)
            if not parsed:
                continue
            full_hash, short_hash, subject = parsed
            text = f"{short_hash} {subject}" if subject else short_hash
            item = QListWidgetItem(text)
            item.setToolTip("Drag this commit into chat.")
            item.setData(_ROLE_HASH, full_hash)
            item.setData(_ROLE_SUBJECT, subject)
            self.log.addItem(item)

    def set_repo_path(self, path: str):
        self.repo_path = path
        self._changes.set_repo_path(path)
        self.refresh()


def _parse_commit_log_line(line: str) -> tuple[str, str, str] | None:
    parts = str(line or "").split(_LOG_SEP, 2)
    if len(parts) != 3:
        return None
    full_hash, short_hash, subject = (part.strip() for part in parts)
    if not full_hash:
        return None
    return full_hash, short_hash or full_hash[:7], subject
