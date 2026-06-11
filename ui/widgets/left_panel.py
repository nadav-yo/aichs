import os
import shutil
import threading
from pathlib import Path
from typing import Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QPushButton, QHBoxLayout, QLabel, QMenu, QSizePolicy, QStyleFactory,
    QAbstractItemView, QInputDialog, QMessageBox, QFrame, QLineEdit,
)
from PyQt6.QtCore import pyqtSignal, Qt, QFileSystemWatcher, QThread, QTimer, QMimeData, QSize, QRectF
from PyQt6.QtGui import (
    QColor, QAction, QBrush, QFont, QGuiApplication, QIcon, QKeySequence,
    QPainter, QPen, QPixmap, QShortcut,
)

from config import IGNORED, MAX_TREE_ENTRIES_PER_DIR
from services.chat_drag import AICHS_FILE_DROP_MIME, file_drop_payload, file_drop_text
from services.file_tree_snapshot import (
    FileTreeSnapshot,
    build_directory_snapshot,
    build_file_tree_snapshot,
)
from services.file_search import clear_workspace_file_cache
from services.git_snapshot import GitSnapshot
from services.git_status import discard_files
from storage.repository import ConversationStore
from storage.settings import SettingsStore
from ui.theme import (
    palette, ACCENT, git_status_color, icon_button_style, files_header_style,
    file_tree_sidebar_style, mono_font_pt, mono_font,
    meta_font_pt, chat_font_pt, current_theme, rail_button_style,
    sidebar_footer_button_style,
)
from ui.widgets.conversation_panel import ConversationPanel
from ui.widgets.git_panel import GitPanel
from ui.widgets.docs_dialog import DocsDialog
from ui.widgets.git_status_icon import git_status_description, paint_git_status_badge
from ui.widgets.settings_dialog import SettingsDialog


_FILE_ICON_TYPES = {
    ".py": ("PY", "#3776ab"),
    ".pyw": ("PY", "#3776ab"),
    ".js": ("JS", "#f7df1e"),
    ".jsx": ("JSX", "#61dafb"),
    ".ts": ("TS", "#3178c6"),
    ".tsx": ("TSX", "#3178c6"),
    ".json": ("{}", "#f59e0b"),
    ".jsonc": ("{}", "#f59e0b"),
    ".md": ("MD", "#60a5fa"),
    ".markdown": ("MD", "#60a5fa"),
    ".yaml": ("YML", "#cb171e"),
    ".yml": ("YML", "#cb171e"),
    ".toml": ("TOML", "#9c4221"),
    ".ini": ("INI", "#8b949e"),
    ".cfg": ("CFG", "#8b949e"),
    ".txt": ("TXT", "#94a3b8"),
    ".css": ("CSS", "#2965f1"),
    ".scss": ("SCSS", "#cf649a"),
    ".html": ("HTML", "#e34f26"),
    ".htm": ("HTML", "#e34f26"),
    ".xml": ("XML", "#f97316"),
    ".go": ("GO", "#00add8"),
    ".rs": ("RS", "#dea584"),
    ".java": ("JV", "#b07219"),
    ".c": ("C", "#5555aa"),
    ".h": ("H", "#5555aa"),
    ".cc": ("C++", "#f34b7d"),
    ".cpp": ("C++", "#f34b7d"),
    ".hpp": ("H++", "#f34b7d"),
    ".cs": ("CS", "#178600"),
    ".php": ("PHP", "#777bb4"),
    ".rb": ("RB", "#cc342d"),
    ".sh": ("SH", "#89e051"),
    ".bash": ("SH", "#89e051"),
    ".ps1": ("PS", "#5391fe"),
    ".bat": ("BAT", "#6b7280"),
    ".sql": ("SQL", "#336791"),
    ".png": ("IMG", "#a855f7"),
    ".jpg": ("IMG", "#a855f7"),
    ".jpeg": ("IMG", "#a855f7"),
    ".gif": ("IMG", "#a855f7"),
    ".webp": ("IMG", "#a855f7"),
    ".svg": ("SVG", "#ffb13b"),
    ".pdf": ("PDF", "#ef4444"),
    ".zip": ("ZIP", "#64748b"),
    ".tar": ("TAR", "#64748b"),
    ".gz": ("GZ", "#64748b"),
    ".docx": ("DOC", "#2b579a"),
    ".xlsx": ("XLS", "#217346"),
    ".pptx": ("PPT", "#d24726"),
}

_FILE_NAME_ICON_TYPES = {
    "dockerfile": ("DOCK", "#2496ed"),
    "makefile": ("MK", "#8b949e"),
    "cmakelists.txt": ("CMAKE", "#064f8c"),
    "license": ("LIC", "#94a3b8"),
}

_ICON_CACHE: dict[tuple, QIcon] = {}
_DISPLAY_NAME_ROLE = Qt.ItemDataRole.UserRole.value + 1
_LOAD_GENERATION_ROLE = Qt.ItemDataRole.UserRole.value + 2


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _ensure_path_in_workspace(root_path: str, path: Path) -> None:
    root = Path(root_path).resolve()
    candidate = path.resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path must stay inside the workspace.")


def _clean_file_tree_leaf_name(name: str) -> str:
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


def _create_workspace_file(root_path: str, folder: str, name: str) -> Path:
    directory = Path(folder)
    if not directory.is_dir():
        raise ValueError("Choose a folder in the workspace.")
    _ensure_path_in_workspace(root_path, directory)
    target = directory / _clean_file_tree_leaf_name(name)
    _ensure_path_in_workspace(root_path, target)
    if target.exists():
        raise FileExistsError(f"File already exists: {target.name}")
    target.touch()
    return target


def _create_workspace_folder(root_path: str, folder: str, name: str) -> Path:
    directory = Path(folder)
    if not directory.is_dir():
        raise ValueError("Choose a folder in the workspace.")
    _ensure_path_in_workspace(root_path, directory)
    target = directory / _clean_file_tree_leaf_name(name)
    _ensure_path_in_workspace(root_path, target)
    if target.exists():
        raise FileExistsError(f"Folder already exists: {target.name}")
    target.mkdir()
    return target


