import os
from pathlib import Path
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPlainTextEdit, QFrame, QTabWidget,
    QScrollArea, QLabel, QSizePolicy, QCheckBox, QPushButton, QLineEdit,
    QCompleter, QApplication, QToolTip, QMenu, QTabBar, QMessageBox, QSplitter,
)
from PyQt6.QtCore import (
    QObject, QRunnable, QThreadPool, QTimer, pyqtSignal, pyqtSlot,
    Qt, QSize, QUrl, QStringListModel, QMimeData,
)
from PyQt6.QtGui import (
    QColor, QCursor, QDrag, QGuiApplication, QKeySequence, QPainter, QPixmap,
    QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat,
)
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_for_filename, guess_lexer, TextLexer
from pygments.token import Token

from config import MAX_FILE_PREVIEW_BYTES
from services.chat_drag import AICHS_FILE_DROP_MIME, file_drop_payload, file_drop_text
from services.diff_html import changed_new_line_numbers
from services.file_editor_refs import AICHS_EDITOR_REF_MIME, editor_ref_payload, editor_ref_text
from services.git_diff import diff_against_head
from services.git_snapshot import build_git_snapshot
from services.performance import time_operation
from services.tool_policy import path_in_repo
from services.language_features import (
    CodeAction, CodeActionResult, Diagnostic, LanguageCompletionProvider,
    apply_code_action as language_apply_code_action,
    code_actions as language_code_actions,
    diagnostics as language_diagnostics,
    format_document as language_format_document,
)
from services.code_completion import (
    CompletionItem, CompletionProvider, LocalCompletionProvider, prefix_at,
)
from storage.settings import (
    DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
    DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
    FILE_EDITOR_AUTO_SAVE_KEY,
    FILE_EDITOR_TAB_SPACES_KEY,
    DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
    DEFAULT_FILE_EDITOR_TAB_SPACES,
    FILE_REVIEW_PROMPT_TEMPLATE_KEY,
    diagnostic_fix_prompt_template,
    file_editor_tab_spaces,
    file_review_prompt_template,
)
from ui.theme import (
    ACCENT, current_theme, palette, mono_font, chat_font_pt, meta_font_pt,
    markdown_css, checkbox_style, compact_field_style, hint_label_style,
    editor_text_area_style, file_tab_style, primary_button_style, secondary_button_style,
    transparent_scroll_area_style,
)
from ui.markdown_html import markdown_body
from ui.widgets.markdown_browser import RemoteImageTextBrowser, copy_code_url_to_clipboard

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
_MARKDOWN_EXTS = {".md", ".markdown", ".mdown", ".mkdn"}
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
_COMPLETION_CONTEXT_CHARS = 160_000
_ASYNC_FILE_READ_BYTES = 128_000
_LOADING_FILE_TEXT = "Loading file..."
_RETIRED_WORKER_POOLS: list[QThreadPool] = []


def _retire_worker_pool(pool: QThreadPool) -> None:
    pool.clear()
    pool.setParent(None)
    _RETIRED_WORKER_POOLS.append(pool)

    def cleanup() -> None:
        if pool.activeThreadCount() > 0:
            QTimer.singleShot(50, cleanup)
            return
        pool.waitForDone(0)
        if pool in _RETIRED_WORKER_POOLS:
            _RETIRED_WORKER_POOLS.remove(pool)

    QTimer.singleShot(0, cleanup)


class _DiagnosticsSignals(QObject):
    done = pyqtSignal(int, object, object)


class _DiagnosticsWorker(QRunnable):
    def __init__(self, generation: int, repo_root: str, path: str, content: str):
        super().__init__()
        self.signals = _DiagnosticsSignals()
        self._generation = generation
        self._repo_root = repo_root
        self._path = path
        self._content = content

    @pyqtSlot()
    def run(self):
        try:
            diagnostics, errors = language_diagnostics(
                self._repo_root,
                self._path,
                self._content,
            )
        except Exception as e:
            diagnostics, errors = [], [f"language diagnostics failed: {e}"]
        self.signals.done.emit(self._generation, diagnostics, errors)


class _CodeActionSignals(QObject):
    done = pyqtSignal(int, object, object)


class _CodeActionListWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        repo_root: str,
        path: str,
        content: str,
        diagnostics: list[Diagnostic],
    ):
        super().__init__()
        self.signals = _CodeActionSignals()
        self._generation = generation
        self._repo_root = repo_root
        self._path = path
        self._content = content
        self._diagnostics = list(diagnostics)

    @pyqtSlot()
    def run(self):
        try:
            actions, errors = language_code_actions(
                self._repo_root,
                self._path,
                self._content,
                self._diagnostics,
            )
        except Exception as e:
            actions, errors = [], [f"language code actions failed: {e}"]
        self.signals.done.emit(self._generation, actions, errors)


class _CodeActionWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        repo_root: str,
        path: str,
        content: str,
        action_id: str,
        diagnostics: list[Diagnostic],
    ):
        super().__init__()
        self.signals = _CodeActionSignals()
        self._generation = generation
        self._repo_root = repo_root
        self._path = path
        self._content = content
        self._action_id = action_id
        self._diagnostics = list(diagnostics)

    @pyqtSlot()
    def run(self):
        try:
            result, errors = language_apply_code_action(
                self._repo_root,
                self._path,
                self._content,
                self._action_id,
                self._diagnostics,
            )
        except Exception as e:
            result, errors = CodeActionResult(message=f"Code action failed: {e}"), [
                f"language code action failed: {e}",
            ]
        self.signals.done.emit(self._generation, result, errors)


class _FormatDocumentWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        repo_root: str,
        path: str,
        content: str,
    ):
        super().__init__()
        self.signals = _CodeActionSignals()
        self._generation = generation
        self._repo_root = repo_root
        self._path = path
        self._content = content

    @pyqtSlot()
    def run(self):
        try:
            result, errors = language_format_document(
                self._repo_root,
                self._path,
                self._content,
            )
        except Exception as e:
            result, errors = CodeActionResult(message=f"Format failed: {e}"), [
                f"language format failed: {e}",
            ]
        self.signals.done.emit(self._generation, result, errors)


class _DiffSignals(QObject):
    done = pyqtSignal(int, object)


class _DiffWorker(QRunnable):
    def __init__(self, generation: int, repo_root: str, path: str):
        super().__init__()
        self.signals = _DiffSignals()
        self._generation = generation
        self._repo_root = repo_root
        self._path = path

    @pyqtSlot()
    def run(self):
        diff_text = None
        try:
            snapshot = build_git_snapshot(self._repo_root)
            diff_text = diff_against_head(
                self._repo_root,
                self._path,
                git_snapshot=snapshot,
            )
        except Exception:
            diff_text = None
        self.signals.done.emit(self._generation, diff_text)


class _MarkdownSignals(QObject):
    done = pyqtSignal(int, str, str)


class _MarkdownPreviewWorker(QRunnable):
    def __init__(self, generation: int, path: str, text: str, theme: str, font_pt: int):
        super().__init__()
        self.signals = _MarkdownSignals()
        self._generation = generation
        self._path = path
        self._text = text
        self._theme = theme
        self._font_pt = font_pt

    @pyqtSlot()
    def run(self):
        with time_operation(
            "markdown.preview",
            detail=f"path={self._path} chars={len(self._text)}",
        ):
            html = _markdown_preview_html(self._text, theme=self._theme, font_pt=self._font_pt)
        self.signals.done.emit(self._generation, self._path, html)


class _FileReadSignals(QObject):
    done = pyqtSignal(int, str, str, object, bool, str, object)


class _FileReadWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        path: str,
        diff_text: str | None,
        line_no: int | None,
    ):
        super().__init__()
        self.signals = _FileReadSignals()
        self._generation = generation
        self._path = path
        self._diff_text = diff_text
        self._line_no = line_no

    @pyqtSlot()
    def run(self):
        content, diff_text, editable, read_only_reason = _read_text_file_state(
            self._path,
            self._diff_text,
        )
        self.signals.done.emit(
            self._generation,
            self._path,
            content,
            diff_text,
            editable,
            read_only_reason,
            self._line_no,
        )


class _FileSaveSignals(QObject):
    done = pyqtSignal(int, str, str, bool, str, bool)


class _FileSaveWorker(QRunnable):
    def __init__(self, generation: int, path: str, text: str, auto: bool):
        super().__init__()
        self.signals = _FileSaveSignals()
        self._generation = generation
        self._path = path
        self._text = text
        self._auto = auto

    @pyqtSlot()
    def run(self):
        error = ""
        try:
            Path(self._path).write_text(self._text, encoding="utf-8")
        except OSError as e:
            error = str(e)
        self.signals.done.emit(
            self._generation,
            self._path,
            self._text,
            not error,
            error,
            self._auto,
        )


