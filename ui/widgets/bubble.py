import base64
import html
import re
from datetime import datetime, date
from pathlib import PurePath

from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QSizePolicy, QMenu, QTextEdit,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize, QEvent, QObject, QRunnable, QThreadPool
from PyQt6.QtCore import QMimeData
from PyQt6.QtGui import QPixmap, QAction, QGuiApplication, QTextCursor

from services.content import content_text, image_blocks
from services.crew import crew_name_from_metadata
from services.file_ref_clipboard import (
    AICHS_MESSAGE_COPY_MIME,
    file_ref_spans,
    file_refs_payload,
)
from services.performance import time_operation
from services.usage import usage_summary
from ui.avatars import AVATAR_SIZE, avatar_label, avatar_pixmap
from ui.markdown_html import code_from_copy_url, markdown_body
from ui.theme import (
    palette, chat_font_pt, bubble_label_style, composer_style, edit_bubble_style,
    markdown_css, markdown_file_link_style, timestamp_style, crew_name_style, crew_tone,
    user_reference_style,
)

_CODE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_STREAM_RENDER_INTERVAL_MS = 50
_ASYNC_MARKDOWN_RENDER_CHARS = 16_000
_FILE_RE = re.compile(
    r"(?P<path>[\w./\\-]+\.(?:py|md|json|yaml|yml|toml|sh|js|ts|tsx|jsx|css|html|txt|rs|go|java|c|cpp|h|hpp|cs|php|rb|swift|kt|sql|xml))",
    re.IGNORECASE,
)
_GENERIC_LANGS = {"", "text", "txt", "plain", "bash", "sh", "shell", "console", "terminal"}
_EXT_LANG = {
    ".py": "python",
    ".md": "markdown",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".css": "css",
    ".html": "html",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".rs": "rust",
    ".go": "go",
    ".sh": "bash",
    ".sql": "sql",
    ".xml": "xml",
}
_HTML_TOKEN_RE = re.compile(r"(<[^>]+>)")


def _linkify_paths(rendered_html: str) -> str:
    out: list[str] = []
    ignored: list[str] = []
    for token in _HTML_TOKEN_RE.split(rendered_html):
        if not token:
            continue
        if token.startswith("<") and token.endswith(">"):
            out.append(token)
            _update_ignored_html_stack(token, ignored)
        elif ignored:
            out.append(token)
        else:
            out.append(_linkify_path_text(token))
    return "".join(out)


def _update_ignored_html_stack(tag: str, ignored: list[str]) -> None:
    match = re.match(r"</?\s*([A-Za-z0-9]+)", tag)
    if not match:
        return
    name = match.group(1).lower()
    if name not in {"a", "style", "pre", "code"}:
        return
    is_close = tag.startswith("</")
    is_self_closing = tag.rstrip().endswith("/>")
    if is_close:
        if name in ignored:
            ignored.remove(name)
    elif not is_self_closing:
        ignored.append(name)


def _linkify_path_text(text: str) -> str:
    spans = file_ref_spans(text)
    if not spans:
        return text
    link_style = markdown_file_link_style()
    parts: list[str] = []
    last = 0
    for start, end, ref in spans:
        parts.append(text[last:start])
        href = html.escape(f"aichs-file:{ref}", quote=True)
        label = html.escape(ref)
        parts.append(
            f'<a class="aichs-file-link" href="{href}" style="{link_style}">{label}</a>'
        )
        last = end
    parts.append(text[last:])
    return "".join(parts)


def _to_html(text: str) -> str:
    body = markdown_body(text, extensions=["fenced_code", "nl2br", "tables"])
    return f"<style>{markdown_css()}</style>{_linkify_paths(body)}"


class _MarkdownRenderSignals(QObject):
    done = pyqtSignal(int, str, str)


class _MarkdownRenderWorker(QRunnable):
    def __init__(self, generation: int, source: str):
        super().__init__()
        self.signals = _MarkdownRenderSignals()
        self._generation = generation
        self._source = source

    def run(self):
        with time_operation("markdown.render", detail=f"chars={len(self._source)}"):
            html_text = _to_html(self._source)
        self.signals.done.emit(self._generation, self._source, html_text)


_MENTION_RE = re.compile(r'@(?:"([^"]+)"|([^\s@]*[^\s@.,:;!?)\]}]))')


