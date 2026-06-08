from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QRect, Qt, QTimer, pyqtSignal, QMimeData
from PyQt6.QtGui import QColor, QFont, QFontMetrics

from services.chat_drag import AICHS_COMMIT_DROP_MIME, commit_drop_payload, commit_drop_text
from services.git_status import run_git
from ui.theme import (
    ACCENT,
    git_changes_list_style,
    mono_font,
    mono_font_pt,
    palette,
    sidebar_section_label_style,
)
from ui.widgets.git_changes_list import GitChangesList

_ROLE_HASH = Qt.ItemDataRole.UserRole
_ROLE_SUBJECT = Qt.ItemDataRole.UserRole + 1
_ROLE_SHORT_HASH = Qt.ItemDataRole.UserRole + 2
_LOG_SEP = "\x1f"


class _CommitLogDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""

        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        p = palette()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hash_color = p["SELECTION_TEXT"] if selected else ACCENT
        subject_color = p["SELECTION_TEXT"] if selected else p["TEXT"]

        short_hash = str(index.data(_ROLE_SHORT_HASH) or "").strip()
        subject = str(index.data(_ROLE_SUBJECT) or "").strip()
        if not short_hash:
            short_hash = str(index.data(_ROLE_HASH) or "").strip()[:7]

        rect = option.rect.adjusted(6, 0, -6, 0)
        hash_font = QFont(option.font)
        hash_font.setWeight(QFont.Weight.DemiBold)
        hash_metrics = QFontMetrics(hash_font)
        hash_width = hash_metrics.horizontalAdvance(short_hash)
        gap = hash_metrics.horizontalAdvance("  ")

        painter.save()
        painter.setFont(hash_font)
        painter.setPen(QColor(hash_color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, short_hash)

        if subject:
            subject_x = rect.x() + hash_width + gap
            subject_rect = QRect(subject_x, rect.y(), max(0, rect.right() - subject_x + 1), rect.height())
            subject_metrics = QFontMetrics(option.font)
            subject_text = subject_metrics.elidedText(
                subject,
                Qt.TextElideMode.ElideRight,
                subject_rect.width(),
            )
            painter.setFont(option.font)
            painter.setPen(QColor(subject_color))
            painter.drawText(
                subject_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                subject_text,
            )
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), QFontMetrics(option.font).height() + 6))
        return size


class _CommitLogList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setItemDelegate(_CommitLogDelegate(self))

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
            item.setData(_ROLE_SHORT_HASH, short_hash)
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
