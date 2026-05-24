import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QPushButton, QHBoxLayout, QLabel, QMenu, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt, QFileSystemWatcher
from PyQt6.QtGui import QColor, QAction

from config import IGNORED, MAX_TREE_ENTRIES_PER_DIR
from storage.repository import ConversationStore
from storage.settings import SettingsStore
from ui.theme import palette, ACCENT, icon_button_style, files_header_style
from ui.widgets.conversation_panel import ConversationPanel
from ui.widgets.git_panel import GitPanel
from ui.widgets.settings_dialog import SettingsDialog


class _PathLabel(QLabel):
    """Workspace folder name; elides when the sidebar is narrow."""

    def __init__(self, path: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("filesPath")
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.set_path(path)

    def set_path(self, path: str):
        self._full_text = os.path.basename(path.rstrip(os.sep)) or path
        self.setToolTip(path)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        if self.width() <= 0:
            super().setText(self._full_text)
            return
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, self.width(),
        )
        super().setText(elided)


class _FilesHeader(QWidget):
    refresh_clicked = pyqtSignal()

    def __init__(self, path: str, refresh_tooltip: str = "Refresh file tree", parent=None):
        super().__init__(parent)
        self.setObjectName("filesHeader")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 6, 6)
        row.setSpacing(4)

        self._path = _PathLabel(path)
        row.addWidget(self._path, 1)

        self._refresh = QPushButton("↻")
        self._refresh.setToolTip(refresh_tooltip)
        self._refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh.setFixedSize(28, 28)
        self._refresh.clicked.connect(self.refresh_clicked.emit)
        row.addWidget(self._refresh)

        self.apply_appearance()

    def set_path(self, path: str):
        self._path.set_path(path)

    def apply_appearance(self):
        self.setStyleSheet(files_header_style())
        self._refresh.setStyleSheet(icon_button_style())


class FileTree(QTreeWidget):
    file_opened = pyqtSignal(str)
    file_attached = pyqtSignal(str)

    def __init__(self, root_path: str, parent=None):
        super().__init__(parent)
        self.root_path = root_path
        self._highlighted: set[str] = set()
        self.setHeaderHidden(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._watcher = QFileSystemWatcher([root_path])
        self._watcher.directoryChanged.connect(lambda _: self.refresh())
        self.itemExpanded.connect(self._on_item_expanded)
        self._populate()
        self.expandToDepth(1)
        self.itemDoubleClicked.connect(self._on_double_click)

    def set_root(self, path: str):
        self.root_path = path
        for d in self._watcher.directories():
            self._watcher.removePath(d)
        self._watcher.addPath(path)
        self._highlighted.clear()
        self.refresh()

    def _on_double_click(self, item: QTreeWidgetItem, _column: int):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            self.file_opened.emit(path)

    def _context_menu(self, pos):
        menu = QMenu(self)
        item = self.itemAt(pos)
        path = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        if path and os.path.isfile(path):
            attach = QAction("Attach to message", self)
            attach.triggered.connect(lambda: self.file_attached.emit(path))
            menu.addAction(attach)
            open_file = QAction("Open", self)
            open_file.triggered.connect(lambda: self.file_opened.emit(path))
            menu.addAction(open_file)
            menu.addSeparator()
        refresh = QAction("Refresh", self)
        refresh.triggered.connect(self.refresh)
        menu.addAction(refresh)
        menu.exec(self.viewport().mapToGlobal(pos))

    def refresh(self):
        self._populate()
        self.expandToDepth(1)

    def mark_touched(self, path: str):
        abs_path = os.path.abspath(
            path if os.path.isabs(path) else os.path.join(self.root_path, path)
        )
        self._highlighted.add(abs_path)
        self._apply_highlights()

    def _populate(self):
        self.clear()
        self._fill(self.invisibleRootItem(), self.root_path)
        self._apply_highlights()

    def _on_item_expanded(self, item: QTreeWidgetItem):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isdir(path) and self._has_placeholder(item):
            item.takeChildren()
            self._fill(item, path)
            self._apply_highlights()

    def _apply_highlights(self):
        accent = QColor(ACCENT)

        def walk(item: QTreeWidgetItem):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path and path in self._highlighted:
                item.setForeground(0, accent)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))

    def _fill(self, parent, path):
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        visible = [
            e for e in entries
            if e.name not in IGNORED and not e.name.startswith(".")
        ]
        for e in visible[:MAX_TREE_ENTRIES_PER_DIR]:
            item = QTreeWidgetItem([e.name])
            item.setData(0, Qt.ItemDataRole.UserRole, e.path)
            parent.addChild(item)
            if e.is_dir():
                item.addChild(QTreeWidgetItem([""]))
        omitted = len(visible) - MAX_TREE_ENTRIES_PER_DIR
        if omitted > 0:
            more = QTreeWidgetItem([f"… {omitted} more"])
            more.setDisabled(True)
            parent.addChild(more)

    @staticmethod
    def _has_placeholder(item: QTreeWidgetItem) -> bool:
        return (
            item.childCount() == 1
            and not item.child(0).data(0, Qt.ItemDataRole.UserRole)
            and not item.child(0).text(0)
        )