def _rename_workspace_path(root_path: str, path: str, name: str) -> Path:
    source = Path(path)
    if not source.is_file() and not source.is_dir():
        raise ValueError("Choose a file or folder in the workspace.")
    _ensure_path_in_workspace(root_path, source)
    if source.resolve(strict=False) == Path(root_path).resolve():
        raise ValueError("Cannot rename the workspace folder.")
    target = source.with_name(_clean_file_tree_leaf_name(name))
    _ensure_path_in_workspace(root_path, target)
    if target == source:
        return source
    if target.exists():
        raise FileExistsError(f"Path already exists: {target.name}")
    source.rename(target)
    return target


def _delete_workspace_path(root_path: str, path: str) -> None:
    target = Path(path)
    _ensure_path_in_workspace(root_path, target)
    if target.resolve(strict=False) == Path(root_path).resolve():
        raise ValueError("Cannot delete the workspace folder.")
    if target.is_file():
        target.unlink()
        return
    if target.is_dir():
        shutil.rmtree(target)
        return
    raise FileNotFoundError(f"Path not found: {target}")


def _is_visible_tree_entry(name: str, is_dir: bool) -> bool:
    if name in IGNORED:
        return False
    return is_dir or not name.startswith(".")


def _file_icon_type(name: str) -> tuple[str, str]:
    lowered = name.lower()
    if lowered in _FILE_NAME_ICON_TYPES:
        return _FILE_NAME_ICON_TYPES[lowered]
    return _FILE_ICON_TYPES.get(Path(name).suffix.lower(), ("", "#7f8a99"))


def _tree_item_icon(
    path: str,
    *,
    dirty: bool = False,
    dirty_descendant: bool = False,
    git_code: str = "",
    git_label: str = "",
) -> QIcon:
    theme = current_theme()
    is_dir = os.path.isdir(path)
    name = os.path.basename(path)
    label, color = ("folder", "#d6a84f") if is_dir else _file_icon_type(name)
    key = (
        theme,
        "dir" if is_dir else label,
        color,
        dirty,
        dirty_descendant,
        git_code,
        git_label,
    )
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if is_dir:
        _paint_folder_icon(painter, color, dirty_descendant)
    else:
        _paint_file_icon(painter, label, color, dirty, git_code, git_label)
    painter.end()
    icon = QIcon(pixmap)
    _ICON_CACHE[key] = icon
    return icon


def _paint_folder_icon(painter: QPainter, color: str, dirty_descendant: bool):
    base = QColor(color)
    shade = QColor("#b9852c") if current_theme() == "light" else QColor("#8f682c")
    painter.setPen(QPen(shade, 1))
    painter.setBrush(QBrush(base))
    painter.drawRoundedRect(QRectF(2, 5, 14, 10), 2, 2)
    painter.drawRoundedRect(QRectF(3, 3, 6, 4), 1.5, 1.5)
    if dirty_descendant:
        painter.setPen(QPen(QColor("#10213f"), 1))
        painter.setBrush(QBrush(QColor(ACCENT)))
        painter.drawEllipse(QRectF(12, 1.5, 5, 5))


def _paint_file_icon(
    painter: QPainter,
    label: str,
    color: str,
    dirty: bool,
    git_code: str = "",
    git_label: str = "",
):
    p = palette()
    painter.setPen(QPen(QColor(p["BORDER"]), 1))
    painter.setBrush(QBrush(QColor(p["BG3"])))
    painter.drawRoundedRect(QRectF(3, 2, 12, 14), 2, 2)
    painter.setPen(QPen(QColor(color), 1))
    painter.drawLine(6, 5, 12, 5)
    painter.drawLine(6, 8, 12, 8)
    badge = QColor(color)
    if current_theme() != "light":
        badge.setAlpha(220)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(badge))
    painter.drawRoundedRect(QRectF(2, 9, 14, 7), 2, 2)
    if label:
        font = QFont()
        font.setBold(True)
        font.setPixelSize(5 if len(label) > 3 else 6)
        painter.setFont(font)
        painter.setPen(QColor("#111827") if label == "JS" else QColor("#ffffff"))
        painter.drawText(QRectF(2, 9, 14, 7), Qt.AlignmentFlag.AlignCenter, label[:5])
    if dirty:
        painter.setPen(QPen(QColor("#10213f"), 1))
        painter.setBrush(QBrush(QColor(ACCENT)))
        painter.drawEllipse(QRectF(12, 1.5, 5, 5))
    if git_code or git_label:
        paint_git_status_badge(
            painter,
            git_code,
            git_label,
            QRectF(0.5, 10.5, 6.5, 6.5),
        )


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
    filter_changed = pyqtSignal(str)

    def __init__(
        self,
        path: str,
        refresh_tooltip: str = "Refresh file tree",
        parent=None,
        *,
        filter_enabled: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("filesHeader")
        self._filter_enabled = filter_enabled

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 6, 6)
        row.setSpacing(4)

        self._path = None
        self._filter = None
        if filter_enabled:
            self._filter = QLineEdit()
            self._filter.setObjectName("filesFilter")
            self._filter.setPlaceholderText("Filter files")
            self._filter.setClearButtonEnabled(True)
            self._filter.setToolTip(path)
            self._filter.textChanged.connect(self.filter_changed.emit)
            row.addWidget(self._filter, 1)
        else:
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
        if self._path is not None:
            self._path.set_path(path)
        if self._filter is not None:
            self._filter.setToolTip(path)
            self._filter.setPlaceholderText("Filter files")
            self._filter.blockSignals(True)
            self._filter.clear()
            self._filter.blockSignals(False)

    def apply_appearance(self):
        self.setStyleSheet(files_header_style())
        self._refresh.setStyleSheet(icon_button_style())


