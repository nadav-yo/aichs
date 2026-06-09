import os
from pathlib import Path

import markdown as _md
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPlainTextEdit, QFrame, QTabWidget,
    QScrollArea, QLabel, QSizePolicy, QCheckBox, QPushButton, QTextBrowser, QLineEdit,
    QCompleter, QApplication, QToolTip, QMenu,
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
from services.diff_html import changed_new_line_numbers
from services.file_editor_refs import AICHS_EDITOR_REF_MIME, editor_ref_payload, editor_ref_text
from services.git_diff import can_diff_against_head, diff_against_head
from services.git_status import is_git_repo
from services.tool_policy import path_in_repo
from services.language_features import (
    CodeAction, CodeActionResult, Diagnostic, LanguageCompletionProvider,
    apply_code_action as language_apply_code_action,
    code_actions as language_code_actions,
    diagnostics as language_diagnostics,
)
from services.code_completion import (
    CompletionItem, CompletionProvider, LocalCompletionProvider, prefix_at,
)
from storage.settings import FILE_EDITOR_AUTO_SAVE_KEY
from ui.theme import (
    ACCENT, MONO_FONT_CSS, current_theme, palette, mono_font, meta_font_pt,
    markdown_css, apply_flat_tab_style,
)

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
            if (
                is_git_repo(self._repo_root)
                and can_diff_against_head(self._repo_root, self._path)
            ):
                diff_text = diff_against_head(self._repo_root, self._path)
        except Exception:
            diff_text = None
        self.signals.done.emit(self._generation, diff_text)


