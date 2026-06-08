from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QRect, Qt, QTimer, pyqtSignal, QMimeData
from PyQt6.QtGui import QColor, QFont, QFontMetrics

from services.chat_drag import AICHS_COMMIT_DROP_MIME, commit_drop_payload, commit_drop_text
from services.diff_html import diff_to_html
from services.git_diff import commit_diff, split_diff_by_file
from services.git_status import (
    count_commits_to_pull,
    count_commits_to_push,
    is_git_repo,
    run_git,
    run_git_command,
)
from storage.settings import SettingsStore
from ui.theme import (
    ACCENT,
    git_changes_list_style,
    markdown_css,
    mono_font,
    mono_font_pt,
    palette,
    sidebar_section_label_style,
)
from ui.widgets.git_changes_list import GitChangesList

_ROLE_HASH = Qt.ItemDataRole.UserRole
_ROLE_SUBJECT = Qt.ItemDataRole.UserRole + 1
_ROLE_SHORT_HASH = Qt.ItemDataRole.UserRole + 2
_ROLE_FILE_DIFF = Qt.ItemDataRole.UserRole + 3
_ROLE_REF_BADGES = Qt.ItemDataRole.UserRole + 4
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


class _CommitDiffDialog(QDialog):
    def __init__(self, short_hash: str, subject: str, diff_text: str, parent=None):
        super().__init__(parent)
        p = palette()
        title_hash = short_hash or "commit"
        self.setWindowTitle(f"Commit {title_hash}")
        self.resize(860, 620)
        self.setMinimumSize(760, 520)
        self.setStyleSheet(
            f"QDialog {{ background:{p['BG2']}; color:{p['TEXT']}; }}"
            f"QLabel#commitHeader {{ color:{p['TEXT']}; padding:0 0 6px 0; }}"
            f"QLabel#commitSummary {{ color:{p['TEXT_DIM']}; padding:0 0 6px 0; }}"
            f"QListWidget#commitFileList {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px; outline:none; }}"
            "QListWidget#commitFileList::item { padding:7px 9px; border-radius:5px; margin:2px 4px; }"
            f"QListWidget#commitFileList::item:hover {{ background:{p['BG2']}; }}"
            f"QListWidget#commitFileList::item:selected {{ background:{p['SELECTION']};"
            f"color:{p['SELECTION_TEXT']}; }}"
            f"QTextBrowser#commitDiffViewer {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px; }}"
            f"QSplitter#commitDiffSplitter {{ background:{p['BG2']}; }}"
            f"QSplitter#commitDiffSplitter::handle {{ background:{p['BORDER_SUBTLE']}; width:1px; }}"
            f"QSplitter#commitDiffSplitter::handle:hover {{ background:{p['BORDER']}; }}"
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
        self._viewer.setHtml(f"<style>{markdown_css()}</style>{diff_to_html(diff_text)}")


class GitPanel(QWidget):
    file_open = pyqtSignal(str)

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

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._changes = GitChangesList(
            repo_path,
            settings=settings,
            current_model_getter=current_model_getter,
        )
        self._changes.file_open.connect(self.file_open.emit)
        self._changes.git_changed.connect(self._on_changes_changed)

        log_wrap = QWidget()
        ll = QVBoxLayout(log_wrap)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(2)

        log_header = QWidget()
        hl = QHBoxLayout(log_header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self._log_lbl = QLabel("Git log")
        hl.addWidget(self._log_lbl, 1)

        self._pull_btn = QPushButton("↓")
        self._pull_btn.setAccessibleName("Pull")
        self._pull_btn.setToolTip("Pull from the upstream branch")
        self._pull_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pull_btn.clicked.connect(self._pull)
        hl.addWidget(self._pull_btn)

        self._push_btn = QPushButton("↑")
        self._push_btn.setAccessibleName("Push")
        self._push_btn.setToolTip("No local commits to push")
        self._push_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._push_btn.clicked.connect(self._push)
        hl.addWidget(self._push_btn)

        ll.addWidget(log_header)

        self._git_action_status = QLabel()
        self._git_action_status.setVisible(False)
        ll.addWidget(self._git_action_status)

        self.log = _CommitLogList()
        self.log.itemDoubleClicked.connect(self._open_commit_diff)
        ll.addWidget(self.log)

        splitter.addWidget(self._changes)
        splitter.addWidget(log_wrap)
        splitter.setSizes([180, 320])
        root.addWidget(splitter, 1)

        self.apply_appearance()
        self._refresh_log()
        self._update_git_action_state()
        timer = QTimer(self)
        timer.timeout.connect(self.refresh)
        timer.start(5000)

    def apply_appearance(self):
        mono = mono_font_pt()
        font = mono_font(mono)

        self._log_lbl.setStyleSheet(sidebar_section_label_style())
        self._git_action_status.setStyleSheet(sidebar_section_label_style())
        p = palette()
        self._pull_btn.setStyleSheet(_git_action_button_style(ACCENT))
        self._push_btn.setStyleSheet(_git_action_button_style(p["SUCCESS"]))
        list_style = git_changes_list_style()
        self.log.setFont(font)
        self.log.setStyleSheet(list_style)
        self._changes.apply_appearance()

    def refresh(self):
        self._changes.refresh()
        self._refresh_log()
        self._update_git_action_state()

    def _on_changes_changed(self):
        self._refresh_log()
        self._update_git_action_state()

    def _pull(self):
        self._run_git_action("Pull", ["git", "pull", "--ff-only"])

    def _push(self):
        self._run_git_action("Push", ["git", "push"])

    def _run_git_action(self, label: str, cmd: list[str]):
        self._set_git_action_status(f"{label}ing...")
        self._set_git_action_buttons_enabled(False)
        QApplication.processEvents()

        result = run_git_command(cmd, self.repo_path, timeout=120)
        detail = _git_action_detail(result.stdout, result.stderr)
        self._set_git_action_status(f"{label} complete" if result.ok else f"{label} failed")
        if detail:
            self._git_action_status.setToolTip(detail)
        self.refresh()

    def _refresh_log(self):
        self.log.clear()
        for raw in run_git(
            ["git", "log", "--decorate=short", "--format=%H%x1f%h%x1f%D%x1f%s", "-40"],
            self.repo_path,
        ).splitlines():
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

    def _update_git_action_state(self):
        is_repo = is_git_repo(self.repo_path)
        ahead = count_commits_to_push(self.repo_path) if is_repo else 0
        behind = count_commits_to_pull(self.repo_path) if is_repo else 0
        self._pull_btn.setText(_git_action_button_text("↓", behind))
        self._push_btn.setText(_git_action_button_text("↑", ahead))
        self._pull_btn.setEnabled(is_repo)
        self._push_btn.setEnabled(is_repo and ahead > 0)
        if not is_repo:
            self._pull_btn.setToolTip("No git repository found")
            self._push_btn.setToolTip("No git repository found")
        else:
            if behind > 0:
                self._pull_btn.setToolTip(
                    f"Pull {behind} upstream commit{'s' if behind != 1 else ''}"
                )
            else:
                self._pull_btn.setToolTip("Pull from the upstream branch")
            if ahead > 0:
                self._push_btn.setToolTip(f"Push {ahead} local commit{'s' if ahead != 1 else ''}")
            else:
                self._push_btn.setToolTip("No local commits to push")

    def _set_git_action_buttons_enabled(self, enabled: bool):
        self._pull_btn.setEnabled(enabled)
        ahead = count_commits_to_push(self.repo_path) if enabled else 0
        self._push_btn.setText(_git_action_button_text("↑", ahead))
        self._push_btn.setEnabled(enabled and ahead > 0)

    def _set_git_action_status(self, text: str):
        self._git_action_status.setText(text)
        self._git_action_status.setVisible(bool(text))
        self._git_action_status.setToolTip("")

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
        self.repo_path = path
        self._changes.set_repo_path(path)
        self.refresh()


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
    count = max(0, int(count or 0))
    return symbol if count == 0 else f"{symbol} ({count})"


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


def _git_action_button_style(accent_color: str = ACCENT, theme: str | None = None) -> str:
    p = palette(theme)
    return (
        f"QPushButton {{ background:{p['BG2']}; color:{accent_color};"
        f"border:1px solid {accent_color}; border-radius:6px;"
        "padding:0 6px; min-width:26px; min-height:22px; }"
        f"QPushButton:hover {{ background:{p['BG3']}; color:{p['TEXT']};"
        f"border-color:{accent_color}; }}"
        f"QPushButton:pressed {{ background:{p['BORDER']}; }}"
        f"QPushButton:disabled {{ background:{p['BG2']}; color:{p['TEXT_DIM']};"
        f"border-color:{p['BORDER_SUBTLE']}; }}"
    )