class _FileTreeRefreshThread(QThread):
    done = pyqtSignal(int, object)

    def __init__(
        self,
        generation: int,
        root_path: str,
        filter_text: str,
        parent=None,
        *,
        git_snapshot: GitSnapshot | None = None,
        git_changes=None,
        load_git_status: bool = True,
    ):
        super().__init__(parent)
        self._generation = generation
        self._root_path = root_path
        self._filter_text = filter_text
        self._git_snapshot = git_snapshot
        self._git_changes = git_changes
        self._load_git_status = load_git_status
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def run(self):
        snapshot = build_file_tree_snapshot(
            self._root_path,
            filter_text=self._filter_text,
            git_snapshot=self._git_snapshot,
            git_changes=self._git_changes,
            load_git_status=self._load_git_status,
            cancelled=self._cancelled.is_set,
        )
        if not self._cancelled.is_set():
            self.done.emit(self._generation, snapshot)


class _FileTreeChildrenThread(QThread):
    done = pyqtSignal(int, str, object)

    def __init__(self, generation: int, path: str, parent=None):
        super().__init__(parent)
        self._generation = generation
        self._path = path

    def run(self):
        self.done.emit(self._generation, self._path, build_directory_snapshot(self._path))


class _FileTreeActionThread(QThread):
    done = pyqtSignal(str, str, str)

    def __init__(
        self,
        root_path: str,
        action: str,
        path: str,
        parent=None,
        *,
        name: str = "",
        rel_path: str = "",
        staged: bool = False,
    ):
        super().__init__(parent)
        self._root_path = root_path
        self._action = action
        self._path = path
        self._name = name
        self._rel_path = rel_path
        self._staged = staged

    def run(self):
        try:
            if self._action == "create_file":
                result_path = _create_workspace_file(self._root_path, self._path, self._name)
            elif self._action == "create_folder":
                result_path = _create_workspace_folder(self._root_path, self._path, self._name)
            elif self._action == "rename":
                result_path = _rename_workspace_path(self._root_path, self._path, self._name)
            elif self._action == "delete":
                _delete_workspace_path(self._root_path, self._path)
                result_path = Path(self._path)
            elif self._action == "discard":
                result = discard_files(self._root_path, [self._rel_path], staged=self._staged)
                if not result.ok:
                    detail = "\n".join(
                        part for part in (result.stdout, result.stderr) if part
                    ).strip()
                    raise RuntimeError(detail or "Discard failed.")
                result_path = Path(self._path)
            else:
                raise ValueError(f"unsupported file tree action: {self._action}")
        except Exception as exc:
            self.done.emit(self._action, "", str(exc))
            return
        self.done.emit(self._action, str(result_path), "")


