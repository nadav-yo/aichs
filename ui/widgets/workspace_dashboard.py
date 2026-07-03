import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from services.relative_time import format_relative_ago
from services.workspace_snapshot import (
    README_NAMES,
    WorkspaceSnapshot,
    build_workspace_snapshot,
)
from services.performance import time_operation
from ui.theme import (
    chat_font_pt,
    contained_list_style,
    markdown_css,
    hint_label_style,
    meta_font_pt,
    palette,
    primary_button_style,
    section_label_style,
    secondary_button_style,
    status_pill_style,
)
from ui.markdown_html import markdown_body
from ui.widgets.markdown_browser import RemoteImageTextBrowser

_ROLE_CONVERSATION_PATH = Qt.ItemDataRole.UserRole + 2


class _WorkspaceRefreshThread(QThread):
    done = pyqtSignal(int, object)

    def __init__(
        self,
        generation: int,
        root: str,
        *,
        git_snapshot=None,
        git_changes=None,
        parent=None,
    ):
        super().__init__(parent)
        self._generation = generation
        self._root = root
        self._git_snapshot = git_snapshot
        self._git_changes = list(git_changes) if git_changes is not None else None

    def run(self):
        self.done.emit(
            self._generation,
            build_workspace_snapshot(
                self._root,
                git_snapshot=self._git_snapshot,
                git_changes=self._git_changes,
            ),
        )