def _linkify_user_text(text: str) -> str:
    """Turn @file mentions in user messages into clickable file links."""
    link_style = user_reference_style()

    def repl(match: re.Match) -> str:
        path = (match.group(1) or match.group(2) or "").strip()
        if not path:
            return match.group(0)
        label = html.escape(match.group(0)).replace(" ", "&nbsp;")
        is_file_ref = any(ch in path for ch in (".", "/", "\\"))
        if not is_file_ref:
            return f'<span style="{link_style}">{label}</span>'
        href = html.escape(f"aichs-file:{path}", quote=True)
        return f'<a href="{href}" style="{link_style}">{label}</a>'

    parts: list[str] = []
    last = 0
    for match in _MENTION_RE.finditer(text):
        parts.append(html.escape(text[last:match.start()]))
        parts.append(repl(match))
        last = match.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def _language_from_info(info: str) -> str:
    first = (info.strip().split() or [""])[0].strip()
    if not first or "=" in first or "." in first or "/" in first or "\\" in first:
        return ""
    return first


def _filename_from_info(info: str) -> str:
    match = _FILE_RE.search(info)
    return match.group("path") if match else ""


def _filename_nearby(text: str) -> str:
    tail = text[-220:]
    matches = list(_FILE_RE.finditer(tail))
    if not matches:
        return ""
    return matches[-1].group("path")


def _display_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized and not re.match(r"^[A-Za-z]:/", normalized):
        return normalized
    try:
        name = PurePath(normalized).name
    except Exception:
        name = path
    return name or path


def _language_from_filename(path: str) -> str:
    suffix = PurePath(path.replace("\\", "/")).suffix.lower()
    return _EXT_LANG.get(suffix, "")


def _artifact_for_block(info: str, code: str, before: str) -> dict | None:
    lang = _language_from_info(info)
    filename = _filename_from_info(info)
    reason = ""
    line_count = len(code.splitlines())

    if filename:
        lang = lang or _language_from_filename(filename)
        title = _display_path(filename)
        reason = "Extracted because the code fence is labeled as a file."
    else:
        nearby = _filename_nearby(before)
        if nearby:
            filename = nearby
            lang = lang or _language_from_filename(nearby)
            title = _display_path(nearby)
            reason = "Extracted because the surrounding text names this file."
        elif line_count >= 80 or len(code) >= 4000:
            label = lang.upper() if lang else "Large"
            title = f"{label} block"
            reason = f"Extracted because it is {line_count} lines long."
        else:
            return None

    if lang.lower() in _GENERIC_LANGS and not filename and line_count < 80:
        return None

    return {
        "language": lang,
        "code": code,
        "title": title,
        "reason": reason,
    }


def _extract_artifacts(text: str) -> tuple[str, list[dict]]:
    artifacts: list[dict] = []
    parts: list[str] = []
    pos = 0
    for match in _CODE_RE.finditer(text):
        info = match.group(1).strip()
        code = match.group(2)
        before = text[:match.start()]
        artifact = _artifact_for_block(info, code, before)
        parts.append(text[pos:match.start()])
        if artifact:
            artifacts.append(artifact)
        else:
            parts.append(match.group(0))
        pos = match.end()
    parts.append(text[pos:])
    return "".join(parts).strip(), artifacts


