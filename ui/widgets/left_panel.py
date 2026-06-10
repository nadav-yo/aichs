import os
import shutil
from pathlib import Path
from typing import Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QPushButton, QHBoxLayout, QLabel, QMenu, QSizePolicy, QStyleFactory,
    QAbstractItemView, QInputDialog, QMessageBox, QFrame, QLineEdit,
)
from PyQt6.QtCore import pyqtSignal, Qt, QFileSystemWatcher, QTimer, QMimeData, QSize, QRectF
from PyQt6.QtGui import (
    QColor, QAction, QBrush, QFont, QGuiApplication, QIcon, QKeySequence,
    QPainter, QPen, QPixmap, QShortcut,
)

from config import IGNORED, MAX_TREE_ENTRIES_PER_DIR
from services.chat_drag import AICHS_FILE_DROP_MIME, file_drop_payload, file_drop_text
from services.git_status import discard_files, list_file_changes
from storage.repository import ConversationStore
from storage.settings import SettingsStore
from ui.theme import (
    palette, ACCENT, git_status_color, icon_button_style, files_header_style,
    file_tree_sidebar_style, mono_font_pt, mono_font,
    meta_font_pt, chat_font_pt, current_theme,
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


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


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


class FileTree(QTreeWidget):
    file_opened = pyqtSignal(str)
    file_attached = pyqtSignal(str)

    def __init__(self, root_path: str, parent=None):
        super().__init__(parent)
        self.setObjectName("fileTree")
        self.root_path = root_path
        self._highlighted: set[str] = set()
        self._dirty_paths: set[str] = set()
        self._dirty_dir_paths: set[str] = set()
        self._git_by_path: dict[str, tuple[str, str]] = {}
        self._filter_text = ""
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

    def _rename_path_dialog(self, path: str):
        current = os.path.basename(path)
        kind = "folder" if os.path.isdir(path) else "file"
        name, ok = QInputDialog.getText(self, f"Rename {kind}", "New name:", text=current)
        if not ok or name.strip() == current:
            return
        try:
            new_path = self.rename_path(path, name)
        except Exception as exc:
            QMessageBox.warning(self, "Rename failed", str(exc))
            return
        self.refresh()
        self.mark_touched(str(new_path))

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
        return self.rename_path(path, name)

    def rename_path(self, path: str, name: str) -> Path:
        source = Path(path)
        if not source.is_file() and not source.is_dir():
            raise ValueError("Choose a file or folder in the workspace.")
        self._ensure_in_workspace(source)
        if source.resolve(strict=False) == Path(self.root_path).resolve():
            raise ValueError("Cannot rename the workspace folder.")
        target = source.with_name(self._clean_leaf_name(name))
        self._ensure_in_workspace(target)
        if target == source:
            return source
        if target.exists():
            raise FileExistsError(f"Path already exists: {target.name}")
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
        self._apply_item_icon(item, path)
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
        if self._filter_text:
            self._fill_filtered()
        else:
            self._fill(self.invisibleRootItem(), self.root_path)
        self._apply_decorations()

    def shutdown(self):
        self._git_timer.stop()
        for directory in self._watcher.directories():
            self._watcher.removePath(directory)

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
            item.takeChildren()
            self._fill(item, path)
            self._apply_decorations()

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

    def _fill_filtered(self):
        root = Path(self.root_path)
        terms = [term for term in self._filter_text.split(" ") if term]
        if not terms:
            return
        parent = self.invisibleRootItem()
        matches = 0

        for dirpath, dirnames, filenames in os.walk(self.root_path, onerror=lambda _e: None):
            dirnames[:] = sorted(
                [
                    name for name in dirnames
                    if name not in IGNORED and not name.startswith(".")
                ],
                key=str.lower,
            )
            entries = [(name, True) for name in dirnames] + [(name, False) for name in filenames]
            for name, is_dir in sorted(entries, key=lambda entry: (not entry[1], entry[0].lower())):
                if name in IGNORED or name.startswith("."):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    rel = Path(path).resolve(strict=False).relative_to(root.resolve()).as_posix()
                except (OSError, ValueError):
                    continue
                folded = rel.casefold()
                if not all(term in folded for term in terms):
                    continue
                item = QTreeWidgetItem([self._display_name(rel, path)])
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                item.setData(0, _DISPLAY_NAME_ROLE, rel)
                self._apply_item_icon(item, path)
                parent.addChild(item)
                matches += 1
                if matches >= MAX_TREE_ENTRIES_PER_DIR:
                    more = QTreeWidgetItem([f"... more matches"])
                    more.setDisabled(True)
                    parent.addChild(more)
                    return

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
            self._apply_item_icon(item, e.path)
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
    file_attach      = pyqtSignal(str)
    file_search_requested = pyqtSignal()
    text_search_requested = pyqtSignal()
    extensions_requested  = pyqtSignal()
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
    ):
        super().__init__(parent)
        self._settings = settings or SettingsStore()
        self._activity_buttons: dict[str, QPushButton] = {}
        self._activity_widgets: dict[str, QWidget] = {}
        self._active_activity = "chats"
        self._collapsed_width = 64
        self._expanded_min_width = 240
        self._expanded_max_width = 480

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
        self._stack.setMinimumWidth(180)
        self._stack.setMaximumWidth(420)

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

        self._files_header = _FilesHeader(root_path, filter_enabled=True)
        self._files_header.refresh_clicked.connect(self._file_tree.refresh)
        self._files_header.filter_changed.connect(self._file_tree.set_filter_text)
        files_layout.addWidget(self._files_header)
        files_layout.addWidget(self._file_tree, 1)

        self._git = GitPanel(
            root_path,
            settings=self._settings,
            current_model_getter=current_model_getter,
        )
        self._git.file_open.connect(self.git_file_open)

        git_wrap = QWidget()
        git_layout = QVBoxLayout(git_wrap)
        git_layout.setContentsMargins(0, 0, 0, 0)
        git_layout.setSpacing(0)

        self._git_header = _FilesHeader(root_path, refresh_tooltip="Refresh git status")
        self._git_header.refresh_clicked.connect(self._git.refresh)
        git_layout.addWidget(self._git_header)
        git_layout.addWidget(self._git, 1)

        self._search_page = _SearchActivityPage()
        self._search_page.file_search_requested.connect(self.file_search_requested)
        self._search_page.text_search_requested.connect(self.text_search_requested)

        self._add_activity("chats", "Chats", self._conv, rail_layout)
        self._add_activity("files", "Files", files_wrap, rail_layout)
        self._add_activity("search", "Search", self._search_page, rail_layout)
        self._add_activity("git", "Git", git_wrap, rail_layout)

        rail_layout.addStretch()

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

    def _on_activity_clicked(self, key: str):
        if key == self._active_activity and not self.is_activity_panel_collapsed():
            self.collapse_activity_panel()
            return
        self.set_active_activity(key)

    def set_active_activity(self, key: str, *, expand: bool = True):
        widget = self._activity_widgets.get(key)
        if widget is None:
            return
        self._active_activity = key
        self._stack.setCurrentWidget(widget)
        if expand:
            self.expand_activity_panel()
        self._sync_activity_buttons()

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
            button.setStyleSheet(_rail_button_style(p, fs, key == self._active_activity))

    def _apply_styles(self):
        p = palette()
        fs = max(11, chat_font_pt() - 2)
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
        footer_style = (
            f"QPushButton {{ background:transparent; color:{p['TEXT_DIM']}; border:none;"
            f"border-radius:7px; padding:6px 2px; font-size:{meta_font_pt()}px; }}"
            f"QPushButton:hover {{ background:{p['BG3']}; color:{p['TEXT']}; }}"
        )
        self._extensions_btn.setStyleSheet(footer_style)
        self._docs_btn.setStyleSheet(footer_style)
        self._settings_btn.setStyleSheet(footer_style)
        self._search_page.apply_appearance()
        self._sync_activity_buttons()

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

    def shutdown(self):
        self._file_tree.shutdown()
        self._git.shutdown()


