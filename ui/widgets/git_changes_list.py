"""Uncommitted changes with whole-file stage, stash, and commit actions."""

from __future__ import annotations

import json
from typing import Callable

from PyQt6.QtCore import QMimeData, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services.commit_message import (
    COMMIT_MESSAGE_PROMPT_ADDITION_KEY,
    CommitMessageThread,
)
from services.git_status import (
    GitCommandResult,
    GitFileChange,
    commit_staged,
    is_git_repo,
    list_file_changes,
    stage_files,
    stash_files,
    unstage_files,
)
from storage.settings import SettingsStore
from ui.theme import (
    ACCENT,
    git_changes_list_style,
    git_status_color,
    mono_font,
    mono_font_pt,
    palette,
    sidebar_section_label_style,
)

_ROLE_ABS_PATH = Qt.ItemDataRole.UserRole
_ROLE_REL_PATH = Qt.ItemDataRole.UserRole + 1
_GIT_CHANGE_MIME = "application/x-aichs-git-change-paths"
_GENERATING_FRAME_COUNT = 4


class _GitChangeList(QListWidget):
    files_dropped = pyqtSignal(bool, bool, list)

    def __init__(self, staged: bool, parent=None):
        super().__init__(parent)
        self.staged = staged
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:
        paths = []
        for item in items:
            rel = str(item.data(_ROLE_REL_PATH) or "").strip()
            if rel:
                paths.append(rel)
        mime = QMimeData()
        if paths:
            payload = json.dumps({"staged": self.staged, "paths": paths}).encode("utf-8")
            mime.setData(_GIT_CHANGE_MIME, payload)
            mime.setText("\n".join(paths))
        return mime

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_GIT_CHANGE_MIME):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(_GIT_CHANGE_MIME):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        payload = _decode_drop_payload(event.mimeData())
        if not payload:
            super().dropEvent(event)
            return
        source_staged, paths = payload
        if source_staged != self.staged and paths:
            self.files_dropped.emit(source_staged, self.staged, paths)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()


