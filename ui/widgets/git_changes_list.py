"""Uncommitted changes with whole-file stage, stash, and commit actions."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Callable

from PyQt6.QtCore import QMimeData, QSize, Qt, QThread, QTimer, pyqtSignal, QRectF
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPixmap,
)
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
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from services.commit_message import CommitMessageThread, format_commit_message, split_commit_message
from services.chat_drag import AICHS_FILE_DROP_MIME, file_drop_payload, file_drop_text
from services.git_snapshot import GitSnapshot, build_git_snapshot, clear_git_snapshot_cache
from services.git_status import (
    GitCommandResult,
    GitFileChange,
    commit_staged,
    discard_files,
    stage_files,
    stash_files,
    unstage_files,
)
from services.performance import time_operation
from storage.settings import (
    COMMIT_MESSAGE_PROMPT_ADDITION_KEY,
    git_panel_lists_split,
)
from storage.settings import SettingsStore
from ui.theme import (
    ACCENT,
    app_font,
    git_change_button_style,
    git_changes_list_style,
    git_commit_field_style,
    git_panel_caption_pt,
    git_panel_path_pt,
    git_panel_section_label_style,
    hint_label_style,
    mono_font,
    palette,
    primary_button_style,
)
from ui.widgets.git_status_icon import git_status_description, git_status_icon, paint_git_status_badge

_ROLE_ABS_PATH = Qt.ItemDataRole.UserRole
_ROLE_REL_PATH = Qt.ItemDataRole.UserRole + 1
_ROLE_STATUS_CODE = Qt.ItemDataRole.UserRole + 2
_ROLE_IS_HEADER = Qt.ItemDataRole.UserRole + 3
_ROLE_STATUS_LABEL = Qt.ItemDataRole.UserRole + 4
_GIT_CHANGE_MIME = "application/x-aichs-git-change-paths"
_GENERATING_FRAME_COUNT = 4
_FOLDER_GROUP_THRESHOLD = 20
_STATUS_ICON_GAP = 6


class _GitChangeDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.data(_ROLE_IS_HEADER):
            self._paint_header(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = QIcon()
        opt.showDecorationSelected = False
        widget = opt.widget
        from PyQt6.QtWidgets import QApplication
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        icon_w = 12
        if widget is not None and hasattr(widget, "iconSize"):
            icon_w = max(12, widget.iconSize().width())
        icon_left = option.rect.left() + 8
        icon_top = option.rect.center().y() - icon_w // 2
        badge_inset = max(1, icon_w // 6)
        badge_size = icon_w - badge_inset * 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_git_status_badge(
            painter,
            str(index.data(_ROLE_STATUS_CODE) or ""),
            str(index.data(_ROLE_STATUS_LABEL) or ""),
            QRectF(icon_left + badge_inset, icon_top + badge_inset, badge_size, badge_size),
        )
        painter.restore()

        p = palette()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        path_color = p["SELECTION_TEXT"] if selected else p["TEXT"]
        path = str(index.data(Qt.ItemDataRole.DisplayRole) or index.data(_ROLE_REL_PATH) or "")

        text_left = option.rect.left() + 8 + icon_w + _STATUS_ICON_GAP
        path_rect = option.rect.adjusted(text_left - option.rect.left(), 0, -8, 0)
        path_font = mono_font(git_panel_path_pt())

        painter.save()
        painter.setFont(path_font)
        painter.setPen(QColor(path_color))
        elided = QFontMetrics(path_font).elidedText(
            path,
            Qt.TextElideMode.ElideMiddle,
            max(0, path_rect.width()),
        )
        painter.drawText(path_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
        painter.restore()

    def _paint_header(self, painter, option, index):
        p = palette()
        rect = option.rect.adjusted(8, 1, -8, 0)
        font = app_font()
        font.setPointSize(git_panel_caption_pt())
        font.setWeight(QFont.Weight.Medium)
        painter.save()
        painter.setFont(font)
        painter.setPen(QColor(p["TEXT_DIM"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(index.data(Qt.ItemDataRole.DisplayRole)))
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.data(_ROLE_IS_HEADER):
            font = app_font()
            font.setPointSize(git_panel_caption_pt())
            size.setHeight(max(size.height(), QFontMetrics(font).height() + 2))
            return size
        size.setHeight(max(size.height(), QFontMetrics(option.font).height() + 5))
        return size


class _GitSectionLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click for bulk actions")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _GitChangeList(QListWidget):
    files_dropped = pyqtSignal(bool, bool, list)
    drag_active_changed = pyqtSignal(bool)

    def __init__(self, staged: bool, parent=None):
        super().__init__(parent)
        self.staged = staged
        self._filter_text = ""
        self._drag_active = False
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setItemDelegate(_GitChangeDelegate(self))
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(12, 12))

    def set_filter_text(self, text: str):
        self._filter_text = str(text or "").strip().lower()
        for row in range(self.count()):
            item = self.item(row)
            if item.data(_ROLE_IS_HEADER):
                continue
            rel = str(item.data(_ROLE_REL_PATH) or "").lower()
            item.setHidden(bool(self._filter_text) and self._filter_text not in rel)

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:
        paths = []
        for item in items:
            if item.data(_ROLE_IS_HEADER):
                continue
            rel = str(item.data(_ROLE_REL_PATH) or "").strip()
            if rel:
                paths.append(rel)
        mime = QMimeData()
        if paths:
            payload = json.dumps({"staged": self.staged, "paths": paths}).encode("utf-8")
            mime.setData(_GIT_CHANGE_MIME, payload)
            mime.setData(AICHS_FILE_DROP_MIME, file_drop_payload(paths))
            mime.setText(file_drop_text(paths))
        return mime

    def _cross_section_drop_payload(self, mime: QMimeData) -> tuple[bool, list[str]] | None:
        payload = _decode_drop_payload(mime)
        if not payload:
            return None
        source_staged, paths = payload
        if source_staged == self.staged or not paths:
            return None
        return payload

    def dragEnterEvent(self, event):
        self._set_drag_active(True)
        if event.mimeData().hasFormat(_GIT_CHANGE_MIME):
            if self._cross_section_drop_payload(event.mimeData()):
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
            else:
                event.ignore()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(_GIT_CHANGE_MIME):
            if self._cross_section_drop_payload(event.mimeData()):
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
            else:
                event.ignore()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drag_active(False)
        payload = self._cross_section_drop_payload(event.mimeData())
        if payload is None:
            if _decode_drop_payload(event.mimeData()) is not None:
                event.ignore()
                return
            super().dropEvent(event)
            return
        source_staged, paths = payload
        self.files_dropped.emit(source_staged, self.staged, paths)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def _set_drag_active(self, active: bool):
        if self._drag_active == active:
            return
        self._drag_active = active
        self.drag_active_changed.emit(active)


class GitChangesList(QWidget):
    file_open = pyqtSignal(str)
    git_changed = pyqtSignal()
    commit_summarized = pyqtSignal(str)
    refresh_pause_changed = pyqtSignal(bool)
    lists_split_changed = pyqtSignal(list)

    def __init__(
        self,
        repo_path: str,
        parent=None,
        *,
        settings: SettingsStore | None = None,
        current_model_getter: Callable[[], str] | None = None,
        defer_refresh: bool = False,
    ):
        super().__init__(parent)
        self.repo_path = repo_path
        self._staged_count = 0
        self._settings = settings or SettingsStore()
        self._current_model_getter = current_model_getter or (lambda: "")
        self._message_thread: CommitMessageThread | None = None
        self._generate_icon = _commit_message_action_icon(self)
        self._generate_frame = 0
        self._auto_refresh_started = False
        self._refresh_generation = 0
        self._refresh_threads: list[_GitChangesRefreshThread] = []
        self._lists_split_restored = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)

        message_row = QHBoxLayout()
        message_row.setContentsMargins(0, 0, 0, 0)
        message_row.setSpacing(4)
        self.message = QTextEdit()
        self.message.setPlaceholderText("Commit message")
        self.message.setMaximumHeight(76)
        self.message.textChanged.connect(self._update_action_state)
        message_row.addWidget(self.message, 1)
        self._generate_btn = QToolButton()
        self._generate_btn.setIcon(self._generate_icon)
        self._generate_btn.setToolTip("Generate commit message from staged files")
        self._generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._generate_btn.clicked.connect(self._generate_commit_message)
        message_row.addWidget(self._generate_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(message_row)

        commit_actions = QHBoxLayout()
        commit_actions.setContentsMargins(0, 0, 0, 0)
        self._commit_btn = QPushButton("Commit")
        self._commit_btn.setToolTip("Stage files and enter a commit message")
        self._commit_btn.clicked.connect(self._commit)
        self._commit_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._commit_btn.customContextMenuRequested.connect(self._commit_context_menu)
        commit_actions.addWidget(self._commit_btn, 1)
        layout.addLayout(commit_actions)

        self._generate_timer = QTimer(self)
        self._generate_timer.setInterval(180)
        self._generate_timer.timeout.connect(self._advance_generate_animation)

        self._lists_splitter = QSplitter(Qt.Orientation.Vertical)
        self._lists_splitter.setChildrenCollapsible(False)

        staged_wrap = QWidget()
        staged_layout = QVBoxLayout(staged_wrap)
        staged_layout.setContentsMargins(0, 0, 0, 0)
        staged_layout.setSpacing(4)
        self._staged_label = _GitSectionLabel("Staged")
        self._staged_label.clicked.connect(lambda: self._section_menu(staged=True))
        staged_layout.addWidget(self._staged_label)
        self.staged_list = _GitChangeList(staged=True)
        self._configure_list(self.staged_list)
        staged_layout.addWidget(self.staged_list, 1)
        self._lists_splitter.addWidget(staged_wrap)

        unstaged_wrap = QWidget()
        unstaged_layout = QVBoxLayout(unstaged_wrap)
        unstaged_layout.setContentsMargins(0, 0, 0, 0)
        unstaged_layout.setSpacing(4)
        unstaged_header = QHBoxLayout()
        unstaged_header.setContentsMargins(0, 0, 0, 0)
        self._unstaged_label = _GitSectionLabel("Unstaged")
        self._unstaged_label.clicked.connect(lambda: self._section_menu(staged=False))
        unstaged_header.addWidget(self._unstaged_label, 1)
        self._filter_btn = QToolButton()
        self._filter_btn.setText("⌕")
        self._filter_btn.setToolTip("Filter unstaged files")
        self._filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter_btn.clicked.connect(self._toggle_filter)
        unstaged_header.addWidget(self._filter_btn)
        unstaged_layout.addLayout(unstaged_header)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter paths")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setVisible(False)
        unstaged_layout.addWidget(self._filter_edit)
        self.unstaged_list = _GitChangeList(staged=False)
        self._filter_edit.textChanged.connect(self.unstaged_list.set_filter_text)
        self._configure_list(self.unstaged_list)
        unstaged_layout.addWidget(self.unstaged_list, 1)
        self._lists_splitter.addWidget(unstaged_wrap)

        layout.addWidget(self._lists_splitter, 1)
        self._lists_splitter.splitterMoved.connect(self._on_lists_split_moved)

        self.apply_appearance()
        self._restore_lists_split()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        if defer_refresh:
            self._set_loading()
        else:
            self.refresh()
        if not defer_refresh:
            self.start_auto_refresh()

    def _configure_list(self, widget: _GitChangeList):
        widget.itemDoubleClicked.connect(self._on_open)
        widget.itemSelectionChanged.connect(self._update_action_state)
        widget.files_dropped.connect(self._move_paths)
        widget.drag_active_changed.connect(self._on_drag_active)
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(lambda pos, w=widget: self._context_menu(w, pos))

    def _on_drag_active(self, active: bool):
        self.refresh_pause_changed.emit(active)

    def _toggle_filter(self):
        visible = not self._filter_edit.isVisible()
        self._filter_edit.setVisible(visible)
        if visible:
            self._filter_edit.setFocus()
        else:
            self._filter_edit.clear()

    def _restore_lists_split(self):
        split = git_panel_lists_split(self._settings.load())
        if len(split) == 2 and sum(split) > 0:
            self._lists_splitter.setSizes(split)
        else:
            self._lists_splitter.setSizes([120, 220])
        self._lists_split_restored = True

    def _on_lists_split_moved(self, _pos: int, _index: int):
        if not self._lists_split_restored:
            return
        self.lists_split_changed.emit(self._lists_splitter.sizes())

    def apply_appearance(self):
        for label in (self._staged_label, self._unstaged_label):
            label.setStyleSheet(git_panel_section_label_style())
        self._filter_btn.setStyleSheet(hint_label_style())
        for widget in (self.staged_list, self.unstaged_list):
            widget.setStyleSheet(git_changes_list_style())
        self.message.setFont(app_font())
        self._apply_field_styles()
        self._update_action_state()

    def _apply_field_styles(self):
        ready = self._staged_count > 0 and bool(self._commit_summary().strip())
        self.message.setStyleSheet(git_commit_field_style(ready=ready))

    def _commit_summary(self) -> str:
        return split_commit_message(self.message.toPlainText())[0]

    def _on_open(self, item: QListWidgetItem):
        if item.data(_ROLE_IS_HEADER):
            return
        path = item.data(_ROLE_ABS_PATH)
        if path:
            self.file_open.emit(path)

    def refresh(self):
        self._refresh_generation += 1
        thread = _GitChangesRefreshThread(self._refresh_generation, self.repo_path, self)
        self._refresh_threads.append(thread)
        thread.done.connect(self._apply_snapshot)
        thread.finished.connect(lambda t=thread: self._release_refresh_thread(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _apply_snapshot(self, generation: int, snapshot: GitSnapshot):
        if generation != self._refresh_generation:
            return
        if snapshot.repo_path != self.repo_path:
            return
        with time_operation(
            "git_changes.apply",
            detail=f"changes={len(snapshot.changes)}",
            slow_ms=50,
        ):
            self.set_repo_state(snapshot.is_repo, list(snapshot.changes))

    def _release_refresh_thread(self, thread: _GitChangesRefreshThread):
        if thread in self._refresh_threads:
            self._refresh_threads.remove(thread)

    def set_repo_state(self, is_repo: bool, changes: list[GitFileChange]):
        if not is_repo:
            self.staged_list.clear()
            self.unstaged_list.clear()
            self._staged_count = 0
            self._staged_label.setText("Staged")
            self._unstaged_label.setText("Unstaged")
            self._add_disabled(self.unstaged_list, "(not a git repository)")
            self._update_action_state()
            return
        self.set_changes(changes)

    def set_changes(self, changes: list[GitFileChange]):
        self.staged_list.clear()
        self.unstaged_list.clear()
        self._staged_count = 0

        staged_items: list[GitFileChange] = []
        unstaged_items: list[GitFileChange] = []
        for ch in changes:
            if ch.staged:
                staged_items.append(ch)
            if ch.unstaged:
                unstaged_items.append(ch)

        for ch in staged_items:
            self._add_change(self.staged_list, ch, ch.staged_label or ch.label)
        self._populate_unstaged(unstaged_items)

        staged_count = self.staged_list.count()
        unstaged_count = sum(
            1 for i in range(self.unstaged_list.count())
            if not self.unstaged_list.item(i).data(_ROLE_IS_HEADER)
        )
        self._staged_count = staged_count
        self._staged_label.setText(f"Staged ({staged_count})" if staged_count else "Staged")
        self._unstaged_label.setText(
            f"Unstaged ({unstaged_count})" if unstaged_count else "Unstaged — clean"
        )
        self.unstaged_list.set_filter_text(self._filter_edit.text())
        self._update_action_state()

    def _populate_unstaged(self, items: list[GitFileChange]):
        if len(items) <= _FOLDER_GROUP_THRESHOLD:
            for ch in items:
                self._add_change(self.unstaged_list, ch, ch.unstaged_label or ch.label)
            return
        groups: dict[str, list[GitFileChange]] = {}
        for ch in items:
            normalized = ch.rel_path.replace("\\", "/")
            folder = PurePosixPath(normalized).parts[0] if "/" in normalized else ""
            groups.setdefault(folder or "(root)", []).append(ch)
        for folder in sorted(groups, key=lambda name: (name == "(root)", name.lower())):
            label = f"{folder}/" if folder != "(root)" else "Repository root"
            header = QListWidgetItem(label)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setData(_ROLE_IS_HEADER, True)
            self.unstaged_list.addItem(header)
            for ch in sorted(groups[folder], key=lambda c: c.rel_path.lower()):
                display_path = self._grouped_display_path(ch.rel_path, folder)
                self._add_change(
                    self.unstaged_list,
                    ch,
                    ch.unstaged_label or ch.label,
                    display_path=display_path,
                )

    @staticmethod
    def _grouped_display_path(rel_path: str, folder: str) -> str:
        if folder == "(root)":
            return rel_path
        normalized = rel_path.replace("\\", "/")
        prefix = f"{folder}/"
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
        return rel_path

    def start_auto_refresh(self):
        if self._auto_refresh_started:
            return
        self._auto_refresh_started = True
        self._refresh_timer.start(5000)

    def pause_auto_refresh(self):
        self._refresh_timer.stop()

    def resume_auto_refresh(self):
        if self._auto_refresh_started:
            self._refresh_timer.start(5000)

    def _set_loading(self):
        self.staged_list.clear()
        self.unstaged_list.clear()
        self._staged_count = 0
        self._staged_label.setText("Staged")
        self._unstaged_label.setText("Unstaged")
        self._add_disabled(self.unstaged_list, "(loading git status)")
        self._update_action_state()

    def _add_change(
        self,
        widget: QListWidget,
        ch: GitFileChange,
        label: str,
        *,
        display_path: str | None = None,
    ):
        prefix = label or "·"
        shown_path = display_path if display_path is not None else ch.rel_path
        item = QListWidgetItem(shown_path)
        item.setIcon(git_status_icon(ch.code, prefix))
        item.setToolTip(f"{git_status_description(ch.code, prefix)} — {ch.rel_path}")
        item.setData(_ROLE_ABS_PATH, ch.abs_path)
        item.setData(_ROLE_REL_PATH, ch.rel_path)
        item.setData(_ROLE_STATUS_CODE, ch.code)
        item.setData(_ROLE_STATUS_LABEL, prefix)
        widget.addItem(item)

    @staticmethod
    def _add_disabled(widget: QListWidget, text: str):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        widget.addItem(item)

    def _all_rel_paths(self, widget: QListWidget, *, selected_only: bool = False) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        items = widget.selectedItems() if selected_only else [
            widget.item(row) for row in range(widget.count())
        ]
        for item in items:
            if item is None or item.data(_ROLE_IS_HEADER):
                continue
            rel = str(item.data(_ROLE_REL_PATH) or "").strip()
            if rel and rel not in seen:
                paths.append(rel)
                seen.add(rel)
        return paths

    def _selected_rel_paths(self, widget: QListWidget) -> list[str]:
        return self._all_rel_paths(widget, selected_only=True)

    def _section_menu(self, *, staged: bool):
        widget = self.staged_list if staged else self.unstaged_list
        menu = QMenu(self)
        selected = self._selected_rel_paths(widget)
        all_paths = self._all_rel_paths(widget)
        if staged:
            unstage = QAction("Unstage all", self)
            unstage.setEnabled(bool(all_paths))
            unstage.triggered.connect(lambda: self._run_change_action("Back", unstage_files(self.repo_path, all_paths)))
            menu.addAction(unstage)
            if selected:
                partial = QAction("Unstage selected", self)
                partial.triggered.connect(lambda: self._run_change_action("Back", unstage_files(self.repo_path, selected)))
                menu.addAction(partial)
        else:
            stage_all = QAction("Stage all", self)
            stage_all.setEnabled(bool(all_paths))
            stage_all.triggered.connect(lambda: self._run_change_action("Stage", stage_files(self.repo_path, all_paths)))
            menu.addAction(stage_all)
            if selected:
                stage_sel = QAction("Stage selected", self)
                stage_sel.triggered.connect(lambda: self._run_change_action("Stage", stage_files(self.repo_path, selected)))
                menu.addAction(stage_sel)
        anchor = self._staged_label if staged else self._unstaged_label
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

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
        self.message.setPlainText(format_commit_message(summary, body))
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
        self._generate_btn.setIcon(_commit_message_busy_icon(frame))
        self._generate_btn.setToolTip("Generating commit message...")

    def _stop_generate_animation(self):
        self._generate_timer.stop()
        self._generate_btn.setIcon(self._generate_icon)
        self._generate_btn.setToolTip("Generate commit message from staged files")

    def _context_menu(self, widget: QListWidget, pos):
        item = widget.itemAt(pos)
        if item and not item.isSelected() and not item.data(_ROLE_IS_HEADER):
            widget.clearSelection()
            item.setSelected(True)
        paths = self._selected_rel_paths(widget)
        if not paths:
            return
        menu = QMenu(self)
        menu.addAction("Stash selected...", lambda: self._stash_selected(widget))
        menu.addAction("Discard changes...", lambda: self._discard_selected(widget))
        menu.exec(widget.viewport().mapToGlobal(pos))

    def _commit_context_menu(self, pos):
        if self._staged_count <= 0 or not self._commit_summary().strip():
            return
        menu = QMenu(self)
        menu.addAction(
            "Commit and summarize in chat",
            lambda: self._commit(summarize_in_chat=True),
        )
        menu.exec(self._commit_btn.mapToGlobal(pos))

    def _stash_selected(self, widget: QListWidget):
        paths = self._selected_rel_paths(widget)
        if not paths:
            return
        default = _default_stash_message(paths)
        message, ok = QInputDialog.getText(self, "Stash selected files", "Message:", text=default)
        if not ok:
            return
        self._run_change_action("Stash", stash_files(self.repo_path, paths, message))

    def _discard_selected(self, widget: _GitChangeList):
        paths = self._selected_rel_paths(widget)
        if not paths:
            return
        if not self._confirm_discard(paths, staged=widget.staged):
            return
        self._run_change_action(
            "Discard",
            discard_files(self.repo_path, paths, staged=widget.staged),
        )

    def _confirm_discard(self, paths: list[str], *, staged: bool) -> bool:
        count = len(paths)
        section = "staged" if staged else "unstaged"
        noun = "change" if count == 1 else "changes"
        detail = (
            f"Discard {count} selected {section} {noun}?\n\n"
            "This permanently removes the selected file changes."
        )
        if staged:
            detail += "\n\nAny unstaged edits on the same selected files will also be discarded."
        answer = QMessageBox.question(
            self,
            "Discard changes?",
            detail,
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def _commit(self, *, summarize_in_chat: bool = False):
        summary, body = split_commit_message(self.message.toPlainText())
        staged_count = self._staged_count
        result = commit_staged(self.repo_path, summary, body)
        if result.ok:
            if summarize_in_chat:
                self.commit_summarized.emit(_commit_chat_summary(summary, body, staged_count))
            self.message.clear()
        self._run_change_action("Commit", result, refresh_history=True)

    def _run_change_action(
        self,
        label: str,
        result: GitCommandResult,
        refresh_history: bool = False,
    ):
        if result.ok:
            clear_git_snapshot_cache(self.repo_path)
            self.refresh()
            if refresh_history:
                self.git_changed.emit()
        else:
            self._show_git_error(label, result)
            self._update_action_state()

    def _update_action_state(self):
        has_summary = bool(self._commit_summary().strip())
        ready = self._staged_count > 0 and has_summary
        self._commit_btn.setEnabled(ready)
        if ready:
            noun = "file" if self._staged_count == 1 else "files"
            self._commit_btn.setToolTip(f"Commit {self._staged_count} staged {noun}")
            self._commit_btn.setStyleSheet(primary_button_style())
        else:
            self._commit_btn.setToolTip("Stage files and enter a commit message")
            self._commit_btn.setStyleSheet(git_change_button_style())
        self._apply_field_styles()
        generating = bool(self._message_thread and self._message_thread.isRunning())
        self._generate_btn.setEnabled(self._staged_count > 0 and not generating)

    def _show_git_error(self, label: str, result: GitCommandResult):
        detail = _git_result_detail(result) or f"{label} failed."
        QMessageBox.warning(self, f"{label} failed", detail)

    def set_repo_path(self, path: str):
        clear_git_snapshot_cache(self.repo_path)
        self.repo_path = path
        clear_git_snapshot_cache(self.repo_path)
        self.refresh()
        self.start_auto_refresh()

    def shutdown(self):
        self._refresh_generation += 1
        self._refresh_timer.stop()
        self._generate_timer.stop()
        if self._message_thread is not None:
            try:
                self._message_thread.disconnect()
            except (AttributeError, RuntimeError, TypeError):
                pass
            is_running = getattr(self._message_thread, "isRunning", lambda: False)
            if is_running():
                self._message_thread.wait(3000)
            delete_later = getattr(self._message_thread, "deleteLater", None)
            if delete_later is not None:
                delete_later()
            self._message_thread = None
        for thread in list(self._refresh_threads):
            try:
                thread.done.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                thread.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            if thread.isRunning():
                thread.wait(3000)
            thread.deleteLater()
        self._refresh_threads.clear()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


class _GitChangesRefreshThread(QThread):
    done = pyqtSignal(int, object)

    def __init__(self, generation: int, repo_path: str, parent=None):
        super().__init__(parent)
        self._generation = generation
        self._repo_path = repo_path

    def run(self):
        self.done.emit(self._generation, build_git_snapshot(self._repo_path))


def _commit_chat_summary(summary: str, body: str, staged_count: int) -> str:
    lines = [f"I committed {staged_count} file(s):", f"- {summary}"]
    if body.strip():
        lines.extend(["", body.strip()])
    return "\n".join(lines)


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
    return git_change_button_style(theme)


def _git_change_field_style(theme: str | None = None) -> str:
    return git_commit_field_style(theme=theme)
