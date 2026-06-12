from __future__ import annotations

import time
from typing import Callable

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QRect, Qt, QThread, QTimer, pyqtSignal, QMimeData
from PyQt6.QtGui import QAction, QColor, QFont, QFontMetrics

from services.chat_drag import AICHS_COMMIT_DROP_MIME, commit_drop_payload, commit_drop_text
from services.diff_html import diff_to_html
from services.git_diff import commit_diff, split_diff_by_file
from services.git_snapshot import GitSnapshot, build_git_snapshot, clear_git_snapshot_cache
from services.git_status import GitCommandResult, run_git_command
from services.performance import time_operation
from storage.settings import (
    DEFAULT_GIT_FIX_PROMPT_TEMPLATE,
    GIT_PANEL_LISTS_SPLIT_KEY,
    GIT_PANEL_MODE_KEY,
    SettingsStore,
    git_fix_prompt_template,
    git_panel_mode,
)
from ui.theme import (
    ACCENT,
    app_font,
    contained_list_style,
    current_theme,
    git_action_button_style,
    git_action_status_error_style,
    git_log_list_style,
    git_mode_button_style,
    hint_label_style,
    markdown_css,
    mono_font,
    mono_font_pt,
    palette,
    sidebar_section_label_style,
    splitter_style,
)
from ui.widgets.git_changes_list import GitChangesList
from ui.widgets.git_sync_buttons import (
    GitSyncButtons,
    fit_git_action_button,
    git_action_button_text,
)

_ROLE_HASH = Qt.ItemDataRole.UserRole
_ROLE_SUBJECT = Qt.ItemDataRole.UserRole + 1
_ROLE_SHORT_HASH = Qt.ItemDataRole.UserRole + 2
_ROLE_FILE_DIFF = Qt.ItemDataRole.UserRole + 3
_ROLE_REF_BADGES = Qt.ItemDataRole.UserRole + 4
_LOG_SEP = "\x1f"


class _GitActionThread(QThread):
    done = pyqtSignal(str, object)

    def __init__(self, label: str, cmd: list[str], repo_path: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._cmd = list(cmd)
        self._repo_path = repo_path

    def run(self):
        self.done.emit(
            self._label,
            run_git_command(self._cmd, self._repo_path, timeout=120),
        )


class _GitRefreshThread(QThread):
    done = pyqtSignal(int, object)

    def __init__(self, generation: int, repo_path: str, parent=None):
        super().__init__(parent)
        self._generation = generation
        self._repo_path = repo_path

    def run(self):
        self.done.emit(self._generation, build_git_snapshot(self._repo_path))


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
        badges = index.data(_ROLE_REF_BADGES) or []
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

        subject_x = rect.x() + hash_width + gap
        if badges:
            subject_x = self._draw_ref_badges(painter, option, badges, subject_x)

        if subject:
            subject_rect = QRect(subject_x, rect.y(), max(0, rect.right() - subject_x + 1), rect.height())
            subject_font = app_font()
            subject_font.setPointSize(max(10, option.font.pointSize()))
            subject_metrics = QFontMetrics(subject_font)
            subject_text = subject_metrics.elidedText(
                subject,
                Qt.TextElideMode.ElideRight,
                subject_rect.width(),
            )
            painter.setFont(subject_font)
            painter.setPen(QColor(subject_color))
            painter.drawText(
                subject_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                subject_text,
            )
        painter.restore()

    def _draw_ref_badges(self, painter, option, badges, x: int) -> int:
        p = palette()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        badge_font = QFont(option.font)
        point_size = badge_font.pointSize()
        if point_size > 0:
            badge_font.setPointSize(max(8, point_size - 1))
        badge_font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(badge_font)
        painter.setFont(badge_font)

        row_rect = option.rect.adjusted(6, 0, -6, 0)
        gap = max(4, metrics.horizontalAdvance(" "))
        for label, kind in badges:
            label = str(label or "").strip()
            if not label:
                continue
            badge_width = metrics.horizontalAdvance(label) + 10
            if x + badge_width > row_rect.right():
                break
            badge_height = min(row_rect.height() - 4, metrics.height() + 4)
            badge_y = row_rect.y() + (row_rect.height() - badge_height) // 2
            badge_rect = QRect(x, badge_y, badge_width, badge_height)
            bg, border, fg = _commit_ref_badge_colors(str(kind or ""), selected, p)
            painter.setPen(QColor(border))
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(badge_rect, 4, 4)
            painter.setPen(QColor(fg))
            painter.drawText(
                badge_rect.adjusted(5, 0, -5, 0),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
            x += badge_width + gap
        painter.setBrush(Qt.BrushStyle.NoBrush)
        return x + gap

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), QFontMetrics(option.font).height() + 10))
        return size


