import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QFrame, QTabWidget,
    QScrollArea, QLabel, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QPixmap

from config import MAX_FILE_PREVIEW_BYTES
from services.highlight import for_path, for_language
from ui.theme import palette, mono_font

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}


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


class FileViewerPanel(QWidget):
    all_closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

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

    def _editor_style(self) -> str:
        p = palette()
        return f"QTextEdit {{ background:{p['BG3']}; color:{p['TEXT']}; border:none; padding:12px; }}"

    def _apply_editor_font(self, editor: QTextEdit):
        editor.setFont(mono_font())
        editor.setStyleSheet(self._editor_style())

    def _make_editor(self) -> QTextEdit:
        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setFrameShape(QFrame.Shape.NoFrame)
        self._apply_editor_font(editor)
        return editor

    def apply_appearance(self):
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if isinstance(widget, QTextEdit):
                self._apply_editor_font(widget)
                content = widget.property("_source_content")
                lang_hint = widget.property("_lang_hint") or ""
                if content is not None:
                    self._set_editor_html(widget, content, lang_hint)
            elif isinstance(widget, _ImageViewer):
                widget.apply_appearance()

    def _set_editor_html(self, editor: QTextEdit, content: str, lang_hint: str):
        html = for_path(content, lang_hint) if lang_hint else for_language(content, "")
        editor.setHtml(html)

    def _find_tab(self, key: str) -> int:
        tab_bar = self._tabs.tabBar()
        for i in range(self._tabs.count()):
            if tab_bar.tabData(i) == key:
                return i
        return -1

    def _add_tab_widget(self, key: str, title: str, widget: QWidget):
        idx = self._tabs.addTab(widget, title)
        self._tabs.tabBar().setTabData(idx, key)
        self._tabs.setCurrentIndex(idx)

    def _add_text_tab(self, key: str, title: str, content: str, lang_hint: str = ""):
        editor = self._make_editor()
        editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        editor.setProperty("_source_content", content)
        editor.setProperty("_lang_hint", lang_hint)
        self._set_editor_html(editor, content, lang_hint)
        self._add_tab_widget(key, title, editor)

    def open_file(self, path: str):
        path = os.path.abspath(path)
        idx = self._find_tab(path)
        if idx >= 0:
            self._tabs.setCurrentIndex(idx)
            return

        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_EXTS:
            self._add_tab_widget(path, os.path.basename(path), _ImageViewer(path))
            return

        try:
            content = _read_text_preview(path)
        except OSError as e:
            content = f"[Could not read file: {e}]"
        self._add_text_tab(path, os.path.basename(path), content, lang_hint=path)

    def open_content(self, content: str, title: str):
        key = f"\0{title}"
        idx = self._find_tab(key)
        if idx >= 0:
            self._tabs.setCurrentIndex(idx)
            return
        self._add_text_tab(key, title, content, lang_hint=title)

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


def _read_text_preview(path: str) -> str:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        raw = f.read(MAX_FILE_PREVIEW_BYTES + 1)
    truncated = len(raw) > MAX_FILE_PREVIEW_BYTES
    text = raw[:MAX_FILE_PREVIEW_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[Preview truncated: showing {MAX_FILE_PREVIEW_BYTES} of {size} bytes]"
    return text