class FileTree(QTreeWidget):
    file_opened = pyqtSignal(str)
    file_attached = pyqtSignal(str)

    def __init__(self, root_path: str, parent=None, *, defer_git_status: bool = False):
        super().__init__(parent)
        self.setObjectName("fileTree")
        self.root_path = root_path
        self._highlighted: set[str] = set()
        self._dirty_paths: set[str] = set()
        self._dirty_dir_paths: set[str] = set()
        self._git_by_path: dict[str, tuple[str, str]] = {}
        self._filter_text = ""
        self._git_timer_started = False
        self._shutting_down = False
        self._refresh_generation = 0
        self._children_generation = 0
        self._pending_expanded_path_keys: set[str] = set()
        self._pending_current_path = ""
        self._refresh_threads: list[_FileTreeRefreshThread] = []
        self._children_threads: list[_FileTreeChildrenThread] = []
        self._action_threads: list[_FileTreeActionThread] = []
        self.setHeaderHidden(True)
        self.setAnimated(False)
        self.setAllColumnsShowFocus(False)
        self.setIconSize(QSize(18, 18))
        self.setStyle(QStyleFactory.create("Fusion"))
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._apply_tree_style()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._shortcut_handles: list[QShortcut] = []
        self._setup_shortcuts()
        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.timeout.connect(self.refresh)
        self._watcher = QFileSystemWatcher([root_path])
        self._watcher.directoryChanged.connect(lambda _: self._schedule_refresh())
        self.itemExpanded.connect(self._on_item_expanded)
        self._populate(load_git_status=False, preserve_state=False)
        self.expandToDepth(1)
        self.itemDoubleClicked.connect(self._on_double_click)
        self._git_timer = QTimer(self)
        self._git_timer.timeout.connect(self._refresh_git_status)
        if not defer_git_status:
            self.start_git_timer()

    def _apply_tree_style(self):
        self.setFont(mono_font(mono_font_pt()))
        self.setStyleSheet(file_tree_sidebar_style())

    def _setup_shortcuts(self):
        self._add_shortcut(QKeySequence(Qt.Key.Key_Return), self._open_selected)
        self._add_shortcut(QKeySequence(Qt.Key.Key_Enter), self._open_selected)
        self._add_shortcut(QKeySequence("F2"), self._rename_selected)
        self._add_shortcut(QKeySequence("Delete"), self._delete_selected)
        self._add_shortcut(QKeySequence("F5"), self.refresh)
        self._add_shortcut(QKeySequence("Ctrl+Alt+N"), self._new_file_selected)
        self._add_shortcut(QKeySequence("Meta+Alt+N"), self._new_file_selected)
        self._add_shortcut(QKeySequence("Ctrl+Shift+N"), self._new_folder_selected)
        self._add_shortcut(QKeySequence("Meta+Shift+N"), self._new_folder_selected)
        self._add_shortcut(QKeySequence("Ctrl+C"), self._copy_selected_relative_path)
        self._add_shortcut(QKeySequence("Ctrl+Shift+C"), self._copy_selected_absolute_path)
        self._add_shortcut(QKeySequence("Shift+F10"), self._open_selected_context_menu)

    def _add_shortcut(self, sequence: QKeySequence, callback):
        shortcut = QShortcut(sequence, self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self._shortcut_handles.append(shortcut)

    def set_root(self, path: str):
        self.root_path = path
        for d in self._watcher.directories():
            self._watcher.removePath(d)
        self._watcher.addPath(path)
        self._highlighted.clear()
        self._dirty_paths.clear()
        self._dirty_dir_paths.clear()
        self._filter_text = ""
        self._git_by_path.clear()
        self.refresh()
        self.start_git_timer()

    def set_filter_text(self, text: str):
        value = " ".join(str(text or "").split()).casefold()
        if self._filter_text == value:
            return
        self._filter_text = value
        self.refresh()

    def set_file_dirty(self, path: str, dirty: bool):
        abs_path = os.path.abspath(
            path if os.path.isabs(path) else os.path.join(self.root_path, path)
        )
        try:
            Path(abs_path).resolve(strict=False).relative_to(Path(self.root_path).resolve())
        except (OSError, ValueError):
            return
        key = _path_key(abs_path)
        if dirty:
            self._dirty_paths.add(key)
        else:
            self._dirty_paths.discard(key)
        self._rebuild_dirty_dirs()
        self._update_git_labels()
        self._apply_decorations()

    def _on_double_click(self, item: QTreeWidgetItem, _column: int):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            self.file_opened.emit(path)

    def _current_path(self) -> str:
        item = self.currentItem()
        path = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        return str(path or "")

    def _selected_new_item_folder(self) -> str:
        path = self._current_path()
        if path and os.path.isdir(path):
            return path
        if path and os.path.isfile(path):
            return str(Path(path).parent)
        return self.root_path

    def _open_selected(self):
        path = self._current_path()
        if os.path.isfile(path):
            self.file_opened.emit(path)
        elif os.path.isdir(path) and self.currentItem() is not None:
            self.currentItem().setExpanded(not self.currentItem().isExpanded())

    def _new_file_selected(self):
        self._new_file_dialog(self._selected_new_item_folder())

    def _new_folder_selected(self):
        self._new_folder_dialog(self._selected_new_item_folder())

    def _rename_selected(self):
        path = self._current_path()
        if path:
            self._rename_path_dialog(path)

    def _delete_selected(self):
        path = self._current_path()
        if path:
            self._delete_path_dialog(path)

    def _copy_selected_relative_path(self):
        path = self._current_path()
        rel_path = self._repo_relative_path(path) if path else ""
        if rel_path:
            QGuiApplication.clipboard().setText(rel_path)

    def _copy_selected_absolute_path(self):
        path = self._current_path()
        if path:
            QGuiApplication.clipboard().setText(str(Path(path).resolve(strict=False)))

    def _open_selected_context_menu(self):
        item = self.currentItem()
        if item is None:
            self._context_menu(self.viewport().rect().center())
            return
        self._context_menu(self.visualItemRect(item).center())

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
            new_file.setShortcut(QKeySequence("Ctrl+Alt+N"))
            new_file.triggered.connect(lambda: self._new_file_dialog(path))
            menu.addAction(new_file)
            new_folder = QAction("New Folder...", self)
            new_folder.setShortcut(QKeySequence("Ctrl+Shift+N"))
            new_folder.triggered.connect(lambda: self._new_folder_dialog(path))
            menu.addAction(new_folder)
            rename_folder = QAction("Rename...", self)
            rename_folder.setShortcut(QKeySequence("F2"))
            rename_folder.triggered.connect(lambda: self._rename_path_dialog(path))
            menu.addAction(rename_folder)
            menu.addSeparator()
            delete_folder = QAction("Delete...", self)
            delete_folder.setShortcut(QKeySequence("Delete"))
            delete_folder.triggered.connect(lambda: self._delete_path_dialog(path))
            menu.addAction(delete_folder)
            menu.addSeparator()
        elif path and os.path.isfile(path):
            attach = QAction("Attach to message", self)
            attach.triggered.connect(lambda: self.file_attached.emit(path))
            menu.addAction(attach)
            open_file = QAction("Open", self)
            open_file.setShortcut(QKeySequence(Qt.Key.Key_Return))
            open_file.triggered.connect(lambda: self.file_opened.emit(path))
            menu.addAction(open_file)
            rename = QAction("Rename...", self)
            rename.setShortcut(QKeySequence("F2"))
            rename.triggered.connect(lambda: self._rename_path_dialog(path))
            menu.addAction(rename)
            delete_file = QAction("Delete...", self)
            delete_file.setShortcut(QKeySequence("Delete"))
            delete_file.triggered.connect(lambda: self._delete_path_dialog(path))
            menu.addAction(delete_file)
            if self._is_discardable_modified_file(path):
                discard = QAction("Discard changes...", self)
                discard.triggered.connect(lambda: self._discard_file_dialog(path))
                menu.addAction(discard)
            menu.addSeparator()
        refresh = QAction("Refresh", self)
        refresh.setShortcut(QKeySequence("F5"))
        refresh.triggered.connect(self.refresh)
        menu.addAction(refresh)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _new_file_dialog(self, folder: str):
        name, ok = QInputDialog.getText(self, "New file", "File name:")
        if not ok:
            return
        self._start_action("create_file", folder, name=name)

    def _new_folder_dialog(self, folder: str):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok:
            return
        self._start_action("create_folder", folder, name=name)

    def _rename_path_dialog(self, path: str):
        current = os.path.basename(path)
        kind = "folder" if os.path.isdir(path) else "file"
        name, ok = QInputDialog.getText(self, f"Rename {kind}", "New name:", text=current)
        if not ok or name.strip() == current:
            return
        self._start_action("rename", path, name=name)

    def _rename_file_dialog(self, path: str):
        self._rename_path_dialog(path)

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
        self._start_action("discard", path, rel_path=rel_path, staged=staged)

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
        self._start_action("delete", path)

    def _start_action(
        self,
        action: str,
        path: str,
        *,
        name: str = "",
        rel_path: str = "",
        staged: bool = False,
    ) -> None:
        if self._shutting_down:
            return
        thread = _FileTreeActionThread(
            self.root_path,
            action,
            path,
            None,
            name=name,
            rel_path=rel_path,
            staged=staged,
        )
        self._action_threads.append(thread)
        thread.done.connect(self._on_action_done)
        thread.finished.connect(lambda t=thread: self._release_action_thread(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_action_done(self, action: str, path: str, error: str) -> None:
        if error:
            QMessageBox.warning(self, self._action_error_title(action, path), error)
            return
        clear_workspace_file_cache(self.root_path)
        self.refresh()
        if action in {"create_file", "create_folder", "rename"} and path:
            self.mark_touched(path)
        if action == "create_file" and path:
            self.file_opened.emit(path)

    def _release_action_thread(self, thread: _FileTreeActionThread):
        if thread in self._action_threads:
            self._action_threads.remove(thread)

    @staticmethod
    def _action_error_title(action: str, path: str) -> str:
        if action == "create_file":
            return "New file failed"
        if action == "create_folder":
            return "New folder failed"
        if action == "rename":
            return "Rename failed"
        if action == "delete":
            kind = "folder" if Path(path).is_dir() else "file"
            return f"Delete {kind} failed"
        if action == "discard":
            return "Discard failed"
        return "File action failed"

    def create_file(self, folder: str, name: str) -> Path:
        path = _create_workspace_file(self.root_path, folder, name)
        clear_workspace_file_cache(self.root_path)
        return path

    def create_folder(self, folder: str, name: str) -> Path:
        path = _create_workspace_folder(self.root_path, folder, name)
        clear_workspace_file_cache(self.root_path)
        return path

    def rename_file(self, path: str, name: str) -> Path:
        source = Path(path)
        if not source.is_file():
            raise ValueError("Choose a file in the workspace.")
        return self.rename_path(path, name)

    def rename_path(self, path: str, name: str) -> Path:
        renamed = _rename_workspace_path(self.root_path, path, name)
        clear_workspace_file_cache(self.root_path)
        return renamed

    def delete_path(self, path: str) -> None:
        _delete_workspace_path(self.root_path, path)
        clear_workspace_file_cache(self.root_path)

    def _ensure_in_workspace(self, path: Path) -> None:
        _ensure_path_in_workspace(self.root_path, path)

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
        return _clean_file_tree_leaf_name(name)

    def refresh(self):
        if self._shutting_down:
            return
        clear_workspace_file_cache(self.root_path)
        self._request_refresh()

    def _schedule_refresh(self, delay_ms: int = 250):
        if self._shutting_down:
            return
        self._refresh_debounce.start(delay_ms)

    def refresh_from_changes(self, changes):
        self._request_refresh(changes=changes)
        self.start_git_timer()
        if not self._filter_text:
            self.expandToDepth(1)

    def refresh_from_git_snapshot(self, snapshot: GitSnapshot):
        self._request_refresh(git_snapshot=snapshot)
        self.start_git_timer()
        if not self._filter_text:
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
        if self._filter_text:
            child = self._find_child_for_path(self.invisibleRootItem(), abs_path)
            if child is None:
                return False
            self.setCurrentItem(child)
            self.scrollToItem(child, QAbstractItemView.ScrollHint.PositionAtCenter)
            return True
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
                was_blocked = self.blockSignals(True)
                try:
                    self.expandItem(child)
                finally:
                    self.blockSignals(was_blocked)
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
        self._apply_item_icon(item, path)
        parent.addChild(item)
        if os.path.isdir(path):
            item.addChild(QTreeWidgetItem([""]))
        self._apply_decorations()
        return item

    def _load_git_status(self, changes=None):
        self._git_by_path = {
            _path_key(ch.abs_path): (ch.code, ch.label)
            for ch in changes
        } if changes is not None else {}

    def _refresh_git_status(self, changes=None):
        if changes is None:
            self.refresh()
            return
        self._load_git_status(changes)
        self._update_git_labels()
        self._apply_decorations()

    def _populate(
        self,
        *,
        git_snapshot: GitSnapshot | None = None,
        changes=None,
        load_git_status: bool = True,
        preserve_state: bool = True,
    ):
        expanded_paths = self._expanded_path_keys() if preserve_state else set()
        current_path = self._current_path() if preserve_state else ""
        snapshot = build_file_tree_snapshot(
            self.root_path,
            filter_text=self._filter_text,
            git_snapshot=git_snapshot,
            git_changes=changes,
            load_git_status=load_git_status,
        )
        self._apply_snapshot(snapshot, expanded_paths=expanded_paths, current_path=current_path)

    def _request_refresh(
        self,
        *,
        git_snapshot: GitSnapshot | None = None,
        changes=None,
        load_git_status: bool = True,
    ):
        if self._shutting_down:
            return
        expanded_paths = self._expanded_path_keys()
        current_path = self._current_path()
        self._refresh_generation += 1
        for old_thread in list(self._refresh_threads):
            old_thread.cancel()
        thread = _FileTreeRefreshThread(
            self._refresh_generation,
            self.root_path,
            self._filter_text,
            None,
            git_snapshot=git_snapshot,
            git_changes=changes,
            load_git_status=load_git_status,
        )
        self._refresh_threads.append(thread)
        thread.done.connect(
            lambda generation, snapshot, expanded_paths=expanded_paths, current_path=current_path: self._apply_async_snapshot(
                generation,
                snapshot,
                expanded_paths,
                current_path,
            )
        )
        thread.finished.connect(lambda t=thread: self._release_refresh_thread(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _apply_async_snapshot(
        self,
        generation: int,
        snapshot: FileTreeSnapshot,
        expanded_paths: set[str],
        current_path: str,
    ):
        if generation != self._refresh_generation:
            return
        self._apply_snapshot(snapshot, expanded_paths=expanded_paths, current_path=current_path)

    def _apply_snapshot(
        self,
        snapshot: FileTreeSnapshot,
        *,
        expanded_paths: set[str] | None = None,
        current_path: str = "",
    ):
        expanded_paths = expanded_paths or set()
        self._pending_expanded_path_keys = set() if snapshot.filter_text else set(expanded_paths)
        self._pending_current_path = current_path
        self.clear()
        self._git_by_path = {
            _path_key(status.abs_path): (status.code, status.label)
            for status in snapshot.git_status
        }
        self._fill_from_snapshot(self.invisibleRootItem(), snapshot)
        if not snapshot.filter_text:
            self._restore_expanded_paths(expanded_paths)
        if current_path:
            item = self._find_item_for_path(current_path)
            if item is not None:
                self.setCurrentItem(item)
                self._pending_current_path = ""
        self._apply_decorations()

    def _release_refresh_thread(self, thread: _FileTreeRefreshThread):
        if thread in self._refresh_threads:
            self._refresh_threads.remove(thread)

    def _expanded_path_keys(self) -> set[str]:
        expanded: set[str] = set()

        def walk(item: QTreeWidgetItem):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path and item.isExpanded():
                expanded.add(_path_key(path))
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))
        return expanded

    def _restore_expanded_paths(self, expanded_paths: set[str]):
        if not expanded_paths:
            return

        def walk(item: QTreeWidgetItem):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path and _path_key(path) in expanded_paths:
                item.setExpanded(True)
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))

    def _find_item_for_path(self, path: str) -> QTreeWidgetItem | None:
        wanted = _path_key(path)

        def walk(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            item_path = item.data(0, Qt.ItemDataRole.UserRole)
            if item_path and _path_key(item_path) == wanted:
                return item
            for index in range(item.childCount()):
                found = walk(item.child(index))
                if found is not None:
                    return found
            return None

        for index in range(self.topLevelItemCount()):
            found = walk(self.topLevelItem(index))
            if found is not None:
                return found
        return None

    def start_git_timer(self):
        if self._git_timer_started:
            return
        self._git_timer_started = True
        self._git_timer.start(5000)

    def shutdown(self):
        self._shutting_down = True
        self._git_timer.stop()
        self._refresh_debounce.stop()
        try:
            self._watcher.directoryChanged.disconnect()
        except TypeError:
            pass
        for directory in self._watcher.directories():
            self._watcher.removePath(directory)
        for thread in list(self._refresh_threads):
            thread.cancel()
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
        for thread in list(self._children_threads):
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
        for thread in list(self._action_threads):
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
        self._refresh_threads.clear()
        self._children_threads.clear()
        self._action_threads.clear()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _update_git_labels(self):
        def walk(item: QTreeWidgetItem):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                item.setText(0, self._display_name(self._item_display_name(item, path), path))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))

    def _on_item_expanded(self, item: QTreeWidgetItem):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.isdir(path) and self._has_placeholder(item):
            self._request_children(item, path)

    def _request_children(self, item: QTreeWidgetItem, path: str):
        if self._shutting_down:
            return
        self._children_generation += 1
        generation = self._children_generation
        item.setData(0, _LOAD_GENERATION_ROLE, generation)
        thread = _FileTreeChildrenThread(generation, path, None)
        self._children_threads.append(thread)
        thread.done.connect(self._apply_children_snapshot)
        thread.finished.connect(lambda t=thread: self._release_children_thread(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _apply_children_snapshot(self, generation: int, path: str, snapshot: FileTreeSnapshot):
        item = self._find_item_for_path(path)
        if item is None:
            return
        if item.data(0, _LOAD_GENERATION_ROLE) != generation:
            return
        item.takeChildren()
        self._fill_from_snapshot(item, snapshot)
        item.setExpanded(True)
        if self._pending_expanded_path_keys:
            self._restore_expanded_paths(self._pending_expanded_path_keys)
        if self._pending_current_path:
            current = self._find_item_for_path(self._pending_current_path)
            if current is not None:
                self.setCurrentItem(current)
                self._pending_current_path = ""
        self._apply_decorations()

    def _release_children_thread(self, thread: _FileTreeChildrenThread):
        if thread in self._children_threads:
            self._children_threads.remove(thread)

    def _display_name(self, name: str, path: str) -> str:
        parts = []
        if os.path.isfile(path) and _path_key(path) in self._dirty_paths:
            parts.append("*")
        parts.append(name)
        return " ".join(parts)

    def _apply_decorations(self):
        accent = QColor(ACCENT)
        default = QColor(palette()["TEXT"])

        def walk(item: QTreeWidgetItem):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if not path:
                for i in range(item.childCount()):
                    walk(item.child(i))
                return
            item.setText(0, self._display_name(self._item_display_name(item, path), path))
            self._apply_item_icon(item, path)
            font = item.font(0)
            font.setBold(os.path.isdir(path))
            item.setFont(0, font)
            git = self._git_by_path.get(_path_key(path))
            if git:
                code, label = git
                item.setForeground(0, QColor(git_status_color(code)))
                item.setToolTip(0, self._tooltip(path, git_code=code, git_label=label))
            elif _path_key(path) in self._highlighted:
                item.setForeground(0, accent)
                item.setToolTip(0, self._tooltip(path))
            else:
                item.setForeground(0, default)
                item.setToolTip(0, self._tooltip(path))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))

    def _fill_from_snapshot(self, parent: QTreeWidgetItem, snapshot: FileTreeSnapshot):
        for entry in snapshot.entries:
            display_name = entry.display_name or entry.name
            item = QTreeWidgetItem([self._display_name(display_name, entry.abs_path)])
            item.setData(0, Qt.ItemDataRole.UserRole, entry.abs_path)
            item.setData(0, _DISPLAY_NAME_ROLE, display_name)
            self._apply_item_icon(item, entry.abs_path)
            parent.addChild(item)
            if entry.is_dir:
                item.addChild(QTreeWidgetItem([""]))
        if snapshot.omitted > 0:
            text = "... more matches" if snapshot.filter_text else f"… {snapshot.omitted} more"
            more = QTreeWidgetItem([text])
            more.setDisabled(True)
            parent.addChild(more)

    @staticmethod
    def _has_placeholder(item: QTreeWidgetItem) -> bool:
        return (
            item.childCount() == 1
            and not item.child(0).data(0, Qt.ItemDataRole.UserRole)
            and not item.child(0).text(0)
        )

    def _apply_item_icon(self, item: QTreeWidgetItem, path: str):
        key = _path_key(path)
        git = self._git_by_path.get(key) if os.path.isfile(path) else None
        git_code, git_label = git or ("", "")
        item.setIcon(
            0,
            _tree_item_icon(
                path,
                dirty=key in self._dirty_paths,
                dirty_descendant=key in self._dirty_dir_paths,
                git_code=git_code,
                git_label=git_label,
            ),
        )

    @staticmethod
    def _item_display_name(item: QTreeWidgetItem, path: str) -> str:
        return str(item.data(0, _DISPLAY_NAME_ROLE) or os.path.basename(path))

    def _tooltip(self, path: str, *, git_code: str = "", git_label: str = "") -> str:
        key = _path_key(path)
        notes = []
        if os.path.isfile(path) and key in self._dirty_paths:
            notes.append("Unsaved editor changes")
        if os.path.isdir(path) and key in self._dirty_dir_paths:
            notes.append("Contains unsaved editor changes")
        if git_label:
            notes.append(f"Git: {git_status_description(git_code, git_label)}")
        if not notes:
            return ""
        rel = os.path.relpath(path, self.root_path)
        return f"{' - '.join(notes)} - {rel}"

    def _rebuild_dirty_dirs(self):
        root = Path(self.root_path).resolve()
        dirs: set[str] = set()
        for dirty in self._dirty_paths:
            path = Path(dirty)
            try:
                path.resolve(strict=False).relative_to(root)
            except (OSError, ValueError):
                continue
            for parent in path.parents:
                if parent == root or root in parent.parents:
                    dirs.add(_path_key(str(parent)))
                if parent == root:
                    break
        self._dirty_dir_paths = dirs