class _TextMinimap(QWidget):
    """Tiny overview strip that mirrors and controls a plain text editor."""

    _WIDTH = 86
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
            color.setAlpha(150)
            return color
        color_for_line = getattr(self._editor, "minimap_color_for_line", None)
        if color_for_line is not None:
            color = color_for_line(text)
            if color is not None:
                color.setAlpha(130)
                return color
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
        if self._style is not None:
            style = self._style.style_for_token(token)
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
        if self._style is None:
            return None
        style = self._style.style_for_token(token)
        color = style.get("color")
        return QColor(f"#{color}") if color else None


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

    def apply_appearance(self):
        p = palette()
        self.setFont(mono_font())
        self.setStyleSheet(
            f"QPlainTextEdit {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:none; selection-background-color:{ACCENT};"
            f"font-family:{MONO_FONT_CSS}; }}"
        )
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

        completion_shortcut = (
            event.key() == Qt.Key.Key_Space
            and event.modifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        if completion_shortcut:
            self._show_completion(manual=True)
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape and not self.isReadOnly():
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
        self._maybe_update_completion(event)

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
        cursor = self.textCursor()
        content = self.toPlainText()
        prefix = prefix_at(content, cursor.position())
        minimum = 1 if manual else 2
        if len(prefix) < minimum:
            self._hide_completion()
            return
        items = self._completion_provider.complete(
            path=self._completion_path,
            content=content,
            position=cursor.position(),
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
        content = self.toPlainText()
        prefix = prefix_at(content, cursor.position())
        if prefix:
            cursor.movePosition(
                QTextCursor.MoveOperation.Left,
                QTextCursor.MoveMode.KeepAnchor,
                len(prefix),
            )
        cursor.insertText(item.insert_text)
        self.setTextCursor(cursor)
        self._hide_completion()

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


class _MarkdownPreview(QTextBrowser):
    edit_requested = pyqtSignal()

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

    def set_markdown(self, text: str, base_path: str):
        parent = str(Path(base_path).parent)
        self.document().setBaseUrl(QUrl.fromLocalFile(parent + os.sep))
        self.setHtml(_markdown_preview_html(text))

    def mousePressEvent(self, event):
        self.edit_requested.emit()
        super().mousePressEvent(event)


def _markdown_preview_html(text: str) -> str:
    body = _md.markdown(text, extensions=["fenced_code", "nl2br", "tables", "toc"])
    p = palette()
    css = (
        markdown_css()
        + f"body {{ background:{p['BG3']}; padding:0; }}"
        + "table { border-collapse:collapse; margin:8px 0; }"
        + f"th,td {{ border:1px solid {p['BORDER']}; padding:5px 8px; }}"
        + f"th {{ background:{p['BG2']}; }}"
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
        self._markdown = self._file_backed and Path(path).suffix.lower() in _MARKDOWN_EXTS
        self._read_only_reason = read_only_reason
        self._diagnostics: list[Diagnostic] = []
        self._language_errors: list[str] = []
        self._completion_provider = LanguageCompletionProvider(self._repo_root)
        self._auto_save = auto_save
        self._dirty = False
        self._rendering = False
        self._edit_mode = False
        self._force_text_view = False
        self._diagnostics_generation = 0
        self._code_action_generation = 0
        self._diff_generation = 0
        self._worker_pool = QThreadPool.globalInstance()
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
        self._editor.setFrameShape(QFrame.Shape.NoFrame)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.edit_requested.connect(self._enter_edit_mode)
        self._editor.cancel_requested.connect(self._cancel_edit)
        self._editor.diagnostic_fix_requested.connect(self._show_diagnostic_actions)
        self._editor.textChanged.connect(self._on_text_changed)
        self._preview = _MarkdownPreview()
        self._preview.edit_requested.connect(self._enter_edit_mode)
        self._minimap = _TextMinimap(self._editor)

        view = QHBoxLayout()
        view.setContentsMargins(0, 0, 0, 0)
        view.setSpacing(0)
        view.addWidget(self._preview, 1)
        view.addWidget(self._editor, 1)
        view.addWidget(self._minimap)
        root.addLayout(view, 1)

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
        self._find_bar.setStyleSheet(
            f"QFrame {{ background:{p['BG2']}; border-top:1px solid {p['BORDER_SUBTLE']};"
            f"border-bottom:1px solid {p['BORDER_SUBTLE']}; }}"
        )
        self._find_query.setStyleSheet(
            f"QLineEdit {{ background:{p['BG3']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:6px;"
            f"padding:4px 8px; font-size:{meta}px; }}"
            f"QLineEdit:focus {{ border:1px solid {ACCENT}; }}"
        )
        self._find_status.setStyleSheet(
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
        self._diff_text = diff_text
        self._editable = self._file_backed and editable
        self._read_only_reason = read_only_reason
        self._set_dirty(False)
        self._edit_mode = False
        self._force_text_view = False
        self._diff_toggle.setChecked(bool(diff_text))
        self._diff_toggle.setVisible(diff_text is not None)
        self._render(diagnostics_delay_ms=0)
        if diff_text is None:
            self._schedule_diff_refresh(delay_ms=0)

    def _on_diff_toggled(self, checked: bool):
        if not checked and self._editable and self._edit_mode:
            self._editor.setFocus()
        self._render(diagnostics_delay_ms=0 if not checked else None)

    def _enter_edit_mode(self):
        if not self._editable or self._is_showing_diff():
            return
        self._edit_mode = True
        self._force_text_view = False
        self._render()
        self._editor.setFocus()

    def _cancel_edit(self):
        if not self._edit_mode or self._is_showing_diff():
            return
        if self._dirty and not self._auto_save:
            self._revert()
            return
        self._edit_mode = False
        self._render()

    def _on_text_changed(self):
        if (
            self._rendering
            or not self._editable
            or not self._edit_mode
            or self._is_showing_diff()
        ):
            return
        self._set_dirty(True)
        if self._auto_save:
            self._schedule_auto_save()
            return
        self._sync_actions()

    def _is_showing_diff(self) -> bool:
        return self._diff_toggle.isChecked() and bool(self._diff_text)

    def _is_markdown_preview(self) -> bool:
        return (
            self._markdown
            and not self._edit_mode
            and not self._force_text_view
            and not self._is_showing_diff()
        )

    def _render(self, *, diagnostics_delay_ms: int | None = None):
        self._rendering = True
        self._editor.blockSignals(True)
        self._editor.configure_syntax(self._lang_hint, self._content)
        self._completion_provider = LanguageCompletionProvider(self._repo_root)
        self._editor.configure_completion(self._path, self._completion_provider)
        self._editor.configure_reference(self._path, self._repo_root)
        if self._editor.toPlainText() != self._content:
            self._editor.setPlainText(self._content)
        if self._is_markdown_preview():
            self._schedule_markdown_preview()
            self._preview.show()
            self._editor.hide()
            self._minimap.hide()
            self._editor.setReadOnly(True)
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
        text = self._editor.toPlainText()
        folded_text = text.casefold()
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
        self._find_status.setText(_find_match_status(folded_text, folded_query, pos))

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
            self._editable = False
            self._read_only_reason = f"Could not read file: {e}"
        else:
            self._content = content
            self._editable = not truncated and not decode_error and not blocked_preview
            self._read_only_reason = _read_only_reason(truncated, decode_error, blocked_preview)
        self._edit_mode = False
        self._set_dirty(False)
        self._render(diagnostics_delay_ms=0)
        self._schedule_diff_refresh(delay_ms=0)
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
        elif self._is_markdown_preview():
            self._set_status("Markdown preview")
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
            self._set_status("Formatted view")
        elif not self._dirty and self._status.text() not in ("Saved", "Auto-saved"):
            self._set_status("")

    def _set_status(self, text: str):
        self._status.setText(text)
        self._status.setVisible(bool(text))

    def _schedule_auto_save(self):
        self._auto_save_timer.start(self._AUTO_SAVE_DELAY_MS)

    def _schedule_markdown_preview(self, delay_ms: int | None = None):
        self._pending_markdown = (self._content, self._path)
        self._markdown_timer.start(
            self._MARKDOWN_DELAY_MS if delay_ms is None else max(0, delay_ms)
        )

    def _apply_markdown_preview(self):
        if self._pending_markdown is None:
            return
        text, path = self._pending_markdown
        self._pending_markdown = None
        self._preview.set_markdown(text, path)

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
        self._diff_text = diff_text
        has_diff = self._diff_text is not None
        self._diff_toggle.setVisible(has_diff)
        if not has_diff and self._diff_toggle.isChecked():
            self._diff_toggle.setChecked(False)
        elif was_showing and has_diff:
            self._render()
        self._sync_actions()

    def _set_diagnostics(self, diagnostics: list[Diagnostic]):
        self._diagnostics = list(diagnostics)
        self._editor.set_diagnostics(self._diagnostics)

    def _current_editor_content(self) -> str:
        return self._editor.toPlainText() if self._edit_mode else self._content

    def _show_diagnostic_actions(self, diagnostics: list[Diagnostic]):
        if not diagnostics:
            return
        actions, errors = language_code_actions(
            self._repo_root,
            self._path,
            self._current_editor_content(),
            diagnostics,
        )
        self._language_errors = list(errors or [])
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
        if content == self._current_editor_content():
            self._set_status(message or "Code action made no changes")
            self._refresh_diagnostics(delay_ms=0)
            return

        self._edit_mode = True
        self._force_text_view = False
        self._content = content
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
        details = _diagnostic_details(diagnostics)
        prompt = (
            f"Please fix this diagnostic in {mention}.\n\n"
            f"Diagnostic tool: {tool}\n"
            f"Diagnostic output:\n{details}"
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
        self._editor.release_resources()

    def closeEvent(self, event):
        self.release_resources()
        super().closeEvent(event)


class FileViewerPanel(QWidget):
    all_closed = pyqtSignal()
    diagnostic_fix_requested = pyqtSignal(str, object)
    active_file_changed = pyqtSignal(str)

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
        self._tabs.currentChanged.connect(self._on_current_tab_changed)

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
            widget.diagnostic_fix_requested.connect(self.diagnostic_fix_requested.emit)
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
                return
            self._add_tab_widget(path, os.path.basename(path), _ImageViewer(path))
            return

        content, diff_text, editable, read_only_reason = self._read_text_file_state(
            path,
            diff_text,
        )

        idx = self._find_tab(path)
        if idx >= 0:
            widget = self._tabs.widget(idx)
            if isinstance(widget, _TextFileTab):
                widget.update_content(content, diff_text, editable, read_only_reason)
                if line_no is not None:
                    widget.goto_line(line_no)
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

        content, diff_text, editable, read_only_reason = self._read_text_file_state(path)
        widget.update_content(content, diff_text, editable, read_only_reason)
        self._sync_tab_title(widget)
        return True

    def _read_text_file_state(
        self,
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

    def open_content(self, content: str, title: str):
        key = f"\0{title}"
        idx = self._find_tab(key)
        if idx >= 0:
            self._tabs.setCurrentIndex(idx)
            return
        self._add_text_tab(key, title, content)

    def _on_tab_close_requested(self, index: int):
        widget = self._tabs.widget(index)
        self._tabs.removeTab(index)
        self._delete_tab_widget(widget)
        if self._tabs.count() == 0:
            self.all_closed.emit()

    def close_current_tab(self) -> bool:
        if self._tabs.count() == 0:
            return False
        widget = self._tabs.currentWidget()
        self._tabs.removeTab(self._tabs.currentIndex())
        self._delete_tab_widget(widget)
        if self._tabs.count() == 0:
            self.all_closed.emit()
        return True

    def _on_current_tab_changed(self, index: int):
        if index < 0:
            return
        key = str(self._tabs.tabBar().tabData(index) or "")
        if key and not key.startswith("\0"):
            self.active_file_changed.emit(key)

    def closeEvent(self, event):
        for i in range(self._tabs.count()):
            self._release_tab_widget(self._tabs.widget(i))
        super().closeEvent(event)

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

    def _delete_tab_widget(self, widget: QWidget | None):
        self._release_tab_widget(widget)
        if widget is not None:
            widget.deleteLater()

    def _release_tab_widget(self, widget: QWidget | None):
        if isinstance(widget, _TextFileTab):
            widget.release_resources()


def _find_match_status(folded_text: str, folded_query: str, pos: int) -> str:
    starts = []
    start = folded_text.find(folded_query)
    while start >= 0:
        starts.append(start)
        start = folded_text.find(folded_query, start + max(1, len(folded_query)))
    if not starts:
        return "No matches"
    current = starts.index(pos) + 1 if pos in starts else 1
    return f"{current} of {len(starts)}"


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


def _relative_file_reference(path: str, repo_root: str) -> str:
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except (OSError, ValueError):
        return Path(path).name


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
