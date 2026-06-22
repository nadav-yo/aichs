import re
from dataclasses import dataclass

from PyQt6.QtCore import QStringListModel, Qt
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QCompleter,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from services.crew import all_crew, get_crew_member
from ui.theme import palette
from ui.widgets.agent_canvas_file_scope import repo_path_candidates


_FILE_MENTION_RE = re.compile(r'@"([^"\n]+)"|@([A-Za-z0-9_./\\:-]+)')


@dataclass(frozen=True)
class CanvasAgent:
    id: str
    name: str
    title: str


DEFAULT_CANVAS_AGENT = CanvasAgent("coder", "Coder", "Default coding agent")


def canvas_agents() -> tuple[CanvasAgent, ...]:
    return (DEFAULT_CANVAS_AGENT,) + tuple(
        CanvasAgent(member.id, member.name, member.title)
        for member in all_crew()
    )


def canvas_agent_for_id(agent_id: str) -> CanvasAgent | None:
    normalized = str(agent_id or "").strip().casefold()
    if normalized == DEFAULT_CANVAS_AGENT.id:
        return DEFAULT_CANVAS_AGENT
    member = get_crew_member(normalized)
    if member is None:
        return None
    return CanvasAgent(member.id, member.name, member.title)


def canvas_agent_id_for_title(title: str) -> str:
    normalized = str(title or "").strip().casefold()
    for agent in canvas_agents():
        if normalized in {agent.id.casefold(), agent.name.casefold()}:
            return agent.id
    return ""