class _TextMinimap(QWidget):
    """Tiny overview strip that mirrors and controls a plain text editor."""

    _WIDTH = 64
    _MIN_THUMB_HEIGHT = 28
    _MAX_PAINTED_LINES = 1200

    def __init__(self, editor: QPlainTextEdit, parent=None):
        super().__init__(parent)
        self._editor = editor
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(40)
        self._update_timer.timeout.connect(self.update)
        self.setFixedWidth(self._WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Minimap")
        self.setMouseTracking(True)

        scroll = self._editor.verticalScrollBar()
        scroll.valueChanged.connect(lambda _value: self._schedule_update())
        scroll.rangeChanged.connect(lambda _minimum, _maximum: self._schedule_update())
        self._editor.document().contentsChanged.connect(self._schedule_update)

    def _schedule_update(self):
        if not self._update_timer.isActive():
            self._update_timer.start()

    def apply_appearance(self):
        p = palette()
        self.setStyleSheet(
            f"QWidget {{ background:{p['BG2']}; border-left:1px solid {p['BORDER_SUBTLE']}; }}"
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = palette()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(p["BG2"]))
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
        step = max(1, blocks // self._MAX_PAINTED_LINES)
        document = self._editor.document()

        for index in range(0, blocks, step):
            block = document.findBlockByNumber(index)
            if not block.isValid():
                continue
            text = block.text().rstrip()
            if text:
                y = int(index * scale)
                line_h = max(1, min(3, int(scale * 0.75) or 1))
                stripped = text.lstrip()
                indent = len(text) - len(stripped)
                x = 4 + min(24, indent * 2)
                usable = max(8, width - x - 8)
                line_w = max(4, int(usable * min(1.0, len(stripped) / 100)))
                painter.fillRect(x, y, line_w, line_h, self._line_color(stripped, p, index + 1))

    def _line_color(self, text: str, p: dict, line_number: int) -> QColor:
        changed_lines = getattr(self._editor, "_changed_lines", set())
        if line_number in changed_lines:
            color = QColor(p["SUCCESS"])
            color.setAlpha(125)
            return color
        color_for_line = getattr(self._editor, "minimap_color_for_line", None)
        if color_for_line is not None:
            color = color_for_line(text)
            if color is not None:
                color.setAlpha(105)
                return color
        if text.startswith("+"):
            color = QColor(p["SUCCESS"])
            color.setAlpha(105)
            return color
        if text.startswith("-"):
            color = QColor("#f87171")
            color.setAlpha(105)
            return color
        if text.startswith("@@"):
            color = QColor(p["LINK"])
            color.setAlpha(100)
            return color
        color = QColor(p["TEXT_DIM"])
        color.setAlpha(62)
        return color

    def _paint_viewport(self, painter: QPainter, p: dict):
        top, thumb_h = self._viewport_thumb()
        if thumb_h >= self.height():
            return
        fill = QColor(p["SELECTION"])
        fill.setAlpha(78)
        border = QColor(p["LINK"])
        border.setAlpha(120)
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
        self.setStyleSheet(transparent_scroll_area_style(bg=p["BG"], include_viewport=False))
        if self._original is None:
            self._label.setStyleSheet(hint_label_style(padding="24px"))

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


def _lexer_for_content(path: str, content: str):
    try:
        return get_lexer_for_filename(path, stripnl=False)
    except Exception:
        try:
            return guess_lexer(content)
        except Exception:
            return TextLexer(stripnl=False)


def _is_completion_text(text: str) -> bool:
    return bool(text) and all(ch.isalnum() or ch == "_" for ch in text)


class _PygmentsHighlighter(QSyntaxHighlighter):
    def __init__(self, document, path: str = "", content: str = ""):
        super().__init__(document)
        self._path = path
        self._content_sample = content[:4096]
        self._lexer = _lexer_for_content(path, self._content_sample)
        self._formats: dict[object, QTextCharFormat] = {}
        self._style = None
        self.apply_appearance()

    def set_source(self, path: str, content: str):
        sample = content[:4096]
        if path == self._path and sample == self._content_sample:
            return
        self._path = path
        self._content_sample = sample
        self._lexer = _lexer_for_content(path, sample)
        self.rehighlight()

    def apply_appearance(self):
        style_name = "default" if current_theme() == "light" else "monokai"
        self._style = HtmlFormatter(style=style_name).style
        self._formats.clear()
        self.rehighlight()

    def highlightBlock(self, text: str):
        if not text:
            return
        try:
            tokens = self._lexer.get_tokens_unprocessed(text)
        except Exception:
            return
        for index, token, value in tokens:
            if value:
                self.setFormat(index, len(value), self._format_for_token(token))

    def _format_for_token(self, token) -> QTextCharFormat:
        cached = self._formats.get(token)
        if cached is not None:
            return cached
        fmt = QTextCharFormat()
        style = self._style_for_token(token)
        if style:
            color = style.get("color")
            if color:
                fmt.setForeground(QColor(f"#{color}"))
            bgcolor = style.get("bgcolor")
            if bgcolor:
                fmt.setBackground(QColor(f"#{bgcolor}"))
            if style.get("bold"):
                fmt.setFontWeight(600)
            if style.get("italic"):
                fmt.setFontItalic(True)
            if style.get("underline"):
                fmt.setFontUnderline(True)
        self._formats[token] = fmt
        return fmt

    def minimap_color_for_line(self, text: str) -> QColor | None:
        if not text:
            return None
        try:
            tokens = self._lexer.get_tokens_unprocessed(text)
        except Exception:
            return None
        for _index, token, value in tokens:
            if not value or not value.strip() or token in Token.Text:
                continue
            color = self._style_color_for_token(token)
            if color is not None:
                return color
        return self._style_color_for_token(Token.Text)

    def _style_color_for_token(self, token) -> QColor | None:
        style = self._style_for_token(token)
        color = style.get("color")
        return QColor(f"#{color}") if color else None

    def _style_for_token(self, token) -> dict:
        if self._style is None:
            return {}
        current = token
        while current is not None:
            try:
                return self._style.style_for_token(current)
            except KeyError:
                current = getattr(current, "parent", None)
        return {}


class _LineNumberArea(QWidget):
    def __init__(self, editor: "_FileTextEdit"):
        super().__init__(editor)
        self._editor = editor
        self.setMouseTracking(True)

    def sizeHint(self):
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.line_number_area_paint_event(event)

    def mouseMoveEvent(self, event):
        self._editor.line_number_area_mouse_move_event(event)

    def mousePressEvent(self, event):
        self._editor.line_number_area_mouse_press_event(event)

    def leaveEvent(self, event):
        self._editor.line_number_area_leave_event(event)


class _FileTextEdit(QPlainTextEdit):
    edit_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    diagnostic_fix_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_number_area = _LineNumberArea(self)
        self._syntax_highlighter = _PygmentsHighlighter(self.document())
        self._changed_lines: set[int] = set()
        self._diagnostics: list[Diagnostic] = []
        self._diagnostics_by_line_cache: dict[int, list[Diagnostic]] = {}
        self._completion_path = ""
        self._completion_provider: CompletionProvider = LocalCompletionProvider()
        self._completion_items: dict[str, CompletionItem] = {}
        self._completion_model = QStringListModel(self)
        self._completer: QCompleter | None = None
        self._reference_path = ""
        self._drag_start_pos = None
        self._drag_start_in_selection = False
        self._hovered_diagnostic_line: int | None = None
        self._tab_spaces = DEFAULT_FILE_EDITOR_TAB_SPACES

        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._update_extra_selections)
        self._update_line_number_area_width(0)

    def configure_syntax(self, path: str, content: str):
        self._syntax_highlighter.set_source(path, content)

    def configure_completion(
        self,
        path: str,
        provider: CompletionProvider | None = None,
    ):
        self._completion_path = path
        if provider is not None:
            self._completion_provider = provider

    def configure_reference(self, path: str, repo_root: str):
        if not path or str(path).startswith("\0"):
            self._reference_path = ""
            return
        try:
            rel = Path(path).resolve().relative_to(Path(repo_root).resolve())
        except (OSError, ValueError):
            self._reference_path = ""
            return
        self._reference_path = rel.as_posix()

    def minimap_color_for_line(self, text: str) -> QColor | None:
        return self._syntax_highlighter.minimap_color_for_line(text)

    def set_tab_spaces(self, spaces: int):
        self._tab_spaces = file_editor_tab_spaces({
            FILE_EDITOR_TAB_SPACES_KEY: spaces,
        })
        self._apply_tab_stop()

    def _apply_tab_stop(self):
        self.setTabStopDistance(
            self.fontMetrics().horizontalAdvance(" ") * self._tab_spaces
        )

    def apply_appearance(self):
        self.setFont(mono_font())
        self.setStyleSheet(editor_text_area_style())
        self._apply_tab_stop()
        self._syntax_highlighter.apply_appearance()
        self._update_line_number_area_width(0)
        self._line_number_area.update()
        self._update_extra_selections()

    def set_changed_lines(self, lines: set[int]):
        self._changed_lines = set(lines)
        self._update_extra_selections()
        self._line_number_area.update()

    def set_diagnostics(self, diagnostics: list[Diagnostic]):
        self._diagnostics = list(diagnostics)
        self._diagnostics_by_line_cache = _diagnostics_by_line(self._diagnostics)
        self._update_line_number_area_width(0)
        self._update_extra_selections()
        self._line_number_area.update()

    def goto_line(self, line_no: int, column: int = 0):
        block = self.document().findBlockByNumber(max(0, line_no - 1))
        if not block.isValid():
            block = self.document().lastBlock()
        cursor = QTextCursor(block)
        cursor.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.MoveAnchor,
            max(0, column),
        )
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    def select_range(self, start: int, length: int, *, focus: bool = True):
        cursor = self.textCursor()
        cursor.setPosition(max(0, start))
        cursor.setPosition(max(0, start + length), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.centerCursor()
        if focus:
            self.setFocus()

    def release_resources(self):
        self._hide_completion()
        completer = getattr(self, "_completer", None)
        if completer is not None:
            try:
                completer.setWidget(None)
            except RuntimeError:
                pass
        highlighter = getattr(self, "_syntax_highlighter", None)
        if highlighter is not None:
            try:
                highlighter.setDocument(None)
            except RuntimeError:
                pass

    def closeEvent(self, event):
        self.release_resources()
        super().closeEvent(event)

    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        marker_width = 12 if self._diagnostics else 0
        return 12 + marker_width + self.fontMetrics().horizontalAdvance("9") * digits

    def line_number_area_paint_event(self, event):
        p = palette()
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor(p["BG2"]))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        width = self._line_number_area.width()
        height = self.fontMetrics().height()
        diagnostics_by_line = self._diagnostics_by_line()
        marker_width = 12 if self._diagnostics else 0

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_diagnostics = diagnostics_by_line.get(block_number + 1)
                if line_diagnostics:
                    color = _diagnostic_color(line_diagnostics[0].severity)
                    marker_y = top + max(2, (height - 7) // 2)
                    painter.setPen(Qt.PenStyle.NoPen)
                    if block_number + 1 == self._hovered_diagnostic_line:
                        halo = QColor(color)
                        halo.setAlpha(70)
                        painter.setBrush(halo)
                        painter.drawEllipse(1, marker_y - 3, 13, 13)
                    painter.setBrush(color)
                    painter.drawEllipse(4, marker_y, 7, 7)
                painter.setPen(QColor(p["TEXT"] if block_number == current else p["TEXT_DIM"]))
                painter.drawText(
                    marker_width, top, width - 6, height,
                    Qt.AlignmentFlag.AlignRight,
                    str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            cr.left(), cr.top(), self.line_number_area_width(), cr.height()
        )

    def setReadOnly(self, ro: bool):
        super().setReadOnly(ro)
        if ro:
            self._hide_completion()
        self._update_extra_selections()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = _event_pos(event)
            self._drag_start_pos = pos
            self._drag_start_in_selection = self._pos_in_selection(pos)
            if self._drag_start_in_selection:
                event.accept()
                return
        else:
            self._drag_start_pos = None
            self._drag_start_in_selection = False
        if self.isReadOnly():
            self.edit_requested.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start_in_selection
            and self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (_event_pos(event) - self._drag_start_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            mime = self._mime_data_for_drag()
            if mime is not None:
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.CopyAction)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        self._drag_start_in_selection = False
        super().mouseReleaseEvent(event)

    def copy(self):
        if self._copy_with_reference():
            return
        super().copy()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy) and self._copy_with_reference():
            event.accept()
            return
        if self._completion_popup_visible():
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                self._insert_completion(self._completer.currentCompletion() if self._completer else "")
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape:
                self._hide_completion()
                event.accept()
                return

        if not self.isReadOnly() and (
            event.key() == Qt.Key.Key_Backtab
            or (
                event.key() == Qt.Key.Key_Tab
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            )
        ):
            self._outdent_current_line()
            event.accept()
            return

        completion_shortcut = (
            event.key() == Qt.Key.Key_Space
            and event.modifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        if completion_shortcut:
            self._show_completion(manual=True)
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not self.isReadOnly()
            and not event.modifiers()
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        ):
            cursor = self.textCursor()
            block_text = cursor.block().text()
            indent = block_text[: len(block_text) - len(block_text.lstrip(" \t"))]
            cursor.insertText("\n" + indent)
            self.setTextCursor(cursor)
            event.accept()
            return
        super().keyPressEvent(event)
        self._maybe_update_completion(event)

    def _outdent_current_line(self) -> bool:
        cursor = self.textCursor()
        block = cursor.block()
        block_text = block.text()
        if block_text.startswith("\t"):
            remove_count = 1
        else:
            leading_spaces = len(block_text) - len(block_text.lstrip(" "))
            remove_count = min(self._tab_spaces, leading_spaces)
        if remove_count <= 0:
            return False

        block_pos = block.position()
        position = cursor.position()
        edit = QTextCursor(block)
        edit.setPosition(block_pos)
        edit.setPosition(block_pos + remove_count, QTextCursor.MoveMode.KeepAnchor)
        edit.removeSelectedText()

        if not cursor.hasSelection():
            cursor.setPosition(max(block_pos, position - remove_count))
            self.setTextCursor(cursor)
        return True

    def _copy_with_reference(self) -> bool:
        mime = self._mime_data_for_selection()
        if mime is None:
            return False
        QGuiApplication.clipboard().setMimeData(mime)
        return True

    def _mime_data_for_selection(self) -> QMimeData | None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return None
        text = cursor.selectedText().replace("\u2029", "\n")
        if not text.strip():
            return None
        mime = QMimeData()
        mime.setText(text)
        ref = self._selected_editor_ref(cursor, text)
        if ref:
            mime.setData(AICHS_EDITOR_REF_MIME, editor_ref_payload([ref]))
        return mime

    def _mime_data_for_drag(self) -> QMimeData | None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return None
        ref = self._selected_editor_ref(cursor)
        if not ref:
            return self._mime_data_for_selection()
        mime = QMimeData()
        mime.setText(editor_ref_text([ref]))
        mime.setData(AICHS_EDITOR_REF_MIME, editor_ref_payload([ref]))
        return mime

    def _selected_editor_ref(self, cursor: QTextCursor, text: str = "") -> dict:
        if not self._reference_path:
            return {}
        start_line, end_line = _cursor_line_range(self.document(), cursor)
        return {
            "path": self._reference_path,
            "start_line": start_line,
            "end_line": end_line,
            "text": text,
        }

    def _pos_in_selection(self, pos) -> bool:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        hit = self.cursorForPosition(pos).position()
        return cursor.selectionStart() <= hit <= cursor.selectionEnd()

    def _maybe_update_completion(self, event):
        if self.isReadOnly():
            self._hide_completion()
            return
        key = event.key()
        if key in (
            Qt.Key.Key_Backspace,
            Qt.Key.Key_Delete,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
        ) or (event.text() and _is_completion_text(event.text())):
            self._show_completion(manual=False)
        elif key not in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._hide_completion()

    def _show_completion(self, *, manual: bool):
        if self.isReadOnly():
            self._hide_completion()
            return
        content, position, prefix = self._completion_context()
        minimum = 1 if manual else 2
        if len(prefix) < minimum:
            self._hide_completion()
            return
        items = self._completion_provider.complete(
            path=self._completion_path,
            content=content,
            position=position,
            prefix=prefix,
        )
        if not items:
            self._hide_completion()
            return

        self._completion_items = {item.label: item for item in items}
        labels = [item.label for item in items]
        self._completion_model.setStringList(labels)
        if not self.isVisible():
            return
        completer = self._ensure_completer()
        completer.setCompletionPrefix(prefix)
        popup = completer.popup()
        popup.setCurrentIndex(completer.completionModel().index(0, 0))

        rect = self.cursorRect()
        rect.setWidth(
            max(
                self.fontMetrics().horizontalAdvance(max(labels, key=len)) + 42,
                completer.popup().sizeHintForColumn(0)
                + completer.popup().verticalScrollBar().sizeHint().width(),
            )
        )
        completer.complete(rect)

    def _insert_completion(self, label: str):
        item = self._completion_items.get(label)
        if item is None:
            return
        cursor = self.textCursor()
        prefix = self._completion_prefix_at_cursor(cursor)
        if prefix:
            cursor.movePosition(
                QTextCursor.MoveOperation.Left,
                QTextCursor.MoveMode.KeepAnchor,
                len(prefix),
            )
        cursor.insertText(item.insert_text)
        self.setTextCursor(cursor)
        self._hide_completion()

    def _completion_context(self) -> tuple[str, int, str]:
        cursor = self.textCursor()
        content, position = self._document_text_window(
            cursor.position(),
            _COMPLETION_CONTEXT_CHARS,
        )
        return content, position, self._completion_prefix_at_cursor(cursor)

    def _document_text_window(self, position: int, max_chars: int) -> tuple[str, int]:
        document = self.document()
        text_length = max(0, document.characterCount() - 1)
        position = max(0, min(position, text_length))
        if text_length <= max_chars:
            start = 0
            end = text_length
        else:
            before = max_chars // 2
            start = max(0, position - before)
            end = min(text_length, start + max_chars)
            start = max(0, end - max_chars)

        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        text = cursor.selectedText().replace("\u2029", "\n").replace("\u2028", "\n")
        return text, position - start

    def _completion_prefix_at_cursor(self, cursor: QTextCursor) -> str:
        return prefix_at(cursor.block().text(), cursor.positionInBlock())

    def _completion_popup_visible(self) -> bool:
        return self._completer is not None and self._completer.popup().isVisible()

    def _hide_completion(self):
        if self._completer is not None:
            self._completer.popup().hide()
        self._completion_model.setStringList([])
        self._completion_items = {}

    def _ensure_completer(self) -> QCompleter:
        if self._completer is None:
            self._completer = QCompleter(self._completion_model, self)
            self._completer.setWidget(self)
            self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self._completer.setWrapAround(False)
            self._completer.activated[str].connect(self._insert_completion)
        return self._completer

    def _update_line_number_area_width(self, _block_count):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy):
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def line_number_area_mouse_move_event(self, event):
        pos = _event_pos(event)
        line = self._diagnostic_line_at_gutter_pos(pos)
        if line != self._hovered_diagnostic_line:
            self._hovered_diagnostic_line = line
            self._line_number_area.update()
        if line is None:
            self._line_number_area.unsetCursor()
            self._line_number_area.setToolTip("")
            QToolTip.hideText()
            return

        diagnostics = self._diagnostics_by_line().get(line, [])
        details = _diagnostic_details(diagnostics)
        self._line_number_area.setCursor(Qt.CursorShape.PointingHandCursor)
        self._line_number_area.setToolTip(details)
        if details:
            try:
                global_pos = event.globalPosition().toPoint()
            except AttributeError:
                global_pos = self._line_number_area.mapToGlobal(pos)
            QToolTip.showText(global_pos, details, self._line_number_area)

    def line_number_area_leave_event(self, _event):
        if self._hovered_diagnostic_line is not None:
            self._hovered_diagnostic_line = None
            self._line_number_area.update()
        self._line_number_area.unsetCursor()
        self._line_number_area.setToolTip("")
        QToolTip.hideText()

    def line_number_area_mouse_press_event(self, event):
        if event.button() != Qt.MouseButton.RightButton:
            return
        line = self._diagnostic_line_at_gutter_pos(_event_pos(event))
        if line is None:
            return
        diagnostics = self._diagnostics_by_line().get(line, [])
        if not diagnostics:
            return
        self.diagnostic_fix_requested.emit(diagnostics)
        event.accept()

    def _update_extra_selections(self):
        selections = []
        p = palette()

        current_line = QTextEdit.ExtraSelection()
        line_color = QColor(p["SELECTION"])
        line_color.setAlpha(90)
        current_line.format.setBackground(line_color)
        current_line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        current_line.cursor = self.textCursor()
        current_line.cursor.clearSelection()
        selections.append(current_line)

        changed_color = QColor(p["SUCCESS_BG"])
        for line in sorted(self._changed_lines):
            block = self.document().findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(changed_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.setPosition(block.position())
            selection.cursor.clearSelection()
            selections.append(selection)

        diagnostics_by_line = self._diagnostics_by_line()
        for line, diagnostics in sorted(diagnostics_by_line.items()):
            block = self.document().findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            color = _diagnostic_color(diagnostics[0].severity)
            color.setAlpha(28)
            selection.format.setBackground(color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = QTextCursor(block)
            selection.cursor.clearSelection()
            selections.append(selection)

        self.setExtraSelections(selections)

    def _diagnostics_by_line(self) -> dict[int, list[Diagnostic]]:
        return self._diagnostics_by_line_cache

    def _diagnostic_line_at_gutter_pos(self, pos) -> int | None:
        if not self._diagnostics or pos.x() > 16:
            return None
        diagnostics_by_line = self._diagnostics_by_line()
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()

        while block.isValid():
            if block.isVisible():
                line_number = block_number + 1
                if line_number in diagnostics_by_line:
                    marker_y = top + max(2, (height - 7) // 2)
                    if 0 <= pos.x() <= 16 and marker_y - 4 <= pos.y() <= marker_y + 12:
                        return line_number
            if top > pos.y():
                return None
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1
        return None


class _MarkdownPreview(RemoteImageTextBrowser):
    edit_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def apply_appearance(self):
        p = palette()
        self.setStyleSheet(
            f"QTextBrowser {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:none; padding:14px 18px; }}"
        )

    def set_html(self, html: str, base_path: str):
        parent = str(Path(base_path).parent)
        self.document().setBaseUrl(QUrl.fromLocalFile(parent + os.sep))
        self.setHtml(html)

    def set_markdown(self, text: str, base_path: str):
        self.set_html(_markdown_preview_html(text), base_path)

    def mousePressEvent(self, event):
        if copy_code_url_to_clipboard(self.anchorAt(_event_pos(event))):
            event.accept()
            return
        self.edit_requested.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _markdown_preview_html(
    text: str,
    *,
    theme: str | None = None,
    font_pt: int | None = None,
) -> str:
    body = markdown_body(
        text,
        extensions=["fenced_code", "nl2br", "tables", "toc"],
        theme=theme,
        font_pt=font_pt,
    )
    p = palette(theme)
    css = (
        markdown_css(font_pt=font_pt, theme=theme)
        + f"body {{ background:{p['BG3']}; padding:8px 10px 14px 10px; }}"
    )
    return f"<style>{css}</style>{body}"


class _TextFileTab(QWidget):
    """Editable file tab with optional read-only git diff vs HEAD."""

    _AUTO_SAVE_DELAY_MS = 450
    _DIAGNOSTICS_DELAY_MS = 500
    _DIFF_DELAY_MS = 200
    _MARKDOWN_DELAY_MS = 80

    dirty_changed = pyqtSignal(bool)
    diagnostic_fix_requested = pyqtSignal(str, object)
    language_context_changed = pyqtSignal()
    markdown_preview_pane_changed = pyqtSignal()

    def __init__(
        self,
        path: str,
        content: str,
        repo_root: str,
        diff_text: str | None,
        editable: bool = True,
        read_only_reason: str = "",
        auto_save: bool = False,
        tab_spaces: int = DEFAULT_FILE_EDITOR_TAB_SPACES,
        file_review_prompt: str = DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
        diagnostic_fix_prompt: str = DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE,
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
        self._markdown = self._file_backed and Path(path).suffix.lower() in _MARKDOWN_EXTS
        self._read_only_reason = read_only_reason
        self._diagnostics: list[Diagnostic] = []
        self._language_errors: list[str] = []
        self._completion_provider = LanguageCompletionProvider(self._repo_root)
        self._auto_save = auto_save
        self._tab_spaces = file_editor_tab_spaces({
            FILE_EDITOR_TAB_SPACES_KEY: tab_spaces,
        })
        self._file_review_prompt = file_review_prompt_template({
            FILE_REVIEW_PROMPT_TEMPLATE_KEY: file_review_prompt,
        })
        self._diagnostic_fix_prompt = diagnostic_fix_prompt_template({
            DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: diagnostic_fix_prompt,
        })
        self._dirty = False
        self._rendering = False
        self._edit_mode = False
        self._preview_pane_active = False
        self._force_text_view = False
        self._content_generation = 0
        self._editor_content_cache_revision: int | None = None
        self._editor_content_cache_text = ""
        self._find_cache_key: tuple[str, int] | None = None
        self._find_cache_text = ""
        self._find_cache_folded_text = ""
        self._find_match_cache: dict[str, list[int]] = {}
        self._diagnostics_generation = 0
        self._code_action_generation = 0
        self._diff_generation = 0
        self._markdown_generation = 0
        self._save_generation = 0
        self._saving = False
        self._worker_pool = QThreadPool(self)
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.timeout.connect(lambda: self._save(auto=True))
        self._diagnostics_timer = QTimer(self)
        self._diagnostics_timer.setSingleShot(True)
        self._diagnostics_timer.timeout.connect(self._start_diagnostics_refresh)
        self._diff_timer = QTimer(self)
        self._diff_timer.setSingleShot(True)
        self._diff_timer.timeout.connect(self._start_diff_refresh)
        self._markdown_timer = QTimer(self)
        self._markdown_timer.setSingleShot(True)
        self._markdown_timer.timeout.connect(self._apply_markdown_preview)
        self._pending_markdown: tuple[str, str] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._toolbar = QFrame()
        self._toolbar.setObjectName("fileEditorToolbar")
        bar = QHBoxLayout(self._toolbar)
        bar.setContentsMargins(8, 3, 8, 3)
        bar.setSpacing(8)
        self._diff_toggle = QCheckBox("Show changes")
        self._diff_toggle.setChecked(bool(diff_text))
        self._diff_toggle.setVisible(diff_text is not None)
        self._diff_toggle.toggled.connect(self._on_diff_toggled)
        bar.addWidget(self._diff_toggle)
        self._preview_toggle = QCheckBox("Show preview")
        self._preview_toggle.setToolTip(
            "Show the rendered Markdown preview in place of chat"
        )
        self._preview_toggle.setChecked(False)
        self._preview_toggle.setVisible(self._markdown)
        self._preview_toggle.toggled.connect(self._on_preview_toggled)
        bar.addWidget(self._preview_toggle)
        self._status = QLabel()
        self._status.setVisible(False)
        bar.addWidget(self._status)
        bar.addStretch(1)
        self._revert_btn = QPushButton("Revert")
        self._revert_btn.setToolTip("Reload this file from disk")
        self._revert_btn.setFixedHeight(24)
        self._revert_btn.clicked.connect(self._revert)
        bar.addWidget(self._revert_btn)
        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Save changes")
        self._save_btn.setFixedHeight(24)
        self._save_btn.clicked.connect(self._save)
        bar.addWidget(self._save_btn)
        root.addWidget(self._toolbar)

        self._find_bar = QFrame()
        find_layout = QHBoxLayout(self._find_bar)
        find_layout.setContentsMargins(8, 4, 8, 4)
        find_layout.setSpacing(6)
        self._find_query = QLineEdit()
        self._find_query.setPlaceholderText("Find in file")
        self._find_query.textChanged.connect(self._on_find_query_changed)
        self._find_query.returnPressed.connect(self._find_next)
        find_layout.addWidget(self._find_query, 1)
        self._find_status = QLabel()
        find_layout.addWidget(self._find_status)
        self._find_prev_btn = QPushButton("Prev")
        self._find_prev_btn.clicked.connect(lambda _checked=False: self._find_previous())
        find_layout.addWidget(self._find_prev_btn)
        self._find_next_btn = QPushButton("Next")
        self._find_next_btn.clicked.connect(lambda _checked=False: self._find_next())
        find_layout.addWidget(self._find_next_btn)
        self._find_close_btn = QPushButton("Close")
        self._find_close_btn.clicked.connect(lambda _checked=False: self._hide_find())
        find_layout.addWidget(self._find_close_btn)
        self._find_bar.hide()
        root.addWidget(self._find_bar)

        self._editor = _FileTextEdit()
        self._editor.set_tab_spaces(self._tab_spaces)
        self._editor.setFrameShape(QFrame.Shape.NoFrame)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.edit_requested.connect(self._enter_edit_mode)
        self._editor.cancel_requested.connect(self._cancel_edit)
        self._editor.diagnostic_fix_requested.connect(self._show_diagnostic_actions)
        self._editor.textChanged.connect(self._on_text_changed)
        self._preview = _MarkdownPreview()
        self._preview.edit_requested.connect(self._enter_edit_mode)
        self._preview.cancel_requested.connect(self._cancel_edit)
        self._minimap = _TextMinimap(self._editor)

        view = QSplitter(Qt.Orientation.Horizontal)
        view.setChildrenCollapsible(False)
        self._view_splitter = view
        view.addWidget(self._editor)
        view.addWidget(self._minimap)
        view.addWidget(self._preview)
        view.setStretchFactor(0, 1)
        view.setStretchFactor(2, 1)
        root.addWidget(view, 1)

        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self._save)
        self._cancel_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._cancel_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._cancel_shortcut.activated.connect(self._cancel_edit)
        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self._find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(self._show_find)

        self.apply_appearance()
        self._render(diagnostics_delay_ms=0)
        if diff_text is None:
            self._schedule_diff_refresh(delay_ms=0)

    def set_auto_save(self, enabled: bool):
        self._auto_save = enabled
        if enabled and self._dirty:
            self._schedule_auto_save()
        else:
            self._auto_save_timer.stop()
        self._sync_actions()

    def set_tab_spaces(self, spaces: int):
        self._tab_spaces = file_editor_tab_spaces({
            FILE_EDITOR_TAB_SPACES_KEY: spaces,
        })
        self._editor.set_tab_spaces(self._tab_spaces)

    def set_prompt_templates(self, file_review_prompt: str, diagnostic_fix_prompt: str):
        self._file_review_prompt = file_review_prompt_template({
            FILE_REVIEW_PROMPT_TEMPLATE_KEY: file_review_prompt,
        })
        self._diagnostic_fix_prompt = diagnostic_fix_prompt_template({
            DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: diagnostic_fix_prompt,
        })

    def apply_appearance(self):
        p = palette()
        meta = meta_font_pt()
        self._toolbar.setStyleSheet(
            f"QFrame#fileEditorToolbar {{ background:{p['BG2']};"
            f"border-bottom:1px solid {p['BORDER_SUBTLE']}; }}"
        )
        toggle_style = checkbox_style(
            font_pt=meta,
            indicator_px=14,
            spacing_px=6,
            text_color=p["TEXT_DIM"],
        )
        self._diff_toggle.setStyleSheet(toggle_style)
        self._preview_toggle.setStyleSheet(toggle_style)
        self._status.setStyleSheet(hint_label_style(selector="QLabel"))
        self._find_bar.setStyleSheet(
            f"QFrame {{ background:{p['BG2']}; border-top:1px solid {p['BORDER_SUBTLE']};"
            f"border-bottom:1px solid {p['BORDER_SUBTLE']}; }}"
        )
        self._find_query.setStyleSheet(compact_field_style(font_pt=meta))
        self._find_status.setStyleSheet(hint_label_style(selector="QLabel"))
        secondary = secondary_button_style(
            border_radius=4,
            padding="2px 10px",
            font_size=meta,
        )
        primary = primary_button_style(
            border_radius=4,
            padding="2px 10px",
            font_size=meta,
            font_weight="600",
        )
        self._revert_btn.setStyleSheet(secondary)
        self._save_btn.setStyleSheet(primary)
        self._find_prev_btn.setStyleSheet(secondary)
        self._find_next_btn.setStyleSheet(secondary)
        self._find_close_btn.setStyleSheet(secondary)
        self._editor.apply_appearance()
        self._preview.apply_appearance()
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
        self._content_generation += 1
        self._invalidate_editor_content_cache()
        self._invalidate_find_cache()
        self._diff_text = diff_text
        self._editable = self._file_backed and editable
        self._read_only_reason = read_only_reason
        self._set_dirty(False)
        self._edit_mode = False
        self._force_text_view = False
        self._diff_toggle.setChecked(bool(diff_text))
        self._diff_toggle.setVisible(diff_text is not None)
        self._preview_toggle.setVisible(self._markdown)
        if diff_text:
            self._preview_toggle.setChecked(False)
        self._render(diagnostics_delay_ms=0)
        if diff_text is None:
            self._schedule_diff_refresh(delay_ms=0)

    def _on_diff_toggled(self, checked: bool):
        if checked and self._markdown and self._preview_toggle.isChecked():
            self._preview_toggle.blockSignals(True)
            self._preview_toggle.setChecked(False)
            self._preview_toggle.blockSignals(False)
            self._invalidate_markdown_preview()
        if not checked and self._editable and self._edit_mode:
            self._editor.setFocus()
        self._render(diagnostics_delay_ms=0 if not checked else None)

    def _on_preview_toggled(self, checked: bool):
        if not self._markdown:
            return
        self._force_text_view = False
        if not checked:
            self._invalidate_markdown_preview()
        elif self._is_showing_diff():
            self._diff_toggle.blockSignals(True)
            self._diff_toggle.setChecked(False)
            self._diff_toggle.blockSignals(False)
        if not checked and self._editable and not self._is_showing_diff():
            self._edit_mode = True
        elif checked and self._editable and not self._is_showing_diff():
            self._edit_mode = True
        self._render()
        if not checked:
            self._editor.setFocus()

    def _enter_edit_mode(self):
        if not self._editable or self._is_showing_diff():
            return
        self._edit_mode = True
        self._force_text_view = False
        if self._markdown and self._preview_toggle.isChecked():
            self._render()
            self._editor.setFocus()
            return
        self._render()
        self._editor.setFocus()

    def _cancel_edit(self):
        if self._is_showing_diff():
            return
        if self._find_bar.isVisible():
            self._hide_find()
            return
        if not self._edit_mode:
            if self._markdown and self._preview_toggle.isChecked():
                self._preview_toggle.setChecked(False)
                return
            if self._force_text_view and self._markdown:
                self._force_text_view = False
                self._render()
            return
        if self._dirty and not self._auto_save:
            if self._markdown:
                self._preview_toggle.setChecked(True)
            self._revert()
            return
        if self._markdown:
            if not self._preview_toggle.isChecked():
                self._preview_toggle.setChecked(True)
                return
            self._edit_mode = True
            self._editor.setFocus()
            return
        self._edit_mode = False
        self._render()

    def is_markdown_preview_pane_active(self) -> bool:
        return self._is_markdown_preview()

    def markdown_preview_widget(self) -> _MarkdownPreview:
        return self._preview

    def preview_is_in_tab(self) -> bool:
        return self._preview.parent() is self._view_splitter

    def take_preview_for_pane(self, host: QWidget) -> _MarkdownPreview:
        layout = host.layout()
        if layout is None:
            raise RuntimeError("preview host requires a layout")
        if self._preview.parent() is not host:
            self._preview.setParent(host)
        if layout.indexOf(self._preview) < 0:
            layout.addWidget(self._preview)
        if self._is_markdown_preview() and not self._preview_has_content():
            self._ensure_markdown_preview()
        self._preview.show()
        return self._preview

    def restore_preview_to_tab(self):
        if self.preview_is_in_tab():
            self._preview.hide()
            return
        host = self._preview.parent()
        host_layout = host.layout() if host is not None else None
        if host_layout is not None:
            host_layout.removeWidget(self._preview)
        if self._view_splitter.indexOf(self._preview) < 0:
            self._view_splitter.insertWidget(2, self._preview)
        self._preview.hide()

    def _preview_has_content(self) -> bool:
        return bool(self._preview.toPlainText().strip())

    def _notify_markdown_preview_pane_changed(self):
        active = self.is_markdown_preview_pane_active()
        if active and not self._preview_has_content():
            self._ensure_markdown_preview()
        if active == self._preview_pane_active:
            return
        self._preview_pane_active = active
        self.markdown_preview_pane_changed.emit()

    def _on_text_changed(self):
        self._invalidate_editor_content_cache()
        self._invalidate_find_cache()
        if (
            self._rendering
            or not self._editable
            or not self._edit_mode
            or self._is_showing_diff()
        ):
            return
        self._set_dirty(True)
        if self._markdown and self._preview_toggle.isChecked():
            self._schedule_markdown_preview()
        if self._auto_save:
            self._schedule_auto_save()
            return
        self._sync_actions()

    def _is_showing_diff(self) -> bool:
        return self._diff_toggle.isChecked() and bool(self._diff_text)

    def _is_markdown_preview(self) -> bool:
        return (
            self._markdown
            and self._preview_toggle.isChecked()
            and not self._force_text_view
            and not self._is_showing_diff()
        )

    def _render(self, *, diagnostics_delay_ms: int | None = None):
        content = self._current_editor_content()
        self._rendering = True
        self._editor.blockSignals(True)
        self._editor.configure_syntax(self._lang_hint, content)
        self._completion_provider = LanguageCompletionProvider(self._repo_root)
        self._editor.configure_completion(self._path, self._completion_provider)
        self._editor.configure_reference(self._path, self._repo_root)
        if not self._dirty and self._editor.toPlainText() != self._content:
            self._editor.setPlainText(self._content)
            self._invalidate_editor_content_cache()
        if self._is_markdown_preview():
            self._schedule_markdown_preview()
            self._editor.show()
            self._minimap.show()
            if self.preview_is_in_tab():
                self._preview.hide()
            self._editor.setReadOnly(not self._editable)
            if self._editable:
                self._edit_mode = True
            self._editor.set_changed_lines(set())
            self._set_diagnostics([])
        elif self._is_showing_diff():
            self._preview.hide()
            self._editor.show()
            self._minimap.show()
            self._editor.setReadOnly(True)
            self._editor.set_changed_lines(changed_new_line_numbers(self._diff_text or ""))
            self._set_diagnostics([])
        else:
            self._preview.hide()
            self._editor.show()
            self._minimap.show()
            self._editor.set_changed_lines(set())
            self._editor.setReadOnly(not (self._editable and self._edit_mode))
            self._refresh_diagnostics(delay_ms=diagnostics_delay_ms)
        self._editor.blockSignals(False)
        self._rendering = False
        self._sync_actions()
        self._minimap.update()
        self._notify_markdown_preview_pane_changed()

    def goto_line(self, line_no: int, column: int = 0):
        self._show_editor_for_navigation()
        self._editor.goto_line(line_no, column)

    def _show_find(self):
        self._show_editor_for_navigation()
        self._find_bar.show()
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            self._find_query.setText(cursor.selectedText())
        self._find_query.setFocus()
        self._find_query.selectAll()
        self._find_next(wrap=False)

    def _hide_find(self):
        self._find_bar.hide()
        self._find_status.clear()
        if self._force_text_view and self._markdown and not self._edit_mode:
            self._force_text_view = False
            self._render()
        else:
            self._editor.setFocus()

    def _show_editor_for_navigation(self):
        if self._is_showing_diff():
            self._diff_toggle.setChecked(False)
        if self._is_markdown_preview():
            if self._editable:
                self._edit_mode = True
            else:
                self._force_text_view = True
            self._render()

    def _on_find_query_changed(self, _text: str):
        self._find_match(forward=True, wrap=False, restart=True)

    def _find_next(self, *, wrap: bool = True):
        self._find_match(forward=True, wrap=wrap, restart=False)

    def _find_previous(self):
        self._find_match(forward=False, wrap=True, restart=False)

    def _find_match(self, *, forward: bool, wrap: bool, restart: bool):
        query = self._find_query.text()
        if not query:
            self._find_status.clear()
            return
        _text, folded_text = self._find_text()
        folded_query = query.casefold()
        cursor = self._editor.textCursor()
        if forward:
            if restart:
                start = 0
            else:
                start = cursor.selectionEnd() if cursor.hasSelection() else cursor.position()
            pos = folded_text.find(folded_query, start)
            if pos < 0 and wrap:
                pos = folded_text.find(folded_query)
        else:
            start = cursor.selectionStart() if cursor.hasSelection() else cursor.position()
            pos = folded_text.rfind(folded_query, 0, start)
            if pos < 0 and wrap:
                pos = folded_text.rfind(folded_query)
        if pos < 0:
            self._find_status.setText("No matches")
            return
        self._editor.select_range(pos, len(query), focus=False)
        self._find_query.setFocus()
        self._find_status.setText(self._find_match_status(folded_query, pos))

    def _find_text(self) -> tuple[str, str]:
        if self._edit_mode:
            key = ("editor", self._editor.document().revision())
        else:
            key = ("content", self._content_generation)
        if key != self._find_cache_key:
            text = self._editor_content_snapshot() if self._edit_mode else self._content
            self._find_cache_key = key
            self._find_cache_text = text
            self._find_cache_folded_text = text.casefold()
            self._find_match_cache = {}
        return self._find_cache_text, self._find_cache_folded_text

    def _find_positions(self, folded_query: str) -> list[int]:
        positions = self._find_match_cache.get(folded_query)
        if positions is not None:
            return positions
        _text, folded_text = self._find_text()
        positions = []
        start = folded_text.find(folded_query)
        while start >= 0:
            positions.append(start)
            start = folded_text.find(folded_query, start + max(1, len(folded_query)))
        self._find_match_cache[folded_query] = positions
        return positions

    def _find_match_status(self, folded_query: str, pos: int) -> str:
        positions = self._find_positions(folded_query)
        if not positions:
            return "No matches"
        current = positions.index(pos) + 1 if pos in positions else 1
        return f"{current} of {len(positions)}"

    def _invalidate_find_cache(self):
        self._find_cache_key = None
        self._find_cache_text = ""
        self._find_cache_folded_text = ""
        self._find_match_cache = {}

    def _invalidate_editor_content_cache(self):
        self._editor_content_cache_revision = None
        self._editor_content_cache_text = ""

    def _editor_content_snapshot(self) -> str:
        revision = self._editor.document().revision()
        if self._editor_content_cache_revision != revision:
            self._editor_content_cache_text = self._editor.toPlainText()
            self._editor_content_cache_revision = revision
        return self._editor_content_cache_text

    def _save(self, *, auto: bool = False):
        if not self._editable or self._is_showing_diff():
            return
        if not auto:
            self._auto_save_timer.stop()
        path = Path(self._path)
        if not path_in_repo(path, self._repo_root):
            self._set_dirty(True)
            self._set_status("Save blocked: outside workspace")
            return
        text = self._current_editor_content() if self._edit_mode or self._dirty else self._content
        self._save_generation += 1
        generation = self._save_generation
        self._saving = True
        self._set_status("Auto-saving..." if auto else "Saving...")
        self._sync_actions()
        worker = _FileSaveWorker(generation, str(path), text, auto)
        worker.signals.done.connect(self._on_save_ready)
        self._worker_pool.start(worker)

    def _on_save_ready(
        self,
        generation: int,
        path: str,
        text: str,
        ok: bool,
        error: str,
        auto: bool,
    ):
        if generation != self._save_generation or path != self._path:
            return
        self._saving = False
        if not ok:
            self._set_dirty(True)
            self._set_status(f"Save failed: {error}")
            self._sync_actions()
            return
        self._content = text
        self._content_generation += 1
        self._invalidate_editor_content_cache()
        self._invalidate_find_cache()
        self._set_dirty(False)
        if not auto:
            self._edit_mode = False
            if self._markdown:
                self._preview_toggle.setChecked(True)
        self._set_status("Auto-saved" if auto else "Saved")
        self._schedule_diff_refresh(delay_ms=0)
        if auto:
            self._refresh_diagnostics(delay_ms=self._DIAGNOSTICS_DELAY_MS)
            self._sync_actions()
        else:
            self._render(diagnostics_delay_ms=0)

    def _revert(self):
        if not self._file_backed or self._is_showing_diff():
            return
        try:
            content, truncated, decode_error, blocked_preview = _read_text_preview_details(self._path)
        except OSError as e:
            self._content = f"[Could not read file: {e}]"
            self._content_generation += 1
            self._invalidate_editor_content_cache()
            self._invalidate_find_cache()
            self._editable = False
            self._read_only_reason = f"Could not read file: {e}"
        else:
            self._content = content
            self._content_generation += 1
            self._invalidate_editor_content_cache()
            self._invalidate_find_cache()
            self._editable = not truncated and not decode_error and not blocked_preview
            self._read_only_reason = _read_only_reason(truncated, decode_error, blocked_preview)
        self._edit_mode = False
        self._set_dirty(False)
        if self._markdown:
            self._preview_toggle.setChecked(True)
        self._render(diagnostics_delay_ms=0)
        self._schedule_diff_refresh(delay_ms=0)
        self._set_status("Reverted" if self._editable else self._read_only_reason)

    def _sync_actions(self):
        showing_diff = self._is_showing_diff()
        show_file_actions = self._file_backed
        self._revert_btn.setVisible(show_file_actions)
        self._save_btn.setVisible(show_file_actions)
        self._preview_toggle.setVisible(self._markdown)
        self._preview_toggle.setEnabled(self._markdown and not self._saving)
        self._revert_btn.setEnabled(show_file_actions and self._dirty and not showing_diff and not self._saving)
        self._save_btn.setEnabled(
            self._editable and self._dirty and not showing_diff and not self._auto_save and not self._saving
        )
        if not self._editable and self._read_only_reason:
            self._set_status(self._read_only_reason)
        elif self._saving:
            if self._status.text() not in ("Saving...", "Auto-saving..."):
                self._set_status("Saving...")
        elif showing_diff:
            if self._status.text() not in ("Saved", "Auto-saved"):
                self._set_status("")
        elif self._dirty:
            if self._status.text() in ("", "Markdown preview", "Formatted view", "Saved", "Auto-saved"):
                self._set_status("Unsaved changes")
        elif self._is_markdown_preview():
            if self._status.text() not in ("Saved", "Auto-saved"):
                self._set_status("")
        elif (
            self._diagnostics
            and not self._dirty
            and self._status.text() not in ("Saved", "Auto-saved")
        ):
            self._set_status(_diagnostic_summary(self._diagnostics))
        elif (
            self._editable
            and not self._edit_mode
            and not self._dirty
            and self._status.text() not in ("Saved", "Auto-saved")
        ):
            self._set_status("")
        elif not self._dirty and self._status.text() not in ("Saved", "Auto-saved"):
            self._set_status("")

    def _set_status(self, text: str):
        self._status.setText(text)
        self._status.setVisible(bool(text))

    def _schedule_auto_save(self):
        self._auto_save_timer.start(self._AUTO_SAVE_DELAY_MS)

    def _schedule_markdown_preview(self, delay_ms: int | None = None):
        self._pending_markdown = (self._markdown_preview_source(), self._path)
        self._markdown_timer.start(
            self._MARKDOWN_DELAY_MS if delay_ms is None else max(0, delay_ms)
        )

    def _markdown_preview_source(self) -> str:
        text = self._current_editor_content()
        if text.strip():
            return text
        if self._content.strip():
            return self._content
        return text

    def _ensure_markdown_preview(self) -> None:
        if not self._is_markdown_preview():
            return
        self._markdown_timer.stop()
        self._pending_markdown = (self._markdown_preview_source(), self._path)
        self._apply_markdown_preview()

    def _apply_markdown_preview(self):
        if self._pending_markdown is None:
            return
        text, path = self._pending_markdown
        self._pending_markdown = None
        self._markdown_generation += 1
        generation = self._markdown_generation
        worker = _MarkdownPreviewWorker(
            generation,
            path,
            text,
            current_theme(),
            chat_font_pt(),
        )
        worker.signals.done.connect(self._on_markdown_preview_ready)
        self._worker_pool.start(worker)

    def _on_markdown_preview_ready(self, generation: int, path: str, html: str):
        if generation != self._markdown_generation:
            return
        if path != self._path or not self._is_markdown_preview():
            return
        self._preview.set_html(html, path)
        if self.is_markdown_preview_pane_active() and not self._preview_pane_active:
            self._preview_pane_active = True
            self.markdown_preview_pane_changed.emit()

    def _invalidate_markdown_preview(self):
        self._markdown_timer.stop()
        self._pending_markdown = None
        self._markdown_generation += 1

    def _refresh_diagnostics(self, delay_ms: int | None = None):
        if not self._file_backed or self._is_showing_diff():
            self._set_diagnostics([])
            return
        self._diagnostics_timer.start(
            self._DIAGNOSTICS_DELAY_MS if delay_ms is None else max(0, delay_ms)
        )

    def _start_diagnostics_refresh(self):
        if not self._file_backed or self._is_showing_diff():
            self._set_diagnostics([])
            return
        self._diagnostics_generation += 1
        generation = self._diagnostics_generation
        content = self._current_editor_content()
        worker = _DiagnosticsWorker(generation, self._repo_root, self._path, content)
        worker.signals.done.connect(self._on_diagnostics_ready)
        self._worker_pool.start(worker)

    def _on_diagnostics_ready(self, generation: int, diagnostics, errors):
        if generation != self._diagnostics_generation:
            return
        if self._is_showing_diff():
            self._set_diagnostics([])
            return
        self._language_errors = list(errors or [])
        self._set_diagnostics(list(diagnostics or []))
        self._sync_actions()

    def _schedule_diff_refresh(self, delay_ms: int | None = None):
        if not self._file_backed:
            return
        self._diff_timer.start(
            self._DIFF_DELAY_MS if delay_ms is None else max(0, delay_ms)
        )

    def _start_diff_refresh(self):
        if not self._file_backed:
            return
        self._diff_generation += 1
        generation = self._diff_generation
        worker = _DiffWorker(generation, self._repo_root, self._path)
        worker.signals.done.connect(self._on_diff_ready)
        self._worker_pool.start(worker)

    def _on_diff_ready(self, generation: int, diff_text):
        if generation != self._diff_generation or not self._file_backed:
            return
        was_showing = self._is_showing_diff()
        had_diff = self._diff_text is not None
        self._diff_text = diff_text
        has_diff = self._diff_text is not None
        self._diff_toggle.setVisible(has_diff)
        if not has_diff and self._diff_toggle.isChecked():
            self._diff_toggle.setChecked(False)
        elif was_showing and has_diff:
            self._render()
        if has_diff != had_diff or was_showing:
            self._sync_actions()

    def _set_diagnostics(self, diagnostics: list[Diagnostic]):
        self._diagnostics = list(diagnostics)
        self._editor.set_diagnostics(self._diagnostics)
        self.language_context_changed.emit()

    def _current_editor_content(self) -> str:
        return self._editor_content_snapshot() if self._edit_mode or self._dirty else self._content

    def _show_diagnostic_actions(self, diagnostics: list[Diagnostic]):
        if not diagnostics:
            return
        self._request_code_actions(diagnostics, safe_only=False)

    def _request_code_actions(self, diagnostics: list[Diagnostic], *, safe_only: bool):
        if not self._editable or self._is_showing_diff():
            self._set_status("Code action unavailable")
            return
        self._code_action_generation += 1
        generation = self._code_action_generation
        self._set_status("Finding safe fixes..." if safe_only else "Finding fixes...")
        worker = _CodeActionListWorker(
            generation,
            self._repo_root,
            self._path,
            self._current_editor_content(),
            diagnostics,
        )
        worker.signals.done.connect(
            lambda gen, actions, errors, captured=list(diagnostics): (
                self._on_code_actions_ready(gen, actions, errors, captured, safe_only=safe_only)
            )
        )
        self._worker_pool.start(worker)

    def _on_code_actions_ready(
        self,
        generation: int,
        actions,
        errors,
        diagnostics: list[Diagnostic],
        *,
        safe_only: bool,
    ):
        if generation != self._code_action_generation:
            return
        self._language_errors = list(errors or [])
        action_list = list(actions or [])
        if safe_only:
            self._show_safe_code_action_choices(action_list, diagnostics)
        else:
            self._show_diagnostic_action_choices(action_list, diagnostics)

    def _show_diagnostic_action_choices(
        self,
        actions: list[CodeAction],
        diagnostics: list[Diagnostic],
    ):
        if not actions:
            self._draft_diagnostic_fix(diagnostics)
            return

        menu = QMenu(self)
        action_items = {}
        for action in actions:
            label = action.title if action.safe else f"{action.title} (unsafe)"
            item = menu.addAction(label)
            item.setEnabled(action.safe)
            action_items[item] = action
        menu.addSeparator()
        ask_chat = menu.addAction("Ask chat to fix")

        selected = menu.exec(QCursor.pos())
        if selected == ask_chat:
            self._draft_diagnostic_fix(diagnostics)
        elif selected in action_items:
            self._run_code_action(action_items[selected], diagnostics)

    def _show_safe_code_actions(self):
        diagnostics = list(self._diagnostics)
        if not diagnostics:
            self._set_status("No problems to fix")
            return
        self._request_code_actions(diagnostics, safe_only=True)

    def _show_safe_code_action_choices(
        self,
        actions: list[CodeAction],
        diagnostics: list[Diagnostic],
    ):
        safe_actions = [action for action in actions if action.safe]
        if not safe_actions:
            self._set_status("No safe fixes available")
            return
        if len(safe_actions) == 1:
            self._run_code_action(safe_actions[0], diagnostics)
            return

        menu = QMenu(self)
        action_items = {}
        for action in safe_actions:
            label = action.title
            if action.source:
                label = f"{label} - {action.source}"
            item = menu.addAction(label)
            action_items[item] = action
        selected = menu.exec(QCursor.pos())
        if selected in action_items:
            self._run_code_action(action_items[selected], diagnostics)

    def _run_code_action(self, action: CodeAction, diagnostics: list[Diagnostic]):
        if not self._editable or self._is_showing_diff():
            self._set_status("Code action unavailable")
            return
        if not action.safe:
            self._set_status("Unsafe code action skipped")
            return
        self._code_action_generation += 1
        generation = self._code_action_generation
        self._set_status(f"Running {action.title}...")
        worker = _CodeActionWorker(
            generation,
            self._repo_root,
            self._path,
            self._current_editor_content(),
            action.id,
            diagnostics,
        )
        worker.signals.done.connect(self._on_code_action_ready)
        self._worker_pool.start(worker)

    def _format_document(self):
        if not self._editable or self._is_showing_diff():
            self._set_status("Format unavailable")
            return
        self._code_action_generation += 1
        generation = self._code_action_generation
        self._set_status("Formatting...")
        worker = _FormatDocumentWorker(
            generation,
            self._repo_root,
            self._path,
            self._current_editor_content(),
        )
        worker.signals.done.connect(self._on_code_action_ready)
        self._worker_pool.start(worker)

    def _on_code_action_ready(self, generation: int, result, errors):
        if generation != self._code_action_generation:
            return
        if errors:
            self._language_errors = list(errors)
        if not isinstance(result, CodeActionResult):
            result = CodeActionResult(message=str(result or "Code action returned no result."))
        self._apply_code_action_content(result.content, result.message)

    def _apply_code_action_content(self, content: str | None, message: str = ""):
        if not self._editable or self._is_showing_diff():
            self._set_status("Code action unavailable")
            return
        if content is None:
            self._set_status(message or "Code action made no changes")
            return
        content = _normalize_editor_newlines(content)
        if content == self._current_editor_content():
            self._set_status(message or "Code action made no changes")
            self._refresh_diagnostics(delay_ms=0)
            return

        self._edit_mode = True
        self._force_text_view = False
        self._content = content
        self._content_generation += 1
        self._invalidate_editor_content_cache()
        self._invalidate_find_cache()
        self._preview.hide()
        self._editor.show()
        self._minimap.show()
        self._editor.setReadOnly(False)
        self._editor.blockSignals(True)
        self._editor.setPlainText(content)
        self._editor.configure_syntax(self._lang_hint, content)
        self._editor.blockSignals(False)
        self._set_dirty(True)
        if message:
            self._set_status(message)
        if self._auto_save:
            self._schedule_auto_save()
        self._refresh_diagnostics(delay_ms=0)
        self._sync_actions()

    def _draft_diagnostic_fix(self, diagnostics: list[Diagnostic]):
        self._draft_diagnostic_fix_prompt(diagnostics, fix_all=False)

    def _draft_all_diagnostic_fixes(self, diagnostics: list[Diagnostic]):
        self._draft_diagnostic_fix_prompt(diagnostics, fix_all=True)

    def _draft_diagnostic_fix_prompt(self, diagnostics: list[Diagnostic], *, fix_all: bool):
        if not diagnostics:
            return
        rel_path = _relative_file_reference(self._path, self._repo_root)
        line = max(1, diagnostics[0].line)
        mention = editor_ref_text([{
            "path": rel_path,
            "start_line": line,
            "end_line": line,
        }])
        tool = _diagnostic_tool_label(diagnostics)
        prompt_tool = _diagnostic_source_label(diagnostics) if fix_all else tool
        details = _diagnostic_details(diagnostics)
        file_mention = _file_mention_text(rel_path)
        command = _diagnostic_tool_command(prompt_tool, rel_path, file_mention)
        default_prompt = (
            DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE
            if fix_all
            else "Fix this {tool} issue in {mention}."
        )
        template = self._diagnostic_fix_prompt
        if not fix_all and template == DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE:
            template = default_prompt
        first_line = _format_prompt_template(
            template,
            {
                "mention": mention,
                "path": rel_path,
                "line": str(line),
                "tool": prompt_tool,
                "file": file_mention,
                "command": command,
            },
            default_prompt,
        )
        if fix_all:
            self.diagnostic_fix_requested.emit(first_line, [rel_path])
            return
        prompt = (
            f"{first_line}\n\n"
            f"Diagnostic tool: {tool}\n"
            f"Diagnostic output:\n{details}"
        )
        self.diagnostic_fix_requested.emit(prompt, [rel_path])

    def _draft_file_question(self):
        rel_path = _relative_file_reference(self._path, self._repo_root)
        mention = _file_mention_text(rel_path)
        first_line = _format_prompt_template(
            self._file_review_prompt,
            {
                "mention": mention,
                "path": rel_path,
            },
            DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE,
        )
        prompt = (
            f"{first_line}\n\n"
            "Summarize what this file does, point out any risks you notice, "
            "and suggest the most useful next actions."
        )
        self.diagnostic_fix_requested.emit(prompt, [rel_path])

    def _set_dirty(self, dirty: bool):
        if self._dirty == dirty:
            if dirty:
                self._set_status("Unsaved changes")
            return
        self._dirty = dirty
        self.dirty_changed.emit(dirty)
        if dirty:
            self._set_status("Unsaved changes")

    def release_resources(self):
        for timer in (
            self._auto_save_timer,
            self._diagnostics_timer,
            self._diff_timer,
            self._markdown_timer,
        ):
            timer.stop()
        self._diagnostics_generation += 1
        self._code_action_generation += 1
        self._diff_generation += 1
        self._markdown_generation += 1
        self._save_generation += 1
        self._saving = False
        _retire_worker_pool(self._worker_pool)
        self._worker_pool = QThreadPool(self)
        self._editor.release_resources()

    def closeEvent(self, event):
        self.release_resources()
        super().closeEvent(event)


class _FileViewerTabBar(QTabBar):
    def __init__(self, repo_root_getter, parent=None):
        super().__init__(parent)
        self._repo_root_getter = repo_root_getter
        self._drag_start_pos = None
        self._drag_start_index = -1

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = _event_pos(event)
            index = self.tabAt(pos)
            self._drag_start_pos = pos
            self._drag_start_index = index if self.mime_data_for_tab(index) is not None else -1
        else:
            self._clear_drag_start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start_index >= 0
            and self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (_event_pos(event) - self._drag_start_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            mime = self.mime_data_for_tab(self._drag_start_index)
            if mime is not None:
                drag = QDrag(self)
                drag.setMimeData(mime)
                self._clear_drag_start()
                drag.exec(Qt.DropAction.CopyAction)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._clear_drag_start()
        super().mouseReleaseEvent(event)

    def mime_data_for_tab(self, index: int) -> QMimeData | None:
        if index < 0 or index >= self.count():
            return None
        path = str(self.tabData(index) or "")
        ref = _file_drop_ref(path, str(self._repo_root_getter() or ""))
        if not ref:
            return None
        mime = QMimeData()
        mime.setData(AICHS_FILE_DROP_MIME, file_drop_payload([ref]))
        mime.setText(file_drop_text([ref]))
        return mime

    def _clear_drag_start(self):
        self._drag_start_pos = None
        self._drag_start_index = -1


class FileViewerPanel(QWidget):
    all_closed = pyqtSignal()
    diagnostic_fix_requested = pyqtSignal(str, object)
    active_file_changed = pyqtSignal(str)
    dirty_file_changed = pyqtSignal(str, bool)
    language_context_changed = pyqtSignal(object)
    markdown_preview_pane_changed = pyqtSignal(bool)

    def __init__(self, repo_root: str = "", settings=None, parent=None):
        super().__init__(parent)
        self._repo_root = repo_root or os.getcwd()
        self._settings = settings
        self._recently_closed_files: list[tuple[str, int | None]] = []
        self._auto_save = self._load_auto_save()
        self._tab_spaces = self._load_tab_spaces()
        self._file_review_prompt = self._load_file_review_prompt_template()
        self._diagnostic_fix_prompt = self._load_diagnostic_fix_prompt_template()
        self._file_read_generation = 0
        self._pending_file_reads: dict[str, int] = {}
        self._worker_pool = QThreadPool.globalInstance()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setTabBar(_FileViewerTabBar(lambda: self._repo_root))
        self._tabs.setDocumentMode(True)
        self._tabs.setTabsClosable(True)
        tab_bar = self._tabs.tabBar()
        tab_bar.setUsesScrollButtons(True)
        tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        tab_bar.setExpanding(False)
        self._tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tabs.currentChanged.connect(self._on_current_tab_changed)

        root.addWidget(self._tabs)
        self._apply_tab_style()

    def set_repo_root(self, path: str):
        new_root = path or os.getcwd()
        if os.path.abspath(new_root) != os.path.abspath(self._repo_root):
            self._recently_closed_files.clear()
        self._repo_root = new_root
        self._emit_language_context_changed()

    def open_paths(self) -> list[str]:
        paths: list[str] = []
        tab_bar = self._tabs.tabBar()
        for i in range(self._tabs.count()):
            key = str(tab_bar.tabData(i) or "")
            if key and not key.startswith("\0"):
                paths.append(key)
        return paths

    def active_path(self) -> str:
        index = self._tabs.currentIndex()
        if index < 0:
            return ""
        key = str(self._tabs.tabBar().tabData(index) or "")
        return "" if key.startswith("\0") else key

    def open_file_states(self) -> list[dict]:
        active = self.active_path()
        states: list[dict] = []
        tab_bar = self._tabs.tabBar()
        for i in range(self._tabs.count()):
            key = str(tab_bar.tabData(i) or "")
            if not key or key.startswith("\0"):
                continue
            line_no = 1
            widget = self._tabs.widget(i)
            if isinstance(widget, _TextFileTab):
                line_no = widget._editor.textCursor().blockNumber() + 1
            states.append({
                "path": key,
                "line": line_no,
                "active": key == active,
            })
        return states

    def restore_open_files(
        self,
        entries: list[dict],
        *,
        repo_root: str | None = None,
    ) -> list[str]:
        if repo_root:
            self.set_repo_root(repo_root)
        skipped: list[str] = []
        active_path = ""
        active_line: int | None = None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip()
            if not path:
                continue
            resolved = os.path.abspath(
                path if os.path.isabs(path) else os.path.join(self._repo_root, path),
            )
            if not os.path.exists(resolved):
                skipped.append(path)
                continue
            try:
                line_no = max(1, int(entry.get("line") or 1))
            except (TypeError, ValueError):
                line_no = 1
            self.open_file(resolved, repo_root=self._repo_root, line_no=line_no)
            if bool(entry.get("active")):
                active_path = resolved
                active_line = line_no
        if active_path:
            self.open_file(active_path, repo_root=self._repo_root, line_no=active_line)
        elif entries:
            first = str(entries[0].get("path") or "").strip()
            if first:
                resolved = os.path.abspath(
                    first if os.path.isabs(first) else os.path.join(self._repo_root, first),
                )
                if os.path.exists(resolved):
                    try:
                        line_no = max(1, int(entries[0].get("line") or 1))
                    except (TypeError, ValueError):
                        line_no = 1
                    self.open_file(resolved, repo_root=self._repo_root, line_no=line_no)
        return skipped

    def active_text_tab(self) -> _TextFileTab | None:
        return self._active_text_tab()

    def active_markdown_preview_pane_active(self) -> bool:
        widget = self._active_text_tab()
        if widget is None:
            return False
        return widget.is_markdown_preview_pane_active()

    def active_language_context(self) -> dict:
        widget = self._active_text_tab()
        if widget is None:
            return {
                "repo_root": self._repo_root,
                "path": self.active_path(),
                "is_text": False,
                "diagnostics": [],
                "errors": [],
            }
        return {
            "repo_root": self._repo_root,
            "path": widget._path,
            "is_text": True,
            "diagnostics": list(widget._diagnostics),
            "errors": list(widget._language_errors),
            "editable": bool(widget._editable and not widget._is_showing_diff()),
        }

    def has_open_tabs(self) -> bool:
        return self._tabs.count() > 0

    def refresh_active_language(self):
        widget = self._active_text_tab()
        if widget is None:
            self._emit_language_context_changed()
            return
        widget._refresh_diagnostics(delay_ms=0)
        self._emit_language_context_changed()

    def show_active_language_actions(self, diagnostics: list[Diagnostic]):
        widget = self._active_text_tab()
        if widget is not None:
            widget._show_diagnostic_actions(list(diagnostics or []))

    def format_active_language(self):
        widget = self._active_text_tab()
        if widget is not None:
            widget._format_document()

    def fix_safe_active_language(self):
        widget = self._active_text_tab()
        if widget is not None:
            widget._show_safe_code_actions()

    def draft_active_language_file_question(self):
        widget = self._active_text_tab()
        if widget is not None:
            widget._draft_file_question()

    def draft_active_language_fix(self, diagnostics: list[Diagnostic]):
        widget = self._active_text_tab()
        if widget is not None:
            widget._draft_diagnostic_fix(list(diagnostics or []))

    def draft_active_language_fix_all(self, diagnostics: list[Diagnostic]):
        widget = self._active_text_tab()
        if widget is not None:
            widget._draft_all_diagnostic_fixes(list(diagnostics or []))

    def reload_settings(self):
        self.set_auto_save(self._load_auto_save())
        self.set_tab_spaces(self._load_tab_spaces())
        self.set_prompt_templates(
            self._load_file_review_prompt_template(),
            self._load_diagnostic_fix_prompt_template(),
        )

    def set_auto_save(self, enabled: bool):
        self._auto_save = enabled
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if isinstance(widget, _TextFileTab):
                widget.set_auto_save(enabled)
                self._sync_tab_title(widget)

    def set_tab_spaces(self, spaces: int):
        self._tab_spaces = file_editor_tab_spaces({
            FILE_EDITOR_TAB_SPACES_KEY: spaces,
        })
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if isinstance(widget, _TextFileTab):
                widget.set_tab_spaces(self._tab_spaces)

    def set_prompt_templates(self, file_review_prompt: str, diagnostic_fix_prompt: str):
        self._file_review_prompt = file_review_prompt_template({
            FILE_REVIEW_PROMPT_TEMPLATE_KEY: file_review_prompt,
        })
        self._diagnostic_fix_prompt = diagnostic_fix_prompt_template({
            DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY: diagnostic_fix_prompt,
        })
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if isinstance(widget, _TextFileTab):
                widget.set_prompt_templates(
                    self._file_review_prompt,
                    self._diagnostic_fix_prompt,
                )

    def apply_appearance(self):
        self._apply_tab_style()
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if isinstance(widget, _TextFileTab):
                widget.apply_appearance()
            elif isinstance(widget, _ImageViewer):
                widget.apply_appearance()

    def _apply_tab_style(self):
        self._tabs.setObjectName("fileViewerTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setDrawBase(False)
        self._tabs.setStyleSheet(file_tab_style())

    def _find_tab(self, key: str) -> int:
        tab_bar = self._tabs.tabBar()
        for i in range(self._tabs.count()):
            if tab_bar.tabData(i) == key:
                return i
        return -1

    def _active_text_tab(self) -> _TextFileTab | None:
        widget = self._tabs.currentWidget()
        path = self.active_path()
        if isinstance(widget, _TextFileTab) and path:
            return widget
        return None

    def _emit_language_context_changed(self):
        self.language_context_changed.emit(self.active_language_context())

    def _emit_markdown_preview_pane_changed(self):
        self.markdown_preview_pane_changed.emit(
            self.active_markdown_preview_pane_active()
        )

    def _emit_language_context_for(self, widget: QWidget):
        if widget is self._tabs.currentWidget():
            self._emit_language_context_changed()

    def _add_tab_widget(self, key: str, title: str, widget: QWidget):
        idx = self._tabs.addTab(widget, title)
        self._tabs.tabBar().setTabData(idx, key)
        widget.setProperty("_base_tab_title", title)
        if isinstance(widget, _TextFileTab):
            widget.dirty_changed.connect(
                lambda dirty, w=widget: self._on_tab_dirty_changed(w, dirty)
            )
            widget.diagnostic_fix_requested.connect(self.diagnostic_fix_requested.emit)
            widget.language_context_changed.connect(
                lambda w=widget: self._emit_language_context_for(w)
            )
            widget.markdown_preview_pane_changed.connect(
                self._emit_markdown_preview_pane_changed
            )
            self._sync_tab_title(widget)
        self._tabs.setCurrentIndex(idx)
        self._emit_language_context_changed()
        if isinstance(widget, _TextFileTab):
            self._emit_markdown_preview_pane_changed()

    def _add_text_tab(self, key: str, title: str, content: str):
        tab = _TextFileTab(
            key,
            content,
            self._repo_root,
            diff_text=None,
            editable=False,
            auto_save=self._auto_save,
            tab_spaces=self._tab_spaces,
            file_review_prompt=self._file_review_prompt,
            diagnostic_fix_prompt=self._diagnostic_fix_prompt,
        )
        self._add_tab_widget(key, title, tab)

    def open_file(
        self,
        path: str,
        repo_root: str | None = None,
        diff_text: str | None = None,
        line_no: int | None = None,
    ):
        if repo_root:
            self._repo_root = repo_root
        path = os.path.abspath(path)

        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_EXTS:
            idx = self._find_tab(path)
            if idx >= 0:
                self._tabs.setCurrentIndex(idx)
                self._emit_language_context_changed()
                return
            self._add_tab_widget(path, os.path.basename(path), _ImageViewer(path))
            return

        idx = self._find_tab(path)
        if self._should_read_text_async(path):
            if idx >= 0:
                widget = self._tabs.widget(idx)
                if isinstance(widget, _TextFileTab):
                    widget.update_content(
                        _LOADING_FILE_TEXT,
                        None,
                        editable=False,
                        read_only_reason=_LOADING_FILE_TEXT,
                    )
                self._tabs.setCurrentIndex(idx)
            else:
                tab = self._create_text_file_tab(
                    path,
                    _LOADING_FILE_TEXT,
                    diff_text=None,
                    editable=False,
                    read_only_reason=_LOADING_FILE_TEXT,
                )
                self._add_tab_widget(path, os.path.basename(path), tab)
            self._start_file_read(path, diff_text=diff_text, line_no=line_no)
            return

        content, diff_text, editable, read_only_reason = self._read_text_file_state(
            path,
            diff_text,
        )

        if idx >= 0:
            widget = self._tabs.widget(idx)
            if isinstance(widget, _TextFileTab):
                widget.update_content(content, diff_text, editable, read_only_reason)
                if line_no is not None:
                    widget.goto_line(line_no)
            self._tabs.setCurrentIndex(idx)
            self._emit_language_context_changed()
            return

        tab = self._create_text_file_tab(
            path,
            content,
            diff_text=diff_text,
            editable=editable,
            read_only_reason=read_only_reason,
        )
        self._add_tab_widget(path, os.path.basename(path), tab)
        if line_no is not None:
            tab.goto_line(line_no)

    def refresh_file(self, path: str, repo_root: str | None = None) -> bool:
        if repo_root:
            self._repo_root = repo_root
        path = os.path.abspath(
            path if os.path.isabs(path) else os.path.join(self._repo_root, path)
        )
        idx = self._find_tab(path)
        if idx < 0:
            return False
        widget = self._tabs.widget(idx)
        if not isinstance(widget, _TextFileTab):
            return False

        if self._should_read_text_async(path):
            if not widget._dirty:
                widget.update_content(
                    _LOADING_FILE_TEXT,
                    None,
                    editable=False,
                    read_only_reason=_LOADING_FILE_TEXT,
                )
            self._start_file_read(path, diff_text=None, line_no=None)
            return True

        content, diff_text, editable, read_only_reason = self._read_text_file_state(path)
        widget.update_content(content, diff_text, editable, read_only_reason)
        self._sync_tab_title(widget)
        self._emit_language_context_for(widget)
        return True

    def _create_text_file_tab(
        self,
        path: str,
        content: str,
        *,
        diff_text: str | None,
        editable: bool,
        read_only_reason: str,
    ) -> _TextFileTab:
        return _TextFileTab(
            path,
            content,
            self._repo_root,
            diff_text=diff_text,
            editable=editable,
            read_only_reason=read_only_reason,
            auto_save=self._auto_save,
            tab_spaces=self._tab_spaces,
            file_review_prompt=self._file_review_prompt,
            diagnostic_fix_prompt=self._diagnostic_fix_prompt,
        )

    def _should_read_text_async(self, path: str) -> bool:
        try:
            return os.path.getsize(path) > _ASYNC_FILE_READ_BYTES
        except OSError:
            return False

    def _start_file_read(
        self,
        path: str,
        *,
        diff_text: str | None,
        line_no: int | None,
    ):
        self._file_read_generation += 1
        generation = self._file_read_generation
        self._pending_file_reads[path] = generation
        worker = _FileReadWorker(generation, path, diff_text, line_no)
        worker.signals.done.connect(self._on_file_read_ready)
        self._worker_pool.start(worker)

    def _on_file_read_ready(
        self,
        generation: int,
        path: str,
        content: str,
        diff_text,
        editable: bool,
        read_only_reason: str,
        line_no,
    ):
        if self._pending_file_reads.get(path) != generation:
            return
        self._pending_file_reads.pop(path, None)
        idx = self._find_tab(path)
        if idx < 0:
            return
        widget = self._tabs.widget(idx)
        if not isinstance(widget, _TextFileTab):
            return
        widget.update_content(content, diff_text, editable, read_only_reason)
        if isinstance(line_no, int):
            widget.goto_line(line_no)
        self._sync_tab_title(widget)
        self._emit_language_context_for(widget)

    def _read_text_file_state(
        self,
        path: str,
        diff_text: str | None = None,
    ) -> tuple[str, str | None, bool, str]:
        return _read_text_file_state(path, diff_text)

    def open_content(self, content: str, title: str):
        key = f"\0{title}"
        idx = self._find_tab(key)
        if idx >= 0:
            self._tabs.setCurrentIndex(idx)
            self._emit_language_context_changed()
            return
        self._add_text_tab(key, title, content)

    def _on_tab_close_requested(self, index: int):
        self._close_tab(index)

    def close_current_tab(self) -> bool:
        if self._tabs.count() == 0:
            return False
        return self._close_tab(self._tabs.currentIndex())

    def reopen_recent_closed_file(self, repo_root: str | None = None) -> str:
        if repo_root:
            self.set_repo_root(repo_root)
        while self._recently_closed_files:
            path, line_no = self._recently_closed_files.pop()
            if self._find_tab(path) >= 0:
                self.open_file(path, repo_root=self._repo_root, line_no=line_no)
                return path
            if os.path.exists(path):
                self.open_file(path, repo_root=self._repo_root, line_no=line_no)
                return path
        return ""

    def close_all_tabs(self):
        had_tabs = self._tabs.count() > 0
        while self._tabs.count():
            widget = self._tabs.widget(0)
            self._tabs.removeTab(0)
            self._delete_tab_widget(widget)
        self._recently_closed_files.clear()
        if had_tabs:
            self.all_closed.emit()
        self._emit_language_context_changed()

    def _close_tab(self, index: int) -> bool:
        if index < 0 or index >= self._tabs.count():
            return False
        widget = self._tabs.widget(index)
        if not self._confirm_close_tab(widget):
            return False
        self._remember_closed_tab(index, widget)
        self._tabs.removeTab(index)
        self._delete_tab_widget(widget)
        if self._tabs.count() == 0:
            self.all_closed.emit()
        self._emit_language_context_changed()
        return True

    def _confirm_close_tab(self, widget: QWidget | None) -> bool:
        if not isinstance(widget, _TextFileTab) or not widget._dirty:
            return True
        name = os.path.basename(self._tab_file_path(widget)) or "this file"
        choice = QMessageBox.question(
            self,
            "Revert unsaved changes?",
            f"Revert unsaved changes to {name} and close the file?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Ok:
            return False
        widget._revert()
        return True

    def _remember_closed_tab(self, index: int, widget: QWidget | None):
        key = str(self._tabs.tabBar().tabData(index) or "")
        if not key or key.startswith("\0"):
            return
        path = os.path.abspath(key)
        line_no = None
        if isinstance(widget, _TextFileTab):
            line_no = widget._editor.textCursor().blockNumber() + 1
        self._recently_closed_files = [
            item for item in self._recently_closed_files if item[0] != path
        ]
        self._recently_closed_files.append((path, line_no))
        del self._recently_closed_files[:-20]

    def _on_current_tab_changed(self, index: int):
        self._emit_language_context_changed()
        self._emit_markdown_preview_pane_changed()
        if index < 0:
            return
        key = str(self._tabs.tabBar().tabData(index) or "")
        if key and not key.startswith("\0"):
            self.active_file_changed.emit(key)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self):
        self._file_read_generation += 1
        self._pending_file_reads.clear()
        for i in range(self._tabs.count()):
            self._release_tab_widget(self._tabs.widget(i))

    def _load_auto_save(self) -> bool:
        if self._settings is None:
            return False
        try:
            data = self._settings.load()
        except Exception:
            return False
        return bool(data.get(FILE_EDITOR_AUTO_SAVE_KEY, False))

    def _load_tab_spaces(self) -> int:
        if self._settings is None:
            return DEFAULT_FILE_EDITOR_TAB_SPACES
        try:
            data = self._settings.load()
        except Exception:
            return DEFAULT_FILE_EDITOR_TAB_SPACES
        return file_editor_tab_spaces(data)

    def _load_file_review_prompt_template(self) -> str:
        if self._settings is None:
            return DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE
        try:
            data = self._settings.load()
        except Exception:
            return DEFAULT_FILE_REVIEW_PROMPT_TEMPLATE
        return file_review_prompt_template(data)

    def _load_diagnostic_fix_prompt_template(self) -> str:
        if self._settings is None:
            return DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE
        try:
            data = self._settings.load()
        except Exception:
            return DEFAULT_DIAGNOSTIC_FIX_PROMPT_TEMPLATE
        return diagnostic_fix_prompt_template(data)

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

    def _on_tab_dirty_changed(self, widget: QWidget, dirty: bool):
        self._sync_tab_title(widget)
        path = self._tab_file_path(widget)
        if path:
            self.dirty_file_changed.emit(path, dirty)

    def _delete_tab_widget(self, widget: QWidget | None):
        self._release_tab_widget(widget)
        if widget is not None:
            widget.deleteLater()

    def _release_tab_widget(self, widget: QWidget | None):
        if isinstance(widget, _TextFileTab):
            path = self._tab_file_path(widget)
            if path and widget._dirty:
                self.dirty_file_changed.emit(path, False)
            widget.release_resources()

    @staticmethod
    def _tab_file_path(widget: QWidget | None) -> str:
        if not isinstance(widget, _TextFileTab):
            return ""
        path = str(widget._path or "")
        return "" if not path or path.startswith("\0") else path


def _event_pos(event):
    try:
        return event.position().toPoint()
    except AttributeError:
        return event.pos()


def _cursor_line_range(document, cursor: QTextCursor) -> tuple[int, int]:
    line_count = max(1, document.blockCount())
    start = min(cursor.selectionStart(), cursor.selectionEnd())
    end = max(cursor.selectionStart(), cursor.selectionEnd())
    if end > start and document.characterAt(end - 1) in ("\n", "\u2029"):
        end -= 1
    start_cursor = QTextCursor(document)
    start_cursor.setPosition(max(0, start))
    end_cursor = QTextCursor(document)
    end_cursor.setPosition(max(start, end))
    start_line = start_cursor.blockNumber() + 1
    end_line = end_cursor.blockNumber() + 1
    start_line = max(1, min(start_line, line_count))
    end_line = max(start_line, min(end_line, line_count))
    return start_line, end_line


def _diagnostic_color(severity: str) -> QColor:
    return QColor({
        "error": "#ef4444",
        "warning": "#f59e0b",
        "hint": "#60a5fa",
        "info": ACCENT,
    }.get(str(severity or "info").lower(), ACCENT))


def _diagnostics_by_line(diagnostics: list[Diagnostic]) -> dict[int, list[Diagnostic]]:
    by_line: dict[int, list[Diagnostic]] = {}
    for diagnostic in diagnostics:
        by_line.setdefault(max(1, diagnostic.line), []).append(diagnostic)
    return by_line


def _diagnostic_summary(diagnostics: list[Diagnostic]) -> str:
    count = len(diagnostics)
    errors = sum(1 for item in diagnostics if item.severity == "error")
    warnings = sum(1 for item in diagnostics if item.severity == "warning")
    plural = "s" if count != 1 else ""
    if errors and warnings:
        return f"{count} problem{plural} ({errors} errors, {warnings} warnings)"
    if errors:
        return f"{count} problem{plural} ({errors} error{'s' if errors != 1 else ''})"
    if warnings:
        return f"{count} problem{plural} ({warnings} warning{'s' if warnings != 1 else ''})"
    return f"{count} problem{plural}"


def _diagnostic_details(diagnostics: list[Diagnostic], limit: int = 8) -> str:
    lines = []
    for item in diagnostics[:limit]:
        label = " ".join(part for part in (item.source, item.code) if part)
        prefix = f"line {item.line}:{item.column + 1} {item.severity or 'info'}"
        if label:
            prefix = f"{prefix} [{label}]"
        lines.append(f"{prefix}: {item.message}")
    remaining = len(diagnostics) - limit
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    return "\n".join(lines)


def _diagnostic_tool_label(diagnostics: list[Diagnostic]) -> str:
    for item in diagnostics:
        label = " ".join(part for part in (item.source, item.code) if part)
        if label:
            return label
    return "diagnostic tool"


def _diagnostic_source_label(diagnostics: list[Diagnostic]) -> str:
    for item in diagnostics:
        source = str(item.source or "").strip()
        if source:
            return source
    return "diagnostic tool"


def _diagnostic_tool_command(tool: str, path: str, file_mention: str) -> str:
    source = str(tool or "").split(maxsplit=1)[0].lower()
    if source == "pymarkdown":
        return f"`pymarkdown scan {path}`"
    if source == "ruff":
        return f"`ruff check {path}`"
    return f"the configured language diagnostics for {file_mention}"


def _format_prompt_template(template: str, values: dict[str, str], default: str) -> str:
    def render(raw: str) -> str:
        return raw.format(**values).strip()

    raw = str(template or "").strip() or default
    try:
        text = render(raw)
    except (IndexError, KeyError, ValueError):
        text = render(default)
    return text or render(default)


def _normalize_editor_newlines(text: str) -> str:
    return re.sub(r"\r+\n", "\n", str(text)).replace("\r", "\n")


def _file_mention_text(path: str) -> str:
    return f'@"{path}"' if any(ch.isspace() for ch in path) else f"@{path}"


def _relative_file_reference(path: str, repo_root: str) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except (OSError, ValueError):
        return Path(path).name


def _file_drop_ref(path: str, repo_root: str) -> str:
    if not path or str(path).startswith("\0") or not os.path.isfile(path):
        return ""
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except (OSError, ValueError):
        return ""


def _read_text_preview(path: str) -> str:
    text, _truncated, _decode_error, _blocked_preview = _read_text_preview_details(path)
    return text


def _read_text_file_state(
    path: str,
    diff_text: str | None = None,
) -> tuple[str, str | None, bool, str]:
    try:
        content, truncated, decode_error, blocked_preview = _read_text_preview_details(path)
    except OSError as e:
        content = f"[Could not read file: {e}]"
        editable = False
        read_only_reason = f"Could not read file: {e}"
    else:
        editable = not truncated and not decode_error and not blocked_preview
        read_only_reason = _read_only_reason(truncated, decode_error, blocked_preview)
    return content, diff_text, editable, read_only_reason


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
