import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QFrame, QTabWidget,
    QScrollArea, QLabel, QSizePolicy, QCheckBox, QPushButton,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut

from config import MAX_FILE_PREVIEW_BYTES
from services.diff_html import inline_new_file_diff_to_html
from services.git_diff import can_diff_against_head, diff_against_head
from services.git_status import is_git_repo
from services.highlight import for_path, for_language
from services.tool_policy import path_in_repo
from storage.settings import FILE_EDITOR_AUTO_SAVE_KEY
from ui.theme import ACCENT, palette, mono_font, meta_font_pt, apply_flat_tab_style

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
_BINARY_PREVIEW_EXTS = {
    ".7z",
    ".bz2",
    ".class",
    ".dll",
    ".dmg",
    ".exe",
    ".gz",
    ".jar",
    ".o",
    ".obj",
    ".pdf",
    ".pyc",
    ".rar",
    ".so",
    ".tar",
    ".tgz",
    ".war",
    ".xz",
    ".zip",
}


class _TextMinimap(QWidget):
    """Tiny overview strip that mirrors and controls a QTextEdit."""

    _WIDTH = 86
    _MIN_THUMB_HEIGHT = 28

    def __init__(self, editor: QTextEdit, parent=None):
        super().__init__(parent)
        self._editor = editor
        self.setFixedWidth(self._WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Minimap")
        self.setMouseTracking(True)

        scroll = self._editor.verticalScrollBar()
        scroll.valueChanged.connect(lambda _value: self.update())
        scroll.rangeChanged.connect(lambda _minimum, _maximum: self.update())
        self._editor.document().contentsChanged.connect(lambda: self.update())

    def apply_appearance(self):
        p = palette()
        self.setStyleSheet(
            f"QWidget {{ background:{p['BG3']}; border-left:1px solid {p['BORDER_SUBTLE']}; }}"
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = palette()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(p["BG3"]))
        self._paint_lines(painter, p)
        self._paint_viewport(painter, p)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._scroll_to_y(event.position().y())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._scroll_to_y(event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        scroll = self._editor.verticalScrollBar()
        scroll.setValue(scroll.value() - event.angleDelta().y())
        event.accept()

    def _paint_lines(self, painter: QPainter, p: dict):
        blocks = max(1, self._editor.document().blockCount())
        height = max(1, self.height())
        width = max(1, self.width())
        scale = height / blocks

        block = self._editor.document().firstBlock()
        index = 0
        while block.isValid():
            text = block.text().rstrip()
            if text:
                y = int(index * scale)
                line_h = max(1, min(3, int(scale * 0.75) or 1))
                stripped = text.lstrip()
                indent = len(text) - len(stripped)
                x = 4 + min(24, indent * 2)
                usable = max(8, width - x - 8)
                line_w = max(4, int(usable * min(1.0, len(stripped) / 100)))
                painter.fillRect(x, y, line_w, line_h, self._line_color(stripped, p))
            index += 1
            block = block.next()

    def _line_color(self, text: str, p: dict) -> QColor:
        if text.startswith("+"):
            color = QColor(p["SUCCESS"])
            color.setAlpha(130)
            return color
        if text.startswith("-"):
            color = QColor("#f87171")
            color.setAlpha(130)
            return color
        if text.startswith("@@"):
            color = QColor(p["LINK"])
            color.setAlpha(120)
            return color
        color = QColor(p["TEXT_DIM"])
        color.setAlpha(85)
        return color

    def _paint_viewport(self, painter: QPainter, p: dict):
        top, thumb_h = self._viewport_thumb()
        if thumb_h >= self.height():
            return
        fill = QColor(p["SELECTION"])
        fill.setAlpha(120)
        border = QColor(p["LINK"])
        border.setAlpha(160)
        rect = self.rect().adjusted(2, top, -3, -(self.height() - top - thumb_h))
        painter.fillRect(rect, fill)
        painter.setPen(border)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _viewport_thumb(self) -> tuple[int, int]:
        scroll = self._editor.verticalScrollBar()
        height = max(1, self.height())
        minimum = scroll.minimum()
        maximum = scroll.maximum()
        page = max(1, scroll.pageStep())
        total = max(page, maximum - minimum + page)
        thumb_h = height if maximum <= minimum else max(
            self._MIN_THUMB_HEIGHT,
            int(height * page / total),
        )
        thumb_h = min(height, thumb_h)
        travel = max(1, height - thumb_h)
        top = 0 if maximum <= minimum else int((scroll.value() - minimum) * travel / (maximum - minimum))
        return max(0, min(travel, top)), thumb_h

    def _scroll_to_y(self, y: float):
        scroll = self._editor.verticalScrollBar()
        minimum = scroll.minimum()
        maximum = scroll.maximum()
        if maximum <= minimum:
            return
        top, thumb_h = self._viewport_thumb()
        travel = max(1, self.height() - thumb_h)
        target = (float(y) - (thumb_h / 2)) / travel
        target = max(0.0, min(1.0, target))
        scroll.setValue(minimum + int(target * (maximum - minimum)))


class _ImageViewer(QScrollArea):
    """Scrollable image tab; scales down large images to fit the viewport."""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._original: QPixmap | None = None

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        self.setWidget(self._label)

        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._label.setText(f"Could not load image:\n{os.path.basename(path)}")
        else:
            self._original = pixmap

        self.apply_appearance()
        self._update_scale()

    def apply_appearance(self):
        p = palette()
        self.setStyleSheet(f"QScrollArea {{ background:{p['BG']}; border:none; }}")
        if self._original is None:
            self._label.setStyleSheet(
                f"color:{p['TEXT_DIM']}; padding:24px; background:transparent;"
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scale()

    def _update_scale(self):
        if not self._original:
            return
        vp = self.viewport().size()
        if self._original.width() <= vp.width() and self._original.height() <= vp.height():
            self._label.setPixmap(self._original)
            return
        scaled = self._original.scaled(
            vp,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)


class _FileTextEdit(QTextEdit):
    edit_requested = pyqtSignal()

    def mousePressEvent(self, event):
        if self.isReadOnly():
            self.edit_requested.emit()
        super().mousePressEvent(event)


class _TextFileTab(QWidget):
    """Editable file tab with optional read-only git diff vs HEAD."""

    dirty_changed = pyqtSignal(bool)

    def __init__(
        self,
        path: str,
        content: str,
        repo_root: str,
        diff_text: str | None,
        editable: bool = True,
        read_only_reason: str = "",
        auto_save: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._path = path
        self._content = content
        self._repo_root = repo_root
        self._lang_hint = path
        self._diff_text = diff_text
        self._file_backed = not path.startswith("\0")
        self._editable = self._file_backed and editable
        self._read_only_reason = read_only_reason
        self._auto_save = auto_save
        self._dirty = False
        self._rendering = False
        self._edit_mode = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        self._diff_toggle = QCheckBox("Show changes")
        self._diff_toggle.setChecked(bool(diff_text))
        self._diff_toggle.setVisible(diff_text is not None)
        self._diff_toggle.toggled.connect(self._on_diff_toggled)
        bar.addWidget(self._diff_toggle)
        self._status = QLabel()
        self._status.setVisible(False)
        bar.addWidget(self._status)
        bar.addStretch(1)
        self._revert_btn = QPushButton("Revert")
        self._revert_btn.setToolTip("Reload this file from disk")
        self._revert_btn.clicked.connect(self._revert)
        bar.addWidget(self._revert_btn)
        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Save changes")
        self._save_btn.clicked.connect(self._save)
        bar.addWidget(self._save_btn)
        root.addLayout(bar)

        self._editor = _FileTextEdit()
        self._editor.setAcceptRichText(False)
        self._editor.setFrameShape(QFrame.Shape.NoFrame)
        self._editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._editor.edit_requested.connect(self._enter_edit_mode)
        self._editor.textChanged.connect(self._on_text_changed)
        self._minimap = _TextMinimap(self._editor)

        view = QHBoxLayout()
        view.setContentsMargins(0, 0, 0, 0)
        view.setSpacing(0)
        view.addWidget(self._editor, 1)
        view.addWidget(self._minimap)
        root.addLayout(view, 1)

        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self._save)

        self.apply_appearance()
        self._render()

    def set_auto_save(self, enabled: bool):
        self._auto_save = enabled
        if enabled and self._dirty:
            self._save(auto=True)
        self._sync_actions()

    def apply_appearance(self):
        p = palette()
        meta = meta_font_pt()
        self._diff_toggle.setStyleSheet(
            f"QCheckBox {{ color:{p['TEXT_DIM']}; font-size:{meta}px; spacing:6px; }}"
            f"QCheckBox::indicator {{ width:14px; height:14px; }}"
        )
        self._status.setStyleSheet(
            f"QLabel {{ color:{p['TEXT_DIM']}; font-size:{meta}px; background:transparent; }}"
        )
        secondary = (
            f"QPushButton {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:4px;"
            f"padding:2px 10px; font-size:{meta}px; }}"
            f"QPushButton:hover {{ background:{p['BORDER']}; }}"
            f"QPushButton:disabled {{ color:{p['TEXT_DIM']}; background:{p['BG2']}; }}"
        )
        primary = (
            f"QPushButton {{ background:{ACCENT}; color:white; border:none;"
            f"border-radius:4px; padding:2px 10px; font-size:{meta}px; }}"
            f"QPushButton:hover {{ background:#0066dd; }}"
            f"QPushButton:disabled {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER']}; }}"
        )
        self._revert_btn.setStyleSheet(secondary)
        self._save_btn.setStyleSheet(primary)
        self._editor.setFont(mono_font())
        self._editor.setStyleSheet(
            f"QTextEdit {{ background:{p['BG3']}; color:{p['TEXT']}; border:none; }}"
        )
        self._minimap.apply_appearance()
        self._sync_actions()
        self._render()

    def update_content(
        self,
        content: str,
        diff_text: str | None,
        editable: bool = True,
        read_only_reason: str = "",
    ):
        if self._dirty:
            self._set_status("Unsaved changes")
            return
        self._content = content
        self._diff_text = diff_text
        self._editable = self._file_backed and editable
        self._read_only_reason = read_only_reason
        self._set_dirty(False)
        self._edit_mode = False
        self._diff_toggle.setChecked(bool(diff_text))
        self._diff_toggle.setVisible(diff_text is not None)
        self._render()

    def _on_diff_toggled(self, checked: bool):
        if not checked and self._editable and self._edit_mode:
            self._editor.setFocus()
        self._render()

    def _enter_edit_mode(self):
        if not self._editable or self._is_showing_diff():
            return
        self._edit_mode = True
        self._render()
        self._editor.setFocus()

    def _on_text_changed(self):
        if (
            self._rendering
            or not self._editable
            or not self._edit_mode
            or self._is_showing_diff()
        ):
            return
        self._content = self._editor.toPlainText()
        if self._auto_save:
            self._save(auto=True)
            return
        self._set_dirty(True)
        self._sync_actions()

    def _is_showing_diff(self) -> bool:
        return self._diff_toggle.isChecked() and bool(self._diff_text)

    def _render(self):
        self._rendering = True
        self._editor.blockSignals(True)
        if self._is_showing_diff():
            self._editor.setReadOnly(True)
            self._editor.setHtml(inline_new_file_diff_to_html(self._diff_text, self._content))
        else:
            self._editor.setReadOnly(not (self._editable and self._edit_mode))
            if self._editable and self._edit_mode:
                self._editor.setPlainText(self._content)
            else:
                self._editor.setHtml(
                    for_path(self._content, self._lang_hint)
                    if self._lang_hint
                    else for_language(self._content, "")
                )
        self._editor.blockSignals(False)
        self._rendering = False
        self._sync_actions()
        self._minimap.update()

    def _save(self, *, auto: bool = False):
        if not self._editable or self._is_showing_diff():
            return
        path = Path(self._path)
        if not path_in_repo(path, self._repo_root):
            self._set_dirty(True)
            self._set_status("Save blocked: outside workspace")
            return
        text = self._editor.toPlainText() if self._edit_mode else self._content
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            self._set_dirty(True)
            self._set_status(f"Save failed: {e}")
            return
        self._content = text
        self._set_dirty(False)
        if not auto:
            self._edit_mode = False
        self._set_status("Auto-saved" if auto else "Saved")
        if is_git_repo(self._repo_root) and can_diff_against_head(
            self._repo_root, self._path,
        ):
            self._diff_text = diff_against_head(self._repo_root, self._path)
            self._diff_toggle.setVisible(self._diff_text is not None)
        if auto:
            self._sync_actions()
        else:
            self._render()

    def _revert(self):
        if not self._file_backed or self._is_showing_diff():
            return
        try:
            content, truncated, decode_error, blocked_preview = _read_text_preview_details(self._path)
        except OSError as e:
            self._content = f"[Could not read file: {e}]"
            self._editable = False
            self._read_only_reason = f"Could not read file: {e}"
        else:
            self._content = content
            self._editable = not truncated and not decode_error and not blocked_preview
            self._read_only_reason = _read_only_reason(truncated, decode_error, blocked_preview)
        self._edit_mode = False
        self._set_dirty(False)
        if is_git_repo(self._repo_root) and can_diff_against_head(
            self._repo_root, self._path,
        ):
            self._diff_text = diff_against_head(self._repo_root, self._path)
            self._diff_toggle.setVisible(self._diff_text is not None)
        self._render()
        self._set_status("Reverted" if self._editable else self._read_only_reason)

    def _sync_actions(self):
        showing_diff = self._is_showing_diff()
        show_file_actions = self._file_backed
        self._revert_btn.setVisible(show_file_actions)
        self._save_btn.setVisible(show_file_actions)
        self._revert_btn.setEnabled(show_file_actions and not showing_diff)
        self._save_btn.setEnabled(
            self._editable and self._dirty and not showing_diff and not self._auto_save
        )
        if not self._editable and self._read_only_reason:
            self._set_status(self._read_only_reason)
        elif showing_diff:
            self._set_status("Diff preview")
        elif (
            self._editable
            and not self._edit_mode
            and not self._dirty
            and self._status.text() not in ("Saved", "Auto-saved")
        ):
            self._set_status("Formatted view")
        elif not self._dirty and self._status.text() not in ("Saved", "Auto-saved"):
            self._set_status("")

    def _set_status(self, text: str):
        self._status.setText(text)
        self._status.setVisible(bool(text))

    def _set_dirty(self, dirty: bool):
        if self._dirty == dirty:
            if dirty:
                self._set_status("Unsaved changes")
            return
        self._dirty = dirty
        self.dirty_changed.emit(dirty)
        if dirty:
            self._set_status("Unsaved changes")


class FileViewerPanel(QWidget):
    all_closed = pyqtSignal()

    def __init__(self, repo_root: str = "", settings=None, parent=None):
        super().__init__(parent)
        self._repo_root = repo_root or os.getcwd()
        self._settings = settings
        self._auto_save = self._load_auto_save()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setTabsClosable(True)
        tab_bar = self._tabs.tabBar()
        tab_bar.setUsesScrollButtons(True)
        tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        tab_bar.setExpanding(False)
        self._tabs.tabCloseRequested.connect(self._on_tab_close_requested)

        root.addWidget(self._tabs)
        self._apply_tab_style()

    def set_repo_root(self, path: str):
        self._repo_root = path

    def reload_settings(self):
        self.set_auto_save(self._load_auto_save())

    def set_auto_save(self, enabled: bool):
        self._auto_save = enabled
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if isinstance(widget, _TextFileTab):
                widget.set_auto_save(enabled)
                self._sync_tab_title(widget)

    def apply_appearance(self):
        self._apply_tab_style()
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if isinstance(widget, _TextFileTab):
                widget.apply_appearance()
            elif isinstance(widget, _ImageViewer):
                widget.apply_appearance()

    def _apply_tab_style(self):
        apply_flat_tab_style(self._tabs, "fileViewerTabs")

    def _find_tab(self, key: str) -> int:
        tab_bar = self._tabs.tabBar()
        for i in range(self._tabs.count()):
            if tab_bar.tabData(i) == key:
                return i
        return -1

    def _add_tab_widget(self, key: str, title: str, widget: QWidget):
        idx = self._tabs.addTab(widget, title)
        self._tabs.tabBar().setTabData(idx, key)
        widget.setProperty("_base_tab_title", title)
        if isinstance(widget, _TextFileTab):
            widget.dirty_changed.connect(lambda _dirty, w=widget: self._sync_tab_title(w))
            self._sync_tab_title(widget)
        self._tabs.setCurrentIndex(idx)

    def _add_text_tab(self, key: str, title: str, content: str):
        tab = _TextFileTab(
            key,
            content,
            self._repo_root,
            diff_text=None,
            editable=False,
            auto_save=self._auto_save,
        )
        self._add_tab_widget(key, title, tab)

    def open_file(
        self,
        path: str,
        repo_root: str | None = None,
        diff_text: str | None = None,
    ):
        if repo_root:
            self._repo_root = repo_root
        path = os.path.abspath(path)

        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_EXTS:
            idx = self._find_tab(path)
            if idx >= 0:
                self._tabs.setCurrentIndex(idx)
                return
            self._add_tab_widget(path, os.path.basename(path), _ImageViewer(path))
            return

        try:
            content, truncated, decode_error, blocked_preview = _read_text_preview_details(path)
        except OSError as e:
            content = f"[Could not read file: {e}]"
            editable = False
            read_only_reason = f"Could not read file: {e}"
        else:
            editable = not truncated and not decode_error and not blocked_preview
            read_only_reason = _read_only_reason(truncated, decode_error, blocked_preview)

        if (
            diff_text is None
            and is_git_repo(self._repo_root)
            and can_diff_against_head(self._repo_root, path)
        ):
            diff_text = diff_against_head(self._repo_root, path)

        idx = self._find_tab(path)
        if idx >= 0:
            widget = self._tabs.widget(idx)
            if isinstance(widget, _TextFileTab):
                widget.update_content(content, diff_text, editable, read_only_reason)
            self._tabs.setCurrentIndex(idx)
            return

        tab = _TextFileTab(
            path,
            content,
            self._repo_root,
            diff_text=diff_text,
            editable=editable,
            read_only_reason=read_only_reason,
            auto_save=self._auto_save,
        )
        self._add_tab_widget(path, os.path.basename(path), tab)

    def open_content(self, content: str, title: str):
        key = f"\0{title}"
        idx = self._find_tab(key)
        if idx >= 0:
            self._tabs.setCurrentIndex(idx)
            return
        self._add_text_tab(key, title, content)

    def _on_tab_close_requested(self, index: int):
        self._tabs.removeTab(index)
        if self._tabs.count() == 0:
            self.all_closed.emit()

    def close_current_tab(self) -> bool:
        if self._tabs.count() == 0:
            return False
        self._tabs.removeTab(self._tabs.currentIndex())
        if self._tabs.count() == 0:
            self.all_closed.emit()
        return True

    def _load_auto_save(self) -> bool:
        if self._settings is None:
            return False
        try:
            data = self._settings.load()
        except Exception:
            return False
        return bool(data.get(FILE_EDITOR_AUTO_SAVE_KEY, False))

    def _sync_tab_title(self, widget: QWidget):
        idx = self._tabs.indexOf(widget)
        if idx < 0:
            return
        base = str(widget.property("_base_tab_title") or self._tabs.tabText(idx).lstrip("* "))
        dirty = isinstance(widget, _TextFileTab) and widget._dirty
        self._tabs.setTabText(idx, f"* {base}" if dirty else base)
        tooltip = base
        if dirty:
            tooltip = f"Unsaved changes - {base}"
        self._tabs.setTabToolTip(idx, tooltip)


def _read_text_preview(path: str) -> str:
    text, _truncated, _decode_error, _blocked_preview = _read_text_preview_details(path)
    return text


def _read_text_preview_details(path: str) -> tuple[str, bool, bool, bool]:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        raw = f.read(MAX_FILE_PREVIEW_BYTES + 1)
    truncated = len(raw) > MAX_FILE_PREVIEW_BYTES
    if _is_binary_preview_path(path) or _looks_binary(raw[:MAX_FILE_PREVIEW_BYTES]):
        return _binary_preview_message(path, size), truncated, False, True
    decode_error = False
    try:
        text = raw[:MAX_FILE_PREVIEW_BYTES].decode("utf-8")
    except UnicodeDecodeError:
        decode_error = True
        text = raw[:MAX_FILE_PREVIEW_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[Preview truncated: showing {MAX_FILE_PREVIEW_BYTES} of {size} bytes]"
    return text, truncated, decode_error, False


def _read_only_reason(truncated: bool, decode_error: bool, blocked_preview: bool = False) -> str:
    if blocked_preview:
        return "Binary/archive preview disabled"
    if truncated:
        return "Preview truncated; saving disabled"
    if decode_error:
        return "Non-UTF-8 preview; saving disabled"
    return ""


def _is_binary_preview_path(path: str) -> bool:
    suffixes = [suffix.lower() for suffix in Path(path).suffixes]
    return any(suffix in _BINARY_PREVIEW_EXTS for suffix in suffixes)


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    if b"\0" in raw:
        return True
    sample = raw[:4096]
    controls = sum(1 for b in sample if b < 32 and b not in b"\t\n\r\f\b")
    return controls / len(sample) > 0.30


def _binary_preview_message(path: str, size: int) -> str:
    name = os.path.basename(path)
    return (
        f"[Cannot preview binary or archive file: {name}]\n"
        f"Size: {size} bytes\n"
        "Open it with an external tool if you need to inspect its contents."
    )