class AgentCanvasInspector(QFrame):
    def __init__(
        self,
        repo_root: str,
        *,
        apply_requested,
        cancel_requested,
        generate_steps_requested,
        add_scope_path_requested,
        open_scope_requested,
        frame_color_requested,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("canvasInspector")
        self.setFixedWidth(340)
        self._repo_root = repo_root

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel("Inspector")
        title.setObjectName("canvasPanelTitle")
        self.selected = InspectorMetaRow("Selected: Goal", selected=True)
        layout.addWidget(title)
        layout.addWidget(self.selected)

        self.lines: list[InspectorMetaRow] = []
        for line in (
            "Type: Goal",
            "Status: idle",
            "Activity: select / move",
            "Role: intent",
            "Purpose: Define what good looks like",
        ):
            row = InspectorMetaRow(line)
            self.lines.append(row)
            layout.addWidget(row)

        layout.addSpacing(8)
        layout.addWidget(self._field_label("Title"))
        self.edit_title = QLineEdit()
        self.edit_title.setObjectName("canvasInspectorField")
        self.edit_title.returnPressed.connect(apply_requested)
        layout.addWidget(self.edit_title)

        self.agent_label = self._field_label("Crew")
        layout.addWidget(self.agent_label)
        self.agent_combo = QComboBox()
        self.agent_combo.setObjectName("canvasInspectorCombo")
        self.agent_combo.addItem("Unassigned", "")
        for agent in canvas_agents():
            self.agent_combo.addItem(f"{agent.name} - {agent.title}", agent.id)
        layout.addWidget(self.agent_combo)

        self.detail_label = self._field_label("Description")
        layout.addWidget(self.detail_label)
        self.edit_detail = RepoPathTextEdit()
        self.edit_detail.setObjectName("canvasInspectorText")
        self.edit_detail.setAcceptRichText(False)
        self.edit_detail.setPlaceholderText("Describe what this node means, when it is done, or what future agents should know.")
        layout.addWidget(self.edit_detail, 1)

        self.frame_color_label = self._field_label("Background color")
        layout.addWidget(self.frame_color_label)
        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        self.frame_color_button = QPushButton("")
        self.frame_color_button.setObjectName("canvasFrameColorButton")
        self.frame_color_button.setFixedSize(34, 30)
        self.frame_color_button.setToolTip("Pick background color")
        self.frame_color_button.clicked.connect(frame_color_requested)
        self.frame_color_field = QLineEdit()
        self.frame_color_field.setObjectName("canvasInspectorField")
        self.frame_color_field.setPlaceholderText("#2f8f62")
        self.frame_color_field.returnPressed.connect(apply_requested)
        color_row.addWidget(self.frame_color_button)
        color_row.addWidget(self.frame_color_field, 1)
        layout.addLayout(color_row)

        self.generate_steps_btn = QPushButton("Generate Steps")
        self.generate_steps_btn.clicked.connect(generate_steps_requested)
        self.generate_steps_btn.setToolTip("Create a runnable work branch from this goal.")
        layout.addWidget(self.generate_steps_btn)

        self.scope_path_label = self._field_label("Add path")
        layout.addWidget(self.scope_path_label)
        self.scope_path_field = RepoPathLineEdit()
        self.scope_path_field.setObjectName("canvasInspectorField")
        self.scope_path_field.setPlaceholderText("Start typing a repo path")
        self.scope_path_field.returnPressed.connect(add_scope_path_requested)
        layout.addWidget(self.scope_path_field)
        self._build_scope_completer()

        self.open_scope_btn = QPushButton("Open Path")
        self.open_scope_btn.clicked.connect(open_scope_requested)
        layout.addWidget(self.open_scope_btn)

        self.cancel_edit_btn = QPushButton("Cancel")
        self.cancel_edit_btn.clicked.connect(cancel_requested)
        self.apply_edit_btn = QPushButton("Update")
        self.apply_edit_btn.clicked.connect(apply_requested)
        self.cancel_edit_btn.setVisible(False)
        self.apply_edit_btn.setVisible(False)
        layout.addStretch()

    def set_repo_root(self, repo_root: str):
        self._repo_root = repo_root
        candidates = repo_path_candidates(self._repo_root)
        self.scope_path_field.set_candidates(candidates)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("canvasInspectorFieldLabel")
        return label

    def _build_scope_completer(self):
        self.scope_path_model = self.scope_path_field.path_model
        self.scope_path_completer = self.scope_path_field.path_completer
        candidates = repo_path_candidates(self._repo_root)
        self.scope_path_field.set_candidates(candidates)


class InspectorMetaRow(QFrame):
    def __init__(self, text: str, *, selected: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("canvasInspectorSelectedRow" if selected else "canvasInspectorMetaRow")
        self.caption = QLabel()
        self.caption.setObjectName("canvasInspectorMetaLabel")
        self.value = QLabel()
        self.value.setObjectName("canvasInspectorSelectedValue" if selected else "canvasInspectorMetaValue")
        self.value.setWordWrap(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(10)
        layout.addWidget(self.caption, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.value, 1)

        self.setText(text)

    def setText(self, text: str):
        label, value = self._split_text(text)
        self.caption.setText(label)
        self.value.setText(value)

    def text(self) -> str:
        label = self.caption.text().strip()
        value = self.value.text().strip()
        return f"{label}: {value}" if label else value

    def setWordWrap(self, enabled: bool):
        self.value.setWordWrap(enabled)

    @staticmethod
    def _split_text(text: str) -> tuple[str, str]:
        raw = str(text or "").strip()
        if ":" not in raw:
            return "", raw
        label, value = raw.split(":", 1)
        return label.strip(), value.strip()


class RepoPathLineEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path_model = QStringListModel(self)
        self.path_completer = QCompleter(self.path_model, self)
        self.path_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.path_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.path_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.path_completer.setMaxVisibleItems(12)
        self.path_completer.activated[str].connect(self._insert_completion)
        self.setCompleter(self.path_completer)
        self.textEdited.connect(self._show_path_completions)

    def set_candidates(self, candidates: list[str]):
        self.path_model.setStringList(candidates)

    def keyPressEvent(self, event: QKeyEvent):
        if self.path_completer.popup().isVisible() and event.key() in {
            Qt.Key.Key_Down,
            Qt.Key.Key_Up,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Escape,
        }:
            event.ignore()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._show_path_completions(self.text())

    def _show_path_completions(self, text: str) -> bool:
        prefix = self._completion_prefix(text)
        if not prefix:
            self.path_completer.popup().hide()
            return False
        self.path_completer.setCompletionPrefix(prefix)
        if self.path_completer.completionCount() <= 0:
            self.path_completer.popup().hide()
            return False
        self.path_completer.complete()
        return True

    def _insert_completion(self, value: str):
        self.setText(value)
        self.setCursorPosition(len(value))

    @staticmethod
    def _completion_prefix(text: str) -> str:
        return str(text or "").strip().lstrip("@").strip('"')


class RepoPathTextEdit(QTextEdit):
    POPUP_MARGIN = 6
    POPUP_ROW_HEIGHT = 24
    POPUP_MAX_HEIGHT = 176

    def __init__(self, parent=None):
        super().__init__(parent)
        self._candidates: list[str] = []
        self._completion_prefix = ""
        self._completion_mode = "mentions"
        self.path_model = QStringListModel(self)
        self.path_popup = QListView(self)
        self.path_popup.setModel(self.path_model)
        self.path_popup.hide()
        self.path_popup.clicked.connect(lambda index: self._insert_completion(str(index.data() or "")))
        self._mention_highlighter = _FileMentionHighlighter(self.document())
        self.textChanged.connect(self._show_current_path_completions)

    def set_candidates(self, candidates: list[str]):
        self._candidates = list(candidates)
        self.setCompletionPrefix(self._completion_prefix)

    def set_completion_mode(self, mode: str):
        mode = "paths" if mode == "paths" else "mentions"
        if self._completion_mode == mode:
            return
        self._completion_mode = mode
        self.path_popup.hide()
        self._completion_prefix = ""
        self.path_model.setStringList([])

    def completer(self):
        return self

    def popup(self):
        return self.path_popup

    def model(self):
        return self.path_model

    def setCompletionPrefix(self, prefix: str):
        self._completion_prefix = str(prefix or "").strip().lstrip("@").strip('"')
        folded = self._completion_prefix.casefold()
        matches = [
            candidate
            for candidate in self._candidates
            if folded and folded in candidate.casefold()
        ][:12]
        self.path_model.setStringList(matches)

    def completionCount(self) -> int:
        return self.path_model.rowCount()

    def keyPressEvent(self, event: QKeyEvent):
        if self.path_popup.isVisible() and event.key() == Qt.Key.Key_Escape:
            self.path_popup.hide()
            event.accept()
            return
        if self.path_popup.isVisible() and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab}:
            index = self.path_popup.currentIndex()
            if not index.isValid() and self.path_model.rowCount() > 0:
                index = self.path_model.index(0, 0)
            if index.isValid():
                self._insert_completion(str(index.data() or ""))
                event.accept()
                return
        if self.path_popup.isVisible() and event.key() in {Qt.Key.Key_Down, Qt.Key.Key_Up}:
            row = self.path_popup.currentIndex().row()
            delta = 1 if event.key() == Qt.Key.Key_Down else -1
            row = max(0, min(self.path_model.rowCount() - 1, row + delta))
            self.path_popup.setCurrentIndex(self.path_model.index(row, 0))
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._show_current_path_completions()

    def _show_current_path_completions(self) -> bool:
        prefix = self._current_line_prefix()
        if not prefix or self._is_exact_candidate(prefix):
            self.path_popup.hide()
            return False
        self.setCompletionPrefix(prefix)
        if self.completionCount() <= 0:
            self.path_popup.hide()
            return False
        rect = self.cursorRect()
        margin = self.POPUP_MARGIN
        available_width = max(80, self.viewport().width() - margin * 2)
        height = min(self.POPUP_MAX_HEIGHT, self.POPUP_ROW_HEIGHT * self.completionCount() + 8)
        y = rect.bottom() + 2
        if y + height > self.viewport().height() - margin:
            y = max(margin, rect.top() - height - 2)
        self.path_popup.setGeometry(margin, y, available_width, height)
        self.path_popup.setCurrentIndex(self.path_model.index(0, 0))
        self.path_popup.show()
        return True

    def _insert_completion(self, value: str):
        if not value:
            return
        cursor = self.textCursor()
        prefix, start = self._completion_span()
        if self._completion_mode == "mentions":
            value = _file_mention_token(value)
        cursor.setPosition(start)
        cursor.setPosition(self.textCursor().position(), cursor.MoveMode.KeepAnchor)
        cursor.insertText(value)
        self.setTextCursor(cursor)
        self.path_popup.hide()

    def _current_line_prefix(self) -> str:
        prefix, _start = self._completion_span()
        return prefix

    def _completion_span(self) -> tuple[str, int]:
        cursor = self.textCursor()
        text = cursor.block().text()
        pos = cursor.positionInBlock()
        before = text[:pos]
        if self._completion_mode == "paths":
            prefix = before or text
            start = cursor.position() - len(before)
            return prefix.strip().lstrip("@").strip('"'), start

        at = max(before.rfind("@"), before.rfind('@"'))
        if at < 0:
            return "", cursor.position()
        if at > 0 and not before[at - 1].isspace():
            return "", cursor.position()
        raw = before[at + 1 :]
        if raw.startswith('"'):
            raw = raw[1:]
        return raw.strip(), cursor.position() - (pos - at)

    def _is_exact_candidate(self, prefix: str) -> bool:
        normalized = str(prefix or "").replace("\\", "/").casefold()
        return any(candidate.replace("\\", "/").casefold() == normalized for candidate in self._candidates)


class _FileMentionHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text: str):
        p = palette()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(p["FILE_LINK"]))
        fmt.setBackground(QColor(p["SELECTION"]))
        fmt.setFontWeight(QFont.Weight.DemiBold)
        for match in _FILE_MENTION_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), fmt)


def _file_mention_token(path: str) -> str:
    value = str(path or "").replace("\\", "/").strip()
    if not value:
        return ""
    if any(ch.isspace() for ch in value):
        return f'@"{value}"'
    return f"@{value}"
