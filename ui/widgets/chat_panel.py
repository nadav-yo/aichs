import copy
import html
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame,
    QLabel, QPushButton, QComboBox, QSizePolicy, QMenu,
)
from PyQt6.QtCore import Qt, QPoint, QPointF, QSize, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTextCursor

from config import IGNORED, MAX_FILE_PREVIEW_BYTES, MAX_TOOL_READ_BYTES
from config import MODELS, MODEL_PROVIDER
from storage.repository import ConversationStore
from storage.settings import SettingsStore
from services.chat import ChatThread
from services.crew import (
    CrewMember,
    all_crew,
    crew_enabled,
    crew_metadata,
    crew_model_choice,
    crew_name_from_metadata,
    crew_prompt,
    crew_settings,
    crew_system_prompt,
    get_crew_member,
    summoned_members,
)
from services.crew_context import crew_context_window
from services.tool_policy import ConversationToolPolicy, ToolApprovalBus, path_in_repo
from ui.widgets.tool_approval_dialog import confirm_process_start, handle_pending_approval
from services.compaction import CompactionThread, should_compact, can_compact, _estimate_tokens
from services.content import (
    build_user_content,
    content_preview,
    is_visible_message,
    prepare_for_storage,
)
from services.auto_title import TitleThread
from services.context_budget import analyze_context
from services.model_registry import api_key_env_var, get_provider_config, load_user_providers
from services.workspace import build_system
from services.export import export_conversation_dialog
from services.processes import RuntimeProcessApi, get_process_manager
from services.tool_registry import (
    RuntimeCommandApi,
    extension_errors,
    extension_overview,
    run_extension_command,
)
from ui.theme import (
    palette, input_bar_style, separator_color,
    send_button_style, stop_button_style, floating_button_style,
    tool_notice_style, center_notice_style, icon_button_style,
)
from services.skills import Skill, load_all as load_skills
from services.shell_tool import is_shell_tool
from services.terminal_refs import terminal_ref
from services.user_terminal import UserTerminalThread
from services.slash_commands import (
    load_all_commands,
    parse_builtin_command,
    parse_builtin_prompt_command,
    parse_extension_command,
    slash_invocation,
)
from ui.widgets.bubble import MessageBubble
from ui.widgets.code_card import ArtifactCard
from ui.widgets.message_input import ComposerWidget
from ui.widgets.file_mention_picker import FileMentionPicker
from ui.widgets.skill_picker import SkillPicker
from ui.widgets.terminal_card import TerminalCard
from ui.widgets.context_ring import ContextRing
from ui.widgets.context_breakdown import ContextBreakdownDialog
from ui.widgets.extension_contributions import ExtensionContributionsBar
from ui.widgets.extensions_dialog import ExtensionsDialog

_INITIAL_RENDER_BYTES = 1 * 1024 * 1024
_INITIAL_RENDER_MESSAGES = 150
_OLDER_RENDER_BYTES = 512 * 1024
_OLDER_RENDER_MESSAGES = 75


def _composer_send_icon() -> QIcon:
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.moveTo(QPointF(3.0, 9.0))
    path.lineTo(QPointF(15.5, 3.0))
    path.lineTo(QPointF(11.0, 15.0))
    path.lineTo(QPointF(8.6, 10.6))
    path.closeSubpath()
    painter.setPen(QPen(QColor("#dbeafe"), 1.5))
    painter.setBrush(QColor(255, 255, 255, 24))
    painter.drawPath(path)
    painter.drawLine(QPointF(8.6, 10.6), QPointF(15.5, 3.0))
    painter.end()
    return QIcon(pix)


def _composer_stop_icon() -> QIcon:
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#fee2e2"))
    painter.drawRoundedRect(5, 5, 8, 8, 2, 2)
    painter.end()
    return QIcon(pix)


@dataclass
class _AssistantRun:
    run_id: str
    conv_id: str
    thread: ChatThread
    model: str
    history_snapshot: list[dict]
    data_snapshot: dict
    partial_text: str = ""
    rendered_chars: int = 0
    bubble: MessageBubble | None = None
    last_edit_path: str = ""
    last_tool_name: str = ""
    last_tool_inputs: dict = field(default_factory=dict)
    active_terminal: TerminalCard | None = None
    crew: dict | None = None
    crew_bubbles: dict[str, MessageBubble] = field(default_factory=dict)


@dataclass
class _CompactionRun:
    conv_id: str
    thread: CompactionThread
    model: str
    history_snapshot: list[dict]
    data_snapshot: dict