def format_timestamp(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        if dt.date() == date.today():
            return dt.strftime("%H:%M")
        return dt.strftime("%b %d, %H:%M")
    except Exception:
        return ""


class _EditInput(QTextEdit):
    submitted = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setAcceptRichText(False)
        self.setStyleSheet(
            edit_bubble_style()
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                event.accept()
                self.submitted.emit(self.toPlainText().strip())
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.cancelled.emit()
            return
        super().keyPressEvent(event)


class _StreamTextView(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self._fit_height()

    def append_text(self, text: str):
        if not text:
            return
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self._fit_height()

    def clear_text(self):
        self.clear()
        self._fit_height()

    def _fit_height(self):
        margins = self.contentsMargins()
        height = int(self.document().size().height()) + margins.top() + margins.bottom() + 4
        self.setFixedHeight(max(24, height))
        self.updateGeometry()


class MessageBubble(QFrame):
    _DOTS = ["●", "● ●", "● ● ●"]

    regenerate_requested  = pyqtSignal(int)
    edit_resend_requested = pyqtSignal(int, str)
    branch_requested      = pyqtSignal(int)
    file_clicked          = pyqtSignal(str)   # relative or absolute path

    def __init__(self, content="", is_user=True, typing=False,
                 history_index: int = -1, timestamp: str = "",
                 crew: dict | None = None, can_regenerate: bool = False,
                 usage: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._is_user = is_user
        self._history_index = history_index
        self._content = content
        self._crew = crew if isinstance(crew, dict) else None
        self._crew_id = str((self._crew or {}).get("id") or "").casefold()
        self._crew_color = str((self._crew or {}).get("color") or "")
        self._crew_avatar = str((self._crew or {}).get("avatar") or "")
        self._can_regenerate = can_regenerate
        self._editing = False
        self._timestamp_lbl = None
        self._usage_lbl = None
        self._speaker_lbl = None
        self._md_source: str | None = None
        self._md_html: str | None = None
        self._markdown_render_generation = 0
        self._markdown_render_pool = QThreadPool.globalInstance()
        self._stream_render_pending = False
        self._stream_render_chunks: list[str] = []
        self._stream_render_timer = QTimer(self)
        self._stream_render_timer.setSingleShot(True)
        self._stream_render_timer.setInterval(_STREAM_RENDER_INTERVAL_MS)
        self._stream_render_timer.timeout.connect(self._flush_stream_text)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        if timestamp:
            self.setToolTip(format_timestamp(timestamp))

        row = QHBoxLayout(self)
        row.setContentsMargins(24, 7, 24, 7)
        row.setSpacing(8)

        portrait = self._portrait(is_user)

        self.body = QVBoxLayout()
        self.body.setSpacing(5)

        for img in image_blocks(content):
            self.body.addWidget(
                self._image_label(img),
                0,
                Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft,
            )

        text = content_text(content) if not typing else ""
        self._copy_text = text
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(440 if is_user else 880)
        if not is_user:
            self.label.setMinimumWidth(520)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.label.setOpenExternalLinks(False)
        self.label.linkActivated.connect(self._on_link)
        self.label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.label.customContextMenuRequested.connect(self._context_menu)
        self.label.installEventFilter(self)
        if is_user and not typing:
            self.label.mouseDoubleClickEvent = lambda e: self._start_edit()

        self._stream_view = None if is_user else _StreamTextView()
        if self._stream_view is not None:
            self._stream_view.setMaximumWidth(880)
            self._stream_view.setMinimumWidth(520)
            self._stream_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._stream_view.customContextMenuRequested.connect(
                lambda pos, widget=self._stream_view: self._context_menu(pos, widget)
            )
            self._stream_view.installEventFilter(self)
            self._stream_view.hide()

        self.edit_input = _EditInput(text)
        self.edit_input.hide()
        self.edit_input.submitted.connect(self._commit_edit)
        self.edit_input.cancelled.connect(self._cancel_edit)

        if is_user:
            self.label.setStyleSheet(bubble_label_style(True))
            if text or typing:
                if text and not typing:
                    self.label.setTextFormat(Qt.TextFormat.RichText)
                    self.label.setText(_linkify_user_text(text))
                else:
                    self.label.setTextFormat(Qt.TextFormat.PlainText)
                self.body.addWidget(self.label, 0, Qt.AlignmentFlag.AlignRight)
            self.body.addWidget(self.edit_input, 0, Qt.AlignmentFlag.AlignRight)
        else:
            speaker = crew_name_from_metadata(self._crew)
            if speaker:
                self._speaker_lbl = QLabel(speaker)
                self._speaker_lbl.setObjectName("crewSpeaker")
                self._speaker_lbl.setStyleSheet(
                    crew_name_style(self._crew_id, self._crew_color)
                )
                self.body.addWidget(self._speaker_lbl, 0, Qt.AlignmentFlag.AlignLeft)
            self.label.setStyleSheet(
                bubble_label_style(False, crew_id=self._crew_id, crew_color=self._crew_color)
            )
            self._stream_view.setStyleSheet(
                bubble_label_style(False, crew_id=self._crew_id, crew_color=self._crew_color)
            )
            self.body.addWidget(self.label, 0, Qt.AlignmentFlag.AlignLeft)
            self.body.addWidget(self._stream_view, 0, Qt.AlignmentFlag.AlignLeft)
            self.body.addWidget(self.edit_input, 0, Qt.AlignmentFlag.AlignLeft)

        if timestamp:
            self._timestamp_lbl = QLabel(format_timestamp(timestamp))
            self._timestamp_lbl.setStyleSheet(timestamp_style())
            self._timestamp_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft
            )
            self.body.addWidget(self._timestamp_lbl)
        if not is_user:
            self._usage_lbl = QLabel("")
            self._usage_lbl.setStyleSheet(timestamp_style())
            self._usage_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.body.addWidget(self._usage_lbl)
            self.set_usage(usage)

        if is_user:
            row.addStretch()
            row.addLayout(self.body)
            row.addWidget(portrait, 0, Qt.AlignmentFlag.AlignTop)
        else:
            row.addWidget(portrait, 0, Qt.AlignmentFlag.AlignTop)
            row.addLayout(self.body)
            row.addStretch()

        self._typing = False
        self._dot_step = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if typing:
            self._start_typing()
        elif not is_user and text:
            self.finalize(text)

    @staticmethod
    def _image_label(block: dict) -> QLabel:
        lbl = QLabel()
        raw = base64.b64decode(block.get("data", ""))
        pixmap = QPixmap()
        pixmap.loadFromData(raw)
        lbl.setPixmap(pixmap.scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation))
        lbl.setStyleSheet("border-radius:8px;")
        return lbl

    def _portrait(self, is_user: bool) -> QLabel:
        if not self._crew_avatar:
            return avatar_label("human" if is_user else "agent")
        lbl = QLabel()
        color = crew_tone(self._crew_id, custom_color=self._crew_color)["accent"]
        lbl.setPixmap(avatar_pixmap(self._crew_avatar, AVATAR_SIZE, color))
        lbl.setFixedSize(QSize(AVATAR_SIZE, AVATAR_SIZE))
        lbl.setStyleSheet("background:transparent;")
        return lbl

    def _start_edit(self):
        if self._typing or self._editing or not self._is_user:
            return
        self._editing = True
        self.label.hide()
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.edit_input.setPlainText(self._copy_text)
        self.edit_input.show()
        self.edit_input.setFocus()
        self.edit_input.selectAll()

    def _commit_edit(self, text: str):
        self.edit_input.hide()
        self.label.show()
        self._editing = False
        if text and text != self._copy_text:
            self.edit_resend_requested.emit(self._history_index, text)
        elif text:
            self._show_user_text(text)

    def _show_user_text(self, text: str):
        self._copy_text = text
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setText(_linkify_user_text(text))

    def _cancel_edit(self):
        self.edit_input.hide()
        self.label.show()
        self._editing = False

    def _start_typing(self):
        self._typing = True
        self._dot_step = 0
        self.label.setText(self._DOTS[0])
        stream_view = getattr(self, "_stream_view", None)
        if stream_view is not None:
            stream_view.hide()
        self._timer.start(350)

    def _tick(self):
        self._dot_step = (self._dot_step + 1) % len(self._DOTS)
        self.label.setText(self._DOTS[self._dot_step])

    def append(self, text):
        if self._typing:
            self._typing = False
            self._timer.stop()
            self.label.setText("")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self._copy_text += text
        if not hasattr(self, "_stream_render_chunks"):
            self._stream_render_chunks = []
        self._stream_render_chunks.append(text)
        if self._stream_render_timer.isActive():
            self._stream_render_pending = True
            return
        self._stream_render_pending = False
        self._render_stream_text()
        self._stream_render_timer.start()

    def _flush_stream_text(self):
        if not self._stream_render_pending:
            return
        self._stream_render_pending = False
        self._render_stream_text()

    def _render_stream_text(self):
        chunks = getattr(self, "_stream_render_chunks", None)
        if chunks is None:
            self.label.setText(self._copy_text)
            return
        text = "".join(chunks)
        chunks.clear()
        if not text:
            return
        stream_view = getattr(self, "_stream_view", None)
        if stream_view is None:
            self.label.setText(self._copy_text)
            return
        if hasattr(self.label, "hide"):
            self.label.hide()
        if hasattr(stream_view, "show"):
            stream_view.show()
        stream_view.append_text(text)

    def is_empty_typing(self) -> bool:
        return self._typing and not self._copy_text

    def finalize(self, full_text: str, on_artifact=None):
        """Render assistant markdown, including fenced code, inside the bubble."""
        if self._is_user:
            return
        self._stream_render_timer.stop()
        self._stream_render_pending = False
        if hasattr(self, "_stream_render_chunks"):
            self._stream_render_chunks.clear()
        stream_view = getattr(self, "_stream_view", None)
        if stream_view is not None:
            stream_view.clear_text()
            stream_view.hide()
        source = full_text.strip()
        rendered = source
        artifacts: list[dict] = []
        if source and on_artifact:
            rendered, artifacts = _extract_artifacts(source)

        self._copy_text = rendered or source or full_text
        self._md_source = rendered or None

        if rendered:
            self.label.show()
            MessageBubble._render_final_markdown(self, rendered)
        else:
            self.label.hide()

        if on_artifact:
            for artifact in artifacts:
                on_artifact(artifact)

    def _render_final_markdown(self, source: str):
        if len(source) < _ASYNC_MARKDOWN_RENDER_CHARS:
            MessageBubble._apply_markdown_html(self, source, _to_html(source))
            return
        self._markdown_render_generation += 1
        generation = self._markdown_render_generation
        self._md_html = None
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setText(source)
        worker = _MarkdownRenderWorker(generation, source)
        worker.signals.done.connect(self._on_markdown_render_done)
        self._markdown_render_pool.start(worker)

    def _on_markdown_render_done(self, generation: int, source: str, html_text: str):
        if generation != self._markdown_render_generation or source != self._md_source:
            return
        MessageBubble._apply_markdown_html(self, source, html_text)

    def _apply_markdown_html(self, source: str, html_text: str):
        self._md_source = source
        self._md_html = html_text
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setText(html_text)

    def apply_appearance(self):
        fs = chat_font_pt()
        self.edit_input.setStyleSheet(edit_bubble_style(fs))
        if self._timestamp_lbl:
            self._timestamp_lbl.setStyleSheet(timestamp_style())
        if self._usage_lbl:
            self._usage_lbl.setStyleSheet(timestamp_style())
        if self._speaker_lbl:
            self._speaker_lbl.setStyleSheet(
                crew_name_style(self._crew_id, self._crew_color)
            )
        if self._is_user:
            self.label.setStyleSheet(bubble_label_style(True, fs))
            if self._copy_text and not self._typing and not self._editing:
                self._show_user_text(self._copy_text)
            return
        self.label.setStyleSheet(
            bubble_label_style(False, fs, self._crew_id, self._crew_color)
        )
        if self._stream_view is not None:
            self._stream_view.setStyleSheet(
                bubble_label_style(False, fs, self._crew_id, self._crew_color)
            )
        if self._md_html:
            self.label.setTextFormat(Qt.TextFormat.RichText)
            self.label.setText(self._md_html)

    def _on_link(self, href: str):
        code = code_from_copy_url(href)
        if code is not None:
            QGuiApplication.clipboard().setText(code)
            return
        if href.startswith("aichs-file:"):
            self.file_clicked.emit(href[len("aichs-file:"):])

    def _context_menu(self, pos, widget=None):
        menu = QMenu(self)
        if self._copy_text:
            copy = QAction("Copy", self)
            copy.triggered.connect(self._copy_to_clipboard)
            menu.addAction(copy)
        if self._history_index >= 0:
            if self._can_regenerate and not self._is_user and not self._typing:
                regen = QAction("Regenerate", self)
                regen.triggered.connect(
                    lambda: self.regenerate_requested.emit(self._history_index)
                )
                menu.addAction(regen)
            if self._is_user and not self._typing:
                edit = QAction("Edit & resend", self)
                edit.triggered.connect(self._start_edit)
                menu.addAction(edit)
            branch = QAction("Branch from here", self)
            branch.triggered.connect(
                lambda: self.branch_requested.emit(self._history_index)
            )
            menu.addAction(branch)
        if menu.actions():
            origin = widget or self.label
            menu.exec(origin.mapToGlobal(pos))

    def _copy_to_clipboard(self):
        QGuiApplication.clipboard().setMimeData(self._copy_mime())

    def _copy_mime(self) -> QMimeData:
        text = self._selected_or_copy_text()
        mime = QMimeData()
        mime.setText(text)
        mime.setData(AICHS_MESSAGE_COPY_MIME, file_refs_payload(text))
        return mime

    def eventFilter(self, obj, event):
        stream_view = getattr(self, "_stream_view", None)
        if obj in (self.label, stream_view) and event.type() == QEvent.Type.KeyPress:
            copy_mods = (
                Qt.KeyboardModifier.ControlModifier |
                Qt.KeyboardModifier.MetaModifier
            )
            if event.key() == Qt.Key.Key_C and event.modifiers() & copy_mods:
                self._copy_to_clipboard()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _selected_or_copy_text(self) -> str:
        stream_view = getattr(self, "_stream_view", None)
        if stream_view is not None and stream_view.isVisible():
            cursor = stream_view.textCursor()
            if cursor.hasSelection():
                return cursor.selectedText().replace("\u2029", "\n")
        try:
            if self.label.hasSelectedText():
                return self.label.selectedText().replace("\u2029", "\n")
        except AttributeError:
            pass
        return self._copy_text

    def set_regenerable(self, enabled: bool):
        self._can_regenerate = bool(enabled)

    def set_usage(self, usage: dict | None):
        if not self._usage_lbl:
            return
        text = usage_summary(usage)
        self._usage_lbl.setText(text)
        self._usage_lbl.setVisible(bool(text))
