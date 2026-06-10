import os
from datetime import datetime
from pathlib import Path

import markdown as _md
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from services.git_status import is_git_repo, list_file_changes, run_git
from storage.repository import ConversationStore, list_workspaces
from ui.theme import (
    ACCENT,
    chat_font_pt,
    markdown_css,
    meta_font_pt,
    palette,
    primary_button_style,
)
from ui.widgets.markdown_browser import RemoteImageTextBrowser

_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_EXISTS = Qt.ItemDataRole.UserRole + 1
_ROLE_CONVERSATION_PATH = Qt.ItemDataRole.UserRole + 2
_PREVIEW_LIMIT = 18_000


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
        self._instructions_preview = QLabel()
        self._instructions_preview.setObjectName("workspaceInstructionsPreview")
        self._instructions_preview.setWordWrap(True)
        self._instructions_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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

    def refresh(self, git_changes=None):
        self._has_loaded = True
        current = self._current_workspace
        current_name = Path(current).name or current
        self._path.setText(current)
        self._current_name.setText(current_name)
        self._current_full_path.setText(current)
        self._refresh_status(git_changes=git_changes)
        self._refresh_readme()
        self._refresh_agents()
        self._refresh_chats()
        self._recent.clear()

        rows = list_workspaces()
        current_key = os.path.normcase(os.path.abspath(current))
        shown = 0
        for row in rows:
            path = str(row.get("path") or "")
            if not path:
                continue
            if os.path.normcase(os.path.abspath(path)) == current_key:
                continue
            self._add_workspace_item(row)
            shown += 1

        if shown == 0:
            item = QListWidgetItem("No recent workspaces yet")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._recent.addItem(item)

    def apply_appearance(self):
        p = palette()
        fs = chat_font_pt()
        meta = meta_font_pt()
        primary = primary_button_style(
            selector="QPushButton#workspaceOpenFolder",
            border_radius=7,
            padding="9px 14px",
        )
        secondary = _secondary_button_style(p, meta)
        self.setStyleSheet(
            f"QWidget#workspaceDashboard {{ background:{p['BG']}; color:{p['TEXT']}; }}"
            "QLabel { background:transparent; }"
            f"QLabel#workspaceDashboardTitle {{ font-size:{max(20, fs + 8)}px;"
            "font-weight:700; }"
            f"QLabel#workspaceDashboardPath {{ color:{p['TEXT_DIM']};"
            f"font-size:{meta}px; }}"
            f"QLabel#workspaceSectionLabel {{ color:{ACCENT}; font-size:{meta}px;"
            "font-weight:700; }"
            f"QLabel#workspaceCurrentName {{ color:{p['TEXT']};"
            f"font-size:{max(14, fs + 1)}px; font-weight:650; }}"
            f"QLabel#workspaceCurrentPath {{ color:{p['TEXT_DIM']};"
            f"font-size:{meta}px; }}"
            f"QFrame#workspaceHomeCard {{ background:{p['BG2']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:8px; }}"
            f"QLabel#workspaceStatusPill {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
            f"padding:4px 7px; font-size:{meta}px; }}"
            f"QLabel#workspaceInstructionsPreview {{ color:{p['TEXT_DIM']};"
            f"font-size:{meta}px; line-height:1.45; }}"
            f"QTextBrowser#workspacePreview {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:7px;"
            f"padding:8px; font-size:{meta}px; }}"
            f"QPushButton {{ {secondary} }}"
            f"QPushButton:hover {{ background:{p['BORDER']}; color:{p['TEXT']}; }}"
            f"{primary}"
            f"QListWidget#workspaceRecentList, QListWidget#workspaceRecentChats {{ background:{p['BG2']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:8px;"
            "outline:none; padding:4px; }"
            "QListWidget#workspaceRecentList::item, QListWidget#workspaceRecentChats::item { padding:10px 12px;"
            "border-radius:6px; }"
            f"QListWidget#workspaceRecentList::item:hover, QListWidget#workspaceRecentChats::item:hover {{ background:{p['BG3']}; }}"
            f"QListWidget#workspaceRecentList::item:selected, QListWidget#workspaceRecentChats::item:selected {{"
            f"background:{p['SELECTION']}; color:{p['SELECTION_TEXT']}; }}"
        )
        if self._has_loaded:
            self._readme_preview.setHtml(_preview_html(_read_readme(self._current_workspace)))

    def _add_workspace_item(self, row: dict):
        path = str(row.get("path") or "")
        exists = bool(row.get("exists"))
        name = str(row.get("name") or Path(path).name or path)
        when = _display_updated_at(str(row.get("updated_at") or ""))
        suffix = when if exists else "Missing folder"
        item = QListWidgetItem(f"{name}\n{path}\n{suffix}")
        item.setToolTip(path)
        item.setData(_ROLE_PATH, path)
        item.setData(_ROLE_EXISTS, exists)
        if not exists:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self._recent.addItem(item)

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
        path = _first_existing(self._current_workspace, _README_NAMES)
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
        self._instructions_preview.setText("Project instructions pending.")
        self._recent_chats.clear()
        self._recent.clear()

    def _refresh_status(self, *, git_changes=None):
        root = self._current_workspace
        agents = (Path(root) / "AGENTS.md").is_file()
        self._agents_status.setText("AGENTS.md" if agents else "No AGENTS.md")
        skill_count = _skill_count(root)
        skill_word = "skill" if skill_count == 1 else "skills"
        self._skills_status.setText(f"{skill_count} {skill_word}" if skill_count else "No skills")
        ext_count = _extension_count(root)
        ext_word = "extension" if ext_count == 1 else "extensions"
        self._extensions_status.setText(f"{ext_count} {ext_word}" if ext_count else "No extensions")
        if not is_git_repo(root):
            self._git_status.setText("No git repo")
            self._branch_status.setText("No branch")
            return
        changes = list_file_changes(root) if git_changes is None else git_changes
        self._git_status.setText(
            "Clean git" if not changes else f"{len(changes)} changed file{'s' if len(changes) != 1 else ''}"
        )
        branch = run_git(["git", "branch", "--show-current"], root).strip()
        self._branch_status.setText(branch or "Detached HEAD")

    def _refresh_readme(self):
        path = _first_existing(self._current_workspace, _README_NAMES)
        has_readme = path is not None
        self._open_readme_btn.setVisible(has_readme)
        if not has_readme:
            self._readme_preview.setHtml(_empty_html("No README found in this workspace."))
            return
        text = _read_text(path)
        self._readme_preview.setHtml(_preview_html(text))

    def _refresh_agents(self):
        path = Path(self._current_workspace) / "AGENTS.md"
        has_agents = path.is_file()
        self._open_agents_btn.setVisible(has_agents)
        if not has_agents:
            self._instructions_preview.setText("No project instructions found.")
            return
        self._instructions_preview.setText(_plain_preview(_read_text(path), limit=900))

    def _refresh_chats(self):
        self._recent_chats.clear()
        rows = ConversationStore(self._current_workspace).list_all()[:5]
        for path, summary in rows:
            title = str(summary.get("title") or "Untitled")
            updated = _display_chat_time(str(summary.get("updated_at") or ""))
            count = int(summary.get("message_count") or 0)
            item = QListWidgetItem(f"{title}\n{updated} - {count} messages")
            item.setData(_ROLE_CONVERSATION_PATH, str(path))
            item.setToolTip(str(path))
            self._recent_chats.addItem(item)
        if not rows:
            item = QListWidgetItem("No chats in this workspace yet")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._recent_chats.addItem(item)