def _compact_text(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _display_tool_path(path: str, cwd: str) -> str:
    if not path:
        return "file"
    try:
        p = Path(path)
        resolved = p if p.is_absolute() else Path(cwd) / p
        rel = resolved.resolve().relative_to(Path(cwd).resolve())
        return str(rel)
    except Exception:
        return path


def _tool_call_notice(name: str, inputs: dict, cwd: str) -> str:
    if name == "read_file":
        path = _display_tool_path(str(inputs.get("path") or ""), cwd)
        offset = inputs.get("offset")
        limit = inputs.get("limit")
        if offset is not None or limit is not None:
            detail = f" from line {offset or 1}"
            if limit is not None:
                detail += f", {limit} lines"
            return f"Reading file '{path}'{detail}"
        return f"Reading file '{path}'"
    if name == "edit_file":
        path = _display_tool_path(str(inputs.get("path") or ""), cwd)
        action = "Creating" if "content" in inputs else "Editing"
        return f"{action} file '{path}'"
    if name == "search_files":
        pattern = _compact_text(inputs.get("pattern") or "")
        directory = _display_tool_path(str(inputs.get("directory") or "."), cwd)
        if pattern:
            return f"Searching files for '{pattern}' in '{directory}'"
        return f"Searching files in '{directory}'"
    if name == "list_files":
        directory = _display_tool_path(str(inputs.get("directory") or "."), cwd)
        glob = _compact_text(inputs.get("glob") or "*")
        return f"Listing files in '{directory}' matching '{glob}'"
    if name == "search_project_chats":
        query = _compact_text(inputs.get("query") or "", 96)
        return f"Searching project chat history for '{query}'" if query else "Searching project chat history"
    if is_shell_tool(name):
        command = _compact_text(inputs.get("command") or "")
        return f"Running command: {command}" if command else "Running command"
    if name == "web_fetch":
        url = _compact_text(inputs.get("url") or "", 96)
        return f"Fetching web page '{url}'" if url else "Fetching web page"
    for label in ("url", "path", "query"):
        value = _compact_text(inputs.get(label) or "", 96)
        if value:
            return f"Using tool '{name}' with {label} '{value}'"
    return f"Using tool '{name}'"


def _tool_notice_html(text: str) -> str:
    p = palette()
    raw = str(text or "")
    label = "Tool"
    detail = raw
    code_detail = False

    if raw.startswith("Running command: "):
        label = "Command"
        detail = raw[len("Running command: "):]
        code_detail = True
    elif raw == "Running command":
        label = "Command"
        detail = "running"
    elif raw.startswith("Reading file "):
        label = "Read"
        detail = raw[len("Reading file "):].strip("'")
        code_detail = True
    elif raw.startswith("Searching files"):
        label = "Search"
        detail = raw[len("Searching "):]
    elif raw.startswith("Listing files"):
        label = "List"
        detail = raw[len("Listing "):]
    elif raw.startswith(("Editing file ", "Creating file ")):
        label, detail = raw.split(" file ", 1)
        detail = detail.strip("'")
        code_detail = True
    elif raw.startswith("Fetching web page "):
        label = "Fetch"
        detail = raw[len("Fetching web page "):].strip("'")
    elif raw.startswith("Tool error: "):
        label = "Tool error"
        detail = raw[len("Tool error: "):]

    label_html = html.escape(label)
    detail_html = html.escape(detail)
    if code_detail:
        detail_html = detail_html.replace(" ", "&nbsp;")
        detail_html = (
            f"<code style=\"background:transparent; color:{p['TEXT']};"
            "font-family:Consolas, monospace;\">"
            f"{detail_html}</code>"
        )
    return (
        f"<span style=\"color:{p['TEXT_DIM']};\">{label_html}</span>"
        f"<span style=\"color:{p['TEXT_DIM']};\">&nbsp;&nbsp;&middot;&nbsp;&nbsp;</span>"
        f"<span style=\"color:{p['TEXT']};\">{detail_html}</span>"
    )


def _tool_debug_text(name: str, inputs: dict | None, output: str, cwd: str) -> str:
    inputs = dict(inputs or {})
    lines = [
        f"Tool: {name}",
        f"CWD: {cwd}",
        "Inputs:",
        _json_debug(inputs) if inputs else "(not captured)",
    ]
    if output:
        lines.extend(["Output:", str(output)])
    if name == "edit_file":
        lines.extend(_edit_file_debug_lines(inputs, cwd))
    return "\n".join(lines)


def _edit_file_debug_lines(inputs: dict, cwd: str) -> list[str]:
    lines = ["edit_file debug:"]
    raw_path = str(inputs.get("path") or "")
    if not raw_path:
        lines.append("  path: (missing)")
        return lines

    try:
        resolved = (Path(raw_path) if Path(raw_path).is_absolute() else Path(cwd) / raw_path).resolve()
    except OSError as exc:
        lines.append(f"  path resolve error: {exc}")
        return lines

    lines.append(f"  path: {raw_path}")
    lines.append(f"  resolved: {resolved}")
    inside = path_in_repo(resolved, cwd)
    lines.append(f"  inside workspace: {inside}")
    if not inside:
        return lines
    lines.append(f"  exists: {resolved.exists()}")
    lines.append(f"  is file: {resolved.is_file()}")
    if not resolved.is_file():
        return lines

    try:
        with resolved.open("r", encoding="utf-8", errors="replace", newline="") as f:
            current = f.read()
    except OSError as exc:
        lines.append(f"  read error: {exc}")
        return lines

    lines.append(f"  current size chars: {len(current)}")
    edits = inputs.get("edits")
    if not isinstance(edits, list):
        lines.append("  edits: (not an edits-mode call)")
        return lines
    for idx, edit in enumerate(edits):
        if not isinstance(edit, dict):
            lines.append(f"  edits[{idx}]: non-object")
            continue
        old_text = edit.get("oldText")
        if not isinstance(old_text, str):
            lines.append(f"  edits[{idx}].oldText: non-string")
            continue
        exact = current.count(old_text)
        lines.append(f"  edits[{idx}].oldText exact occurrences: {exact}")
        newline_flexible = _newline_flexible_count(current, old_text)
        if newline_flexible != exact:
            lines.append(
                f"  edits[{idx}].oldText newline-flexible occurrences: "
                f"{newline_flexible}"
            )
        lines.append(f"  edits[{idx}].oldText repr: {_debug_repr(old_text)}")
        stripped = old_text.rstrip("\r\n")
        if stripped != old_text:
            lines.append(
                f"  edits[{idx}].oldText without trailing newline occurrences: "
                f"{current.count(stripped)}"
            )
    return lines


def _newline_flexible_count(current: str, old_text: str) -> int:
    if not any(ch in old_text for ch in "\r\n"):
        return current.count(old_text)
    normalized_old = old_text.replace("\r\n", "\n").replace("\r", "\n")
    pattern = "(?:\\r\\n|\\n|\\r)".join(
        re.escape(part) for part in normalized_old.split("\n")
    )
    return len(list(re.finditer(pattern, current)))


def _json_debug(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _debug_repr(value: str, limit: int = 500) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class _ConversationRuntime:
    queued: list[dict] = field(default_factory=list)
    run: _AssistantRun | None = None
    compaction: _CompactionRun | None = None
    tool_policy: ConversationToolPolicy = field(default_factory=ConversationToolPolicy)


class _MessageListContainer(QWidget):
    """Message column; notifies when layout height changes (new/tall bubbles)."""

    def __init__(self, on_resize=None, parent=None):
        super().__init__(parent)
        self._on_resize = on_resize

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._on_resize:
            self._on_resize()


class _ScrollHost(QWidget):
    """Scroll area container with a floating jump-to-bottom button."""

    def __init__(self, scroll: QScrollArea, jump_btn: QPushButton, parent=None):
        super().__init__(parent)
        self._jump_btn = jump_btn

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        jump_btn.setParent(self)
        jump_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        btn = self._jump_btn
        btn.move(self.width() - btn.width() - 16, self.height() - btn.height() - 16)


class ChatPanel(QWidget):
    saved        = pyqtSignal()
    conversation_created = pyqtSignal(str)
    open_code    = pyqtSignal(str, str)   # content, title
    open_file    = pyqtSignal(str, object)
    file_written = pyqtSignal(str)
    file_write_completed = pyqtSignal(str)

    def __init__(self, store: ConversationStore, cwd: str = "",
                 settings: SettingsStore | None = None, parent=None):
        super().__init__(parent)
        self.store              = store
        self._settings          = settings or SettingsStore()
        self.cwd                = cwd or os.getcwd()
        self._approval_bus      = ToolApprovalBus(self)
        self._approval_bus.approval_needed.connect(self._on_approval_needed)
        self.history            = []
        self.conv_id            = None
        self.conv_data          = None
        self.active_bubble      = None
        self.thread             = None
        self.compaction_thread  = None
        self.title_thread       = None
        self._last_edit_path    = ""
        self._active_terminal   = None
        self._runtimes: dict[str, _ConversationRuntime] = {}
        self._orphan_threads: list[QThread] = []
        self._user_terminal_threads: list[UserTerminalThread] = []
        self._auto_scroll       = True
        self._programmatic_scroll = False
        self._history_prepend_enabled = True
        self._last_scroll_value = 0
        self._bubbles: dict[int, MessageBubble] = {}
        self._render_start_index = 0
        self._older_btn: QPushButton | None = None
        self._stream_buffer: list[str] = []
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setInterval(100)
        self._stream_flush_timer.timeout.connect(self._flush_stream_buffer)
        self._scroll_layout_timer = QTimer(self)
        self._scroll_layout_timer.setSingleShot(True)
        self._scroll_layout_timer.setInterval(100)
        self._scroll_layout_timer.timeout.connect(
            lambda: self._scroll_to_bottom(force=True)
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # top bar
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 10, 16, 8)
        bar.setSpacing(6)

        self.provider_combo = QComboBox()
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(220)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)

        bar.addWidget(self.provider_combo)
        bar.addWidget(self.model_combo)

        self._extension_bar = ExtensionContributionsBar(
            self.cwd,
            on_action=self._handle_extension_action,
            parent=self,
        )
        bar.addWidget(self._extension_bar)

        bar.addStretch()

        self.extensions_btn = QPushButton("Extensions")
        self.extensions_btn.setToolTip("View loaded extensions")
        self.extensions_btn.clicked.connect(self.show_extensions)
        bar.addWidget(self.extensions_btn)

        self.context_ring = ContextRing()
        self.context_ring.clicked.connect(self._show_context_breakdown)
        bar.addWidget(self.context_ring)

        root.addLayout(bar)

        self.refresh_models()

        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(self._sep)

        # messages
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.msg_container = _MessageListContainer(self._on_message_list_resize)
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setContentsMargins(0, 18, 0, 18)
        self.msg_layout.setSpacing(4)
        self.msg_layout.addStretch()

        self.scroll.setWidget(self.msg_container)

        self.jump_btn = QPushButton("↓")
        self.jump_btn.setFixedSize(36, 36)
        self.jump_btn.hide()
        self.jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump_btn.clicked.connect(self._resume_auto_scroll)

        self.scroll_host = _ScrollHost(self.scroll, self.jump_btn)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self.scroll_host, 1)

        # input bar
        self._input_frame = QFrame()
        input_col = QVBoxLayout(self._input_frame)
        input_col.setContentsMargins(22, 8, 22, 10)
        input_col.setSpacing(6)

        self.composer = ComposerWidget()
        self.composer.send_requested.connect(self.send)
        self.composer.input.edit_last_requested.connect(self.edit_last_message)

        self._skill_picker: SkillPicker | None = None
        self._file_picker: FileMentionPicker | None = None
        self.composer.input.slash_changed.connect(self._on_slash_changed)
        self.composer.input.terminal_changed.connect(self._on_terminal_hint_changed)
        self.composer.input.picker_next.connect(lambda: self._skill_picker and self._skill_picker.select_next())
        self.composer.input.picker_prev.connect(lambda: self._skill_picker and self._skill_picker.select_prev())
        self.composer.input.picker_confirm.connect(lambda: self._skill_picker and self._skill_picker.confirm())
        self.composer.input.picker_complete.connect(self._complete_slash_selection)
        self.composer.input.mention_changed.connect(self._on_file_mention_changed)
        self.composer.input.mention_next.connect(lambda: self._file_picker and self._file_picker.select_next())
        self.composer.input.mention_prev.connect(lambda: self._file_picker and self._file_picker.select_prev())
        self.composer.input.mention_confirm.connect(lambda: self._file_picker and self._file_picker.confirm())

        self.btn = QPushButton("Send")
        self.btn.setFixedSize(72, 30)
        self.btn.setIcon(_composer_send_icon())
        self.btn.setIconSize(QSize(14, 14))
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self.send)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedSize(72, 30)
        self.stop_btn.setIcon(_composer_stop_icon())
        self.stop_btn.setIconSize(QSize(13, 13))
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.clicked.connect(self._stop_streaming)
        self.stop_btn.hide()

        self._queue_label = QLabel()
        self._queue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._queue_label.hide()

        self._queue_list = QWidget()
        self._queue_list_layout = QVBoxLayout(self._queue_list)
        self._queue_list_layout.setContentsMargins(0, 0, 0, 0)
        self._queue_list_layout.setSpacing(4)
        self._queue_list.hide()

        self.composer.action_row.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.composer.action_row.addWidget(self.stop_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        input_col.addWidget(self.composer)
        input_col.addWidget(self._queue_label)
        input_col.addWidget(self._queue_list)
        root.addWidget(self._input_frame)

        self._apply_chrome()
        self._apply_input_preferences()

    # ── public API ────────────────────────────────────────────────────────────

    def _runtime_for(self, conv_id: str) -> _ConversationRuntime:
        rt = self._runtimes.get(conv_id)
        if rt is None:
            rt = _ConversationRuntime()
            self._runtimes[conv_id] = rt
        return rt

    def _visible_runtime(self) -> _ConversationRuntime | None:
        return self._runtimes.get(self.conv_id) if self.conv_id else None

    def _visible_queue(self) -> list[dict]:
        rt = self._visible_runtime()
        return rt.queued if rt else []

    def _visible_run(self) -> _AssistantRun | None:
        rt = self._visible_runtime()
        return rt.run if rt else None

    def _visible_compaction(self) -> _CompactionRun | None:
        rt = self._visible_runtime()
        return rt.compaction if rt else None

    def _find_run(self, run_id: str) -> _AssistantRun | None:
        for rt in self._runtimes.values():
            run = rt.run
            if run and run.run_id == run_id:
                return run
        return None

    def _find_compaction(self, conv_id: str) -> _CompactionRun | None:
        rt = self._runtimes.get(conv_id)
        return rt.compaction if rt else None

    def _sync_visible_runtime_refs(self):
        run = self._visible_run()
        compaction = self._visible_compaction()
        self.thread = run.thread if run else None
        self.active_bubble = run.bubble if run else None
        self._last_edit_path = run.last_edit_path if run else ""
        self._active_terminal = run.active_terminal if run else None
        self.compaction_thread = compaction.thread if compaction else None

    def _detach_visible_run_ui(self):
        run = self._visible_run()
        if run:
            run.bubble = None
            run.active_terminal = None
            run.last_edit_path = self._last_edit_path
        self.active_bubble = None
        self._active_terminal = None
        self._last_edit_path = ""

    def _release_thread(self, thread: QThread | None, *, cancel: bool = False):
        """Disconnect UI and keep the QThread alive until it finishes."""
        if thread is None:
            return
        try:
            thread.disconnect()
        except TypeError:
            pass
        if cancel and hasattr(thread, "cancel"):
            thread.cancel()

        self._keep_thread_until_finished(thread)

    def _keep_thread_until_finished(self, thread: QThread | None):
        """Retain a QThread object until Qt reports it has fully stopped."""
        if thread is None:
            return
        if thread in self._orphan_threads:
            return

        def _forget():
            try:
                self._orphan_threads.remove(thread)
            except ValueError:
                pass
            thread.deleteLater()

        if thread.isRunning():
            self._orphan_threads.append(thread)
            thread.finished.connect(_forget)
        else:
            thread.deleteLater()

    def _dispose_runtime(self, conv_id: str):
        rt = self._runtimes.pop(conv_id, None)
        if rt is None:
            return
        if rt.run:
            rt.run.bubble = None
            self._release_thread(rt.run.thread, cancel=True)
        if rt.compaction:
            self._release_thread(rt.compaction.thread)
        rt.queued.clear()

    def _reset_view(self):
        self._auto_scroll = True
        self.jump_btn.hide()
        self.history       = []
        self.conv_id       = None
        self.conv_data     = None
        self.active_bubble = None
        self._stream_buffer.clear()
        self._stream_flush_timer.stop()
        self._bubbles      = {}
        self._render_start_index = 0
        self._older_btn = None
        self._update_queue_ui()
        self._clear_bubbles()
        self._update_context_ui()
        self._apply_default_model(self.provider_combo.currentText())
        self._sync_visible_runtime_refs()
        self._refresh_runtime_controls()
        self.composer.focus_input()

    def new_conversation(self):
        self._flush_stream_buffer()
        self._detach_visible_run_ui()
        self._save()
        self._reset_view()

    def on_conversation_deleted(self, conv_id: str):
        was_active = self.conv_id == conv_id
        if was_active:
            self._flush_stream_buffer()
            self._detach_visible_run_ui()
            if self.title_thread and self.title_thread.conv_id == conv_id:
                self._release_thread(self.title_thread)
                self.title_thread = None
        self._dispose_runtime(conv_id)
        if was_active:
            self._reset_view()

    def load_conversation(self, path: str):
        data = self.store.load(path)
        if self.conv_id == data.get("id") and self.conv_data is not None:
            return
        self.new_conversation()
        self.conv_id   = data["id"]
        self.conv_data = data
        self.history   = prepare_for_storage(data.get("messages", []))
        self.conv_data["messages"] = self.history
        self._set_model(data.get("model", ""))
        self._runtime_for(self.conv_id)
        self._update_queue_ui()
        self._render_history_tail()
        self._render_visible_run()
        self._sync_visible_runtime_refs()
        self._refresh_runtime_controls()
        self._update_context_ui()
        self._scroll_to_bottom_after_load()

    def update_title(self, conv_id: str, title: str):
        if self.conv_id == conv_id and self.conv_data is not None:
            self.conv_data["title"] = title

    def is_streaming(self) -> bool:
        return self._visible_run() is not None

    def stop_streaming(self):
        if self._visible_run():
            self._stop_streaming()

    def stop_managed_processes(self):
        get_process_manager().stop_workspace(self.cwd)

    def attach_file(self, path: str):
        try:
            rel = Path(path).resolve().relative_to(Path(self.cwd).resolve()).as_posix()
        except ValueError:
            rel = os.path.basename(path)
        self.composer.input.add_file_mention(rel)
        self.composer.focus_input()

    def draft_diagnostic_fix(self, text: str, file_refs: list[str] | None = None):
        draft = str(text or "").strip()
        if not draft:
            return
        self.composer.remember_file_refs(file_refs or [])
        input_box = self.composer.input
        current = input_box.toPlainText().rstrip()
        was_blocked = input_box.blockSignals(True)
        input_box.setPlainText(f"{current}\n\n{draft}" if current else draft)
        input_box.blockSignals(was_blocked)
        input_box.exit_mention_mode()
        input_box.exit_slash_mode()
        if self._file_picker:
            self._file_picker.hide()
        if self._skill_picker:
            self._skill_picker.hide()
        cursor = input_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        input_box.setTextCursor(cursor)
        self.composer.focus_input()

    def edit_last_message(self):
        if self._visible_run():
            return
        for i in range(len(self.history) - 1, -1, -1):
            if not is_visible_message(self.history[i]):
                continue
            if self.history[i]["role"] != "user":
                continue
            bubble = self._bubbles.get(i)
            if bubble:
                bubble._start_edit()
                self.scroll.ensureWidgetVisible(bubble)
            return

    def export_conversation(self):
        if not self.history:
            return
        if self.conv_id and self.conv_data is not None:
            self._save()
            data = dict(self.conv_data)
            data["messages"] = list(self.history)
        else:
            data = {
                "title": "Untitled",
                "model": self.model_combo.currentText(),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "messages": list(self.history),
            }
        export_conversation_dialog(data, parent=self.window())

    def show_extensions(self):
        ExtensionsDialog(
            self.cwd,
            parent=self.window(),
            on_reload=self._refresh_extension_ui,
        ).exec()

    def _handle_extension_action(self, action: dict):
        action_type = str(action.get("type") or "")
        if action_type == "open_file":
            path = str(action.get("path") or "")
            if not path:
                return
            abs_path = path if os.path.isabs(path) else os.path.join(self.cwd, path)
            try:
                resolved = Path(abs_path).resolve()
                resolved.relative_to(Path(self.cwd).resolve())
            except (OSError, ValueError):
                self._add_notice("Extension action blocked: file is outside the workspace.")
                return
            if not resolved.is_file():
                self._add_notice(f"Extension action could not find file: {path}")
                return
            self.open_file.emit(str(resolved), None)
        elif action_type == "copy":
            QGuiApplication.clipboard().setText(str(action.get("text") or ""))
            self._add_notice("Copied extension panel text.")
        elif action_type == "send_message":
            text = str(action.get("text") or action.get("message") or "").strip()
            if not text:
                self._add_notice("Extension action has no message to send.")
                return
            draft = {
                "content": build_user_content(text, [], []),
                "title_text": text,
                "skill": None,
            }
            if self._visible_run() or self._visible_compaction():
                self._ensure_conversation(text, self.model_combo.currentText())
                self._runtime_for(self.conv_id).queued.append(draft)
                self._update_queue_ui()
            else:
                self._send_draft(draft)
        elif action_type == "run_extension_command":
            name = str(action.get("command") or action.get("name") or "").strip()
            args = str(action.get("args") or "")
            if not name:
                self._add_notice("Extension action has no command to run.")
                return
            self._run_extension_command(name, args)
        else:
            self._add_notice(f"Unsupported extension action: {action_type or 'unknown'}")

    # ── send / receive ────────────────────────────────────────────────────────

    def send(self):
        text = self.composer.text()
        images = self.composer.strip.images()
        if not text and not images:
            return
        pasted_file_refs = (
            self.composer.take_pasted_file_refs()
            if hasattr(self.composer, "take_pasted_file_refs")
            else []
        )
        pasted_chat_refs = (
            self.composer.take_pasted_chat_refs()
            if hasattr(self.composer, "take_pasted_chat_refs")
            else []
        )

        if text.startswith("!") and not images:
            self._run_user_terminal_from_input(text)
            return

        files = _message_files(self.cwd, text, pasted_file_refs)

        cmd = parse_builtin_command(text) if text and not images else None
        if cmd:
            if self._visible_run() or self._visible_compaction():
                self._add_notice("Finish the current response or compaction before running a command.")
                return
            self.composer.clear()
            self.composer.input.exit_slash_mode()
            self.composer.input.exit_mention_mode()
            if self._skill_picker:
                self._skill_picker.hide()
            if self._file_picker:
                self._file_picker.hide()
            self._run_builtin_command(cmd)
            return

        builtin_prompt_cmd = parse_builtin_prompt_command(text) if text and not images else None
        if builtin_prompt_cmd:
            invocation = slash_invocation(text)
            trailing_text = invocation[1] if invocation else ""
            if not trailing_text:
                self.composer.clear()
                self.composer.input.exit_slash_mode()
                self.composer.input.exit_mention_mode()
                if self._skill_picker:
                    self._skill_picker.hide()
                self._activate_extension_command(builtin_prompt_cmd)
                return
            text = trailing_text
            files = _message_files(self.cwd, text, pasted_file_refs)
            self.composer.set_skill(self._skill_from_command(builtin_prompt_cmd))

        ext_cmd = parse_extension_command(text, self.cwd) if text and not images else None
        if ext_cmd:
            invocation = slash_invocation(text)
            trailing_text = invocation[1] if invocation else ""
            if ext_cmd.executable:
                self.composer.clear()
                self.composer.input.exit_slash_mode()
                self.composer.input.exit_mention_mode()
                if self._skill_picker:
                    self._skill_picker.hide()
                if self._file_picker:
                    self._file_picker.hide()
                self._run_extension_command(ext_cmd.name, trailing_text)
                return
            if not trailing_text:
                self.composer.clear()
                self.composer.input.exit_slash_mode()
                self.composer.input.exit_mention_mode()
                if self._skill_picker:
                    self._skill_picker.hide()
                self._activate_extension_command(ext_cmd)
                return
            text = trailing_text
            files = _message_files(self.cwd, text, pasted_file_refs)
            self.composer.set_skill(self._skill_from_extension_command(ext_cmd))

        draft = {
            "content": build_user_content(text, images, files),
            "title_text": text or "Image",
            "skill": self.composer.active_skill(),
            "crew": _first_summoned_crew(text, self._settings.load()),
            "chat_refs": pasted_chat_refs,
        }

        self.composer.clear()
        self.composer.clear_skill()
        self.composer.input.exit_mention_mode()
        self.composer.input.exit_slash_mode()
        if self._skill_picker:
            self._skill_picker.hide()
        if self._file_picker:
            self._file_picker.hide()

        if self._visible_run() or self._visible_compaction():
            self._ensure_conversation(draft["title_text"], self.model_combo.currentText())
            self._runtime_for(self.conv_id).queued.append(draft)
            self._update_queue_ui()
            return

        self._send_draft(draft)

    def _send_draft(self, draft: dict):
        crew = draft.get("crew")
        model = self._model_for_crew(crew)
        content = draft["content"]
        title_text = draft["title_text"]

        self._ensure_conversation(title_text, model)

        self._enter_streaming()

        now = datetime.now().isoformat()
        user_msg = {"role": "user", "content": content, "created_at": now}
        if draft.get("synthetic"):
            user_msg["synthetic"] = draft["synthetic"]
        self.history.append(user_msg)
        user_idx = len(self.history) - 1
        if self._render_start_index == user_idx:
            self._render_start_index = user_idx
        if is_visible_message(user_msg):
            self._add_bubble(content, is_user=True, history_index=user_idx, timestamp=now)
        chat_ref_context = _chat_ref_context(draft.get("chat_refs"))
        if chat_ref_context:
            self.history.append({
                "role": "user",
                "content": chat_ref_context,
                "synthetic": "chat_refs",
            })
        if draft.get("crew"):
            self._add_notice(_crew_notice_text(crew_metadata(draft["crew"], self._settings.load()), "joined"))

        self._save(touch_updated=True)
        self._maybe_auto_title()

        self._start_assistant(skill=draft.get("skill"), crew=draft.get("crew"))
        self._pin_to_bottom()

    def _run_user_terminal_from_input(self, text: str):
        command = text[1:].strip()
        if not command:
            self._add_notice("Type a command after ! to run it in the workspace.")
            return
        if self._visible_run() or self._visible_compaction():
            self._add_notice("Finish the current response or compaction before running a terminal command.")
            return

        model = self.model_combo.currentText()
        self._ensure_conversation(command, model)
        self.composer.clear()
        self.composer.input.exit_mention_mode()
        self.composer.input.exit_slash_mode()
        if self._skill_picker:
            self._skill_picker.hide()
        if self._file_picker:
            self._file_picker.hide()

        now = datetime.now().isoformat()
        content = f"! {command}"
        user_msg = {"role": "user", "content": content, "created_at": now}
        self.history.append(user_msg)
        user_idx = len(self.history) - 1
        self._add_bubble(content, is_user=True, history_index=user_idx, timestamp=now)
        self._add_tool_notice(f"Running command: {command}")
        card = self._add_terminal_card()
        self._save(touch_updated=True)

        thread = UserTerminalThread(command, self.cwd, self)
        conv_id = self.conv_id
        thread.line.connect(lambda line, cid=conv_id, c=card: self._on_user_terminal_line(cid, c, line))
        thread.done.connect(
            lambda result, t=thread, cid=conv_id, c=card: self._on_user_terminal_done(t, cid, c, result)
        )
        thread.finished.connect(lambda t=thread: self._forget_user_terminal_thread(t))
        self._user_terminal_threads.append(thread)
        thread.start()
        self._pin_to_bottom()

    def _ensure_conversation(self, title_text: str, model: str):
        if self.conv_id is not None:
            return
        self.conv_id = ConversationStore.new_id()
        now = datetime.now().isoformat()
        self.conv_data = {
            "id":         self.conv_id,
            "title":      ConversationStore.make_title(title_text),
            "title_auto": True,
            "created_at": now,
            "updated_at": now,
            "model":      model,
            "cwd":        self.cwd,
            "messages":   [],
        }
        self._runtime_for(self.conv_id)
        self.store.save(self.conv_id, self.conv_data)
        self.saved.emit()
        self.conversation_created.emit(self.conv_id)

    def _start_assistant(self, skill=None, crew: CrewMember | None = None):
        if not self.conv_id or self.conv_data is None:
            return
        model = self._model_for_crew(crew)
        conv_id = self.conv_id
        history_snapshot = copy.deepcopy(self.history)
        data_snapshot = copy.deepcopy(self.conv_data)
        data_snapshot["messages"] = copy.deepcopy(history_snapshot)
        self._start_assistant_run(
            conv_id,
            model,
            history_snapshot,
            data_snapshot,
            skill=skill,
            crew=crew,
            visible=True,
        )

    def _start_assistant_run(
        self,
        conv_id: str,
        model: str,
        history_snapshot: list[dict],
        data_snapshot: dict,
        *,
        skill=None,
        crew: CrewMember | None = None,
        visible: bool = False,
    ):
        run_id = uuid4().hex
        settings = self._settings.load()
        system = self._build_system(skill, crew=crew, settings=settings)
        allowed_tools = list(crew.tools) if crew else (skill.tools if skill else None)
        write_roots = list(crew.write_roots) if crew else None
        tool_policy = self._runtime_for(conv_id).tool_policy
        thread_history = crew_context_window(history_snapshot) if crew else copy.deepcopy(history_snapshot)
        thread = ChatThread(
            model, copy.deepcopy(thread_history), system, self.cwd,
            allowed_tools=allowed_tools,
            tool_policy=tool_policy,
            approval_bus=self._approval_bus,
            write_roots=write_roots,
            enable_crew_tool=(crew is None),
            crew_settings=settings,
            configured_providers=set(self._configured_providers()),
        )
        crew_meta = crew_metadata(crew, settings) if crew else None
        bubble = (
            self._add_bubble("", is_user=False, typing=True, crew=crew_meta)
            if visible
            else None
        )
        run = _AssistantRun(
            run_id=run_id,
            conv_id=conv_id,
            thread=thread,
            model=model,
            history_snapshot=history_snapshot,
            data_snapshot=data_snapshot,
            bubble=bubble,
            crew=crew_meta,
        )
        self._runtime_for(conv_id).run = run
        if visible:
            self._sync_visible_runtime_refs()
        thread.chunk.connect(lambda text, rid=run_id: self._on_chunk(rid, text))
        thread.tool_called.connect(lambda name, inputs, rid=run_id: self._on_tool_called(rid, name, inputs))
        thread.bash_line.connect(lambda line, rid=run_id: self._on_bash_line(rid, line))
        thread.tool_result.connect(lambda name, output, rid=run_id: self._on_tool_result(rid, name, output))
        thread.crew_started.connect(lambda meta, rid=run_id: self._on_crew_started(rid, meta))
        thread.crew_chunk.connect(lambda meta, text, rid=run_id: self._on_crew_chunk(rid, meta, text))
        thread.crew_done.connect(lambda meta, text, rid=run_id: self._on_crew_done(rid, meta, text))
        thread.crew_error.connect(lambda meta, text, rid=run_id: self._on_crew_error(rid, meta, text))
        thread.runtime_event.connect(lambda event, rid=run_id: self._on_runtime_event(rid, event))
        thread.done.connect(lambda full, rid=run_id: self._on_done(rid, full))
        thread.error.connect(lambda msg, rid=run_id: self._on_error(rid, msg))
        thread.start()

    def regenerate(self, idx: int = -1):
        if self._visible_run():
            return
        if idx != _latest_regenerable_assistant_index(self.history):
            return
        crew = _crew_for_history_message(self.history, idx)
        user_idx = self._find_turn_user_index()
        if user_idx is None:
            return
        self.history = self.history[: user_idx + 1]
        bubble = self._bubbles.get(user_idx)
        if bubble:
            self._truncate_ui_after(bubble)
        for k in list(self._bubbles.keys()):
            if k > user_idx:
                del self._bubbles[k]
        self._sync_regenerate_flags()
        self._save(touch_updated=True)
        self._enter_streaming()
        self._start_assistant(crew=crew)

    def _edit_resend(self, idx: int, text: str):
        if self._visible_run() or idx < 0 or idx >= len(self.history):
            return
        if not text:
            return
        old = self.history[idx]
        content = old["content"]
        if isinstance(content, list):
            blocks = [b for b in content if b.get("type") != "text"]
            if text:
                blocks.insert(0, {"type": "text", "text": text})
            new_content = blocks if blocks else text
        else:
            new_content = text

        bubble = self._bubbles.get(idx)
        if bubble:
            self._truncate_ui_after(bubble)
        for k in list(self._bubbles.keys()):
            if k >= idx:
                del self._bubbles[k]

        self.history = self.history[:idx]
        now = datetime.now().isoformat()
        self.history.append({"role": "user", "content": new_content, "created_at": now})
        new_idx = len(self.history) - 1
        self._add_bubble(new_content, is_user=True, history_index=new_idx, timestamp=now)
        self._sync_regenerate_flags()
        self._save(touch_updated=True)
        self._enter_streaming()
        self._start_assistant(crew=_first_summoned_crew(text, self._settings.load()))

    def _branch(self, idx: int):
        if idx < 0 or idx >= len(self.history):
            return
        conv_id = ConversationStore.new_id()
        base_title = (
            self.conv_data.get("title", "Untitled") if self.conv_data else "Conversation"
        )
        data = {
            "id":         conv_id,
            "title":      f"{base_title} (branch)",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "model":      self.model_combo.currentText(),
            "messages":   prepare_for_storage(self.history[: idx + 1]),
        }
        self.store.save(conv_id, data)
        self.saved.emit()

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_provider_changed(self, provider: str):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(MODELS.get(provider, []))
        self.model_combo.blockSignals(False)
        self._apply_default_model(provider)

    def _on_model_changed(self, model: str):
        if not model:
            return
        self._update_context_ui()
        provider = self.provider_combo.currentText()
        data = self._settings.load()
        defaults = data.get("default_models", {})
        if defaults.get(provider) == model:
            return
        defaults[provider] = model
        self._settings.update({"default_models": defaults})

    def _apply_default_model(self, provider: str):
        defaults = self._settings.load().get("default_models", {})
        model = defaults.get(provider)
        if model and model in MODELS.get(provider, []):
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentText(model)
            self.model_combo.blockSignals(False)

    def refresh_models(self):
        provider = self.provider_combo.currentText()
        model = self.model_combo.currentText()
        providers = self._configured_providers()

        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        self.provider_combo.addItems(providers)
        self.provider_combo.blockSignals(False)

        if provider in providers:
            self.provider_combo.setCurrentText(provider)
        elif providers:
            self.provider_combo.setCurrentText(providers[0])
        self._on_provider_changed(self.provider_combo.currentText())
        if model and model in MODELS.get(self.provider_combo.currentText(), []):
            self.model_combo.setCurrentText(model)
        self._update_context_ui()

    def _configured_providers(self) -> list[str]:
        saved = self._settings.load()
        user_providers = set(load_user_providers().keys())
        provider_keys = saved.get("provider_api_keys", {})
        configured = []
        for provider in MODELS:
            cfg = get_provider_config(provider)
            if not cfg:
                continue
            if provider in user_providers:
                configured.append(provider)
                continue
            key = str(provider_keys.get(provider, "")).strip()
            if not key and provider == "claude":
                key = str(saved.get("anthropic_api_key", "")).strip()
            if not key and provider == "openai":
                key = str(saved.get("openai_api_key", "")).strip()
            env_var = api_key_env_var(cfg.api_key_spec)
            if key or (env_var and os.environ.get(env_var)) or (cfg.api_key_spec and not env_var):
                configured.append(provider)
        order = saved.get("provider_order", [])
        if not isinstance(order, list):
            return configured
        ordered = []
        seen = set()
        for provider in order:
            provider = str(provider)
            if provider in configured and provider not in seen:
                ordered.append(provider)
                seen.add(provider)
        ordered.extend(provider for provider in configured if provider not in seen)
        return ordered

    def _build_system(
        self,
        skill=None,
        crew: CrewMember | None = None,
        settings: dict | None = None,
    ) -> str:
        settings = settings or self._settings.load()
        custom = settings.get("system_prompt", "").strip()
        base = skill.prompt if skill else (custom or None)
        system = build_system(self.cwd, base)
        return crew_system_prompt(crew, system, crew_prompt(crew, settings)) if crew else system

    def _model_for_crew(self, crew: CrewMember | None) -> str:
        current = self.model_combo.currentText()
        if not crew:
            return current
        saved = self._settings.load()
        configured = set(self._configured_providers())
        model = crew_settings(saved, crew)["model"]
        return crew_model_choice(crew, current, {crew.id: model}, configured)

    def _on_slash_changed(self, text: str):
        if not text:
            if self._skill_picker:
                self._skill_picker.hide()
            return
        invocation = slash_invocation(text)
        if invocation and invocation[1].strip():
            if self._skill_picker:
                self._skill_picker.hide()
            return
        if self._skill_picker is None:
            self._skill_picker = SkillPicker(
                load_skills(self.cwd),
                load_all_commands(self.cwd),
                include_terminal=True,
                parent=self,
            )
            self._skill_picker.skill_selected.connect(self._on_skill_selected)
            self._skill_picker.command_selected.connect(self._on_command_selected)
            self._skill_picker.terminal_selected.connect(self._on_terminal_hint_selected)
        self._skill_picker.filter(text)
        if self._skill_picker.count() == 0:
            self._skill_picker.hide()
            return
        self._position_skill_picker()
        self._skill_picker.show()
        self._skill_picker.raise_()

    def _on_terminal_hint_changed(self, text: str):
        if not text:
            if self._skill_picker:
                self._skill_picker.hide()
            return
        if self._skill_picker is None:
            self._skill_picker = SkillPicker(
                load_skills(self.cwd),
                load_all_commands(self.cwd),
                include_terminal=True,
                parent=self,
            )
            self._skill_picker.skill_selected.connect(self._on_skill_selected)
            self._skill_picker.command_selected.connect(self._on_command_selected)
            self._skill_picker.terminal_selected.connect(self._on_terminal_hint_selected)
        self._skill_picker.filter(text)
        if self._skill_picker.count() == 0:
            self._skill_picker.hide()
            return
        self._position_skill_picker()
        self._skill_picker.show()
        self._skill_picker.raise_()

    def _on_file_mention_changed(self, text: str):
        if not text:
            if self._file_picker:
                self._file_picker.hide()
            return
        if self._file_picker is None:
            self._file_picker = FileMentionPicker(
                _list_mention_files(self.cwd),
                crew=_enabled_crew(self._settings.load()),
                parent=self,
            )
            self._file_picker.file_selected.connect(self._on_file_mention_selected)
            self._file_picker.crew_selected.connect(self._on_crew_mention_selected)
        else:
            self._file_picker.set_files(_list_mention_files(self.cwd))
            self._file_picker.set_crew(_enabled_crew(self._settings.load()))
        self._file_picker.filter(text)
        if self._file_picker.count() == 0:
            self._file_picker.hide()
            return
        self._position_file_picker()
        self._file_picker.show()
        self._file_picker.raise_()

    def _position_skill_picker(self):
        frame_pos = self._input_frame.mapTo(self, QPoint(0, 0))
        w = self._input_frame.width()
        h = self._skill_picker.height()
        self._skill_picker.setFixedWidth(w)
        self._skill_picker.move(frame_pos.x(), frame_pos.y() - h - 4)

    def _position_file_picker(self):
        frame_pos = self._input_frame.mapTo(self, QPoint(0, 0))
        w = self._input_frame.width()
        h = self._file_picker.height()
        self._file_picker.setFixedWidth(w)
        self._file_picker.move(frame_pos.x(), frame_pos.y() - h - 4)

    def _on_file_mention_selected(self, rel_path: str, _abs_path: str):
        self.composer.input.insert_file_mention(rel_path)
        if self._file_picker:
            self._file_picker.hide()
        self.composer.focus_input()

    def _on_crew_mention_selected(self, name: str):
        self.composer.input.insert_crew_mention(name)
        if self._file_picker:
            self._file_picker.hide()
        self.composer.focus_input()

    def _on_skill_selected(self, skill):
        if _should_complete_slash_selection(self.composer.text(), skill.name):
            self._complete_slash_item(skill)
            return
        self.composer.set_skill(skill)
        self.composer.input.clear()
        self.composer.input.exit_slash_mode()
        self._skill_picker.hide()
        self.composer.focus_input()

    def _on_command_selected(self, command):
        if _should_complete_slash_selection(self.composer.text(), command.name):
            self._complete_slash_item(command)
            return
        self.composer.clear()
        self.composer.input.exit_slash_mode()
        self._skill_picker.hide()
        if getattr(command, "source", "builtin") == "builtin" and getattr(command, "executable", False):
            self._run_builtin_command(command.name)
        elif getattr(command, "executable", False):
            self._run_extension_command(command.name, "")
        else:
            self._activate_extension_command(command)

    def _on_terminal_hint_selected(self):
        self.composer.input.complete_terminal_command()
        if self._skill_picker:
            self._skill_picker.hide()
        self.composer.focus_input()

    def _activate_extension_command(self, command):
        self.composer.set_skill(self._skill_from_extension_command(command))
        self.composer.focus_input()

    def _complete_slash_selection(self):
        if not self._skill_picker:
            return
        current = self._skill_picker.current()
        if not current:
            return
        kind, item = current
        if kind == "terminal":
            self._on_terminal_hint_selected()
            return
        self._complete_slash_item(item)

    def _complete_slash_item(self, item):
        name = getattr(item, "name", "")
        if not name:
            return
        self.composer.input.complete_slash_command(name)
        self.composer.input.exit_slash_mode()
        if self._skill_picker:
            self._skill_picker.hide()
        self.composer.focus_input()

    def _skill_from_extension_command(self, command):
        return self._skill_from_command(command)

    def _skill_from_command(self, command):
        skill = Skill(
            name=command.name,
            description=command.description,
            prompt=command.prompt,
            tools=command.tools,
        )
        return skill

    def _run_builtin_command(self, name: str):
        if name == "compact":
            self.compact_conversation(force=True)
        elif name == "reload":
            self._skill_picker = None
            self._file_picker = None
            self._update_context_ui()
            self._refresh_extension_ui()
            errors = extension_errors(self.cwd)
            if errors:
                self._add_notice(f"Reloaded with {len(errors)} extension error(s). Check the extension file.")
            else:
                self._add_notice("Reloaded skills and extensions.")

    def _run_extension_command(self, name: str, args: str):
        result, errors = run_extension_command(
            self.cwd,
            name,
            args,
            model=self.model_combo.currentText(),
            history=copy.deepcopy(self.history),
            conversation_id=self.conv_id or "",
            runtime=self._runtime_command_api(),
        )
        if errors:
            self._add_notice(f"Extension command failed: {errors[-1].splitlines()[-1]}")
            return
        if isinstance(result, str) and result.strip():
            self._add_notice(result.strip())
        elif isinstance(result, dict):
            notice = str(result.get("notice") or result.get("message") or "").strip()
            if notice:
                self._add_notice(notice)

    def _runtime_command_api(self) -> RuntimeCommandApi:
        return RuntimeCommandApi(
            show_notice=self._add_notice,
            send_message=lambda text: self._send_or_queue_text(text, prefer_queue=False),
            enqueue_message=lambda text: self._send_or_queue_text(text, prefer_queue=True),
            compact_now=lambda force: self.compact_conversation(force=force),
            compact_and_resume=self._compact_and_resume_from_command,
            process_factory=lambda extension_id: RuntimeProcessApi(
                get_process_manager(),
                workspace=self.cwd,
                extension_id=extension_id,
                approve_start=lambda request: confirm_process_start(self.window(), request),
            ),
        )

    def _send_or_queue_text(self, text: str, *, prefer_queue: bool, synthetic: str = ""):
        text = str(text or "").strip()
        if not text:
            return
        draft = {
            "content": build_user_content(text, [], []),
            "title_text": text,
            "skill": None,
        }
        if synthetic:
            draft["synthetic"] = synthetic
        if prefer_queue or self._visible_run() or self._visible_compaction():
            self._ensure_conversation(text, self.model_combo.currentText())
            self._runtime_for(self.conv_id).queued.append(draft)
            self._update_queue_ui()
            return
        self._send_draft(draft)

    def _compact_and_resume_from_command(self, resume_prompt: str, force: bool):
        prompt = str(resume_prompt or "").strip()
        if not prompt:
            prompt = "Continue the active task from the compacted context."
        if not self.history:
            self._add_notice("Nothing to continue — start a conversation first.")
            return
        self._send_or_queue_runtime_text(prompt, "extension_resume")
        if self._visible_run() or self._visible_compaction():
            self._add_notice("Continuation queued for the current conversation.")
            return
        self.compact_conversation(force=force)
        if not self._visible_compaction():
            self._start_next_queued()

    def _send_or_queue_runtime_text(self, text: str, synthetic: str):
        self._send_or_queue_text(text, prefer_queue=True, synthetic=synthetic)

    def compact_conversation(self, force: bool = False):
        if self._visible_run() or self._visible_compaction():
            return
        if not self.history:
            self._add_notice("Nothing to compact — start a conversation first.")
            return
        model = self.model_combo.currentText()
        budget = self._context_budget()
        if not force and not should_compact(
            model, self.history, context_tokens=budget.used_tokens,
        ):
            self._add_notice("Context is not large enough to compact yet.")
            return
        if not can_compact(self.history, model, force=force):
            if force:
                self._add_notice("Nothing to compact — need an older completed turn first.")
            else:
                self._add_notice("Nothing to compact — recent messages already fit in context.")
            return
        self._add_notice("Compacting conversation context…")
        conv_id = self.conv_id
        if not conv_id:
            return
        history_snapshot = copy.deepcopy(self.history)
        thread = CompactionThread(model, history_snapshot, force=force)
        self._runtime_for(conv_id).compaction = _CompactionRun(
            conv_id=conv_id,
            thread=thread,
            model=model,
            history_snapshot=history_snapshot,
            data_snapshot=copy.deepcopy(self.conv_data) if self.conv_data else {"id": conv_id},
        )
        self._refresh_runtime_controls()
        thread.done.connect(lambda compacted, cid=conv_id: self._on_compacted(cid, compacted))
        thread.error.connect(lambda msg, cid=conv_id: self._on_compaction_error(cid, msg))
        thread.start()

    def _on_chunk(self, run_id: str, text: str):
        run = self._find_run(run_id)
        if not run:
            return
        run.partial_text += text
        if run.conv_id == self.conv_id:
            if run.bubble and run.bubble.is_empty_typing():
                self._remove_empty_active_typing_bubble()
            self._stream_buffer.append(text)
            if not self._stream_flush_timer.isActive():
                self._stream_flush_timer.start()

    def _on_approval_needed(self, pending):
        handle_pending_approval(self, self._approval_bus, pending)

    def _on_tool_called(self, run_id: str, name: str, inputs: dict):
        run = self._find_run(run_id)
        if not run:
            return
        if run.conv_id != self.conv_id:
            return
        self._flush_stream_buffer()
        self._remove_empty_active_typing_bubble()
        bubble = run.bubble
        if bubble and bubble._copy_text.strip():
            bubble.finalize(bubble._copy_text)
        run.bubble = None
        self.active_bubble = None
        run.last_tool_name = name
        run.last_tool_inputs = copy.deepcopy(inputs)
        if name == "edit_file":
            path = inputs.get("path", "")
            run.last_edit_path = path
            self._last_edit_path = path
            if path:
                self.file_written.emit(path)
        self._add_tool_notice(
            _tool_call_notice(name, inputs, self.cwd),
            debug_text=_tool_debug_text(name, inputs, "", self.cwd),
        )
        if is_shell_tool(name):
            run.active_terminal = self._add_terminal_card()
            self._active_terminal = run.active_terminal

    def _on_bash_line(self, run_id: str, line: str):
        run = self._find_run(run_id)
        if not run:
            return
        if run.conv_id != self.conv_id:
            return
        if run.active_terminal:
            run.active_terminal.append_line(line)
            self._bottom()

    def _on_crew_started(self, run_id: str, meta: dict):
        run = self._find_run(run_id)
        if not run or run.conv_id != self.conv_id:
            return
        self._flush_stream_buffer()
        self._remove_empty_active_typing_bubble()
        run.bubble = None
        self.active_bubble = None
        self._add_notice(_crew_notice_text(meta, "joined"))
        bubble = self._add_bubble("", is_user=False, typing=True, crew=meta)
        run.crew_bubbles[_crew_invocation_key(meta)] = bubble

    def _on_crew_chunk(self, run_id: str, meta: dict, text: str):
        run = self._find_run(run_id)
        if not run or run.conv_id != self.conv_id:
            return
        key = _crew_invocation_key(meta)
        bubble = run.crew_bubbles.get(key)
        if bubble is None:
            bubble = self._add_bubble("", is_user=False, typing=True, crew=meta)
            run.crew_bubbles[key] = bubble
        bubble.append(text)
        self._bottom()

    def _on_crew_done(self, run_id: str, meta: dict, text: str):
        run = self._find_run(run_id)
        if not run or run.conv_id != self.conv_id:
            return
        key = _crew_invocation_key(meta)
        bubble = run.crew_bubbles.pop(key, None)
        now = datetime.now().isoformat()
        crew_msg = {
            "role": "assistant",
            "content": text,
            "created_at": now,
            "crew": _crew_history_meta(meta),
        }
        if isinstance(meta.get("usage"), dict):
            crew_msg["usage"] = dict(meta["usage"])
        self.history.append(crew_msg)
        idx = len(self.history) - 1
        if bubble is None:
            bubble = self._add_bubble(
                text, is_user=False, history_index=idx, timestamp=now,
                crew=meta, usage=crew_msg.get("usage"),
            )
        else:
            bubble._history_index = idx
            self._bubbles[idx] = bubble
            bubble.set_usage(crew_msg.get("usage"))
            bubble.finalize(text)
        self._add_notice(_crew_notice_text(meta, "left"))
        self._update_context_ui()

    def _on_crew_error(self, run_id: str, meta: dict, text: str):
        run = self._find_run(run_id)
        if not run or run.conv_id != self.conv_id:
            return
        key = _crew_invocation_key(meta)
        bubble = run.crew_bubbles.pop(key, None)
        if bubble:
            bubble.append(text)
        else:
            self._add_bubble(text, is_user=False, crew=meta)
        self._add_notice(_crew_notice_text(meta, "left"))

    def _on_tool_result(self, run_id: str, name: str, output: str):
        run = self._find_run(run_id)
        if not run:
            return
        if run.conv_id != self.conv_id:
            return
        if is_shell_tool(name) and run.active_terminal:
            import re
            m = re.search(r'\[exit (\d+)\]', output)
            run.active_terminal.finish(int(m.group(1)) if m else 0)
            run.active_terminal = None
            self._active_terminal = None
        elif name == "edit_file" and output.startswith("[tool error]"):
            preview = output[:200].replace("\n", " ") + ("…" if len(output) > 200 else "")
            inputs = run.last_tool_inputs if run.last_tool_name == name else {}
            self._add_tool_notice(
                f"Tool error: {preview[len('[tool error] '):]}",
                debug_text=_tool_debug_text(name, inputs, output, self.cwd),
            )
            run.last_edit_path = ""
            self._last_edit_path = ""
        elif name == "edit_file" and run.last_edit_path:
            self.file_write_completed.emit(run.last_edit_path)
            self._add_file_card(run.last_edit_path)
            run.last_edit_path = ""
            self._last_edit_path = ""
        elif output.startswith("[tool error]"):
            preview = output[:200].replace("\n", " ") + ("…" if len(output) > 200 else "")
            message = preview.removeprefix("[tool error]").strip()
            inputs = run.last_tool_inputs if run.last_tool_name == name else {}
            self._add_tool_notice(
                f"Tool error: {message}",
                debug_text=_tool_debug_text(name, inputs, output, self.cwd),
            )
        self._show_post_tool_thinking(run)

    def _on_user_terminal_done(
        self,
        thread: UserTerminalThread,
        conv_id: str | None,
        card: TerminalCard,
        result: dict,
    ):
        if conv_id == self.conv_id:
            try:
                card.finish(
                    int(result.get("exit_code") or 0),
                    detail=_terminal_status_detail(result),
                    ref=_terminal_result_ref(result),
                )
            except RuntimeError:
                pass
        if not conv_id:
            return

        now = datetime.now().isoformat()
        msg = {
            "role": "assistant",
            "content": str(result.get("summary") or ""),
            "created_at": now,
            "synthetic": "terminal_result",
            "terminal": {
                "command": str(result.get("command") or ""),
                "cwd": str(result.get("cwd") or ""),
                "exit_code": int(result.get("exit_code") or 0),
                "duration_s": float(result.get("duration_s") or 0.0),
                "line_count": int(result.get("line_count") or 0),
                "stored_line_count": int(result.get("stored_line_count") or 0),
                "truncated": bool(result.get("truncated")),
                "output": str(result.get("output") or ""),
            },
        }

        if conv_id == self.conv_id:
            self.history.append(msg)
            self._save(touch_updated=True)
            self._maybe_auto_title()
        else:
            data = self.store.load_by_id(conv_id)
            history = prepare_for_storage(data.get("messages", []))
            history.append(msg)
            data["messages"] = prepare_for_storage(history)
            data["updated_at"] = now
            self.store.save(conv_id, data)
            self.saved.emit()

    def _on_user_terminal_line(self, conv_id: str | None, card: TerminalCard, line: str):
        if conv_id != self.conv_id:
            return
        try:
            card.append_line(line)
        except RuntimeError:
            pass

    def _forget_user_terminal_thread(self, thread: UserTerminalThread):
        if thread in self._user_terminal_threads:
            self._user_terminal_threads.remove(thread)
        thread.deleteLater()

    def _on_runtime_event(self, run_id: str, event: dict):
        run = self._find_run(run_id)
        if not run or run.conv_id != self.conv_id:
            return
        event_type = event.get("type")
        if event_type == "notice":
            text = str(event.get("text") or "").strip()
            if text:
                self._add_notice(text)
        elif event_type == "compaction":
            status = event.get("status")
            source = event.get("source")
            if status == "compacted":
                if source == "auto-preflight":
                    self._add_notice("Auto-compacted context before sending the next request.")
                else:
                    self._add_notice("Runtime extension compacted context before continuing.")
            elif status == "unchanged":
                if source == "auto-preflight":
                    self._add_notice("Checked context size; no safe compaction cut was available.")
                else:
                    self._add_notice("Runtime extension checked compaction; no cut was available.")
        elif event_type == "compaction_failed":
            self._add_notice(f"Runtime extension compaction failed: {event.get('error')}")
        elif event_type == "blocked":
            self._add_notice(str(event.get("text") or "Runtime extension blocked the request."))

    def _on_done(self, run_id: str, full: str):
        run = self._find_run(run_id)
        if not run:
            return
        is_current = run.conv_id == self.conv_id
        if is_current:
            self._flush_stream_buffer()
        now = datetime.now().isoformat()
        assistant_msg = {"role": "assistant", "content": full, "created_at": now}
        if run.crew:
            assistant_msg["crew"] = dict(run.crew)
        if run.thread.last_usage:
            assistant_msg["usage"] = dict(run.thread.last_usage)
        run_history = copy.deepcopy(run.thread.history)
        if not _history_ends_with_assistant_text(run_history, full):
            run_history.append(assistant_msg)
        run_history = prepare_for_storage(run_history)
        run_data = copy.deepcopy(run.data_snapshot)

        if is_current:
            self.history = run_history
            asst_idx = len(self.history) - 1
        else:
            if run.conv_id and run_data:
                run_data["messages"] = run_history
                run_data["updated_at"] = now
                self.store.save(run.conv_id, run_data)
                self.saved.emit()
            asst_idx = -1

        bubble = run.bubble if is_current else None
        completed_thread = run.thread
        self._runtime_for(run.conv_id).run = None
        run.bubble = None
        if is_current:
            self.active_bubble = None

        if is_current and bubble is None and full:
            bubble = self._add_bubble(full, is_user=False, crew=run.crew)

        if is_current and bubble:
            bubble._history_index = asst_idx
            self._bubbles[asst_idx] = bubble
            bubble.set_usage(assistant_msg.get("usage"))
            bubble_idx = self.msg_layout.indexOf(bubble)
            offset = [1]

            def add_artifact(artifact):
                card = self._wrap_artifact(
                    ArtifactCard(
                        artifact.get("language", ""),
                        artifact.get("code", ""),
                        lambda c, t: self.open_code.emit(c, t),
                        artifact.get("title", ""),
                        artifact.get("reason", ""),
                    )
                )
                self.msg_layout.insertWidget(bubble_idx + offset[0], card)
                offset[0] += 1
                self._bottom()

            bubble.finalize(full, on_artifact=add_artifact)
            self._sync_regenerate_flags()

        if is_current and run.crew:
            self._add_notice(_crew_notice_text(run.crew, "left"))

        if is_current:
            self.history = prepare_for_storage(self.history)
            if self.conv_data is not None:
                self.conv_data["messages"] = self.history
            self._sync_visible_runtime_refs()
            self._exit_streaming()
            self._maybe_auto_title()
            model = self.model_combo.currentText()
            budget = self._context_budget()
            if should_compact(model, self.history, context_tokens=budget.used_tokens):
                self.compact_conversation(force=False)
            else:
                self._save(touch_updated=True)
                self._start_next_queued()
        else:
            self._refresh_runtime_controls()
            self._start_next_queued_for(run.conv_id, run_data)
        self._keep_thread_until_finished(completed_thread)

    def _on_compacted(self, conv_id: str, compacted: list):
        compaction = self._find_compaction(conv_id)
        if not compaction:
            return
        completed_thread = compaction.thread
        self._runtime_for(conv_id).compaction = None
        if conv_id == self.conv_id:
            self._sync_visible_runtime_refs()
            self.history = prepare_for_storage(compacted)
            if self.conv_data is not None:
                self.conv_data["messages"] = self.history
            self._render_history_tail()
            self._scroll_to_bottom_later()
            self._add_notice("Context compacted — conversation continues.")
            self._update_context_ui()
            self._save(touch_updated=True)
            self._refresh_runtime_controls()
            self._start_next_queued()
        else:
            data = copy.deepcopy(compaction.data_snapshot)
            data["messages"] = prepare_for_storage(compacted)
            data["updated_at"] = datetime.now().isoformat()
            self.store.save(conv_id, data)
            self.saved.emit()
            self._refresh_runtime_controls()
            self._start_next_queued_for(conv_id, data)
        self._keep_thread_until_finished(completed_thread)

    def _on_compaction_error(self, conv_id: str, msg: str):
        if not self._find_compaction(conv_id):
            return
        completed_thread = self._find_compaction(conv_id).thread
        self._runtime_for(conv_id).compaction = None
        if conv_id == self.conv_id:
            self._sync_visible_runtime_refs()
            self._add_notice(f"Compaction failed: {msg}")
            self._exit_streaming()
            self._update_context_ui()
            self._save()
            self._start_next_queued()
        else:
            self._refresh_runtime_controls()
        self._keep_thread_until_finished(completed_thread)

    def _maybe_auto_title(self):
        if not self.conv_id or not self.conv_data:
            return
        if not self.conv_data.get("title_auto", False):
            return
        user_msgs = [
            m for m in self.history
            if m.get("role") == "user" and is_visible_message(m)
        ]
        if len(user_msgs) != 1:
            return

        preview = content_preview(user_msgs[0].get("content", "")).strip()
        if not preview:
            return

        if self.title_thread and self.title_thread.isRunning():
            return

        self.title_thread = TitleThread(
            self.conv_id,
            self._resolve_model(self.model_combo.currentText()),
            preview[:100],
        )
        self.title_thread.done.connect(self._on_auto_title_done)
        self.title_thread.error.connect(self._on_auto_title_error)
        self.title_thread.start()

    def _on_auto_title_done(self, conv_id: str, title: str):
        completed_thread = self.title_thread
        self.title_thread = None
        try:
            if conv_id != self.conv_id or self.conv_data is None:
                return
            if not self.conv_data.get("title_auto", False):
                return
            self.conv_data["title"] = title
            self.conv_data["title_auto"] = False
            self.store.save(self.conv_id, self.conv_data)
            self.saved.emit()
        finally:
            self._keep_thread_until_finished(completed_thread)

    def _on_auto_title_error(self, _msg: str):
        completed_thread = self.title_thread
        self.title_thread = None
        self._keep_thread_until_finished(completed_thread)

    def _on_error(self, run_id: str, msg: str):
        run = self._find_run(run_id)
        if not run:
            return
        is_current = run.conv_id == self.conv_id
        if is_current:
            self._flush_stream_buffer()
        if is_current and run.bubble:
            run.bubble.append(f"[Error: {msg}]")
        if is_current and run.crew:
            self._add_notice(_crew_notice_text(run.crew, "left"))
        if is_current:
            self.active_bubble = None
        completed_thread = run.thread
        self._runtime_for(run.conv_id).run = None
        run.bubble = None
        if is_current:
            self._sync_visible_runtime_refs()
            self._exit_streaming()
        else:
            self._refresh_runtime_controls()
        self._keep_thread_until_finished(completed_thread)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _default_model_for_provider(self, provider: str) -> str:
        defaults = self._settings.load().get("default_models", {})
        models = MODELS.get(provider, [])
        default = defaults.get(provider)
        if default and default in models:
            return default
        return models[0] if models else ""

    def _resolve_model(self, model: str | None) -> str:
        model = str(model or "").strip()
        if model and model in MODEL_PROVIDER:
            provider = MODEL_PROVIDER[model]
            if model in MODELS.get(provider, []):
                return model
            return self._default_model_for_provider(provider)
        providers = self._configured_providers()
        if providers:
            return self._default_model_for_provider(providers[0])
        for provider_models in MODELS.values():
            if provider_models:
                return provider_models[0]
        return model

    def _set_model(self, model: str):
        model = self._resolve_model(model)
        if not model:
            return
        provider = MODEL_PROVIDER[model]
        self.provider_combo.setCurrentText(provider)
        self._on_provider_changed(provider)
        self.model_combo.setCurrentText(model)

    def set_model(self, model: str):
        if model in MODEL_PROVIDER:
            self._set_model(model)

    def current_model(self) -> str:
        return self.model_combo.currentText()

    def _save(self, *, touch_updated: bool = False):
        if self.conv_id and self.conv_data is not None:
            self.conv_data["messages"]   = prepare_for_storage(self.history)
            if touch_updated or not self.conv_data.get("updated_at"):
                self.conv_data["updated_at"] = datetime.now().isoformat()
            self.store.save(self.conv_id, self.conv_data)
            self.saved.emit()
        self._update_context_ui()

    def _context_budget(self):
        custom = self._settings.load().get("system_prompt", "").strip()
        return analyze_context(
            self.model_combo.currentText(),
            self.cwd,
            self.history,
            custom_system=custom,
        )

    def _update_context_ui(self):
        budget = self._context_budget()
        self.context_ring.set_budget(budget)
        self._refresh_extension_ui()

    def _flush_stream_buffer(self):
        run = self._visible_run()
        if not run:
            self._stream_buffer.clear()
            self._stream_flush_timer.stop()
            return
        if not self._stream_buffer:
            self._stream_flush_timer.stop()
            return
        text = "".join(self._stream_buffer)
        self._stream_buffer.clear()
        if run.bubble is None:
            run.bubble = self._add_bubble(
                "", is_user=False, typing=True, crew=run.crew,
            )
        self.active_bubble = run.bubble
        if run.bubble:
            run.bubble.append(text)
            run.rendered_chars += len(text)
            self._bottom()

    def _show_context_breakdown(self):
        ContextBreakdownDialog(
            self._context_budget(),
            self.model_combo.currentText(),
            parent=self.window(),
        ).exec()

    def _refresh_extension_ui(self):
        if not hasattr(self, "_extension_bar"):
            return
        self._extension_bar.set_context(
            cwd=self.cwd,
            model=self.model_combo.currentText(),
            history=self.history,
        )

    def _add_terminal_card(self) -> TerminalCard:
        card = TerminalCard()
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, self._wrap_artifact(card))
        self._bottom()
        return card

    def _add_terminal_result_card(self, msg: dict, *, at_top: bool = False) -> TerminalCard:
        result = msg.get("terminal") if isinstance(msg.get("terminal"), dict) else {}
        card = TerminalCard()
        card.set_output(str(result.get("output") or ""))
        card.finish(
            int(result.get("exit_code") or 0),
            detail=_terminal_status_detail(result),
            ref=_terminal_result_ref(result),
        )
        insert_at = 1 if at_top and self._older_btn else 0 if at_top else self.msg_layout.count() - 1
        self.msg_layout.insertWidget(insert_at, self._wrap_artifact(card))
        self._bottom()
        return card

    def _add_file_card(self, file_path: str):
        abs_path = str(
            Path(file_path) if Path(file_path).is_absolute() else Path(self.cwd) / file_path
        )
        name = os.path.basename(abs_path)
        try:
            content = _read_text_preview(abs_path)
        except OSError as e:
            content = f"[Could not read file: {e}]"

        def on_open(_, __):
            self.open_file.emit(abs_path, None)

        card = self._wrap_artifact(
            ArtifactCard(
                "",
                content,
                on_open,
                name,
                "Edited file",
                show_language=False,
                show_preview_actions=False,
                max_width=960,
            )
        )
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, card)
        self._bottom()

    def _wrap_artifact(self, card) -> QWidget:
        """Left-align an ArtifactCard to match AI bubble positioning."""
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(60, 4, 24, 4)
        row.addWidget(card)
        row.addStretch()
        return wrapper

    def _add_tool_notice(self, text: str, debug_text: str = ""):
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(60, 1, 24, 1)
        row.setSpacing(0)
        lbl = QLabel(text)
        lbl.setObjectName("aichs-tool-notice")
        lbl.setProperty("aichs-tool-text", text)
        lbl.setProperty("aichs-tool-debug-text", debug_text or text)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setText(_tool_notice_html(text))
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(880)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lbl.setStyleSheet(tool_notice_style())
        lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lbl.customContextMenuRequested.connect(
            lambda pos, label=lbl: self._show_tool_notice_menu(label, pos)
        )
        row.addWidget(lbl, 1)
        row.addStretch()
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, wrapper)
        self._bottom()

    def _show_tool_notice_menu(self, label: QLabel, pos):
        text = str(label.property("aichs-tool-text") or label.text() or "")
        debug_text = str(label.property("aichs-tool-debug-text") or text)
        menu = QMenu(self)
        copy_message = menu.addAction("Copy message")
        copy_debug = menu.addAction("Copy debug info")
        selected = menu.exec(label.mapToGlobal(pos))
        if selected == copy_message:
            QGuiApplication.clipboard().setText(text)
        elif selected == copy_debug:
            QGuiApplication.clipboard().setText(debug_text)

    def _add_notice(self, text: str):
        lbl = QLabel(text)
        lbl.setObjectName("aichs-center-notice")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(center_notice_style())
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, lbl)
        self._bottom()

    def _show_post_tool_thinking(self, run: _AssistantRun):
        if run.conv_id != self.conv_id:
            return
        if run.bubble and run.bubble.is_empty_typing():
            self.active_bubble = run.bubble
            return
        if run.bubble:
            return
        run.bubble = self._add_bubble("", is_user=False, typing=True, crew=run.crew)
        self.active_bubble = run.bubble

    def _add_bubble(self, content, is_user: bool, typing: bool = False,
                    history_index: int = -1, timestamp: str = "",
                    crew: dict | None = None, usage: dict | None = None) -> MessageBubble:
        bubble = self._make_bubble(
            content, is_user, typing=typing,
            history_index=history_index, timestamp=timestamp, crew=crew, usage=usage,
        )
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, bubble)
        if history_index >= 0:
            self._bubbles[history_index] = bubble
        self._bottom()
        return bubble

    def _make_bubble(self, content, is_user: bool, typing: bool = False,
                     history_index: int = -1, timestamp: str = "",
                     crew: dict | None = None, usage: dict | None = None) -> MessageBubble:
        bubble = MessageBubble(
            content, is_user, typing=typing,
            history_index=history_index, timestamp=timestamp, crew=crew,
            can_regenerate=(history_index == _latest_regenerable_assistant_index(self.history)),
            usage=usage,
        )
        bubble.regenerate_requested.connect(self.regenerate)
        bubble.edit_resend_requested.connect(self._edit_resend)
        bubble.branch_requested.connect(self._branch)
        bubble.file_clicked.connect(self._open_linked_file)
        return bubble

    def _sync_regenerate_flags(self):
        latest = _latest_regenerable_assistant_index(self.history)
        for idx, bubble in self._bubbles.items():
            bubble.set_regenerable(idx == latest)

    def _open_linked_file(self, path: str):
        abs_path = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        if not os.path.isfile(abs_path):
            return
        self.open_file.emit(abs_path, None)

    def _find_turn_user_index(self) -> int | None:
        i = len(self.history) - 1
        while i >= 0:
            msg = self.history[i]
            if not is_visible_message(msg):
                i -= 1
                continue
            if msg["role"] == "user":
                content = msg.get("content")
                if isinstance(content, list) and content:
                    if content[0].get("type") == "tool_result":
                        i -= 1
                        continue
                return i
            i -= 1
        return None

    def _truncate_ui_after(self, widget: QWidget):
        idx = self.msg_layout.indexOf(widget)
        if idx < 0:
            return
        while self.msg_layout.count() > idx + 2:
            item = self.msg_layout.takeAt(idx + 1)
            if item.widget():
                item.widget().deleteLater()

    def _apply_chrome(self):
        self._update_context_ui()
        sep = separator_color()
        self._sep.setStyleSheet(
            f"background:{sep}; color:{sep}; border:none; max-height:1px;"
        )
        self._input_frame.setStyleSheet(input_bar_style())
        self.jump_btn.setStyleSheet(floating_button_style())
        self.btn.setStyleSheet(send_button_style())
        self.stop_btn.setStyleSheet(stop_button_style())

    def _apply_input_preferences(self):
        self.composer.set_enter_to_send(
            bool(self._settings.load().get("enter_to_send", False))
        )

    def apply_appearance(self):
        self._apply_chrome()
        self._apply_input_preferences()
        self.composer.apply_appearance()
        self._update_queue_ui()
        for i in range(self.msg_layout.count() - 1):
            w = self.msg_layout.itemAt(i).widget()
            if isinstance(w, MessageBubble):
                w.apply_appearance()
            elif w:
                for card in w.findChildren(ArtifactCard):
                    card.apply_appearance()
                for card in w.findChildren(TerminalCard):
                    card.apply_appearance()
        for lbl in self.msg_container.findChildren(QLabel):
            name = lbl.objectName()
            if name == "aichs-tool-notice":
                lbl.setStyleSheet(tool_notice_style())
                raw = lbl.property("aichs-tool-text")
                if raw:
                    lbl.setText(_tool_notice_html(str(raw)))
            elif name == "aichs-center-notice":
                lbl.setStyleSheet(center_notice_style())

    def set_cwd(self, cwd: str):
        self.cwd = cwd
        self._update_context_ui()

    def _clear_bubbles(self):
        self._bubbles = {}
        self._older_btn = None
        while self.msg_layout.count() > 1:
            item = self.msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_history_tail(self):
        self._clear_bubbles()
        self._render_start_index = _window_start(
            self.history,
            len(self.history),
            _INITIAL_RENDER_BYTES,
            _INITIAL_RENDER_MESSAGES,
        )
        self._sync_older_button()
        for i in range(self._render_start_index, len(self.history)):
            self._insert_history_bubble(i)
        self._sync_regenerate_flags()

    def _render_visible_run(self):
        run = self._visible_run()
        if not run:
            return
        run.bubble = self._add_bubble(
            "", is_user=False, typing=True, crew=run.crew,
        )
        self.active_bubble = run.bubble
        run.rendered_chars = 0
        if run.partial_text:
            run.bubble.append(run.partial_text)
            run.rendered_chars = len(run.partial_text)
        self._bottom()

    def _refresh_runtime_controls(self):
        self._sync_visible_runtime_refs()
        if self._visible_run():
            self._enter_streaming()
        elif self._visible_compaction():
            self._enter_compaction()
        else:
            self._exit_streaming()

    def _prepend_history_page(self):
        if self._render_start_index <= 0:
            self._sync_older_button()
            return

        old_start = self._render_start_index
        new_start = _window_start(
            self.history,
            old_start,
            _OLDER_RENDER_BYTES,
            _OLDER_RENDER_MESSAGES,
        )
        if new_start >= old_start:
            return

        bar = self.scroll.verticalScrollBar()
        old_max = bar.maximum()
        old_value = bar.value()

        if self._older_btn:
            idx = self.msg_layout.indexOf(self._older_btn)
            if idx >= 0:
                item = self.msg_layout.takeAt(idx)
                if item.widget():
                    item.widget().deleteLater()
            self._older_btn = None

        for i in range(old_start - 1, new_start - 1, -1):
            self._insert_history_bubble(i, at_top=True)

        self._render_start_index = new_start
        self._sync_older_button()

        def restore():
            self._programmatic_scroll = True
            bar.setValue(old_value + (bar.maximum() - old_max))
            self._programmatic_scroll = False

        QTimer.singleShot(0, restore)

    def _insert_history_bubble(self, history_index: int, *, at_top: bool = False):
        msg = self.history[history_index]
        if msg.get("synthetic") == "terminal_result":
            return self._add_terminal_result_card(msg, at_top=at_top)
        if not is_visible_message(msg):
            return None
        bubble = self._make_bubble(
            msg["content"],
            is_user=(msg["role"] == "user"),
            history_index=history_index,
            timestamp=msg.get("created_at", ""),
            crew=msg.get("crew"),
            usage=msg.get("usage"),
        )
        insert_at = 1 if at_top and self._older_btn else 0 if at_top else self.msg_layout.count() - 1
        self.msg_layout.insertWidget(insert_at, bubble)
        self._bubbles[history_index] = bubble
        bubble.set_regenerable(history_index == _latest_regenerable_assistant_index(self.history))
        return bubble

    def _sync_older_button(self):
        if self._render_start_index <= 0:
            if self._older_btn:
                idx = self.msg_layout.indexOf(self._older_btn)
                if idx >= 0:
                    item = self.msg_layout.takeAt(idx)
                    if item.widget():
                        item.widget().deleteLater()
                self._older_btn = None
            return

        if self._older_btn is None:
            self._older_btn = QPushButton()
            self._older_btn.setStyleSheet(self._older_button_style())
            self._older_btn.clicked.connect(self._prepend_history_page)
            self.msg_layout.insertWidget(0, self._older_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self._older_btn.setText(f"Load older messages ({self._render_start_index} hidden)")
        self._older_btn.setStyleSheet(self._older_button_style())

    def _older_button_style(self) -> str:
        p = palette()
        return (
            f"QPushButton {{ background:{p['BG3']}; color:{p['TEXT_DIM']};"
            f"border:1px solid {p['BORDER']}; border-radius:8px;"
            "padding:6px 12px; margin:8px 0; }}"
            f"QPushButton:hover {{ color:{p['TEXT']}; background:{p['BORDER']}; }}"
        )

    def _remove_empty_active_typing_bubble(self):
        run = self._visible_run()
        bubble = run.bubble if run else self.active_bubble
        if not bubble or not bubble.is_empty_typing():
            return
        idx = self.msg_layout.indexOf(bubble)
        if idx >= 0:
            item = self.msg_layout.takeAt(idx)
            if item.widget():
                item.widget().deleteLater()
        if run:
            run.bubble = None
        self.active_bubble = None

    def _enter_streaming(self):
        self._auto_scroll = True
        self.jump_btn.hide()
        self.composer.set_enabled(True)
        self.btn.setText("Queue")
        self.btn.setStyleSheet(send_button_style())
        self.btn.setEnabled(True)
        self.stop_btn.show()
        self.stop_btn.setEnabled(True)
        self.stop_btn.setStyleSheet(stop_button_style())
        self._update_queue_ui()
        self.composer.focus_input()

    def _exit_streaming(self):
        self.jump_btn.hide()
        self.composer.set_enabled(True)
        self.btn.setText("Send")
        self.btn.setStyleSheet(send_button_style())
        self.stop_btn.hide()
        self._update_queue_ui()
        self.composer.focus_input()

    def _enter_compaction(self):
        self.jump_btn.hide()
        self.composer.set_enabled(True)
        self.btn.setText("Queue")
        self.btn.setStyleSheet(send_button_style())
        self.btn.setEnabled(True)
        self.stop_btn.hide()
        self._update_queue_ui()
        self.composer.focus_input()

    def _stop_streaming(self):
        run = self._visible_run()
        if run:
            run.thread.cancel()
        self.stop_btn.setEnabled(False)

    def _set_input_enabled(self, enabled: bool):
        """Used for non-streaming states (compaction). Does not touch stop/send mode."""
        self.composer.set_enabled(enabled)
        if enabled:
            self.composer.focus_input()

    def _is_at_bottom(self, threshold: int = 40) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.maximum() - bar.value() <= threshold

    def _on_scroll(self, value: int):
        if self._programmatic_scroll:
            self._last_scroll_value = value
            return
        if (
            self._history_prepend_enabled
            and value <= 24
            and self._render_start_index > 0
        ):
            self._prepend_history_page()
            return

        prev = self._last_scroll_value
        self._last_scroll_value = value

        if not self._visible_run():
            return
        if self._is_at_bottom():
            self._auto_scroll = True
            self.jump_btn.hide()
        elif prev is not None and value < prev:
            self._auto_scroll = False
            self.jump_btn.show()
            self.jump_btn.raise_()
        elif self._auto_scroll:
            self._scroll_to_bottom(force=True)

    def _on_message_list_resize(self):
        if self._auto_scroll:
            self._scroll_to_bottom(force=True)

    def _resume_auto_scroll(self):
        self._auto_scroll = True
        self.jump_btn.hide()
        self._scroll_to_bottom(force=True)

    def _scroll_to_bottom(self, force: bool = False):
        if not force and not self._auto_scroll:
            return
        bar = self.scroll.verticalScrollBar()
        self._programmatic_scroll = True
        bar.setValue(bar.maximum())
        self._last_scroll_value = bar.value()
        self._programmatic_scroll = False

    def _bottom(self):
        if not self._auto_scroll:
            return
        self._pin_to_bottom()

    def _pin_to_bottom(self):
        """Keep the viewport pinned to the latest content (send, stream, layout growth)."""
        self._scroll_to_bottom(force=True)
        QTimer.singleShot(0, lambda: self._scroll_to_bottom(force=True))
        self._scroll_layout_timer.start()

    def _scroll_to_bottom_later(self):
        self._scroll_to_bottom_after_load()

    def _scroll_to_bottom_after_load(self):
        """Scroll to latest messages after rebuilding the transcript (chat switch, compaction)."""
        self._auto_scroll = True
        self.jump_btn.hide()
        self._history_prepend_enabled = False
        self._programmatic_scroll = True

        def scroll():
            bar = self.scroll.verticalScrollBar()
            self._programmatic_scroll = True
            bar.setValue(bar.maximum())
            self._last_scroll_value = bar.value()
            self._programmatic_scroll = False

        scroll()
        for ms in (0, 50, 150, 300):
            QTimer.singleShot(ms, scroll)

        def finish():
            scroll()
            self._programmatic_scroll = False
            self._history_prepend_enabled = True

        QTimer.singleShot(300, finish)

    def _start_next_queued(self):
        if self.conv_id:
            self._start_next_queued_for(self.conv_id)

    def _start_next_queued_for(self, conv_id: str, base_data: dict | None = None):
        rt = self._runtime_for(conv_id)
        if rt.run or rt.compaction or not rt.queued:
            return
        if conv_id != self.conv_id and base_data is None:
            return
        draft = rt.queued.pop(0)
        if conv_id == self.conv_id:
            self._update_queue_ui()
            self._send_draft(draft)
            return

        data = copy.deepcopy(base_data)
        history = copy.deepcopy(data.get("messages", []))
        now = datetime.now().isoformat()
        user_msg = {"role": "user", "content": draft["content"], "created_at": now}
        if draft.get("synthetic"):
            user_msg["synthetic"] = draft["synthetic"]
        history.append(user_msg)
        chat_ref_context = _chat_ref_context(draft.get("chat_refs"))
        if chat_ref_context:
            history.append({
                "role": "user",
                "content": chat_ref_context,
                "synthetic": "chat_refs",
            })
        data["messages"] = history
        data["updated_at"] = now
        model = data.get("model") or self.model_combo.currentText()
        persisted = copy.deepcopy(data)
        persisted["messages"] = prepare_for_storage(history)
        self.store.save(conv_id, persisted)
        self.saved.emit()
        self._start_assistant_run(
            conv_id,
            model,
            history,
            data,
            skill=draft.get("skill"),
            crew=draft.get("crew"),
            visible=False,
        )

    def _next_queued_index_for_current_chat(self) -> int | None:
        return 0 if self._visible_queue() else None

    def _update_queue_ui(self):
        while self._queue_list_layout.count():
            item = self._queue_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        visible = list(enumerate(self._visible_queue()))
        count = len(visible)
        if count:
            noun = "message" if count == 1 else "messages"
            self._queue_label.setText(f"{count} queued {noun}")
            self._queue_label.setStyleSheet(center_notice_style())
            self._queue_label.show()
            for idx, draft in visible:
                self._queue_list_layout.addWidget(self._queue_item(idx, draft))
            self._queue_list.show()
        else:
            self._queue_label.hide()
            self._queue_list.hide()

    def _queue_item(self, idx: int, draft: dict) -> QWidget:
        row = QFrame()
        row.setObjectName("queueItem")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        label = QLabel(_queue_preview(draft))
        label.setObjectName("queuePreview")
        label.setWordWrap(False)

        cancel = QPushButton("x")
        cancel.setToolTip("Cancel queued message")
        cancel.setFixedSize(24, 24)
        cancel.clicked.connect(lambda _, i=idx: self._cancel_queued(i))

        layout.addWidget(label, 1)
        layout.addWidget(cancel)

        p = palette()
        row.setStyleSheet(
            f"QFrame#queueItem {{ background-color: {p['BG3']};"
            f" border: 1px solid {p['BORDER']}; border-radius: 8px; }}"
        )
        label.setStyleSheet(f"color: {p['TEXT_DIM']}; background: transparent;")
        cancel.setStyleSheet(icon_button_style(24))
        return row

    def _cancel_queued(self, idx: int):
        queue = self._visible_queue()
        if 0 <= idx < len(queue):
            queue.pop(idx)
            self._update_queue_ui()


def _read_text_preview(path: str) -> str:
    p = Path(path)
    size = p.stat().st_size
    with p.open("rb") as f:
        raw = f.read(MAX_FILE_PREVIEW_BYTES + 1)
    truncated = len(raw) > MAX_FILE_PREVIEW_BYTES
    text = raw[:MAX_FILE_PREVIEW_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[Preview truncated: showing {MAX_FILE_PREVIEW_BYTES} of {size} bytes]"
    return text


def _queue_preview(draft: dict) -> str:
    content = draft.get("content")
    text = content_preview(content).replace("\n", " ").strip()
    if not text:
        text = draft.get("title_text", "Queued message")
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return text


def _should_complete_slash_selection(text: str, name: str) -> bool:
    invocation = slash_invocation(text)
    if not invocation:
        return False
    typed, args = invocation
    return not args.strip() and typed.casefold() != str(name or "").casefold()


def _first_summoned_crew(text: str, settings: dict | None = None) -> CrewMember | None:
    members = summoned_members(text)
    for member in members:
        if crew_enabled(settings, member):
            return member
    return None


def _enabled_crew(settings: dict | None = None) -> list[CrewMember]:
    return [member for member in all_crew() if crew_enabled(settings, member)]


def _crew_for_history_message(history: list[dict], idx: int) -> CrewMember | None:
    if idx < 0 or idx >= len(history):
        return None
    meta = history[idx].get("crew")
    if not isinstance(meta, dict):
        return None
    return get_crew_member(str(meta.get("id") or ""))


def _latest_regenerable_assistant_index(history: list[dict]) -> int:
    if not history:
        return -1
    idx = len(history) - 1
    while idx >= 0 and history[idx].get("synthetic") == "terminal_result":
        idx -= 1
    if idx < 0:
        return -1
    return idx if history[idx].get("role") == "assistant" else -1


def _terminal_status_detail(result: dict) -> str:
    exit_code = int(result.get("exit_code") or 0)
    duration = float(result.get("duration_s") or 0.0)
    stored = int(result.get("stored_line_count") or 0)
    total = int(result.get("line_count") or stored)
    noun = "line" if stored == 1 else "lines"
    if result.get("truncated") and total > stored:
        line_text = f"{stored} of {total} {noun}"
    else:
        line_text = f"{stored} {noun}"
    return f"exit {exit_code} · {duration:.1f}s · {line_text}"


def _terminal_result_ref(result: dict) -> str:
    stored = int(result.get("stored_line_count") or 0)
    return terminal_ref(1, max(1, stored))


def _history_ends_with_assistant_text(history: list[dict], text: str) -> bool:
    if not history:
        return False
    last = history[-1]
    return last.get("role") == "assistant" and last.get("content") == text


def _crew_notice_text(crew: CrewMember | dict, action: str) -> str:
    if isinstance(crew, CrewMember):
        name = crew.name
    else:
        name = crew_name_from_metadata(crew)
    name = name or "Crew"
    verb = "joined" if action == "joined" else "left"
    return f"{name} {verb} the thread."


def _crew_invocation_key(meta: dict) -> str:
    return str(meta.get("invocation_id") or meta.get("id") or "crew")


def _crew_history_meta(meta: dict) -> dict:
    return {
        "id": str(meta.get("id") or ""),
        "name": crew_name_from_metadata(meta),
        "title": str(meta.get("title") or ""),
        "preferred_model": str(meta.get("preferred_model") or ""),
        "model": str(meta.get("model") or ""),
        "color": str(meta.get("color") or ""),
        "avatar": str(meta.get("avatar") or ""),
    }


def _crew_model_choice(
    crew: CrewMember,
    fallback: str,
    saved_models: dict | None,
    configured_providers: set[str] | None = None,
) -> str:
    return crew_model_choice(crew, fallback, saved_models, configured_providers)


def _message_render_bytes(msg: dict) -> int:
    if not is_visible_message(msg):
        return 0
    return len(content_preview(msg.get("content", "")).encode("utf-8", errors="replace"))


def _window_start(history: list[dict], end: int, byte_limit: int, message_limit: int) -> int:
    total = 0
    start = end
    while start > 0 and end - start < message_limit:
        size = _message_render_bytes(history[start - 1])
        if start < end and total + size > byte_limit:
            break
        total += size
        start -= 1
    return start


_MENTION_RE = re.compile(r'@(?:"([^"]+)"|([^\s@]*[^\s@.,:;!?)\]}]))')


def _list_mention_files(cwd: str, limit: int = 800) -> list[tuple[str, str]]:
    root = Path(cwd).resolve()
    out: list[tuple[str, str]] = []
    try:
        walker = os.walk(root)
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [
                d for d in dirnames
                if d not in IGNORED and not d.startswith(".")
            ]
            for name in sorted(filenames, key=str.lower):
                if name in IGNORED or name.startswith("."):
                    continue
                abs_path = Path(dirpath) / name
                rel = abs_path.relative_to(root).as_posix()
                out.append((rel, str(abs_path)))
                if len(out) >= limit:
                    return out
    except OSError:
        return out
    return out


def _mentioned_files(cwd: str, text: str) -> list[dict]:
    refs = [
        (match.group(1) or match.group(2) or "").strip()
        for match in _MENTION_RE.finditer(text)
    ]
    return _files_for_refs(cwd, refs)


def _message_files(cwd: str, text: str, hidden_refs: list[str] | None = None) -> list[dict]:
    refs = [
        (match.group(1) or match.group(2) or "").strip()
        for match in _MENTION_RE.finditer(text)
    ]
    refs.extend(hidden_refs or [])
    return _files_for_refs(cwd, refs)


def _chat_ref_context(refs: list[dict] | None) -> str:
    cleaned = []
    seen = set()
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        conv_id = str(ref.get("id") or "").strip()
        if not conv_id or conv_id in seen:
            continue
        seen.add(conv_id)
        title = " ".join(str(ref.get("title") or "Untitled").split()) or "Untitled"
        cleaned.append((conv_id, title))
    if not cleaned:
        return ""
    lines = [
        "[Hidden chat references from drag/drop]",
        "Use read_project_chat with these exact conversation_id values if the referenced chat contents are needed.",
    ]
    for conv_id, title in cleaned:
        lines.append(f"- {title} (conversation_id: {conv_id})")
    return "\n".join(lines)


def _files_for_refs(cwd: str, refs: list[str]) -> list[dict]:
    root = Path(cwd).resolve()
    seen: set[str] = set()
    files: list[dict] = []
    for raw in refs:
        lookup = str(raw or "").replace("\\", "/")
        if not lookup:
            continue
        key = lookup.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            path = (root / lookup).resolve()
            path.relative_to(root)
        except (OSError, ValueError):
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            with path.open("rb") as f:
                data = f.read(MAX_TOOL_READ_BYTES + 1)
        except OSError:
            continue
        truncated = len(data) > MAX_TOOL_READ_BYTES
        content = data[:MAX_TOOL_READ_BYTES].decode("utf-8", errors="replace")
        files.append({
            "path": raw,
            "content": content,
            "truncated": truncated,
            "size": size,
        })
    return files
