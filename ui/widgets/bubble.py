import base64
import re
from datetime import datetime, date

import markdown as _md
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QSizePolicy, QMenu, QTextEdit,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QAction, QGuiApplication

from services.content import content_text, file_blocks, image_blocks
from ui.avatars import avatar_label
from ui.theme import (
    palette, chat_font_pt, bubble_label_style, composer_style, edit_bubble_style,
    markdown_css, markdown_file_link_style, timestamp_style,
)

_CODE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_PATH_RE  = re.compile(
    r"<code>([^<]*?(?:/[^<]*?\.(?:py|md|json|yaml|yml|toml|sh|js|ts|tsx|jsx|css|html|txt))[^<]*?)</code>"
)


def _linkify_paths(html: str) -> str:
    return _PATH_RE.sub(
        lambda m: (
            f'<code><a class="aicc-file-link" href="aicc-file:{m.group(1)}" '
            f'style="{markdown_file_link_style()}">'
            f'{m.group(1)}</a></code>'
        ),
        html,
    )


def _to_html(text: str) -> str:
    html = _md.markdown(text, extensions=["nl2br", "tables"])
    return f"<style>{markdown_css()}</style>{_linkify_paths(html)}"


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


class MessageBubble(QFrame):
    _DOTS = ["●", "● ●", "● ● ●"]

    regenerate_requested  = pyqtSignal(int)
    edit_resend_requested = pyqtSignal(int, str)
    branch_requested      = pyqtSignal(int)
    file_clicked          = pyqtSignal(str)   # relative or absolute path

    def __init__(self, content="", is_user=True, typing=False,
                 history_index: int = -1, timestamp: str = "", parent=None):
        super().__init__(parent)
        self._is_user = is_user
        self._history_index = history_index
        self._content = content
        self._editing = False
        self._timestamp_lbl = None
        self._md_source: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        if timestamp:
            self.setToolTip(format_timestamp(timestamp))

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 6, 20, 6)
        row.setSpacing(8)

        portrait = avatar_label("human" if is_user else "agent")

        self.body = QVBoxLayout()
        self.body.setSpacing(4)

        for img in image_blocks(content):
            self.body.addWidget(
                self._image_label(img),
                0,
                Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft,
            )
        for file in file_blocks(content):
            self.body.addWidget(
                self._file_label(file),
                0,
                Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft,
            )

        text = content_text(content) if not typing else ""
        self._copy_text = text
        self.label = QLabel(text)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(480)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.label.setOpenExternalLinks(False)
        self.label.linkActivated.connect(self._on_link)
        self.label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.label.customContextMenuRequested.connect(self._context_menu)
        if is_user and not typing:
            self.label.mouseDoubleClickEvent = lambda e: self._start_edit()

        self.edit_input = _EditInput(text)
        self.edit_input.hide()
        self.edit_input.submitted.connect(self._commit_edit)
        self.edit_input.cancelled.connect(self._cancel_edit)

        if is_user:
            self.label.setStyleSheet(bubble_label_style(True))
            if text or typing:
                self.body.addWidget(self.label, 0, Qt.AlignmentFlag.AlignRight)
            self.body.addWidget(self.edit_input, 0, Qt.AlignmentFlag.AlignRight)
        else:
            self.label.setStyleSheet(bubble_label_style(False))
            self.body.addWidget(self.label, 0, Qt.AlignmentFlag.AlignLeft)
            self.body.addWidget(self.edit_input, 0, Qt.AlignmentFlag.AlignLeft)

        if timestamp:
            self._timestamp_lbl = QLabel(format_timestamp(timestamp))
            self._timestamp_lbl.setStyleSheet(timestamp_style())
            self._timestamp_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft
            )
            self.body.addWidget(self._timestamp_lbl)

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

    @staticmethod
    def _file_label(block: dict) -> QLabel:
        lbl = QLabel()
        path = block.get("path", "file")
        suffix = " (truncated)" if block.get("truncated") else ""
        lbl.setText(f"Attached file: {path}{suffix}")
        p = palette()
        lbl.setStyleSheet(
            f"background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px; padding:6px 8px;"
        )
        return lbl

    def _start_edit(self):
        if self._typing or self._editing or not self._is_user:
            return
        self._editing = True
        self.label.hide()
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

    def _cancel_edit(self):
        self.edit_input.hide()
        self.label.show()
        self._editing = False

    def _start_typing(self):
        self._typing = True
        self._dot_step = 0
        self.label.setText(self._DOTS[0])
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
        self.label.setText(self._copy_text)

    def is_empty_typing(self) -> bool:
        return self._typing and not self._copy_text

    def finalize(self, full_text: str, on_artifact=None):
        """Render markdown and extract code blocks as artifact cards."""
        if self._is_user:
            return
        blocks = _CODE_RE.findall(full_text) if on_artifact else []
        clean  = _CODE_RE.sub("", full_text).strip() if blocks else full_text.strip()
        self._copy_text = clean or full_text
        self._md_source = clean or None

        if clean:
            self.label.setTextFormat(Qt.TextFormat.RichText)
            self.label.setText(_to_html(clean))
        else:
            self.label.hide()

        if on_artifact:
            for lang, code in blocks:
                on_artifact(lang.strip(), code)

    def apply_appearance(self):
        fs = chat_font_pt()
        self.label.setStyleSheet(bubble_label_style(self._is_user, fs))
        self.edit_input.setStyleSheet(edit_bubble_style(fs))
        if self._timestamp_lbl:
            self._timestamp_lbl.setStyleSheet(timestamp_style())
        if self._md_source and not self._is_user:
            self.label.setTextFormat(Qt.TextFormat.RichText)
            self.label.setText(_to_html(self._md_source))

    def _on_link(self, href: str):
        if href.startswith("aicc-file:"):
            self.file_clicked.emit(href[len("aicc-file:"):])

    def _context_menu(self, pos):
        menu = QMenu(self)
        if self._copy_text:
            copy = QAction("Copy", self)
            copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(self._copy_text))
            menu.addAction(copy)
        if self._history_index >= 0:
            if not self._is_user and not self._typing:
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
            menu.exec(self.label.mapToGlobal(pos))