def _display_updated_at(value: str) -> str:
    if not value:
        return "Recent"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return "Recent"
    return dt.strftime("Last opened %b %d, %Y %H:%M")


_README_NAMES = ("README.md", "README.markdown", "README.txt", "README")


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


def _secondary_button_style(p: dict, meta: int) -> str:
    return (
        f"background:{p['BG3']}; color:{p['TEXT_DIM']};"
        f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:6px;"
        f"padding:6px 10px; font-size:{meta}px; font-weight:600;"
    )


def _first_existing(root: str, names: tuple[str, ...]) -> Path | None:
    base = Path(root)
    for name in names:
        path = base / name
        if path.is_file():
            return path
    return None


def _read_readme(root: str) -> str:
    path = _first_existing(root, _README_NAMES)
    return _read_text(path) if path else ""


def _read_text(path: Path | None, limit: int = _PREVIEW_LIMIT) -> str:
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:limit]


def _preview_html(text: str) -> str:
    if not text.strip():
        return _empty_html("README is empty.")
    body = _md.markdown(text, extensions=["fenced_code", "tables", "toc"])
    return f"<style>{markdown_css()}</style>{body}"


def _empty_html(text: str) -> str:
    p = palette()
    return (
        f"<style>body {{ color:{p['TEXT_DIM']}; font-family:sans-serif;"
        "margin:0; padding:0; }}</style>"
        f"<p>{text}</p>"
    )


def _plain_preview(text: str, limit: int = 900) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    compact = "\n".join(line for line in lines if line)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _display_chat_time(value: str) -> str:
    if not value:
        return "Recent"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return "Recent"
    return dt.strftime("%b %d, %Y %H:%M")


def _extension_count(root: str) -> int:
    ext_dir = Path(root) / ".aichs" / "extensions"
    if not ext_dir.is_dir():
        return 0
    count = 0
    for child in ext_dir.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_file() and child.suffix == ".py":
            count += 1
        elif child.is_dir() and (child / "extension.py").is_file():
            count += 1
    return count


def _skill_count(root: str) -> int:
    skills_dir = Path(root) / ".aichs" / "skills"
    if not skills_dir.is_dir():
        return 0
    return sum(
        1
        for child in skills_dir.glob("*.md")
        if child.is_file() and not child.name.startswith(".")
    )