class _CommitLogList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setItemDelegate(_CommitLogDelegate(self))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

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

    def _context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.clearSelection()
            self.setCurrentItem(item)
            item.setSelected(True)

        subject = str(item.data(_ROLE_SUBJECT) or "").strip()
        commit_hash = str(item.data(_ROLE_HASH) or "").strip()
        short_hash = str(item.data(_ROLE_SHORT_HASH) or "").strip()

        menu = QMenu(self)
        copy_message = QAction("Copy commit message", self)
        copy_message.setData("message")
        copy_message.setEnabled(bool(subject))
        menu.addAction(copy_message)
        copy_hash = QAction("Copy commit hash", self)
        copy_hash.setData("hash")
        copy_hash.setEnabled(bool(commit_hash))
        menu.addAction(copy_hash)
        ask_chat = QAction("Ask about this commit in chat", self)
        ask_chat.setData("ask")
        menu.addAction(ask_chat)

        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        choice = _commit_log_menu_choice(chosen)
        if choice in {"message", "copy commit message"}:
            self._copy_commit_message(subject)
        elif choice in {"hash", "copy commit hash"}:
            self._copy_commit_hash(commit_hash)
        elif choice in {"ask", "ask about this commit in chat"}:
            self._ask_about_commit(short_hash, commit_hash, subject)

    def _ask_about_commit(self, short_hash: str, full_hash: str, subject: str):
        widget = self.parentWidget()
        while widget is not None and not isinstance(widget, GitPanel):
            widget = widget.parentWidget()
        if isinstance(widget, GitPanel):
            widget.request_commit_chat(short_hash, full_hash, subject)

    def _copy_commit_message(self, message: str):
        message = str(message or "").strip()
        if message:
            QApplication.clipboard().setText(message)

    def _copy_commit_hash(self, commit_hash: str):
        commit_hash = str(commit_hash or "").strip()
        if commit_hash:
            QApplication.clipboard().setText(commit_hash)


class _CommitDiffDialog(QDialog):
    def __init__(self, short_hash: str, subject: str, diff_text: str, parent=None):
        super().__init__(parent)
        self._theme = current_theme()
        p = palette(self._theme)
        title_hash = short_hash or "commit"
        self.setWindowTitle(f"Commit {title_hash}")
        self.resize(860, 620)
        self.setMinimumSize(760, 520)
        file_list_style = contained_list_style(
            selector="QListWidget#commitFileList",
            item_padding="7px 9px",
            item_radius=5,
            item_margin="2px 4px",
            border_radius=8,
            bg=p["BG3"],
            border=p["BORDER"],
        )
        resize_handle_style = splitter_style(selector="QSplitter#commitDiffSplitter")
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']}; }}"
            f"QLabel#commitHeader {{ color:{p['TEXT']}; padding:0 0 6px 0; }}"
            f"{hint_label_style(selector='QLabel#commitSummary', padding='0 0 6px 0')}"
            f"{file_list_style}"
            f"QTextBrowser#commitDiffViewer {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px; }}"
            f"{resize_handle_style}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        header = QLabel(f"{short_hash} {subject}".strip() or title_hash)
        header.setObjectName("commitHeader")
        header.setTextFormat(Qt.TextFormat.PlainText)
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_font = QFont(header.font())
        header_font.setWeight(QFont.Weight.DemiBold)
        header.setFont(header_font)
        layout.addWidget(header)

        file_diffs = [
            (file_diff.path, file_diff.diff, file_diff.added, file_diff.removed)
            for file_diff in split_diff_by_file(diff_text)
        ] or [("(no changed files)", "", 0, 0)]

        summary = QLabel(_commit_diff_summary(file_diffs))
        summary.setObjectName("commitSummary")
        layout.addWidget(summary)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("commitDiffSplitter")
        self._file_list = QListWidget()
        self._file_list.setObjectName("commitFileList")
        self._file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._file_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._file_list.currentItemChanged.connect(self._on_file_selected)
        splitter.addWidget(self._file_list)

        self._viewer = QTextBrowser()
        self._viewer.setObjectName("commitDiffViewer")
        self._viewer.setOpenExternalLinks(False)
        splitter.addWidget(self._viewer)
        splitter.setSizes([240, 620])

        for path, diff, added, removed in file_diffs:
            item = QListWidgetItem(_file_diff_label(path, added, removed))
            item.setToolTip(path)
            item.setData(_ROLE_FILE_DIFF, diff)
            self._file_list.addItem(item)
        layout.addWidget(splitter, 1)
        if self._file_list.count():
            self._file_list.setCurrentRow(0)

    def _on_file_selected(self, current: QListWidgetItem | None, _previous=None):
        diff_text = str(current.data(_ROLE_FILE_DIFF) or "") if current else ""
        self._viewer.setHtml(
            f"<style>{markdown_css(theme=self._theme)}</style>"
            f"{diff_to_html(diff_text, theme=self._theme)}"
        )