class GitChangesList(QWidget):
    file_open = pyqtSignal(str)
    git_changed = pyqtSignal()

    def __init__(
        self,
        repo_path: str,
        parent=None,
        *,
        settings: SettingsStore | None = None,
        current_model_getter: Callable[[], str] | None = None,
    ):
        super().__init__(parent)
        self.repo_path = repo_path
        self._staged_count = 0
        self._settings = settings or SettingsStore()
        self._current_model_getter = current_model_getter or (lambda: "")
        self._message_thread: CommitMessageThread | None = None
        self._generate_icon = _commit_message_action_icon(self)
        self._generate_frame = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.summary = QLineEdit()
        self.summary.setPlaceholderText("Commit Message")
        self.summary.textChanged.connect(self._update_action_state)
        self._generate_action = QAction(self._generate_icon, "", self)
        self._generate_action.setToolTip("Generate commit message from staged files")
        self._generate_action.triggered.connect(self._generate_commit_message)
        self.summary.addAction(
            self._generate_action,
            QLineEdit.ActionPosition.TrailingPosition,
        )
        layout.addWidget(self.summary)

        self._generate_timer = QTimer(self)
        self._generate_timer.setInterval(180)
        self._generate_timer.timeout.connect(self._advance_generate_animation)

        self.body = QTextEdit()
        self.body.setPlaceholderText("Optional body")
        self.body.setMaximumHeight(70)
        layout.addWidget(self.body)

        commit_actions = QHBoxLayout()
        commit_actions.setContentsMargins(0, 0, 0, 0)
        commit_actions.setSpacing(4)
        self._commit_btn = QPushButton("Commit")
        self._commit_btn.setToolTip("Commit staged files")
        self._commit_btn.clicked.connect(self._commit)
        commit_actions.addWidget(self._commit_btn)
        layout.addLayout(commit_actions)

        self._staged_label = QLabel("Staged")
        layout.addWidget(self._staged_label)

        self.staged_list = _GitChangeList(staged=True)
        self._configure_list(self.staged_list)
        layout.addWidget(self.staged_list)

        self._unstaged_label = QLabel("Unstaged")
        layout.addWidget(self._unstaged_label)

        self.unstaged_list = _GitChangeList(staged=False)
        self._configure_list(self.unstaged_list)
        layout.addWidget(self.unstaged_list)

        self.apply_appearance()
        self.refresh()

        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(5000)

    def _configure_list(self, widget: QListWidget):
        widget.itemDoubleClicked.connect(self._on_open)
        widget.itemSelectionChanged.connect(self._update_action_state)
        widget.files_dropped.connect(self._move_paths)
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(lambda pos, w=widget: self._context_menu(w, pos))

    def apply_appearance(self):
        for label in (self._staged_label, self._unstaged_label):
            label.setStyleSheet(sidebar_section_label_style())
        font = mono_font(mono_font_pt())
        for widget in (self.staged_list, self.unstaged_list):
            widget.setFont(font)
            widget.setStyleSheet(git_changes_list_style())
        field_style = _git_change_field_style()
        self.summary.setStyleSheet(field_style)
        self.body.setStyleSheet(field_style)
        action_style = _git_change_button_style()
        self._commit_btn.setStyleSheet(action_style)

    def _on_open(self, item: QListWidgetItem):
        path = item.data(_ROLE_ABS_PATH)
        if path:
            self.file_open.emit(path)

    def refresh(self):
        self.staged_list.clear()
        self.unstaged_list.clear()
        self._staged_count = 0

        if not is_git_repo(self.repo_path):
            self._staged_label.setText("Staged")
            self._unstaged_label.setText("Unstaged")
            self._add_disabled(self.unstaged_list, "(not a git repository)")
            self._update_action_state()
            return

        changes = list_file_changes(self.repo_path)
        for ch in changes:
            if ch.staged:
                self._add_change(self.staged_list, ch, ch.staged_label or ch.label)
            if ch.unstaged:
                self._add_change(self.unstaged_list, ch, ch.unstaged_label or ch.label)

        staged_count = self.staged_list.count()
        unstaged_count = self.unstaged_list.count()
        self._staged_count = staged_count
        self._staged_label.setText(f"Staged ({staged_count})" if staged_count else "Staged")
        self._unstaged_label.setText(
            f"Unstaged ({unstaged_count})" if unstaged_count else "Unstaged — clean"
        )
        self._update_action_state()

    def _add_change(self, widget: QListWidget, ch: GitFileChange, label: str):
        prefix = label or "·"
        item = QListWidgetItem(f"{prefix} {ch.rel_path}")
        item.setToolTip(f"{prefix} — {ch.rel_path}")
        item.setData(_ROLE_ABS_PATH, ch.abs_path)
        item.setData(_ROLE_REL_PATH, ch.rel_path)
        item.setForeground(QColor(git_status_color(ch.code)))
        widget.addItem(item)

    @staticmethod
    def _add_disabled(widget: QListWidget, text: str):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        widget.addItem(item)

    def _selected_rel_paths(self, widget: QListWidget) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for item in widget.selectedItems():
            rel = str(item.data(_ROLE_REL_PATH) or "").strip()
            if rel and rel not in seen:
                paths.append(rel)
                seen.add(rel)
        return paths

    def _move_paths(self, source_staged: bool, target_staged: bool, paths: list[str]):
        if source_staged == target_staged:
            return
        if target_staged:
            self._run_change_action("Stage", stage_files(self.repo_path, paths))
        else:
            self._run_change_action("Back", unstage_files(self.repo_path, paths))

    def _generate_commit_message(self):
        if self._message_thread and self._message_thread.isRunning():
            return
        if self._staged_count <= 0:
            return
        model = str(self._current_model_getter() or "").strip()
        if not model:
            QMessageBox.warning(self, "Generate commit message failed", "No model selected.")
            return
        guidance = str(
            self._settings.load().get(COMMIT_MESSAGE_PROMPT_ADDITION_KEY, "")
        ).strip()
        self._message_thread = CommitMessageThread(model, self.repo_path, guidance)
        self._message_thread.done.connect(self._on_commit_message_generated)
        self._message_thread.error.connect(self._on_commit_message_error)
        self._message_thread.finished.connect(self._on_commit_message_finished)
        self._start_generate_animation()
        self._update_action_state()
        self._message_thread.start()

    def _on_commit_message_generated(self, summary: str, body: str):
        self.summary.setText(summary)
        self.body.setPlainText(body)
        self._update_action_state()

    def _on_commit_message_error(self, detail: str):
        QMessageBox.warning(
            self,
            "Generate commit message failed",
            detail or "Could not generate a commit message.",
        )

    def _on_commit_message_finished(self):
        self._message_thread = None
        self._stop_generate_animation()
        self._update_action_state()

    def _start_generate_animation(self):
        self._generate_frame = 0
        self._advance_generate_animation()
        self._generate_timer.start()

    def _advance_generate_animation(self):
        frame = self._generate_frame % _GENERATING_FRAME_COUNT
        self._generate_frame += 1
        self._generate_action.setIcon(_commit_message_busy_icon(frame))
        self._generate_action.setText("")
        self._generate_action.setToolTip("Generating commit message...")

    def _stop_generate_animation(self):
        self._generate_timer.stop()
        self._generate_action.setIcon(self._generate_icon)
        self._generate_action.setText("")
        self._generate_action.setToolTip("Generate commit message from staged files")

    def _context_menu(self, widget: QListWidget, pos):
        item = widget.itemAt(pos)
        if item and not item.isSelected():
            widget.clearSelection()
            item.setSelected(True)
        paths = self._selected_rel_paths(widget)
        if not paths:
            return
        menu = QMenu(self)
        stash = QAction("Stash selected...", self)
        stash.triggered.connect(lambda: self._stash_selected(widget))
        menu.addAction(stash)
        menu.exec(widget.viewport().mapToGlobal(pos))

    def _stash_selected(self, widget: QListWidget):
        paths = self._selected_rel_paths(widget)
        if not paths:
            return
        default = _default_stash_message(paths)
        message, ok = QInputDialog.getText(self, "Stash selected files", "Message:", text=default)
        if not ok:
            return
        self._run_change_action("Stash", stash_files(self.repo_path, paths, message))

    def _commit(self):
        result = commit_staged(
            self.repo_path,
            self.summary.text(),
            self.body.toPlainText(),
        )
        if result.ok:
            self.summary.clear()
            self.body.clear()
        self._run_change_action("Commit", result, refresh_history=True)

    def _run_change_action(
        self,
        label: str,
        result: GitCommandResult,
        refresh_history: bool = False,
    ):
        if result.ok:
            self.refresh()
            if refresh_history:
                self.git_changed.emit()
        else:
            self._show_git_error(label, result)
            self._update_action_state()

    def _update_action_state(self):
        has_summary = bool(self.summary.text().strip())
        self._commit_btn.setEnabled(self._staged_count > 0 and has_summary)
        generating = bool(self._message_thread and self._message_thread.isRunning())
        self._generate_action.setEnabled(self._staged_count > 0 and not generating)

    def _show_git_error(self, label: str, result: GitCommandResult):
        detail = _git_result_detail(result) or f"{label} failed."
        QMessageBox.warning(self, f"{label} failed", detail)

    def set_repo_path(self, path: str):
        self.repo_path = path
        self.refresh()