class LeftPanel(QWidget):
    selected         = pyqtSignal(str)
    new_chat         = pyqtSignal()
    renamed          = pyqtSignal(str, str)
    file_open        = pyqtSignal(str)
    file_attach      = pyqtSignal(str)
    settings_changed = pyqtSignal()

    def __init__(self, store: ConversationStore, root_path: str,
                 settings: SettingsStore | None = None, parent=None):
        super().__init__(parent)
        self._settings = settings or SettingsStore()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        self._conv = ConversationPanel(store)
        self._conv.selected.connect(self.selected)
        self._conv.new_chat.connect(self.new_chat)
        self._conv.renamed.connect(self.renamed)

        self._file_tree = FileTree(root_path)
        self._file_tree.file_opened.connect(self.file_open)
        self._file_tree.file_attached.connect(self.file_attach)

        files_wrap = QWidget()
        files_layout = QVBoxLayout(files_wrap)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(0)

        self._files_header = _FilesHeader(root_path)
        self._files_header.refresh_clicked.connect(self._file_tree.refresh)
        files_layout.addWidget(self._files_header)
        files_layout.addWidget(self._file_tree, 1)

        self._git = GitPanel(root_path)
        self._git.file_open.connect(self.file_open)

        git_wrap = QWidget()
        git_layout = QVBoxLayout(git_wrap)
        git_layout.setContentsMargins(0, 0, 0, 0)
        git_layout.setSpacing(0)

        self._git_header = _FilesHeader(root_path, refresh_tooltip="Refresh git status")
        self._git_header.refresh_clicked.connect(self._git.refresh)
        git_layout.addWidget(self._git_header)
        git_layout.addWidget(self._git, 1)

        tabs.addTab(self._conv, "History")
        tabs.addTab(files_wrap, "Files")
        tabs.addTab(git_wrap, "Git")

        root.addWidget(tabs, 1)

        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setToolTip("Settings (⌘,)")
        self._settings_btn.setFixedHeight(36)
        self._settings_btn.clicked.connect(self.open_settings)
        root.addWidget(self._settings_btn)

        self._apply_styles()

    def _apply_styles(self):
        p = palette()
        self._files_header.apply_appearance()
        self._git_header.apply_appearance()
        self._settings_btn.setStyleSheet(
            f"QPushButton {{ background:{p['BG2']}; color:{p['TEXT_DIM']}; border:none;"
            f"border-top:1px solid {p['BORDER_SUBTLE']}; font-size:18px; padding:10px; }}"
            f"QPushButton:hover {{ background:{p['BG3']}; color:{p['TEXT']}; }}"
        )

    def apply_appearance(self):
        self._apply_styles()
        self._conv.apply_appearance()
        self._git.apply_appearance()

    def open_settings(self):
        if SettingsDialog(self._settings, self).exec():
            self.settings_changed.emit()

    def set_workspace(self, path: str):
        self._file_tree.set_root(path)
        self._git.set_repo_path(path)
        self._files_header.set_path(path)
        self._git_header.set_path(path)

    def refresh(self):
        self._conv.refresh()

    def mark_file_touched(self, path: str):
        self._file_tree.mark_touched(path)
        self._file_tree.refresh()
        self._git.refresh()