class _DashboardListRow(QWidget):
    def __init__(
        self,
        title: str,
        details: list[str] | tuple[str, ...] = (),
        *,
        empty: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._empty = empty
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(5)
        self.title = QLabel(title)
        self.title.setWordWrap(False)
        layout.addWidget(self.title)
        self.details = QLabel("\n".join(str(line) for line in details if str(line)))
        self.details.setWordWrap(False)
        if self.details.text():
            layout.addWidget(self.details)
        else:
            self.details.hide()
        self.apply_appearance()

    def apply_appearance(self):
        p = palette()
        fs = chat_font_pt()
        title_color = p["TEXT_DIM"] if self._empty else p["TEXT"]
        self.setStyleSheet("background:transparent;")
        self.title.setStyleSheet(
            f"color:{title_color}; font-size:{fs}px; background:transparent;"
        )
        self.details.setStyleSheet(hint_label_style())


class WorkspaceDashboard(QWidget):
    switch_requested = pyqtSignal(str)
    conversation_requested = pyqtSignal(str)
    open_file_requested = pyqtSignal(str)
    new_chat_requested = pyqtSignal()
    file_search_requested = pyqtSignal()
    text_search_requested = pyqtSignal()

    def __init__(self, current_workspace: str, parent=None, *, defer_refresh: bool = False):
        super().__init__(parent)
        self.setObjectName("workspaceDashboard")
        self._current_workspace = os.path.abspath(current_workspace)
        self._has_loaded = False
        self._refresh_generation = 0
        self._refresh_threads: list[_WorkspaceRefreshThread] = []
        self._readme_exists = False
        self._readme_text = ""
        self._agents_exists = False
        self._agents_text = ""
        self._snapshot_applied = False
        self._session_context: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(14)
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)
        self._title = QLabel("Welcome back")
        self._title.setObjectName("workspaceDashboardTitle")
        self._path = QLabel()
        self._path.setObjectName("workspaceDashboardPath")
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path.setWordWrap(True)
        title_col.addWidget(self._title)
        title_col.addWidget(self._path)
        header.addLayout(title_col, 1)
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("homeDashboardBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(14)

        self._active_card = _card()
        active_layout = QVBoxLayout(self._active_card)
        active_layout.setContentsMargins(16, 14, 16, 14)
        active_layout.setSpacing(8)
        active_header = QHBoxLayout()
        active_header.setContentsMargins(0, 0, 0, 0)
        self._active_title = QLabel("No active session")
        self._active_title.setObjectName("homeActiveTitle")
        self._active_badge = QLabel()
        self._active_badge.setObjectName("homeActiveBadge")
        self._active_badge.hide()
        active_header.addWidget(self._active_title, 1)
        active_header.addWidget(self._active_badge, 0, Qt.AlignmentFlag.AlignTop)
        active_layout.addLayout(active_header)
        self._active_meta = QLabel()
        self._active_meta.setObjectName("homeActiveMeta")
        self._active_meta.setWordWrap(True)
        active_layout.addWidget(self._active_meta)
        self._active_model = QLabel()
        self._active_model.setObjectName("homeActiveModel")
        self._active_model.hide()
        active_layout.addWidget(self._active_model)
        active_actions = QHBoxLayout()
        active_actions.setContentsMargins(0, 4, 0, 0)
        active_actions.setSpacing(8)
        self._open_session_btn = QPushButton("Open Session")
        self._open_session_btn.setObjectName("homeOpenSession")
        self._open_session_btn.clicked.connect(self._open_active_session)
        self._new_session_btn = QPushButton("New Session")
        self._new_session_btn.clicked.connect(self.new_chat_requested.emit)
        for button in (self._open_session_btn, self._new_session_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            active_actions.addWidget(button)
        active_actions.addStretch(1)
        active_layout.addLayout(active_actions)
        body_layout.addWidget(self._active_card)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        self._recent_card = _card()
        recent_layout = QVBoxLayout(self._recent_card)
        recent_layout.setContentsMargins(16, 14, 16, 14)
        recent_layout.setSpacing(8)
        recent_layout.addWidget(_section_label("Recent Sessions"))
        self._recent_chats = QListWidget()
        self._recent_chats.setObjectName("workspaceRecentChats")
        self._recent_chats.itemActivated.connect(self._activate_chat)
        self._recent_chats.itemClicked.connect(self._activate_chat)
        recent_layout.addWidget(self._recent_chats, 1)
        grid.addWidget(self._recent_card, 0, 0)

        self._actions_card = _card()
        actions_layout = QVBoxLayout(self._actions_card)
        actions_layout.setContentsMargins(16, 14, 16, 14)
        actions_layout.setSpacing(8)
        actions_layout.addWidget(_section_label("Quick Actions"))
        self._new_chat_btn = QPushButton("New Session")
        self._new_chat_btn.clicked.connect(self.new_chat_requested.emit)
        self._file_search_btn = QPushButton("File Search")
        self._file_search_btn.clicked.connect(self.file_search_requested.emit)
        self._text_search_btn = QPushButton("Text Search")
        self._text_search_btn.clicked.connect(self.text_search_requested.emit)
        for button in (self._new_chat_btn, self._file_search_btn, self._text_search_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions_layout.addWidget(button)
        actions_layout.addStretch(1)
        grid.addWidget(self._actions_card, 0, 1)
        body_layout.addLayout(grid)

        self._workspace_toggle = QToolButton()
        self._workspace_toggle.setObjectName("homeWorkspaceToggle")
        self._workspace_toggle.setText("▸ About this workspace")
        self._workspace_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._workspace_toggle.setCheckable(True)
        self._workspace_toggle.setChecked(False)
        self._workspace_toggle.clicked.connect(self._sync_workspace_section)
        body_layout.addWidget(self._workspace_toggle)

        self._workspace_section = QWidget()
        self._workspace_section.hide()
        workspace_layout = QVBoxLayout(self._workspace_section)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(14)

        self._overview_card = _card()
        overview_layout = QVBoxLayout(self._overview_card)
        overview_layout.setContentsMargins(16, 14, 16, 14)
        overview_layout.setSpacing(8)
        self._current_name = QLabel()
        self._current_name.setObjectName("workspaceCurrentName")
        self._current_full_path = QLabel()
        self._current_full_path.setObjectName("workspaceCurrentPath")
        self._current_full_path.setWordWrap(True)
        self._current_full_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        overview_layout.addWidget(self._current_name)
        overview_layout.addWidget(self._current_full_path)
        self._status_row = QHBoxLayout()
        self._status_row.setContentsMargins(0, 4, 0, 0)
        self._status_row.setSpacing(6)
        self._git_status = _status_pill()
        self._branch_status = _status_pill()
        self._agents_status = _status_pill()
        self._extensions_status = _status_pill()
        for label in (
            self._git_status,
            self._branch_status,
            self._agents_status,
            self._extensions_status,
        ):
            self._status_row.addWidget(label)
        self._status_row.addStretch(1)
        overview_layout.addLayout(self._status_row)
        workspace_layout.addWidget(self._overview_card)

        docs = QGridLayout()
        docs.setContentsMargins(0, 0, 0, 0)
        docs.setHorizontalSpacing(14)
        docs.setColumnStretch(0, 1)
        docs.setColumnStretch(1, 1)

        self._readme_card = _card()
        readme_layout = QVBoxLayout(self._readme_card)
        readme_layout.setContentsMargins(16, 14, 16, 14)
        readme_layout.setSpacing(8)
        readme_header = QHBoxLayout()
        readme_header.setContentsMargins(0, 0, 0, 0)
        readme_header.addWidget(_section_label("README"), 1)
        self._open_readme_btn = QPushButton("Open")
        self._open_readme_btn.clicked.connect(self._open_readme)
        readme_header.addWidget(self._open_readme_btn)
        readme_layout.addLayout(readme_header)
        self._readme_preview = RemoteImageTextBrowser()
        self._readme_preview.setObjectName("workspacePreview")
        self._readme_preview.setOpenExternalLinks(False)
        readme_layout.addWidget(self._readme_preview, 1)
        docs.addWidget(self._readme_card, 0, 0)

        self._instructions_card = _card()
        instructions_layout = QVBoxLayout(self._instructions_card)
        instructions_layout.setContentsMargins(16, 14, 16, 14)
        instructions_layout.setSpacing(8)
        instructions_header = QHBoxLayout()
        instructions_header.setContentsMargins(0, 0, 0, 0)
        instructions_header.addWidget(_section_label("Project Instructions"), 1)
        self._open_agents_btn = QPushButton("Open")
        self._open_agents_btn.clicked.connect(self._open_agents)
        instructions_header.addWidget(self._open_agents_btn)
        instructions_layout.addLayout(instructions_header)
        self._instructions_preview = RemoteImageTextBrowser()
        self._instructions_preview.setObjectName("workspaceInstructionsPreview")
        self._instructions_preview.setOpenExternalLinks(False)
        self._instructions_preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        instructions_layout.addWidget(self._instructions_preview, 1)
        docs.addWidget(self._instructions_card, 0, 1)
        workspace_layout.addLayout(docs)
        body_layout.addWidget(self._workspace_section)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if defer_refresh:
            self._set_placeholders()
        else:
            self.refresh()
        self.apply_appearance()

    def set_session_context(self, context: dict | None):
        self._session_context = dict(context or {})
        self._apply_session_context()

    def set_current_workspace(self, path: str):
        self._current_workspace = os.path.abspath(path)
        self.refresh()

    def refresh(self, *, git_snapshot=None, git_changes=None):
        self._has_loaded = True
        current = self._current_workspace
        current_name = Path(current).name or current
        self._path.setText(f"{current_name}  ·  {current}")
        self._current_name.setText(current_name)
        self._current_full_path.setText(current)
        if not self._snapshot_applied:
            self._set_placeholders()
        self._git_status.setText("Git pending")
        self._branch_status.setText("Branch pending")
        self._refresh_generation += 1
        thread = _WorkspaceRefreshThread(
            self._refresh_generation,
            current,
            git_snapshot=git_snapshot,
            git_changes=git_changes,
            parent=self,
        )
        self._refresh_threads.append(thread)
        thread.done.connect(self._apply_snapshot)
        thread.finished.connect(lambda t=thread: self._release_refresh_thread(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def apply_appearance(self):
        p = palette()
        fs = chat_font_pt()
        meta = meta_font_pt()
        primary = primary_button_style(
            selector="QPushButton#homeOpenSession",
            border_radius=7,
            padding="9px 14px",
        )
        secondary = secondary_button_style(
            padding="6px 10px",
            font_size=meta,
            font_weight="600",
            text_color=p["TEXT_DIM"],
            border_color=p["BORDER_SUBTLE"],
        )
        recent_list_style = contained_list_style(
            selector="QListWidget#workspaceRecentChats",
            item_padding="10px 12px",
            item_radius=6,
            item_margin="0px",
            border_radius=8,
        )
        section_style = section_label_style(
            selector="QLabel#workspaceSectionLabel",
            text_color=p["TEXT"],
            font_weight="650",
        )
        status_style = status_pill_style(
            selector="QLabel#workspaceStatusPill",
            padding="4px 7px",
            border_radius=6,
            font_pt=meta,
        )
        self.setStyleSheet(
            f"QWidget#workspaceDashboard {{ background:{p['BG']}; color:{p['TEXT']}; }}"
            "QLabel { background:transparent; }"
            f"QLabel#workspaceDashboardTitle {{ font-size:{max(20, fs + 8)}px;"
            "font-weight:700; }"
            f"{hint_label_style(selector='QLabel#workspaceDashboardPath')}"
            f"{section_style}"
            f"QLabel#workspaceCurrentName {{ color:{p['TEXT']};"
            f"font-size:{max(14, fs + 1)}px; font-weight:650; }}"
            f"{hint_label_style(selector='QLabel#workspaceCurrentPath')}"
            f"QLabel#homeActiveTitle {{ color:{p['TEXT']}; font-size:{max(15, fs + 1)}px;"
            f"font-weight:650; }}"
            f"{hint_label_style(selector='QLabel#homeActiveMeta, QLabel#homeActiveModel')}"
            f"QLabel#homeActiveBadge {{ color:{p['SUCCESS']}; font-size:{meta}px;"
            f"font-weight:600; padding:2px 8px; border:1px solid {p['SUCCESS_BORDER']};"
            f"border-radius:999px; background:{p['SUCCESS_BG']}; }}"
            f"QFrame#workspaceHomeCard {{ background:{p['BG2']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:8px; }}"
            f"QToolButton#homeWorkspaceToggle {{ color:{p['TEXT_DIM']}; border:0px;"
            f"padding:4px 0px; font-size:{meta}px; font-weight:600; text-align:left; }}"
            f"QToolButton#homeWorkspaceToggle:hover {{ color:{p['TEXT']}; }}"
            f"{status_style}"
            f"QTextBrowser#workspacePreview, QTextBrowser#workspaceInstructionsPreview {{"
            f"background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:7px;"
            f"padding:8px; font-size:{meta}px; }}"
            f"{secondary}"
            f"{primary}"
            f"{recent_list_style}"
        )
        if self._snapshot_applied:
            self._readme_preview.setHtml(
                _markdown_panel_html(
                    self._readme_text,
                    empty_text=(
                        "README is empty."
                        if self._readme_exists
                        else "No README found in this workspace."
                    ),
                )
            )
            self._instructions_preview.setHtml(
                _markdown_panel_html(
                    self._agents_text,
                    empty_text=(
                        "Project instructions are empty."
                        if self._agents_exists
                        else "No project instructions found."
                    ),
                )
            )
        for widget in self.findChildren(_DashboardListRow):
            widget.apply_appearance()
        self._apply_session_context()

    def _sync_workspace_section(self):
        expanded = self._workspace_toggle.isChecked()
        self._workspace_toggle.setText(
            "▾ About this workspace" if expanded else "▸ About this workspace"
        )
        self._workspace_section.setVisible(expanded)

    def _open_active_session(self):
        path = str(self._session_context.get("conversation_path") or "")
        if path:
            self.conversation_requested.emit(path)

    def _apply_session_context(self):
        ctx = self._session_context
        path = str(ctx.get("conversation_path") or "")
        title = str(ctx.get("title") or "").strip() or "Untitled"
        if not path:
            self._active_title.setText("No active session")
            self._active_meta.setText("Start a new session or pick one from recent sessions.")
            self._active_badge.hide()
            self._active_model.hide()
            self._open_session_btn.setEnabled(False)
            return
        self._active_title.setText(title)
        self._open_session_btn.setEnabled(True)
        parts: list[str] = []
        updated = str(ctx.get("updated_at") or "")
        if updated:
            try:
                rel = format_relative_ago(datetime.fromisoformat(updated))
                parts.append(f"Last active {rel} ago" if rel != "now" else "Last active now")
            except ValueError:
                pass
        count = int(ctx.get("message_count") or 0)
        if count:
            word = "message" if count == 1 else "messages"
            parts.append(f"{count} {word}")
        open_files = int(ctx.get("open_file_count") or 0)
        if open_files:
            word = "file" if open_files == 1 else "files"
            parts.append(f"{open_files} open {word}")
        self._active_meta.setText(" · ".join(parts) if parts else "Ready to continue")
        model = str(ctx.get("current_model") or "").strip()
        if model:
            self._active_model.setText(f"Current model: {model}")
            self._active_model.show()
        else:
            self._active_model.hide()
        if ctx.get("is_streaming"):
            self._active_badge.setText("Active")
            self._active_badge.show()
        elif ctx.get("is_queued"):
            self._active_badge.setText("Queued")
            self._active_badge.show()
        else:
            self._active_badge.hide()

    def _activate_chat(self, item: QListWidgetItem):
        path = str(item.data(_ROLE_CONVERSATION_PATH) or "")
        if path:
            self.conversation_requested.emit(path)

    def _open_readme(self):
        path = _first_existing(self._current_workspace, README_NAMES)
        if path:
            self.open_file_requested.emit(str(path))

    def _open_agents(self):
        path = Path(self._current_workspace) / "AGENTS.md"
        if path.is_file():
            self.open_file_requested.emit(str(path))

    def _set_placeholders(self):
        current = self._current_workspace
        current_name = Path(current).name or current
        self._path.setText(f"{current_name}  ·  {current}")
        self._current_name.setText(current_name)
        self._current_full_path.setText(current)
        self._git_status.setText("Git pending")
        self._branch_status.setText("Branch pending")
        self._agents_status.setText("Project pending")
        self._extensions_status.setText("Extensions pending")
        self._readme_preview.setHtml(_empty_html("Workspace preview pending."))
        self._instructions_preview.setHtml(_empty_html("Project instructions pending."))
        self._recent_chats.clear()

    def _apply_snapshot(self, generation: int, snapshot: WorkspaceSnapshot):
        if generation != self._refresh_generation:
            return
        if os.path.normcase(os.path.abspath(snapshot.root)) != os.path.normcase(
            os.path.abspath(self._current_workspace)
        ):
            return
        with time_operation(
            "workspace.apply",
            detail=f"chats={len(snapshot.recent_chats)}",
            slow_ms=50,
        ):
            self._snapshot_applied = True
            current_name = snapshot.name
            self._path.setText(f"{current_name}  ·  {snapshot.root}")
            self._current_name.setText(snapshot.name)
            self._current_full_path.setText(snapshot.root)
            self._apply_status(snapshot)
            self._apply_readme(snapshot)
            self._apply_agents(snapshot)
            self._apply_chats(snapshot)

    def _release_refresh_thread(self, thread: _WorkspaceRefreshThread):
        if thread in self._refresh_threads:
            self._refresh_threads.remove(thread)

    def shutdown(self):
        self._refresh_generation += 1
        for thread in list(self._refresh_threads):
            try:
                thread.done.disconnect()
            except TypeError:
                pass
            try:
                thread.finished.disconnect()
            except TypeError:
                pass
            if thread.isRunning():
                thread.wait(3000)
            thread.deleteLater()
        self._refresh_threads.clear()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _apply_status(self, snapshot: WorkspaceSnapshot):
        self._agents_status.setText("AGENTS.md" if snapshot.agents_exists else "No AGENTS.md")
        ext_count = snapshot.extensions_count
        ext_word = "extension" if ext_count == 1 else "extensions"
        self._extensions_status.setText(f"{ext_count} {ext_word}" if ext_count else "No extensions")
        if not snapshot.git_repo:
            self._git_status.setText("No git repo")
            self._branch_status.setText("No branch")
            return
        self._git_status.setText(
            "Clean git"
            if not snapshot.changed_count
            else f"{snapshot.changed_count} changed file{'s' if snapshot.changed_count != 1 else ''}"
        )
        self._branch_status.setText(snapshot.branch or "Detached HEAD")

    def _apply_readme(self, snapshot: WorkspaceSnapshot):
        self._open_readme_btn.setVisible(snapshot.readme_exists)
        self._readme_exists = snapshot.readme_exists
        self._readme_text = snapshot.readme_text
        if not snapshot.readme_exists:
            self._readme_preview.setHtml(_empty_html("No README found in this workspace."))
            return
        self._readme_preview.setHtml(_preview_html(snapshot.readme_text))

    def _apply_agents(self, snapshot: WorkspaceSnapshot):
        self._open_agents_btn.setVisible(snapshot.agents_exists)
        self._agents_exists = snapshot.agents_exists
        self._agents_text = snapshot.agents_text
        if not snapshot.agents_exists:
            self._instructions_preview.setHtml(_empty_html("No project instructions found."))
            return
        self._instructions_preview.setHtml(
            _markdown_panel_html(
                snapshot.agents_text,
                empty_text="Project instructions are empty.",
            )
        )

    def _apply_chats(self, snapshot: WorkspaceSnapshot):
        self._recent_chats.clear()
        for chat in snapshot.recent_chats:
            title = str(chat.title or "Untitled")
            updated = str(chat.updated_at or "")
            rel = ""
            if updated:
                try:
                    rel = format_relative_ago(datetime.fromisoformat(updated))
                    rel = "now" if rel == "now" else f"{rel} ago"
                except ValueError:
                    rel = "Recent"
            count = int(chat.message_count or 0)
            message_word = "message" if count == 1 else "messages"
            meta = f"{rel} · {count} {message_word}" if rel else f"{count} {message_word}"
            item = QListWidgetItem()
            item.setSizeHint(_dashboard_row_size(2))
            item.setData(_ROLE_CONVERSATION_PATH, str(chat.path))
            item.setToolTip(str(chat.path))
            self._recent_chats.addItem(item)
            self._recent_chats.setItemWidget(item, _DashboardListRow(title, [meta]))
        if not snapshot.recent_chats:
            item = QListWidgetItem()
            item.setSizeHint(_dashboard_row_size(1))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._recent_chats.addItem(item)
            self._recent_chats.setItemWidget(
                item,
                _DashboardListRow("No sessions in this workspace yet", empty=True),
            )


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("workspaceHomeCard")
    return frame


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("workspaceSectionLabel")
    return label


def _status_pill() -> QLabel:
    label = QLabel()
    label.setObjectName("workspaceStatusPill")
    label.setWordWrap(False)
    return label


def _dashboard_row_size(lines: int):
    height = 28 + max(1, int(lines)) * 22
    return QSize(0, height)


def _first_existing(root: str, names: tuple[str, ...]) -> Path | None:
    base = Path(root)
    for name in names:
        path = base / name
        if path.is_file():
            return path
    return None


def _preview_html(text: str) -> str:
    return _markdown_panel_html(text, empty_text="README is empty.")


def _markdown_panel_html(text: str, *, empty_text: str) -> str:
    if not text.strip():
        return _empty_html(empty_text)
    body = markdown_body(text, extensions=["fenced_code", "tables", "toc"])
    p = palette()
    css = markdown_css() + f"body {{ background:{p['BG3']}; padding:6px 8px 12px 8px; }}"
    return f"<style>{css}</style>{body}"


def _empty_html(text: str) -> str:
    p = palette()
    return (
        f"<style>body {{ color:{p['TEXT_DIM']}; font-family:sans-serif;"
        "margin:0; padding:6px 8px; }}</style>"
        f"<p>{text}</p>"
    )