def _default_stash_message(paths: list[str]) -> str:
    shown = [path for path in paths[:3]]
    suffix = "" if len(paths) <= 3 else f", +{len(paths) - 3} more"
    return f"AICHS stash: {', '.join(shown)}{suffix}"


def _decode_drop_payload(mime: QMimeData) -> tuple[bool, list[str]] | None:
    if not mime.hasFormat(_GIT_CHANGE_MIME):
        return None
    try:
        raw = bytes(mime.data(_GIT_CHANGE_MIME)).decode("utf-8")
        data = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    paths = [str(path) for path in data.get("paths", []) if str(path or "").strip()]
    return bool(data.get("staged")), paths


def _git_result_detail(result: GitCommandResult) -> str:
    return "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)


def _commit_message_action_icon(widget: QWidget):
    fallback = QStyle.StandardPixmap.SP_FileDialogDetailedView
    pixmap = getattr(QStyle.StandardPixmap, "SP_BrowserReload", fallback)
    return widget.style().standardIcon(pixmap)


def _commit_message_busy_icon(frame: int, theme: str | None = None) -> QIcon:
    p = palette(theme)
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)

    active = QColor(ACCENT)
    inactive = QColor(p["TEXT_DIM"])
    inactive.setAlpha(150)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    for index, x in enumerate((5, 9, 13)):
        painter.setBrush(active if index == frame % 3 else inactive)
        radius = 3 if index == frame % 3 else 2
        painter.drawEllipse(x - radius, 9 - radius, radius * 2, radius * 2)
    painter.end()
    return QIcon(pixmap)


def _git_change_button_style(theme: str | None = None) -> str:
    p = palette(theme)
    return (
        f"QPushButton {{ background:{p['BG2']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
        "padding:3px 8px; min-height:22px; }"
        f"QPushButton:hover {{ background:{p['BG3']}; border-color:{p['BORDER']}; }}"
        f"QPushButton:pressed {{ background:{p['BORDER']}; }}"
        f"QPushButton:disabled {{ background:{p['BG2']}; color:{p['TEXT_DIM']};"
        f"border-color:{p['BORDER_SUBTLE']}; }}"
    )


def _git_change_field_style(theme: str | None = None) -> str:
    p = palette(theme)
    return (
        f"QLineEdit, QTextEdit {{ background:{p['BG3']}; color:{p['TEXT']};"
        f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
        "padding:4px 6px; }"
        f"QLineEdit:focus, QTextEdit:focus {{ border-color:{ACCENT}; }}"
    )
