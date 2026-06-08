import os
import shutil
from pathlib import Path
from typing import Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QPushButton, QHBoxLayout, QLabel, QMenu, QSizePolicy, QStyleFactory,
    QAbstractItemView, QInputDialog, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt, QFileSystemWatcher, QTimer, QMimeData
from PyQt6.QtGui import QColor, QAction

from config import IGNORED, MAX_TREE_ENTRIES_PER_DIR
from services.chat_drag import AICHS_FILE_DROP_MIME, file_drop_payload, file_drop_text
from services.git_status import discard_files, list_file_changes
from storage.repository import ConversationStore
from storage.settings import SettingsStore
from ui.theme import (
    palette, ACCENT, git_status_color, icon_button_style, files_header_style,
    file_tree_sidebar_style, mono_font_pt, mono_font,
    apply_flat_tab_style, sidebar_settings_button_style,
)
from ui.widgets.conversation_panel import ConversationPanel
from ui.widgets.git_panel import GitPanel
from ui.widgets.docs_dialog import DocsDialog
from ui.widgets.settings_dialog import SettingsDialog


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


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
        self.setObjectName("fileTree")
        self.root_path = root_path
        self._highlighted: set[str] = set()
        self._git_by_path: dict[str, tuple[str, str]] = {}
        self.setHeaderHidden(True)
        self.setAnimated(False)
        self.setAllColumnsShowFocus(False)
        self.setStyle(QStyleFactory.create("Fusion"))
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._apply_tree_style()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._watcher = QFileSystemWatcher([root_path])
        self._watcher.directoryChanged.connect(lambda _: self.refresh())
        self.itemExpanded.connect(self._on_item_expanded)
        self._populate()
        self.expandToDepth(1)
        self.itemDoubleClicked.connect(self._on_double_click)
        self._git_timer = QTimer(self)
        self._git_timer.timeout.connect(self._refresh_git_status)
        self._git_timer.start(5000)

    def _apply_tree_style(self):
        self.setFont(mono_font(mono_font_pt()))
        self.setStyleSheet(file_tree_sidebar_style())

    def set_root(self, path: str):
        self.root_path = path
        for d in self._watcher.directories():
            self._watcher.removePath(d)
        self._watcher.addPath(path)
        self._highlighted.clear()
        self._git_by_path.clear()
        self.refresh()

    def _on_double_click(self, item: QTreeWidgetItem, _column: int):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            self.file_opened.emit(path)

    def mimeData(self, items: list[QTreeWidgetItem]) -> QMimeData:
        refs = []
        root = Path(self.root_path).resolve()
        for item in items:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if not path or not os.path.isfile(path):
                continue
            try:
                refs.append(Path(path).resolve().relative_to(root).as_posix())
            except (OSError, ValueError):
                continue
        mime = QMimeData()
        if refs:
            mime.setData(AICHS_FILE_DROP_MIME, file_drop_payload(refs))
            mime.setText(file_drop_text(refs))
        return mime

    def _context_menu(self, pos):
        menu = QMenu(self)
        item = self.itemAt(pos)
        path = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        if path and os.path.isdir(path):
            new_file = QAction("New File...", self)
            new_file.triggered.connect(lambda: self._new_file_dialog(path))
            menu.addAction(new_file)
            new_folder = QAction("New Folder...", self)
            new_folder.triggered.connect(lambda: self._new_folder_dialog(path))
            menu.addAction(new_folder)
            menu.addSeparator()
            delete_folder = QAction("Delete...", self)
            delete_folder.triggered.connect(lambda: self._delete_path_dialog(path))
            menu.addAction(delete_folder)
            menu.addSeparator()
        elif path and os.path.isfile(path):
            attach = QAction("Attach to message", self)
            attach.triggered.connect(lambda: self.file_attached.emit(path))
            menu.addAction(attach)
            open_file = QAction("Open", self)
            open_file.triggered.connect(lambda: self.file_opened.emit(path))
            menu.addAction(open_file)
            rename = QAction("Rename...", self)
            rename.triggered.connect(lambda: self._rename_file_dialog(path))
            menu.addAction(rename)
            delete_file = QAction("Delete...", self)
            delete_file.triggered.connect(lambda: self._delete_path_dialog(path))
            menu.addAction(delete_file)
            if self._is_discardable_modified_file(path):
                discard = QAction("Discard changes...", self)
                discard.triggered.connect(lambda: self._discard_file_dialog(path))
                menu.addAction(discard)
            menu.addSeparator()
        refresh = QAction("Refresh", self)
        refresh.triggered.connect(self.refresh)
        menu.addAction(refresh)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _new_file_dialog(self, folder: str):
        name, ok = QInputDialog.getText(self, "New file", "File name:")
        if not ok:
            return
        try:
            path = self.create_file(folder, name)
        except Exception as exc:
            QMessageBox.warning(self, "New file failed", str(exc))
            return
        self.refresh()
        self.mark_touched(str(path))
        self.file_opened.emit(str(path))

    def _new_folder_dialog(self, folder: str):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok:
            return
        try:
            path = self.create_folder(folder, name)
        except Exception as exc:
            QMessageBox.warning(self, "New folder failed", str(exc))
            return
        self.refresh()
        self.mark_touched(str(path))

    def _rename_file_dialog(self, path: str):
        current = os.path.basename(path)
        name, ok = QInputDialog.getText(self, "Rename file", "New name:", text=current)
        if not ok or name.strip() == current:
            return
        try:
            new_path = self.rename_file(path, name)
        except Exception as exc:
            QMessageBox.warning(self, "Rename failed", str(exc))
            return
        self.refresh()
        self.mark_touched(str(new_path))

    def _discard_file_dialog(self, path: str):
        rel_path = self._repo_relative_path(path)
        if not rel_path:
            return
        answer = QMessageBox.question(
            self,
            "Discard changes?",
            f"Discard changes to '{rel_path}'?\n\nThis permanently removes the file changes.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Discard:
            return
        code = self._git_by_path.get(_path_key(path), ("", ""))[0]
        staged = code[:1] == "M" and (code + " ")[1] == " "
        result = discard_files(self.root_path, [rel_path], staged=staged)
        if not result.ok:
            detail = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            QMessageBox.warning(self, "Discard failed", detail or "Discard failed.")
            return
        self.refresh()

    def _delete_path_dialog(self, path: str):
        target = Path(path)
        kind = "folder" if target.is_dir() else "file"
        answer = QMessageBox.question(
            self,
            f"Delete {kind}",
            f"Delete {kind} '{target.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.delete_path(path)
        except Exception as exc:
            QMessageBox.warning(self, f"Delete {kind} failed", str(exc))
            return
        self.refresh()

    def create_file(self, folder: str, name: str) -> Path:
        directory = Path(folder)
        if not directory.is_dir():
            raise ValueError("Choose a folder in the workspace.")
        self._ensure_in_workspace(directory)
        target = directory / self._clean_leaf_name(name)
        self._ensure_in_workspace(target)
        if target.exists():
            raise FileExistsError(f"File already exists: {target.name}")
        target.touch()
        return target

    def create_folder(self, folder: str, name: str) -> Path:
        directory = Path(folder)
        if not directory.is_dir():
            raise ValueError("Choose a folder in the workspace.")
        self._ensure_in_workspace(directory)
        target = directory / self._clean_leaf_name(name)
        self._ensure_in_workspace(target)
        if target.exists():
            raise FileExistsError(f"Folder already exists: {target.name}")
        target.mkdir()
        return target

    def rename_file(self, path: str, name: str) -> Path:
        source = Path(path)
        if not source.is_file():
            raise ValueError("Choose a file in the workspace.")
        self._ensure_in_workspace(source)
        target = source.with_name(self._clean_leaf_name(name))
        self._ensure_in_workspace(target)
        if target == source:
            return source
        if target.exists():
            raise FileExistsError(f"File already exists: {target.name}")
        source.rename(target)
        return target

    def delete_path(self, path: str) -> None:
        target = Path(path)
        self._ensure_in_workspace(target)
        if target.resolve(strict=False) == Path(self.root_path).resolve():
            raise ValueError("Cannot delete the workspace folder.")
        if target.is_file():
            target.unlink()
            return
        if target.is_dir():
            shutil.rmtree(target)
            return
        raise FileNotFoundError(f"Path not found: {target}")

    def _ensure_in_workspace(self, path: Path) -> None:
        root = Path(self.root_path).resolve()
        candidate = path.resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            raise ValueError("Path must stay inside the workspace.")

    def _repo_relative_path(self, path: str) -> str:
        try:
            return Path(path).resolve(strict=False).relative_to(
                Path(self.root_path).resolve()
            ).as_posix()
        except (OSError, ValueError):
            return ""

    def _is_discardable_modified_file(self, path: str) -> bool:
        if not os.path.isfile(path):
            return False
        code, label = self._git_by_path.get(_path_key(path), ("", ""))
        return label == "M" and code in {" M", "M "}

    @staticmethod
    def _clean_leaf_name(name: str) -> str:
        clean = name.strip()
        if not clean:
            raise ValueError("Enter a file name.")
        if clean in {".", ".."}:
            raise ValueError("Enter a file name.")
        separators = {os.sep}
        if os.altsep:
            separators.add(os.altsep)
        if any(separator in clean for separator in separators):
            raise ValueError("Enter a file name, not a path.")
        parsed = Path(clean)
        if parsed.drive or parsed.root:
            raise ValueError("Enter a file name, not a path.")
        return clean

    def refresh(self):
        self._populate()
        self.expandToDepth(1)

    def mark_touched(self, path: str):
        abs_path = os.path.abspath(
            path if os.path.isabs(path) else os.path.join(self.root_path, path)
        )
        self._highlighted.add(_path_key(abs_path))
        self._apply_decorations()

    def reveal_file(self, path: str) -> bool:
        abs_path = os.path.abspath(
            path if os.path.isabs(path) else os.path.join(self.root_path, path)
        )
        root = Path(self.root_path).resolve()
        try:
            rel_parts = Path(abs_path).resolve(strict=False).relative_to(root).parts
        except (OSError, ValueError):
            return False
        if not rel_parts:
            return False
        current = self.invisibleRootItem()
        current_path = str(root)
        target_item = None
        for index, part in enumerate(rel_parts):
            child_path = os.path.join(current_path, part)
            child = self._find_child_for_path(current, child_path)
            if child is None and os.path.exists(child_path):
                child = self._append_path_item(current, child_path)
            if child is None:
                return False
            target_item = child
            if index < len(rel_parts) - 1:
                if self._has_placeholder(child):
                    child.takeChildren()
                    self._fill(child, child_path)
                    self._apply_decorations()
                self.expandItem(child)
            current = child
            current_path = child_path
        if target_item is None:
            return False
        self.setCurrentItem(target_item)
        self.scrollToItem(target_item, QAbstractItemView.ScrollHint.PositionAtCenter)
        return True

    def _find_child_for_path(self, parent: QTreeWidgetItem, path: str) -> QTreeWidgetItem | None:
        wanted = _path_key(path)
        for index in range(parent.childCount()):
            child = parent.child(index)
            child_path = child.data(0, Qt.ItemDataRole.UserRole)
            if child_path and _path_key(child_path) == wanted:
                return child
        return None

    def _append_path_item(self, parent: QTreeWidgetItem, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([self._display_name(os.path.basename(path), path)])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        parent.addChild(item)
        if os.path.isdir(path):
            item.addChild(QTreeWidgetItem([""]))
        self._apply_decorations()
        return item

    def _load_git_status(self):
        self._git_by_path = {
            _path_key(ch.abs_path): (ch.code, ch.label)
            for ch in list_file_changes(self.root_path)
        }

    def _refresh_git_status(self):
        self._load_git_status()
        self._update_git_labels()
        self._apply_decorations()

    def _populate(self):
        self.clear()
        self._load_git_status()
        self._fill(self.invisibleRootItem(), self.root_path)
        self._apply_decorations()

    def _update_git_labels(self):
        def walk(item: QTreeWidgetItem):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                item.setText(0, self._display_name(os.path.basename(path), path))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))

    def _on_item_expanded(self, item: QTreeWidgetItem):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isdir(path) and self._has_placeholder(item):
            item.takeChildren()
            self._fill(item, path)
            self._apply_decorations()

    def _display_name(self, name: str, path: str) -> str:
        git = self._git_by_path.get(_path_key(path))
        if git and os.path.isfile(path):
            return f"{git[1]} {name}"
        return name

    def _apply_decorations(self):
        accent = QColor(ACCENT)
        default = QColor(palette()["TEXT"])

        def walk(item: QTreeWidgetItem):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if not path:
                for i in range(item.childCount()):
                    walk(item.child(i))
                return
            git = self._git_by_path.get(_path_key(path))
            if git:
                code, label = git
                item.setForeground(0, QColor(git_status_color(code)))
                item.setToolTip(0, f"{label} — {os.path.relpath(path, self.root_path)}")
            elif _path_key(path) in self._highlighted:
                item.setForeground(0, accent)
                item.setToolTip(0, "")
            else:
                item.setForeground(0, default)
                item.setToolTip(0, "")
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
            item = QTreeWidgetItem([self._display_name(e.name, e.path)])
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
    deleted          = pyqtSignal(str)
    file_open        = pyqtSignal(str)
    file_attach      = pyqtSignal(str)
    settings_changed = pyqtSignal()

    def __init__(
        self,
        store: ConversationStore,
        root_path: str,
        settings: SettingsStore | None = None,
        parent=None,
        *,
        current_model_getter: Callable[[], str] | None = None,
    ):
        super().__init__(parent)
        self._settings = settings or SettingsStore()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabs = QTabWidget()
        self._tabs = tabs

        self._conv = ConversationPanel(store, settings=self._settings)
        self._conv.selected.connect(self.selected)
        self._conv.new_chat.connect(self.new_chat)
        self._conv.renamed.connect(self.renamed)
        self._conv.deleted.connect(self.deleted)

        self._file_tree = FileTree(root_path)
        self._file_tree.file_opened.connect(self.file_open)
        self._file_tree.file_attached.connect(self.file_attach)

        files_wrap = QWidget()
        self._files_wrap = files_wrap
        files_layout = QVBoxLayout(files_wrap)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(0)

        self._files_header = _FilesHeader(root_path)
        self._files_header.refresh_clicked.connect(self._file_tree.refresh)
        files_layout.addWidget(self._files_header)
        files_layout.addWidget(self._file_tree, 1)

        self._git = GitPanel(
            root_path,
            settings=self._settings,
            current_model_getter=current_model_getter,
        )
        self._git.file_open.connect(self.file_open)

        git_wrap = QWidget()
        git_layout = QVBoxLayout(git_wrap)
        git_layout.setContentsMargins(0, 0, 0, 0)
        git_layout.setSpacing(0)

        self._git_header = _FilesHeader(root_path, refresh_tooltip="Refresh git status")
        self._git_header.refresh_clicked.connect(self._git.refresh)
        git_layout.addWidget(self._git_header)
        git_layout.addWidget(self._git, 1)

        tabs.addTab(self._conv, "Chats")
        tabs.addTab(files_wrap, "Files")
        tabs.addTab(git_wrap, "Git")

        root.addWidget(tabs, 1)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(0)

        self._docs_btn = QPushButton("?")
        self._docs_btn.setToolTip("Documentation")
        self._docs_btn.setFixedHeight(34)
        self._docs_btn.clicked.connect(self.open_docs)
        footer_layout.addWidget(self._docs_btn)

        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setToolTip("Settings (⌘,)")
        self._settings_btn.setFixedHeight(34)
        self._settings_btn.clicked.connect(self.open_settings)
        footer_layout.addWidget(self._settings_btn)
        root.addWidget(footer)

        self._apply_styles()

    def _apply_styles(self):
        apply_flat_tab_style(self._tabs, "sidebarTabs")
        self._files_header.apply_appearance()
        self._git_header.apply_appearance()
        self._docs_btn.setStyleSheet(sidebar_settings_button_style())
        self._settings_btn.setStyleSheet(sidebar_settings_button_style())

    def apply_appearance(self):
        self._apply_styles()
        self._conv.apply_appearance()
        self._file_tree._apply_tree_style()
        self._file_tree._refresh_git_status()
        self._git.apply_appearance()

    def open_settings(self):
        if SettingsDialog(self._settings, self).exec():
            self.settings_changed.emit()

    def open_docs(self):
        DocsDialog(self).exec()

    def set_workspace(self, path: str):
        self._file_tree.set_root(path)
        self._git.set_repo_path(path)
        self._files_header.set_path(path)
        self._git_header.set_path(path)

    def refresh(self):
        self._conv.refresh()

    def select_conversation(self, conv_id: str):
        self._conv.select_conversation(conv_id)

    def clear_conversation_selection(self):
        self._conv.clear_selection()

    def mark_file_touched(self, path: str):
        self._file_tree.mark_touched(path)
        self._file_tree._refresh_git_status()
        self._git.refresh()
        QTimer.singleShot(250, self._file_tree.refresh)
        QTimer.singleShot(250, self._git.refresh)

    def reveal_file(self, path: str) -> bool:
        self._tabs.setCurrentWidget(self._files_wrap)
        return self._file_tree.reveal_file(path)