class GitPanel(QWidget):
    file_open = pyqtSignal(str)
    git_help_requested = pyqtSignal(str, object)
    commit_summarized = pyqtSignal(str)

    def __init__(
        self,
        repo_path: str,
        parent=None,
        *,
        settings: SettingsStore | None = None,
        current_model_getter: Callable[[], str] | None = None,
        defer_refresh: bool = False,
        auto_refresh: bool = True,
    ):
        super().__init__(parent)
        self.repo_path = repo_path
        self._settings = settings or SettingsStore()
        self._loaded = False
        self._auto_refresh_enabled = auto_refresh
        self._auto_refresh_started = False
        self._refresh_paused = False
        self._git_action_thread: _GitActionThread | None = None
        self._refresh_generation = 0
        self._refresh_pending = False
        self._refresh_threads: list[_GitRefreshThread] = []
        self._last_snapshot = GitSnapshot(repo_path=repo_path, is_repo=False)
        self._last_git_action_failure: tuple[str, list[str], GitCommandResult] | None = None
        self._last_refresh_monotonic: float | None = None
        self._header_refresh_btn: QPushButton | None = None
        self._git_header = None
        self._mode_manual = bool(self._settings.load().get(GIT_PANEL_MODE_KEY))
        self._sync_sets: list[GitSyncButtons] = []
        self._header_sync = GitSyncButtons(compact=True, parent=self)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        mode_bar = QWidget()
        mode_layout = QHBoxLayout(mode_bar)
        mode_layout.setContentsMargins(10, 8, 10, 4)
        mode_layout.setSpacing(6)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_buttons: dict[str, QPushButton] = {}
        for key, label in (("changes", "Changes"), ("history", "History")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, mode=key: self._select_mode(mode, manual=True))
            self._mode_group.addButton(btn)
            self._mode_buttons[key] = btn
            mode_layout.addWidget(btn, 1)
        root.addWidget(mode_bar)

        self._git_action_status = QLabel()
        self._git_action_status.setWordWrap(True)
        self._git_action_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._git_action_status.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._git_action_status.customContextMenuRequested.connect(
            self._show_git_action_status_menu
        )
        self._git_action_status.setVisible(False)
        status_wrap = QWidget()
        status_layout = QVBoxLayout(status_wrap)
        status_layout.setContentsMargins(10, 0, 10, 4)
        status_layout.setSpacing(0)
        status_layout.addWidget(self._git_action_status)
        root.addWidget(status_wrap)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._changes = GitChangesList(
            repo_path,
            settings=settings,
            current_model_getter=current_model_getter,
            defer_refresh=True,
        )
        self._changes.file_open.connect(self.file_open.emit)
        self._changes.git_changed.connect(self._on_changes_changed)
        self._changes.commit_summarized.connect(self.commit_summarized.emit)
        self._changes.refresh_pause_changed.connect(self._on_refresh_pause)
        self._changes.lists_split_changed.connect(self._save_lists_split)
        self._stack.addWidget(self._changes)

        history_wrap = QWidget()
        self._history_page = history_wrap
        history_layout = QVBoxLayout(history_wrap)
        history_layout.setContentsMargins(10, 4, 10, 8)
        history_layout.setSpacing(6)
        history_label = QLabel("Recent commits")
        history_label.setStyleSheet(sidebar_section_label_style())
        history_layout.addWidget(history_label)
        self.log = _CommitLogList()
        self.log.itemDoubleClicked.connect(self._open_commit_diff)
        history_layout.addWidget(self.log, 1)
        self._stack.addWidget(history_wrap)

        self._register_sync(self._header_sync)
        self._select_mode(git_panel_mode(self._settings.load()), manual=self._mode_manual)
        self.apply_appearance()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        if not defer_refresh:
            self.refresh()
        if auto_refresh and not defer_refresh:
            self.start_auto_refresh()

    @property
    def _pull_btn(self) -> QPushButton:
        return self._header_sync.pull_btn

    @property
    def _push_btn(self) -> QPushButton:
        return self._header_sync.push_btn

    def header_sync(self) -> GitSyncButtons:
        return self._header_sync

    def create_header_sync(self) -> GitSyncButtons:
        return self._header_sync

    def _register_sync(self, widget: GitSyncButtons):
        widget.pull_clicked.connect(self._pull)
        widget.push_clicked.connect(self._push)
        self._sync_sets.append(widget)
        self._update_git_action_state(self._last_snapshot)

    def attach_git_header(self, header) -> None:
        self._git_header = header

    def attach_refresh_button(self, button: QPushButton):
        self._header_refresh_btn = button
        self._update_refresh_tooltip()

    def request_commit_chat(self, short_hash: str, full_hash: str = "", subject: str = ""):
        short_hash = str(short_hash or "").strip()
        full_hash = str(full_hash or "").strip()
        subject = str(subject or "").strip()
        prompt = (
            f"Help me understand commit {short_hash or full_hash[:7]}:\n"
            f"{subject or '(no subject)'}"
        )
        self.git_help_requested.emit(prompt, [])

    def _select_mode(self, mode: str, *, manual: bool = False):
        if mode == "sync":
            mode = "changes"
        if mode not in self._mode_buttons:
            mode = "changes"
        if manual:
            self._mode_manual = True
            self._settings.update({GIT_PANEL_MODE_KEY: mode})
        for key, btn in self._mode_buttons.items():
            btn.setChecked(key == mode)
            btn.setStyleSheet(git_mode_button_style(active=key == mode))
        page = {
            "changes": self._changes,
            "history": self._history_page,
        }[mode]
        self._stack.setCurrentWidget(page)

    def _maybe_auto_mode(self, snapshot: GitSnapshot):
        if self._mode_manual:
            return
        dirty = any(ch.staged or ch.unstaged for ch in snapshot.changes)
        self._select_mode("changes" if dirty else "history")

    def _save_lists_split(self, sizes: list[int]):
        if len(sizes) == 2:
            self._settings.update({GIT_PANEL_LISTS_SPLIT_KEY: sizes})

    def _on_refresh_pause(self, paused: bool):
        self._refresh_paused = paused
        if paused:
            self._refresh_timer.stop()
            self._changes.pause_auto_refresh()
        else:
            self.resume_auto_refresh()
            self._changes.resume_auto_refresh()

    def apply_appearance(self):
        mono = mono_font_pt()
        font = mono_font(mono)
        self._apply_git_action_status_style()
        palette()
        for widget in self._sync_sets:
            widget.apply_appearance()
        self.log.setFont(font)
        self.log.setStyleSheet(git_log_list_style())
        self._changes.apply_appearance()
        for key, btn in self._mode_buttons.items():
            btn.setStyleSheet(git_mode_button_style(active=btn.isChecked()))

    def refresh(self):
        if self._refresh_paused:
            return
        if any(thread.isRunning() for thread in self._refresh_threads):
            self._refresh_pending = True
            if self._auto_refresh_enabled:
                self.start_auto_refresh()
            return
        self._refresh_generation += 1
        if self._header_refresh_btn is not None:
            self._header_refresh_btn.setEnabled(False)
        thread = _GitRefreshThread(self._refresh_generation, self.repo_path, self)
        self._refresh_threads.append(thread)
        thread.done.connect(self._apply_snapshot)
        thread.finished.connect(lambda t=thread: self._release_refresh_thread(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._loaded = True
        if self._auto_refresh_enabled:
            self.start_auto_refresh()

    def set_changes(self, changes):
        self._changes.set_changes(changes)

    def apply_snapshot(self, snapshot: GitSnapshot):
        if snapshot.repo_path != self.repo_path:
            return
        with time_operation(
            "git.apply",
            detail=f"changes={len(snapshot.changes)} commits={len(snapshot.log_lines)}",
            slow_ms=50,
        ):
            self._last_snapshot = snapshot
            self._changes.set_repo_state(snapshot.is_repo, list(snapshot.changes))
            self._set_log_lines(snapshot.log_lines if snapshot.is_repo else ())
            self._update_git_action_state(snapshot)
            self._update_git_header(snapshot)
            self._maybe_auto_mode(snapshot)
            self._last_refresh_monotonic = time.monotonic()
            self._update_refresh_tooltip()
            if self._header_refresh_btn is not None:
                self._header_refresh_btn.setEnabled(True)
            self._loaded = True
            if self._auto_refresh_enabled:
                self.start_auto_refresh()

    def _update_git_header(self, snapshot: GitSnapshot):
        if self._git_header is None:
            return
        if not snapshot.is_repo:
            self._git_header.set_path_hint("")
            return
        branch = snapshot.branch or "detached HEAD"
        parts: list[str] = []
        if snapshot.ahead:
            parts.append(f"↑{snapshot.ahead} to push")
        if snapshot.behind:
            parts.append(f"↓{snapshot.behind} to pull")
        if not parts:
            parts.append("up to date")
        self._git_header.set_path_hint(f"{branch} · {' · '.join(parts)}")

    def _update_refresh_tooltip(self):
        if self._header_refresh_btn is None:
            return
        base = "Refresh git status"
        if self._last_refresh_monotonic is None:
            self._header_refresh_btn.setToolTip(base)
            return
        age = max(0, int(time.monotonic() - self._last_refresh_monotonic))
        self._header_refresh_btn.setToolTip(f"{base} · updated {age}s ago")

    def ensure_loaded(self):
        self._auto_refresh_enabled = True
        if not self._loaded:
            self.refresh()
        else:
            self.start_auto_refresh()

    def start_auto_refresh(self):
        if not self._auto_refresh_enabled:
            return
        if self._auto_refresh_started and self._refresh_timer.isActive():
            return
        self._auto_refresh_started = True
        if not self._refresh_paused:
            self._refresh_timer.start(5000)

    def resume_auto_refresh(self):
        if self._auto_refresh_started and not self._refresh_paused:
            self._refresh_timer.start(5000)

    def _on_changes_changed(self):
        clear_git_snapshot_cache(self.repo_path)
        self.refresh()

    def _pull(self):
        self._run_git_action("Pull", ["git", "pull", "--ff-only"])

    def _push(self):
        self._run_git_action("Push", ["git", "push"])

    def _run_git_action(self, label: str, cmd: list[str]):
        if self._git_action_thread is not None and self._git_action_thread.isRunning():
            return
        self._last_git_action_failure = None
        self._set_git_action_status(f"{label}ing...")
        self._set_git_action_buttons_enabled(False)
        thread = _GitActionThread(label, cmd, self.repo_path, self)
        self._git_action_thread = thread
        thread.done.connect(self._on_git_action_done)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_git_action_thread)
        thread.start()

    def _on_git_action_done(self, label: str, result):
        detail = _git_action_detail(result.stdout, result.stderr)
        if result.ok:
            self._last_git_action_failure = None
            self._set_git_action_status(f"{label} complete")
        else:
            cmd = self._git_action_thread._cmd if self._git_action_thread else []
            self._last_git_action_failure = (label, list(cmd), result)
            summary = _git_action_failure_summary(label, result)
            self._set_git_action_status(summary, failed=True)
        if detail:
            self._git_action_status.setToolTip(detail)
        clear_git_snapshot_cache(self.repo_path)
        self.refresh()

    def _clear_git_action_thread(self):
        self._git_action_thread = None

    def _apply_snapshot(self, generation: int, snapshot: GitSnapshot):
        if generation != self._refresh_generation:
            return
        if snapshot.repo_path != self.repo_path:
            return
        self.apply_snapshot(snapshot)

    def _release_refresh_thread(self, thread: _GitRefreshThread):
        if thread in self._refresh_threads:
            self._refresh_threads.remove(thread)
        if (
            self._refresh_pending
            and not self._refresh_paused
            and not any(existing.isRunning() for existing in self._refresh_threads)
        ):
            self._refresh_pending = False
            self.refresh()

    def _set_log_lines(self, lines):
        self.log.clear()
        for raw in lines:
            parsed = _parse_commit_log_line(raw)
            if not parsed:
                continue
            full_hash, short_hash, refs, subject = parsed
            badges = _commit_ref_badges(refs)
            text = f"{short_hash} {subject}" if subject else short_hash
            item = QListWidgetItem(text)
            tooltip = "Drag this commit into chat."
            if badges:
                tooltip += "\nRefs: " + ", ".join(label for label, _kind in badges)
            item.setToolTip(tooltip)
            item.setData(_ROLE_HASH, full_hash)
            item.setData(_ROLE_SUBJECT, subject)
            item.setData(_ROLE_SHORT_HASH, short_hash)
            item.setData(_ROLE_REF_BADGES, badges)
            self.log.addItem(item)

    def _update_git_action_state(self, snapshot: GitSnapshot | None = None):
        snapshot = snapshot or self._last_snapshot
        is_repo = snapshot.is_repo
        ahead = snapshot.ahead
        behind = snapshot.behind
        for sync in self._sync_sets:
            sync.pull_btn.setText(git_action_button_text("↓", behind))
            sync.push_btn.setText(git_action_button_text("↑", ahead))
            fit_git_action_button(sync.pull_btn, compact=sync._compact)
            fit_git_action_button(sync.push_btn, compact=sync._compact)
            sync.pull_btn.setEnabled(is_repo)
            sync.push_btn.setEnabled(is_repo and ahead > 0)
            if not is_repo:
                sync.pull_btn.setToolTip("No git repository found")
                sync.push_btn.setToolTip("No git repository found")
            elif behind > 0:
                sync.pull_btn.setToolTip(
                    f"Pull {behind} upstream commit{'s' if behind != 1 else ''}"
                )
            else:
                sync.pull_btn.setToolTip("Pull from the upstream branch")
            if ahead > 0:
                sync.push_btn.setToolTip(f"Push {ahead} local commit{'s' if ahead != 1 else ''}")
            else:
                sync.push_btn.setToolTip("No local commits to push")

    def _set_git_action_buttons_enabled(self, enabled: bool):
        ahead = self._last_snapshot.ahead if enabled else 0
        is_repo = self._last_snapshot.is_repo
        for sync in self._sync_sets:
            sync.pull_btn.setEnabled(enabled and is_repo)
            sync.push_btn.setText(git_action_button_text("↑", ahead))
            fit_git_action_button(sync.push_btn, compact=sync._compact)
            sync.push_btn.setEnabled(enabled and is_repo and ahead > 0)

    def _set_git_action_status(self, text: str, *, failed: bool = False):
        self._git_action_status.setText(text)
        self._git_action_status.setVisible(bool(text))
        self._git_action_status.setToolTip("")
        self._apply_git_action_status_style(failed=failed)

    def _apply_git_action_status_style(self, *, failed: bool | None = None):
        if failed is None:
            failed = self._last_git_action_failure is not None
        if not failed:
            self._git_action_status.setStyleSheet(sidebar_section_label_style())
            return
        self._git_action_status.setStyleSheet(git_action_status_error_style())

    def _show_git_action_status_menu(self, pos):
        detail = self._git_action_status.toolTip().strip()
        failure = self._last_git_action_failure
        if not detail and failure is None:
            return

        menu = QMenu(self)
        ask_agent = QAction("Ask agent about failure", self)
        ask_agent.setData("ask")
        ask_agent.setEnabled(failure is not None)
        menu.addAction(ask_agent)
        copy_details = QAction("Copy details", self)
        copy_details.setData("copy")
        copy_details.setEnabled(bool(detail))
        menu.addAction(copy_details)

        chosen = menu.exec(self._git_action_status.mapToGlobal(pos))
        choice = _git_action_status_menu_choice(chosen)
        if choice in {"ask", "ask agent about failure"} and failure is not None:
            label, cmd, result = failure
            self.git_help_requested.emit(
                _git_action_failure_prompt(
                    label,
                    cmd,
                    self.repo_path,
                    result,
                    self._git_fix_prompt_template(),
                ),
                [],
            )
        elif choice in {"copy", "copy details"} and detail:
            QApplication.clipboard().setText(detail)

    def _open_commit_diff(self, item: QListWidgetItem):
        full_hash = str(item.data(_ROLE_HASH) or "").strip()
        if not full_hash:
            return
        short_hash = str(item.data(_ROLE_SHORT_HASH) or "").strip() or full_hash[:7]
        subject = str(item.data(_ROLE_SUBJECT) or "").strip()
        diff_text = commit_diff(self.repo_path, full_hash)
        self._show_commit_diff_dialog(short_hash, subject, diff_text or "")

    def _show_commit_diff_dialog(self, short_hash: str, subject: str, diff_text: str):
        dlg = _CommitDiffDialog(short_hash, subject, diff_text, self)
        dlg.exec()

    def set_repo_path(self, path: str):
        clear_git_snapshot_cache(self.repo_path)
        self.repo_path = path
        clear_git_snapshot_cache(self.repo_path)
        self._changes.set_repo_path(path)
        self._last_snapshot = GitSnapshot(repo_path=path, is_repo=False)
        self.refresh()

    def _git_fix_prompt_template(self) -> str:
        try:
            data = self._settings.load()
        except Exception:
            data = {}
        return git_fix_prompt_template(data)

    def shutdown(self):
        self._refresh_generation += 1
        self._refresh_pending = False
        self._refresh_timer.stop()
        if self._git_action_thread is not None:
            try:
                self._git_action_thread.disconnect()
            except (AttributeError, RuntimeError, TypeError):
                pass
            is_running = getattr(self._git_action_thread, "isRunning", lambda: False)
            if is_running():
                self._git_action_thread.wait(3000)
            delete_later = getattr(self._git_action_thread, "deleteLater", None)
            if delete_later is not None:
                delete_later()
            self._git_action_thread = None
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
        self._changes.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def _parse_commit_log_line(line: str) -> tuple[str, str, list[str], str] | None:
    parts = str(line or "").split(_LOG_SEP, 3)
    if len(parts) == 3:
        full_hash, short_hash, subject = (part.strip() for part in parts)
        refs: list[str] = []
    elif len(parts) == 4:
        full_hash, short_hash, refs_text, subject = (part.strip() for part in parts)
        refs = _parse_commit_refs(refs_text)
    else:
        return None
    if not full_hash:
        return None
    return full_hash, short_hash or full_hash[:7], refs, subject


def _commit_log_menu_choice(action: QAction | None) -> str:
    if action is None:
        return ""
    value = str(action.data() or "").strip().lower()
    if value:
        return value
    return str(action.text() or "").replace("&", "").strip().lower()


def _parse_commit_refs(refs_text: str) -> list[str]:
    return [ref.strip() for ref in str(refs_text or "").split(",") if ref.strip()]


def _commit_ref_badges(refs: list[str]) -> list[tuple[str, str]]:
    badges: list[tuple[str, str]] = []
    seen: set[str] = set()
    for ref in refs:
        label = ""
        kind = "branch"
        if ref == "HEAD":
            label = "HEAD"
            kind = "head"
        elif ref.startswith("HEAD -> "):
            label = "HEAD"
            kind = "head"
        elif ref.startswith("origin/"):
            label = ref
            kind = "origin"
        if label and label not in seen:
            seen.add(label)
            badges.append((label, kind))
    return badges


def _commit_ref_badge_colors(kind: str, selected: bool, p: dict) -> tuple[str, str, str]:
    if selected:
        return p["BG2"], p["SELECTION_TEXT"], p["SELECTION_TEXT"]
    if kind == "origin":
        return p["SUCCESS_BG"], p["SUCCESS_BORDER"], p["SUCCESS"]
    if kind == "head":
        return p["SELECTION"], ACCENT, p["SELECTION_TEXT"]
    return p["BG3"], p["BORDER"], p["TEXT_DIM"]


def _git_action_button_text(symbol: str, count: int) -> str:
    return git_action_button_text(symbol, count)


def _fit_git_action_button(button: QPushButton, *, compact: bool = True) -> None:
    fit_git_action_button(button, compact=compact)


def _git_action_button_style(accent_color: str = ACCENT, theme: str | None = None) -> str:
    return git_action_button_style(accent_color, theme)


def _file_diff_label(path: str, added: int, removed: int) -> str:
    stats = []
    if added:
        stats.append(f"+{added}")
    if removed:
        stats.append(f"-{removed}")
    return path if not stats else f"{path} ({' '.join(stats)})"


def _commit_diff_summary(file_diffs: list[tuple[str, str, int, int]]) -> str:
    changed = len(file_diffs)
    added = sum(item[2] for item in file_diffs)
    removed = sum(item[3] for item in file_diffs)
    noun = "file" if changed == 1 else "files"
    stats = []
    if added:
        stats.append(f"+{added}")
    if removed:
        stats.append(f"-{removed}")
    suffix = "" if not stats else f"  {' '.join(stats)}"
    return f"{changed} {noun} changed{suffix}"


def _git_action_detail(stdout: str, stderr: str) -> str:
    return "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)


def _git_action_failure_summary(label: str, result: GitCommandResult) -> str:
    detail = _git_action_detail(result.stdout, result.stderr)
    first_line = next((line.strip() for line in detail.splitlines() if line.strip()), "")
    if not first_line:
        first_line = f"exit code {result.returncode}"
    return f"{label} failed: {first_line}"


def _git_action_failure_prompt(
    label: str,
    cmd: list[str],
    repo_path: str,
    result: GitCommandResult,
    template: str = DEFAULT_GIT_FIX_PROMPT_TEMPLATE,
) -> str:
    action = label.lower()
    command = " ".join(str(part) for part in cmd if str(part).strip())
    detail = _git_action_detail(result.stdout, result.stderr) or "(no output)"
    first_line = _format_git_fix_prompt_template(
        template,
        {
            "action": action,
            "label": label,
            "repo": repo_path,
            "command": command or action,
            "exit_code": str(result.returncode),
            "output": detail,
        },
    )
    return (
        f"{first_line}\n\n"
        f"Repository: {repo_path}\n"
        f"Command: {command or action}\n"
        f"Exit code: {result.returncode}\n\n"
        f"Output:\n{detail}"
    )


def _format_git_fix_prompt_template(template: str, values: dict[str, str]) -> str:
    def render(raw: str) -> str:
        return raw.format(**values).strip()

    raw = str(template or "").strip() or DEFAULT_GIT_FIX_PROMPT_TEMPLATE
    try:
        text = render(raw)
    except (IndexError, KeyError, ValueError):
        text = render(DEFAULT_GIT_FIX_PROMPT_TEMPLATE)
    return text or render(DEFAULT_GIT_FIX_PROMPT_TEMPLATE)


def _git_action_status_menu_choice(action: QAction | None) -> str:
    if action is None:
        return ""
    value = str(action.data() or "").strip().lower()
    if value:
        return value
    return str(action.text() or "").replace("&", "").strip().lower()
