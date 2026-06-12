import os
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from services.workspace_snapshot import (
    README_NAMES,
    WorkspaceSnapshot,
    build_workspace_snapshot,
    display_chat_time,
    display_updated_at,
)
from services.performance import time_operation
from storage.repository import remove_workspace
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

_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_EXISTS = Qt.ItemDataRole.UserRole + 1
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
        meta_font_pt()
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

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(14)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)
        self._title = QLabel("Workspace")
        self._title.setObjectName("workspaceDashboardTitle")
        self._path = QLabel()
        self._path.setObjectName("workspaceDashboardPath")
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path.setWordWrap(True)
        title_col.addWidget(self._title)
        title_col.addWidget(self._path)
        header.addLayout(title_col, 1)

        self._open_btn = QPushButton("Open Folder")
        self._open_btn.setObjectName("workspaceOpenFolder")
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(self._open_folder)
        header.addWidget(self._open_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        root.addLayout(grid, 1)

        self._overview_card = _card()
        overview_layout = QVBoxLayout(self._overview_card)
        overview_layout.setContentsMargins(16, 14, 16, 14)
        overview_layout.setSpacing(8)
        overview_layout.addWidget(_section_label("Overview"))
        self._current_name = QLabel()
        self._current_name.setObjectName("workspaceCurrentName")
        self._current_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
        self._skills_status = _status_pill()
        self._extensions_status = _status_pill()
        for label in (
            self._git_status,
            self._branch_status,
            self._agents_status,
            self._skills_status,
            self._extensions_status,
        ):
            self._status_row.addWidget(label)
        self._status_row.addStretch(1)
        overview_layout.addLayout(self._status_row)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(8)
        self._new_chat_btn = QPushButton("New Chat")
        self._new_chat_btn.clicked.connect(self.new_chat_requested.emit)
        self._file_search_btn = QPushButton("File Search")
        self._file_search_btn.clicked.connect(self.file_search_requested.emit)
        self._text_search_btn = QPushButton("Text Search")
        self._text_search_btn.clicked.connect(self.text_search_requested.emit)
        for button in (self._new_chat_btn, self._file_search_btn, self._text_search_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        actions.addStretch(1)
        overview_layout.addLayout(actions)
        grid.addWidget(self._overview_card, 0, 0)

        self._readme_card = _card()
        readme_layout = QVBoxLayout(self._readme_card)
        readme_layout.setContentsMargins(16, 14, 16, 14)
        readme_layout.setSpacing(8)
        readme_header = QHBoxLayout()
        readme_header.setContentsMargins(0, 0, 0, 0)
        self._readme_title = _section_label("README")
        readme_header.addWidget(self._readme_title, 1)
        self._open_readme_btn = QPushButton("Open")
        self._open_readme_btn.clicked.connect(self._open_readme)
        readme_header.addWidget(self._open_readme_btn)
        readme_layout.addLayout(readme_header)
        self._readme_preview = RemoteImageTextBrowser()
        self._readme_preview.setObjectName("workspacePreview")
        self._readme_preview.setOpenExternalLinks(False)
        readme_layout.addWidget(self._readme_preview, 1)
        grid.addWidget(self._readme_card, 1, 0)

        self._instructions_card = _card()
        instructions_layout = QVBoxLayout(self._instructions_card)
        instructions_layout.setContentsMargins(16, 14, 16, 14)
        instructions_layout.setSpacing(8)
        instructions_header = QHBoxLayout()
        instructions_header.setContentsMargins(0, 0, 0, 0)
        self._instructions_title = _section_label("Project Instructions")
        instructions_header.addWidget(self._instructions_title, 1)
        self._open_agents_btn = QPushButton("Open")
        self._open_agents_btn.clicked.connect(self._open_agents)
        instructions_header.addWidget(self._open_agents_btn)
        instructions_layout.addLayout(instructions_header)
        self._instructions_preview = RemoteImageTextBrowser()
        self._instructions_preview.setObjectName("workspaceInstructionsPreview")
        self._instructions_preview.setOpenExternalLinks(False)
        self._instructions_preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        instructions_layout.addWidget(self._instructions_preview, 1)
        grid.addWidget(self._instructions_card, 2, 0)

        self._chats_card = _card()
        chats_layout = QVBoxLayout(self._chats_card)
        chats_layout.setContentsMargins(16, 14, 16, 14)
        chats_layout.setSpacing(8)
        chats_layout.addWidget(_section_label("Recent Chats"))
        self._recent_chats = QListWidget()
        self._recent_chats.setObjectName("workspaceRecentChats")
        self._recent_chats.itemActivated.connect(self._activate_chat)
        self._recent_chats.itemClicked.connect(self._activate_chat)
        chats_layout.addWidget(self._recent_chats, 1)
        grid.addWidget(self._chats_card, 0, 1, 2, 1)

        self._workspaces_card = _card()
        workspaces_layout = QVBoxLayout(self._workspaces_card)
        workspaces_layout.setContentsMargins(16, 14, 16, 14)
        workspaces_layout.setSpacing(8)
        workspaces_layout.addWidget(_section_label("Recent Workspaces"))
        self._recent = QListWidget()
        self._recent.setObjectName("workspaceRecentList")
        self._recent.itemActivated.connect(self._activate_item)
        self._recent.itemClicked.connect(self._activate_item)
        self._recent.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._recent.customContextMenuRequested.connect(self._show_recent_menu)
        workspaces_layout.addWidget(self._recent, 1)
        grid.addWidget(self._workspaces_card, 2, 1)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if defer_refresh:
            self._set_placeholders()
        else:
            self.refresh()
        self.apply_appearance()

    def set_current_workspace(self, path: str):
        self._current_workspace = os.path.abspath(path)
        self.refresh()

    def refresh(self, *, git_snapshot=None, git_changes=None):
        self._has_loaded = True
        current = self._current_workspace
        current_name = Path(current).name or current
        self._path.setText(current)
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
            selector="QPushButton#workspaceOpenFolder",
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
            selector="QListWidget#workspaceRecentList, QListWidget#workspaceRecentChats",
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
            f"QFrame#workspaceHomeCard {{ background:{p['BG2']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:8px; }}"
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

    def _add_workspace_item(self, row):
        path = str(row.path or "")
        exists = bool(row.exists)
        name = str(row.name or Path(path).name or path)
        when = display_updated_at(str(row.updated_at or ""))
        suffix = when if exists else "Missing folder"
        item = QListWidgetItem()
        item.setSizeHint(_dashboard_row_size(3))
        item.setToolTip(path)
        item.setData(_ROLE_PATH, path)
        item.setData(_ROLE_EXISTS, exists)
        if not exists:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self._recent.addItem(item)
        self._recent.setItemWidget(item, _DashboardListRow(name, [path, suffix]))

    def _activate_item(self, item: QListWidgetItem):
        if not bool(item.data(_ROLE_EXISTS)):
            return
        path = str(item.data(_ROLE_PATH) or "")
        if path:
            self.switch_requested.emit(path)

    def _activate_chat(self, item: QListWidgetItem):
        path = str(item.data(_ROLE_CONVERSATION_PATH) or "")
        if path:
            self.conversation_requested.emit(path)

    def _show_recent_menu(self, pos):
        item = self._recent.itemAt(pos)
        if item is None:
            return
        path = str(item.data(_ROLE_PATH) or "")
        if not path:
            return
        menu = QMenu(self)
        remove = QAction("Remove from Recent", self)
        menu.addAction(remove)
        chosen = menu.exec(self._recent.mapToGlobal(pos))
        if chosen is remove:
            self._remove_recent_workspace(path)

    def _remove_recent_workspace(self, path: str):
        if not remove_workspace(path):
            return
        target = _path_key(path)
        for index in range(self._recent.count() - 1, -1, -1):
            item = self._recent.item(index)
            if _path_key(str(item.data(_ROLE_PATH) or "")) == target:
                self._recent.takeItem(index)
        if self._recent.count() == 0:
            self._add_empty_workspace_item()

    def _open_folder(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Open workspace",
            self._current_workspace,
            QFileDialog.Option.ShowDirsOnly,
        )
        if path:
            self.switch_requested.emit(path)

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
        self._path.setText(current)
        self._current_name.setText(current_name)
        self._current_full_path.setText(current)
        self._git_status.setText("Git pending")
        self._branch_status.setText("Branch pending")
        self._agents_status.setText("Project pending")
        self._skills_status.setText("Skills pending")
        self._extensions_status.setText("Extensions pending")
        self._readme_preview.setHtml(_empty_html("Workspace preview pending."))
        self._instructions_preview.setHtml(_empty_html("Project instructions pending."))
        self._recent_chats.clear()
        self._recent.clear()

    def _apply_snapshot(self, generation: int, snapshot: WorkspaceSnapshot):
        if generation != self._refresh_generation:
            return
        if os.path.normcase(os.path.abspath(snapshot.root)) != os.path.normcase(os.path.abspath(self._current_workspace)):
            return
        with time_operation(
            "workspace.apply",
            detail=(
                f"chats={len(snapshot.recent_chats)} "
                f"workspaces={len(snapshot.recent_workspaces)}"
            ),
            slow_ms=50,
        ):
            self._snapshot_applied = True
            self._path.setText(snapshot.root)
            self._current_name.setText(snapshot.name)
            self._current_full_path.setText(snapshot.root)
            self._apply_status(snapshot)
            self._apply_readme(snapshot)
            self._apply_agents(snapshot)
            self._apply_chats(snapshot)
            self._apply_recent_workspaces(snapshot)

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
        skill_count = snapshot.skills_count
        skill_word = "skill" if skill_count == 1 else "skills"
        self._skills_status.setText(f"{skill_count} {skill_word}" if skill_count else "No skills")
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
            updated = display_chat_time(str(chat.updated_at or ""))
            count = int(chat.message_count or 0)
            message_word = "message" if count == 1 else "messages"
            meta = f"{updated} - {count} {message_word}"
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
                _DashboardListRow("No chats in this workspace yet", empty=True),
            )

    def _apply_recent_workspaces(self, snapshot: WorkspaceSnapshot):
        self._recent.clear()
        for row in snapshot.recent_workspaces:
            self._add_workspace_item(row)
        if not snapshot.recent_workspaces:
            self._add_empty_workspace_item()

    def _add_empty_workspace_item(self):
        item = QListWidgetItem()
        item.setSizeHint(_dashboard_row_size(1))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._recent.addItem(item)
        self._recent.setItemWidget(
            item,
            _DashboardListRow("No recent workspaces yet", empty=True),
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


def _path_key(path: str) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.abspath(path))


def _preview_html(text: str) -> str:
    return _markdown_panel_html(text, empty_text="README is empty.")


def _markdown_panel_html(text: str, *, empty_text: str) -> str:
    if not text.strip():
        return _empty_html(empty_text)
    body = markdown_body(text, extensions=["fenced_code", "tables", "toc"])
    p = palette()
    css = (
        markdown_css()
        + f"body {{ background:{p['BG3']}; padding:6px 8px 12px 8px; }}"
    )
    return f"<style>{css}</style>{body}"


def _empty_html(text: str) -> str:
    p = palette()
    return (
        f"<style>body {{ color:{p['TEXT_DIM']}; font-family:sans-serif;"
        "margin:0; padding:6px 8px; }}</style>"
        f"<p>{text}</p>"
    )