class LeftPanel(QWidget):
    selected         = pyqtSignal(str)
    new_chat         = pyqtSignal()
    renamed          = pyqtSignal(str, str)
    deleted          = pyqtSignal(str)
    file_open        = pyqtSignal(str)
    git_file_open    = pyqtSignal(str)
    git_help_requested = pyqtSignal(str, object)
    file_attach      = pyqtSignal(str)
    file_search_requested = pyqtSignal()
    text_search_requested = pyqtSignal()
    extensions_requested  = pyqtSignal()
    workspace_requested   = pyqtSignal()
    activity_selected     = pyqtSignal(str)
    activity_panel_collapsed_changed = pyqtSignal(bool)
    settings_changed = pyqtSignal()

    def __init__(
        self,
        store: ConversationStore,
        root_path: str,
        settings: SettingsStore | None = None,
        parent=None,
        *,
        current_model_getter: Callable[[], str] | None = None,
        defer_refresh: bool = False,
    ):
        super().__init__(parent)
        self._settings = settings or SettingsStore()
        self._activity_buttons: dict[str, QPushButton] = {}
        self._activity_widgets: dict[str, QWidget] = {}
        self._active_activity = "chats"
        self._collapsed_width = 64
        self._expanded_min_width = 280
        self._expanded_max_width = 640
        self._defer_refresh = defer_refresh

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._rail = QFrame()
        self._rail.setObjectName("activityRail")
        self._rail.setFixedWidth(64)
        rail_layout = QVBoxLayout(self._rail)
        rail_layout.setContentsMargins(6, 8, 6, 8)
        rail_layout.setSpacing(6)

        self._stack = QStackedWidget()
        self._stack.setObjectName("activityStack")
        self._stack.setMinimumWidth(216)
        self._stack.setMaximumWidth(576)

        self._conv = ConversationPanel(store, settings=self._settings)
        self._conv.selected.connect(self.selected)
        self._conv.new_chat.connect(self.new_chat)
        self._conv.renamed.connect(self.renamed)
        self._conv.deleted.connect(self.deleted)

        self._file_tree = FileTree(root_path, defer_git_status=defer_refresh)
        self._file_tree.file_opened.connect(self.file_open)
        self._file_tree.file_attached.connect(self.file_attach)

        files_wrap = QWidget()
        self._files_wrap = files_wrap
        files_layout = QVBoxLayout(files_wrap)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(0)

        self._files_header = _FilesHeader(root_path, filter_enabled=True)
        self._files_header.refresh_clicked.connect(self._file_tree.refresh)
        self._files_header.filter_changed.connect(self._file_tree.set_filter_text)
        files_layout.addWidget(self._files_header)
        files_layout.addWidget(self._file_tree, 1)

        self._git = GitPanel(
            root_path,
            settings=self._settings,
            current_model_getter=current_model_getter,
            defer_refresh=defer_refresh,
        )
        self._git.file_open.connect(self.git_file_open)
        self._git.git_help_requested.connect(self.git_help_requested)

        git_wrap = QWidget()
        git_layout = QVBoxLayout(git_wrap)
        git_layout.setContentsMargins(0, 0, 0, 0)
        git_layout.setSpacing(0)

        self._git_header = _FilesHeader(root_path, refresh_tooltip="Refresh git status")
        self._git_header.refresh_clicked.connect(self._git.refresh)
        git_layout.addWidget(self._git_header)
        git_layout.addWidget(self._git, 1)

        self._add_activity_action("workspace", "Work", rail_layout, tooltip="Workspace")
        self._add_activity("chats", "Chats", self._conv, rail_layout)
        self._add_activity("files", "Files", files_wrap, rail_layout)
        self._add_activity("git", "Git", git_wrap, rail_layout)

        rail_layout.addStretch()

        self._search_btn = QPushButton("Search")
        self._search_btn.setToolTip("Open search")
        self._search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_btn.clicked.connect(self._show_search_menu)
        rail_layout.addWidget(self._search_btn)

        self._extensions_btn = QPushButton("Ext")
        self._extensions_btn.setToolTip("Extensions")
        self._extensions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._extensions_btn.clicked.connect(self.extensions_requested.emit)
        rail_layout.addWidget(self._extensions_btn)

        self._docs_btn = QPushButton("Docs")
        self._docs_btn.setToolTip("Documentation")
        self._docs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._docs_btn.clicked.connect(self.open_docs)
        rail_layout.addWidget(self._docs_btn)

        self._settings_btn = QPushButton("Settings")
        self._settings_btn.setToolTip("Settings (Ctrl+,)")
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(self.open_settings)
        rail_layout.addWidget(self._settings_btn)

        root.addWidget(self._rail)
        root.addWidget(self._stack, 1)
        self.set_active_activity("chats")

        self._apply_styles()

    def _add_activity(self, key: str, label: str, widget: QWidget, rail_layout: QVBoxLayout):
        button = QPushButton(label)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(label)
        button.clicked.connect(lambda _checked=False, k=key: self._on_activity_clicked(k))
        self._activity_buttons[key] = button
        self._activity_widgets[key] = widget
        self._stack.addWidget(widget)
        rail_layout.addWidget(button)

    def _add_activity_action(
        self,
        key: str,
        label: str,
        rail_layout: QVBoxLayout,
        *,
        tooltip: str | None = None,
    ):
        button = QPushButton(label)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip or label)
        button.clicked.connect(lambda _checked=False, k=key: self._on_activity_clicked(k))
        self._activity_buttons[key] = button
        rail_layout.addWidget(button)

    def _on_activity_clicked(self, key: str):
        if key == "workspace":
            self.show_workspace_activity()
            return
        if key == self._active_activity and not self.is_activity_panel_collapsed():
            self.collapse_activity_panel()
            return
        self.set_active_activity(key)

    def show_workspace_activity(self):
        self._active_activity = "workspace"
        self.collapse_activity_panel()
        self._sync_activity_buttons()
        self.workspace_requested.emit()

    def set_active_activity(self, key: str, *, expand: bool = True):
        widget = self._activity_widgets.get(key)
        if widget is None:
            return
        self._active_activity = key
        self._stack.setCurrentWidget(widget)
        if key == "git" and not self._defer_refresh:
            self._git.ensure_loaded()
        if expand:
            self.expand_activity_panel()
        self._sync_activity_buttons()
        self.activity_selected.emit(key)

    def active_activity(self) -> str:
        return self._active_activity

    def is_activity_panel_collapsed(self) -> bool:
        return self._stack.isHidden()

    def collapse_activity_panel(self):
        if self._stack.isHidden():
            return
        self._stack.hide()
        self.setMinimumWidth(self._collapsed_width)
        self.setMaximumWidth(self._collapsed_width)
        self._sync_activity_buttons()
        self.activity_panel_collapsed_changed.emit(True)

    def expand_activity_panel(self):
        was_collapsed = self._stack.isHidden()
        self.setMinimumWidth(self._expanded_min_width)
        self.setMaximumWidth(self._expanded_max_width)
        self._stack.show()
        self._sync_activity_buttons()
        if was_collapsed:
            self.activity_panel_collapsed_changed.emit(False)

    def _sync_activity_buttons(self):
        p = palette()
        fs = max(11, chat_font_pt() - 2)
        for key, button in self._activity_buttons.items():
            button.setStyleSheet(
                rail_button_style(font_size=fs, active=key == self._active_activity)
            )

    def _apply_styles(self):
        p = palette()
        self._rail.setStyleSheet(
            f"QFrame#activityRail {{ background:{p['BG']};"
            f"border-right:1px solid {p['BORDER_SUBTLE']}; }}"
        )
        self._stack.setStyleSheet(
            f"QStackedWidget#activityStack {{ background:{p['BG2']};"
            f"border-right:1px solid {p['BORDER_SUBTLE']}; }}"
        )
        self._files_header.apply_appearance()
        self._git_header.apply_appearance()
        footer_style = sidebar_footer_button_style()
        self._extensions_btn.setStyleSheet(footer_style)
        self._search_btn.setStyleSheet(footer_style)
        self._docs_btn.setStyleSheet(footer_style)
        self._settings_btn.setStyleSheet(footer_style)
        self._sync_activity_buttons()

    def apply_appearance(self):
        self._apply_styles()
        self._conv.apply_appearance()
        self._file_tree._apply_tree_style()
        self._file_tree._apply_decorations()
        self._git.apply_appearance()

    def open_settings(self):
        if SettingsDialog(self._settings, self, cwd=self._file_tree.root_path).exec():
            self.settings_changed.emit()

    def open_docs(self):
        DocsDialog(self).exec()

    def _show_search_menu(self):
        menu = QMenu(self)
        file_action = menu.addAction("File Search")
        text_action = menu.addAction("Text Search")
        chosen = menu.exec(self._search_btn.mapToGlobal(self._search_btn.rect().bottomLeft()))
        if chosen == file_action:
            self.file_search_requested.emit()
        elif chosen == text_action:
            self.text_search_requested.emit()

    def set_workspace(self, path: str, store: ConversationStore | None = None):
        if store is not None:
            self._conv.set_store(store)
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
        clear_workspace_file_cache(self._file_tree.root_path)
        self._file_tree.mark_touched(path)
        QTimer.singleShot(250, self._file_tree.refresh)
        QTimer.singleShot(250, self._git.refresh)

    def set_file_dirty(self, path: str, dirty: bool):
        self._file_tree.set_file_dirty(path, dirty)

    def reveal_file(self, path: str, *, activate: bool = True) -> bool:
        if activate:
            self.set_active_activity("files")
        return self._file_tree.reveal_file(path)

    def focus_file_browser(self):
        self.set_active_activity("files")
        if self._file_tree.currentItem() is None and self._file_tree.topLevelItemCount():
            self._file_tree.setCurrentItem(self._file_tree.topLevelItem(0))
        self._file_tree.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def apply_initial_git_changes(self, changes):
        self._file_tree.refresh_from_changes(changes)
        self._git.set_changes(changes)
        self._defer_refresh = False
        if self._active_activity == "git":
            self._git.ensure_loaded()

    def apply_initial_git_snapshot(self, snapshot: GitSnapshot):
        self._file_tree.refresh_from_git_snapshot(snapshot)
        self._git.apply_snapshot(snapshot)
        self._defer_refresh = False
        if self._active_activity == "git":
            self._git.ensure_loaded()

    def shutdown(self):
        self._file_tree.shutdown()
        self._git.shutdown()