class _SearchActivityPage(QWidget):
    file_search_requested = pyqtSignal()
    text_search_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        title = QLabel("Search")
        title.setObjectName("searchActivityTitle")
        layout.addWidget(title)

        self._file_btn = QPushButton("Open File Search")
        self._file_btn.setToolTip("Search workspace files")
        self._file_btn.clicked.connect(self.file_search_requested.emit)
        layout.addWidget(self._file_btn)

        self._text_btn = QPushButton("Open Text Search")
        self._text_btn.setToolTip("Search text inside workspace files")
        self._text_btn.clicked.connect(self.text_search_requested.emit)
        layout.addWidget(self._text_btn)
        layout.addStretch()

        self.apply_appearance()

    def apply_appearance(self):
        p = palette()
        self.setStyleSheet(f"QWidget {{ background:{p['BG2']}; }}")
        title = self.findChild(QLabel, "searchActivityTitle")
        if title:
            title.setStyleSheet(
                f"color:{p['TEXT']}; font-size:{max(13, chat_font_pt())}px;"
                "font-weight:700; padding:2px 0 8px 0;"
            )
        button_style = (
            f"background-color:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER_SUBTLE']}; border-radius:7px;"
            "padding:8px 10px;"
        )
        self._file_btn.setStyleSheet(button_style)
        self._text_btn.setStyleSheet(button_style)


def _rail_button_style(p: dict, font_size: int, active: bool) -> str:
    bg = p["SELECTION"] if active else "transparent"
    fg = p["SELECTION_TEXT"] if active else p["TEXT_DIM"]
    return (
        f"QPushButton {{ background-color:{bg}; color:{fg}; border:0px;"
        "border-radius:7px; padding:7px 2px;"
        f"font-size:{font_size}px; font-weight:600; }}"
        f"QPushButton:hover {{ background-color:{p['BG3']}; color:{p['TEXT']}; }}"
    )
