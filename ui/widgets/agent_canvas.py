import copy
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime
from html import escape, unescape
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPainterPath, QPen, QShortcut
from PyQt6.QtWidgets import (
    QColorDialog,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFrame,
    QGraphicsPathItem,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import MODELS
from storage.agent_canvas import canvas_artifacts_dir
from storage.repository import ConversationStore
from storage.settings import (
    DEFAULT_GRAPH_AGENT_PROMPT,
    SettingsStore,
    canvas_action_auto_approve,
    canvas_parallel_limit,
    canvas_run_mode,
    graph_agent_prompt,
    graph_generation_strategy,
)
from services.chat import ChatThread
from services.conversation_run import ConversationRunManager
from services.content import prepare_for_storage
from services.agent_canvas_run import GraphRunEngine, GraphRunError, GraphRunSession
from services.crew import crew_model_choice, crew_prompt, crew_settings, crew_system_prompt, get_crew_member
from services.model_registry import configured_provider_ids
from services.tool_policy import ConversationToolPolicy, ToolApprovalBus
from services.tool_registry import extension_canvas_context_snippets, extension_canvas_tools
from services.workspace import build_system
from ui.theme import (
    agent_canvas_style,
    compact_combo_box_style,
    graph_question_dialog_style,
    palette,
    primary_button_style,
    secondary_button_style,
)
from ui.widgets.tool_approval_dialog import handle_pending_approval
from ui.widgets.agent_canvas_file_scope import (
    absolute_ref,
    normalize_scope_ref,
    relative_ref,
    repo_path_candidates,
    scope_refs,
    scope_title,
)
from ui.widgets.agent_canvas_inspector import (
    AgentCanvasInspector,
    canvas_agent_for_id,
    canvas_agent_id_for_title,
    canvas_agents,
)
from ui.widgets.agent_canvas_items import _GraphEdge, _GraphFrame, _GraphNode
from ui.widgets.agent_canvas_schema import (
    CanvasEdge,
    CanvasToken,
    CreationAction,
    NODE_STATUSES,
    _CREATION_ACTIONS,
    _CONNECTION_RULES,
    canvas_token_payload,
    component_spec,
    connection_rule,
    connection_rules_for_target,
    default_token_for_kind,
    edge_color,
    input_ports,
    output_ports,
    parse_canvas_token,
)
from ui.widgets.agent_canvas_view import _GraphView
from ui.widgets.bubble import _to_html as assistant_markdown_html


_LOG = logging.getLogger(__name__)
_CANVAS_SHUTDOWN_WAIT_MS = 3000
_DETACHED_SHUTDOWN_THREADS: list[ChatThread] = []
_DOD_REVIEW_PROMPT_LIMIT = 8000
_DOD_REVIEW_MAX_ITEMS = 24
_DOD_REVIEW_ITEM_LIMIT = 420


GRAPH_AGENT_SYSTEM_PROMPT = DEFAULT_GRAPH_AGENT_PROMPT

GRAPH_AGENT_TOOLS = (
    "read_graph",
    "web_fetch",
    "propose_graph_patch",
    "apply_graph_patch",
    "create_dod_fix_action",
    "ask_user",
)


class _GraphTranscript(QTextEdit):
    anchorClicked = pyqtSignal(QUrl)
    userScrollChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewport().setMouseTracking(True)

    def mouseMoveEvent(self, event):
        anchor = self.anchorAt(event.position().toPoint())
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if anchor else Qt.CursorShape.IBeamCursor
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        super().leaveEvent(event)

    def wheelEvent(self, event):
        super().wheelEvent(event)
        self.userScrollChanged.emit()

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        if event.key() in {
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        }:
            self.userScrollChanged.emit()

    def mouseReleaseEvent(self, event):
        anchor = self.anchorAt(event.position().toPoint())
        if anchor:
            self.anchorClicked.emit(QUrl(anchor))
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _TranscriptHost(QWidget):
    """Transcript container with a floating jump-to-bottom button."""

    def __init__(self, transcript: _GraphTranscript, jump_btn: QPushButton, parent=None):
        super().__init__(parent)
        self._jump_btn = jump_btn
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(transcript)
        jump_btn.setParent(self)
        jump_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        btn = self._jump_btn
        btn.move(self.width() - btn.width() - 14, self.height() - btn.height() - 14)


GRAPH_PATCH_OPERATIONS = (
    "add_node",
    "update_node",
    "delete_node",
    "connect",
    "delete_edge",
    "set_active",
)

GRAPH_NODE_KINDS = ("goal", "operation", "context", "scope", "evidence", "decision", "dod")
GRAPH_PATCH_NODE_KINDS = ("goal", "operation", "context", "scope", "evidence", "decision", "dod")


class _GraphToolDispatcher(QObject):
    request = pyqtSignal(object)


class AgentCanvasPanel(QWidget):
    open_file_requested = pyqtSignal(str)
    open_conversation_requested = pyqtSignal(str)
    conversation_created = pyqtSignal(str)
    conversation_updated = pyqtSignal(str)
    conversation_chunk = pyqtSignal(str, str)
    conversation_tool_called = pyqtSignal(str, str, dict)
    conversation_tool_result = pyqtSignal(str, str, str)
    conversation_run_finished = pyqtSignal(str)
    graph_changed = pyqtSignal()
    attention_changed = pyqtSignal(bool)

    def __init__(
        self,
        repo_root: str,
        settings: SettingsStore | None = None,
        parent=None,
        graph_agent_runner=None,
        run_agent_runner=None,
    ):
        super().__init__(parent)
        self._repo_root = repo_root
        self._conversation_store = ConversationStore(repo_root)
        self._conversation_run_manager = ConversationRunManager(self._conversation_store, repo_root, self)
        self._conversation_run_manager.conversation_created.connect(self.conversation_created.emit)
        self._conversation_run_manager.conversation_updated.connect(self.conversation_updated.emit)
        self._conversation_run_manager.chunk.connect(self._on_conversation_run_chunk)
        self._conversation_run_manager.tool_called.connect(self._on_conversation_run_tool_called)
        self._conversation_run_manager.tool_result.connect(self._on_conversation_run_tool_result)
        self._conversation_run_manager.approval_required.connect(self._on_conversation_run_approval_required)
        self._conversation_run_manager.done.connect(self._on_conversation_run_done)
        self._conversation_run_manager.error.connect(self._on_conversation_run_error)
        self._conversation_run_manager.finished.connect(self._on_conversation_run_finished)
        self._conversation_run_nodes: dict[str, tuple[int, str]] = {}
        self._settings = settings or SettingsStore()
        self._lazy_restore_callback = None
        self._graph_agent_runner = graph_agent_runner
        self._run_agent_runner = run_agent_runner
        self._restoring_graph = False
        self._next_node_id = 1
        self._next_frame_id = 1
        self._nodes: dict[int, _GraphNode] = {}
        self._edges: list[CanvasEdge] = []
        self._outgoing_edge_counts: dict[int, dict[int, int]] = {}
        self._incoming_edge_counts: dict[int, dict[int, int]] = {}
        self._frames: dict[int, _GraphFrame] = {}
        self._connect_anchor: _GraphNode | None = None
        self._drag_edge: QGraphicsPathItem | None = None
        self._drag_source_port = "out"
        self._active_node_id: int | None = None
        self._last_selected_node_id: int | None = None
        self._last_selected_frame_id: int | None = None
        self._selection_guard = False
        self._closing = False
        self._closed = False
        self._question_attention = False
        self._run_attention = False
        self._attention_active = False
        self._collapsed_hidden_nodes: set[int] = set()
        self._auto_compacted_goal_ids: set[int] = set()
        self._pending_zoom_compaction: float | None = None
        self._inspector_snapshot: dict | None = None
        self._populating_inspector = False
        self._layouting_graph = False
        self._graph_chat_messages: list[dict[str, str]] = []
        self._graph_agent_thread: ChatThread | None = None
        self._graph_agent_scope_goal_id: int | None = None
        self._graph_agent_generation_mode = False
        self._graph_agent_generation_goal_id: int | None = None
        self._graph_agent_applied_patches = 0
        self._graph_agent_stop_requested = False
        self._graph_agent_stream_index: int | None = None
        self._graph_agent_stream_text = ""
        self._graph_chat_auto_scroll = True
        self._graph_chat_programmatic_scroll = False
        self._graph_agent_thinking_step = 0
        self._graph_agent_thinking_timer = QTimer(self)
        self._graph_agent_thinking_timer.setInterval(350)
        self._graph_agent_thinking_timer.timeout.connect(self._advance_graph_agent_thinking)
        self._graph_tool_status_index: int | None = None
        self._graph_tool_stats: dict[str, dict[str, int]] = {}
        self._graph_tool_events: dict[str, list[dict[str, str]]] = {}
        self._expanded_graph_tool: str | None = None
        self._graph_tool_last = ""
        self._graph_check_failures: list[dict[str, str]] = []
        self._graph_tool_dispatcher = _GraphToolDispatcher(self)
        self._graph_tool_dispatcher.request.connect(self._handle_graph_tool_request)
        self._run_engine = GraphRunEngine()
        self._run_session: GraphRunSession | None = None
        self._run_thread: ChatThread | None = None
        self._run_threads: dict[int, ChatThread] = {}
        self._run_thread_attempt_id = ""
        self._node_run_history: dict[int, list[dict]] = {}
        self._run_last_edit_path = ""
        self._run_chat_render_pending = False
        self._pending_run_conversation_saves: set[tuple[int, str]] = set()
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._undo_limit = 80
        self._restoring_undo_state = False
        self._run_chat_render_timer = QTimer(self)
        self._run_chat_render_timer.setInterval(120)
        self._run_chat_render_timer.setSingleShot(True)
        self._run_chat_render_timer.timeout.connect(self._flush_run_chat_render)
        self._run_conversation_save_timer = QTimer(self)
        self._run_conversation_save_timer.setInterval(800)
        self._run_conversation_save_timer.setSingleShot(True)
        self._run_conversation_save_timer.timeout.connect(self._flush_run_conversation_saves)
        self._zoom_compaction_timer = QTimer(self)
        self._zoom_compaction_timer.setInterval(90)
        self._zoom_compaction_timer.setSingleShot(True)
        self._zoom_compaction_timer.timeout.connect(self._flush_zoom_auto_compaction)
        self._inspector_auto_apply_timer = QTimer(self)
        self._inspector_auto_apply_timer.setInterval(350)
        self._inspector_auto_apply_timer.setSingleShot(True)
        self._inspector_auto_apply_timer.timeout.connect(self._flush_inspector_auto_apply)
        self._expanded_run_tools: set[tuple[str, int]] = set()
        self._approval_bus = ToolApprovalBus(self)
        self._approval_bus.approval_needed.connect(
            lambda pending: handle_pending_approval(self, self._approval_bus, pending)
        )
        self._tool_policy = ConversationToolPolicy()
        self._delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self)
        self._delete_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._delete_shortcut.activated.connect(self.delete_selected)
        self._undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._undo_shortcut.activated.connect(self.undo_graph_change)
        self._redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        self._redo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._redo_shortcut.activated.connect(self.redo_graph_change)
        self.setObjectName("agentCanvas")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        canvas_column = QVBoxLayout()
        canvas_column.setContentsMargins(0, 0, 0, 0)
        canvas_column.setSpacing(0)
        body.addLayout(canvas_column, 1)

        self._scene = QGraphicsScene(self)
        self._graph = _GraphView()
        self._graph.setScene(self._scene)
        self._graph.token_dropped.connect(self.add_token_node)
        self._graph.files_dropped.connect(self._add_file_nodes)
        self._graph.delete_requested.connect(self.delete_selected)
        self._graph.activate_requested.connect(self._activate_selected_node)
        self._graph.edit_requested.connect(self._edit_selected_node)
        self._graph.connection_cancel_requested.connect(self.cancel_connection_drag)
        self._graph.canvas_context_requested.connect(self._show_canvas_context_menu)
        self._graph.zoom_changed.connect(self._on_graph_zoom_changed)
        self._canvas_splitter = QSplitter(Qt.Orientation.Vertical)
        self._canvas_splitter.setObjectName("agentCanvasVerticalSplitter")
        self._canvas_splitter.setChildrenCollapsible(False)
        self._canvas_splitter.addWidget(self._graph)
        self._canvas_splitter.addWidget(self._build_graph_chat())
        self._canvas_splitter.setStretchFactor(0, 1)
        self._canvas_splitter.setStretchFactor(1, 0)
        self._canvas_splitter.setSizes([700, 170])
        self._canvas_splitter.splitterMoved.connect(lambda *_args: self._notify_graph_changed())
        canvas_column.addWidget(self._canvas_splitter, 1)

        self._inspector = self._build_inspector()
        body.addWidget(self._inspector)

        self._seed_graph()
        self._reset_undo_history()
        self.apply_appearance()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(140)
        self._status_timer.timeout.connect(self._advance_status_animation)
        self._status_timer.start()

    def set_repo_root(self, repo_root: str):
        self._repo_root = repo_root
        self._conversation_store = ConversationStore(repo_root)
        self._conversation_run_manager.set_workspace(self._conversation_store, repo_root)
        self._inspector.set_repo_root(repo_root)

    def apply_appearance(self):
        self.setStyleSheet(agent_canvas_style())
        self.refresh_models()
        for button in (
            self._add_goal_btn,
            self._autoformat_btn,
            self._fit_btn,
            self._open_scope_btn,
            self._generate_steps_btn,
            self._frame_color_btn,
            self._cancel_edit_btn,
            self._graph_chat_clear_btn,
            self._graph_chat_send_btn,
            self._run_accept_btn,
            self._run_rerun_btn,
            self._run_guidance_btn,
            self._run_extend_btn,
        ):
            button.setStyleSheet(secondary_button_style())
        self._run_accept_btn.setStyleSheet(primary_button_style())
        self._apply_edit_btn.setStyleSheet(primary_button_style())
        header_combo_style = (
            compact_combo_box_style(
                selector="QComboBox#canvasProviderCombo",
                padding="3px 8px",
                drop_down_width=18,
            )
            + compact_combo_box_style(
                selector="QComboBox#canvasModelCombo",
                padding="3px 8px",
                drop_down_width=18,
            )
        )
        self._provider_combo.setStyleSheet(header_combo_style)
        self._model_combo.setStyleSheet(header_combo_style)
        self._refresh_edges()
        self._graph.viewport().update()

    def closeEvent(self, event):
        if self._closed:
            super().closeEvent(event)
            return
        self._stop_graph_agent_thread(wait=True)
        self._stop_run_thread(wait=True)
        self._stop_panel_timers()
        self._close_completion_popup()
        self._dispose_scene_items()
        self._closed = True
        super().closeEvent(event)

    def close(self):
        if self._closed:
            return True if self.parent() is None else super().close()
        self._stop_graph_agent_thread(wait=True)
        self._stop_run_thread(wait=True)
        self._stop_panel_timers()
        self._close_completion_popup()
        self._dispose_scene_items()
        self._closed = True
        if self.parent() is None:
            self.hide()
            return True
        closed = super().close()
        return closed

    def _stop_graph_agent_thread(self, *, wait: bool = False):
        thread = self._graph_agent_thread
        if thread is not None and thread.isRunning():
            thread.cancel()
            if wait:
                self._wait_or_detach_chat_thread(thread, "canvas graph agent")

    def _stop_run_thread(self, *, wait: bool = False):
        threads = list(self._run_threads.values())
        if self._run_thread is not None and self._run_thread not in threads:
            threads.append(self._run_thread)
        for index, thread in enumerate(threads, start=1):
            if thread is not None and thread.isRunning():
                thread.cancel()
                if wait:
                    self._wait_or_detach_chat_thread(thread, f"canvas run agent {index}")

    def _wait_or_detach_chat_thread(self, thread: ChatThread, label: str) -> bool:
        if not thread.isRunning():
            return True
        if not hasattr(thread, "wait"):
            self._disconnect_chat_thread_signals(thread)
            self._detach_shutdown_thread(thread)
            return False
        if thread.wait(_CANVAS_SHUTDOWN_WAIT_MS):
            return True
        self._disconnect_chat_thread_signals(thread)
        _LOG.warning(
            "%s still running after %sms during canvas shutdown; detached late UI callbacks",
            label,
            _CANVAS_SHUTDOWN_WAIT_MS,
        )
        self._detach_shutdown_thread(thread)
        return False

    @staticmethod
    def _disconnect_chat_thread_signals(thread: ChatThread):
        for signal_name in (
            "chunk",
            "tool_called",
            "bash_line",
            "tool_result",
            "crew_started",
            "crew_chunk",
            "crew_done",
            "crew_error",
            "runtime_event",
            "done",
            "error",
            "finished",
        ):
            signal = getattr(thread, signal_name, None)
            if signal is None:
                continue
            try:
                signal.disconnect()
            except TypeError:
                pass

    @staticmethod
    def _detach_shutdown_thread(thread: ChatThread):
        if thread in _DETACHED_SHUTDOWN_THREADS:
            return
        _DETACHED_SHUTDOWN_THREADS.append(thread)

        def forget():
            try:
                _DETACHED_SHUTDOWN_THREADS.remove(thread)
            except ValueError:
                pass
            delete_later = getattr(thread, "deleteLater", None)
            if callable(delete_later):
                delete_later()

        finished = getattr(thread, "finished", None)
        if finished is None:
            forget()
            return
        finished.connect(forget)

    def _stop_panel_timers(self):
        for timer_name in (
            "_graph_agent_thinking_timer",
            "_run_chat_render_timer",
            "_run_conversation_save_timer",
            "_zoom_compaction_timer",
            "_inspector_auto_apply_timer",
            "_status_timer",
        ):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()

    def _dispose_scene_items(self):
        if not hasattr(self, "_scene"):
            return
        self._closing = True
        try:
            self._scene.clear()
            self._nodes.clear()
            self._edges.clear()
            self._frames.clear()
            self._connect_anchor = None
            self._drag_edge = None
            self._last_selected_node_id = None
            self._last_selected_frame_id = None
        finally:
            self._closing = False

    def _close_completion_popup(self):
        if not hasattr(self, "_scope_path_field"):
            return
        for widget in (self._scope_path_field, self._edit_detail):
            completer = widget.completer() if hasattr(widget, "completer") else None
            if completer is None:
                continue
            popup = completer.popup()
            popup.hide()
            if widget is self._scope_path_field:
                popup.close()
        self._scope_path_field.setCompleter(None)

    def add_token_node(self, token: CanvasToken, point: QPointF | None = None) -> _GraphNode:
        point = point if point is not None else self._next_spawn_point()
        node = self._create_node(token, point)
        self._select_node(node)
        return node

    def connect_nodes(self, source_id: int, target_id: int, source_port: str | None = None) -> bool:
        if source_id == target_id:
            return False
        if source_id not in self._nodes or target_id not in self._nodes:
            return False
        source = self._nodes[source_id]
        target = self._nodes[target_id]
        rule = connection_rule(source.token, target.token, source_port)
        if rule is None:
            self._set_mode(f"cannot connect {source.token.kind} -> {target.token.kind}")
            return False
        return self._connect_with_kind(
            source_id,
            target_id,
            rule.kind,
            rule.label,
            source_port=rule.source_port,
            target_port=rule.target_port,
        )

    def _connect_with_kind(
        self,
        source_id: int,
        target_id: int,
        kind: str,
        label: str | None = None,
        *,
        source_port: str = "out",
        target_port: str = "in",
        sync_visibility: bool = True,
        sync_layout: bool = True,
        skip_cycle_check: bool = False,
    ) -> bool:
        if source_id == target_id:
            return False
        if source_id not in self._nodes or target_id not in self._nodes:
            return False
        if any(
            edge.source_id == source_id
            and edge.target_id == target_id
            and edge.source_port == source_port
            and edge.target_port == target_port
            for edge in self._edges
        ):
            return False
        source = self._nodes[source_id]
        target = self._nodes[target_id]
        label = label or kind.replace("_", " ").title()
        if not skip_cycle_check:
            cycle_path = self._cycle_path_for_new_edge(source_id, target_id)
            if cycle_path:
                summary = self._format_cycle_path(cycle_path)
                if self._restoring_graph:
                    raise ValueError(f"Saved canvas contains a cycle: {summary}.")
                self._set_mode(f"blocked cycle: {summary}")
                return False
        item = _GraphEdge(source_id, target_id, kind, self._show_edge_menu)
        item.setToolTip(label)
        self._scene.addItem(item)
        self._edges.append(CanvasEdge(source_id, target_id, kind, source_port, target_port, item))
        self._index_edge_adjacency(source_id, target_id)
        self._update_edge(item, source, target, kind, source_port, target_port)
        if sync_layout:
            self._sync_root_goal()
            self._refresh_goal_subgoal_children(source_id)
            if sync_visibility:
                self._apply_goal_collapse_visibility()
            self._sync_graph_frames()
            self._sync_counts()
            self._set_mode(f"{label}: {source.token.title} -> {target.token.title}")
            self._notify_graph_changed()
        elif sync_visibility:
            self._apply_goal_collapse_visibility()
        return True

    def _refresh_goal_subgoal_children(self, goal_id: int | None = None):
        if goal_id is not None:
            goal = self._nodes.get(goal_id)
            if goal is None or goal.token.kind != "goal":
                return
            self._set_goal_subgoal_children(goal)
            return
        for node in self._nodes.values():
            if node.token.kind == "goal":
                self._set_goal_subgoal_children(node)

    def _set_goal_subgoal_children(self, goal: _GraphNode):
        outgoing = self._outgoing_edge_counts.get(goal.node_id, {})
        child_titles: list[str] = []
        for child_id in outgoing:
            child = self._nodes.get(child_id)
            if child is not None and child.token.kind == "goal":
                child_titles.append(child.token.title)
        child_titles.sort(key=lambda title: title.casefold())
        goal.set_collapsed_goal_children(child_titles)

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)

    def graph_items(self) -> list[CanvasToken]:
        self._ensure_lazy_restored()
        return [node.token for node in self._nodes.values()]

    def set_lazy_restore_callback(self, callback):
        self._lazy_restore_callback = callback

    def _ensure_lazy_restored(self):
        callback = self._lazy_restore_callback
        if callable(callback):
            callback()

    def graph_state(self) -> dict:
        center = self._graph.mapToScene(self._graph.viewport().rect().center())
        selected = self._selected_node()
        selected_frame = self._selected_frame()
        return {
            "format": "aichs-agent-canvas/v1",
            "version": 1,
            "next_node_id": self._next_node_id,
            "next_frame_id": self._next_frame_id,
            "active_node_id": self._active_node_id,
            "selected_node_id": selected.node_id if selected is not None else None,
            "selected_frame_id": selected_frame.frame_id if selected_frame is not None else None,
            "view": {
                "zoom": float(self._graph._zoom),
                "center": {"x": float(center.x()), "y": float(center.y())},
            },
            "nodes": [
                {
                    "id": node.node_id,
                    "kind": node.token.kind,
                    "title": node.token.title,
                    "detail": node.token.detail,
                    "x": float(node.pos().x()),
                    "y": float(node.pos().y()),
                    "status": node.status,
                    "status_note": node._status_note,
                    "agent_id": node.agent_id,
                    "agent_name": node.agent_name,
                    "collapsed": node.is_collapsed() and node.node_id not in self._auto_compacted_goal_ids,
                    "run_history": self._serializable_run_history(node.node_id),
                }
                for node in self._nodes.values()
            ],
            "edges": [
                {
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "kind": edge.kind,
                    "source_port": edge.source_port,
                    "target_port": edge.target_port,
                }
                for edge in self._edges
            ],
            "frames": [
                {
                    "id": frame.frame_id,
                    "title": frame.title,
                    "color": frame.color,
                    "root_id": frame.root_id,
                    "node_ids": sorted(frame.node_ids),
                    "x": float(frame.scene_rect().x()),
                    "y": float(frame.scene_rect().y()),
                    "w": float(frame.scene_rect().width()),
                    "h": float(frame.scene_rect().height()),
                }
                for frame in self._frames.values()
            ],
            "graph_chat": [
                {
                    "role": str(message.get("role") or ""),
                    "text": str(message.get("text") or ""),
                }
                for message in self._graph_chat_messages
                if str(message.get("text") or "").strip()
            ],
            "graph_tool_activity": [
                {
                    "tool": str(name),
                    "status": str(event.get("status") or ""),
                    "summary": str(event.get("summary") or ""),
                    "detail": str(event.get("detail") or ""),
                }
                for name, events in self._graph_tool_events.items()
                for event in events
                if str(event.get("summary") or "").strip()
            ],
            "graph_check_failures": [
                {
                    "summary": str(failure.get("summary") or ""),
                    "detail": str(failure.get("detail") or ""),
                    "error": str(failure.get("error") or ""),
                }
                for failure in self._graph_check_failures
                if isinstance(failure, dict) and str(failure.get("summary") or "").strip()
            ],
            "graph_chat_split": [int(size) for size in self._canvas_splitter.sizes()],
        }

    def _reset_undo_history(self):
        self._undo_stack = [copy.deepcopy(self.graph_state())]
        self._redo_stack = []

    def _record_undo_snapshot(self):
        if self._restoring_graph or self._restoring_undo_state:
            return
        state = copy.deepcopy(self.graph_state())
        if self._undo_stack and self._same_graph_snapshot(self._undo_stack[-1], state):
            return
        self._undo_stack.append(state)
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack = self._undo_stack[-self._undo_limit :]
        self._redo_stack.clear()

    @staticmethod
    def _same_graph_snapshot(left: dict, right: dict) -> bool:
        return json.dumps(left, sort_keys=True, separators=(",", ":"), ensure_ascii=False) == json.dumps(
            right,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def can_undo_graph_change(self) -> bool:
        return len(self._undo_stack) > 1 and not self._is_graph_agent_running() and not self._is_run_agent_running()

    def can_redo_graph_change(self) -> bool:
        return bool(self._redo_stack) and not self._is_graph_agent_running() and not self._is_run_agent_running()

    def undo_graph_change(self):
        if not self.can_undo_graph_change():
            self._set_mode("nothing to undo")
            return
        current = self._undo_stack.pop()
        target = copy.deepcopy(self._undo_stack[-1])
        self._redo_stack.append(current)
        self._restore_graph_history_state(target, "undid graph change")

    def redo_graph_change(self):
        if not self.can_redo_graph_change():
            self._set_mode("nothing to redo")
            return
        target = self._redo_stack.pop()
        self._undo_stack.append(copy.deepcopy(target))
        self._restore_graph_history_state(target, "redid graph change")

    def _restore_graph_history_state(self, state: dict, mode: str):
        self._restoring_undo_state = True
        try:
            warning = self.restore_graph_state(copy.deepcopy(state), reset_undo=False)
        finally:
            self._restoring_undo_state = False
        if warning:
            self._set_mode(warning)
            return
        self._set_mode(mode)
        self.graph_changed.emit()

    def read_graph_tool(self, scope_goal_id: int | None = None) -> dict:
        state = self.graph_state()
        scope = self._graph_scope_for_goal(scope_goal_id, state) if scope_goal_id is not None else None
        nodes = state["nodes"]
        edges = state["edges"]
        frames = state.get("frames", [])
        scoped_node_ids: set[int] | None = None
        if scope is not None:
            scoped_node_ids = set(scope["node_ids"])
            nodes = [node for node in nodes if int(node["id"]) in scoped_node_ids]
            edges = [
                edge
                for edge in edges
                if int(edge["source_id"]) in scoped_node_ids and int(edge["target_id"]) in scoped_node_ids
            ]
            frames = [
                frame
                for frame in frames
                if any(int(node_id) in scoped_node_ids for node_id in frame.get("node_ids", []))
            ]
        visible_node_ids = {int(node["id"]) for node in nodes}
        selected_node_id = state["selected_node_id"]
        active_node_id = state["active_node_id"]
        if scoped_node_ids is not None:
            selected_node_id = selected_node_id if selected_node_id in visible_node_ids else scope_goal_id
            active_node_id = active_node_id if active_node_id in visible_node_ids else None
        root_goal_ids = [node.node_id for node in self._root_goal_nodes()]
        if scoped_node_ids is not None:
            root_goal_ids = [node_id for node_id in root_goal_ids if node_id in visible_node_ids]
            if scope_goal_id is not None and scope_goal_id not in root_goal_ids:
                root_goal_ids = [scope_goal_id, *root_goal_ids]
        scope_payload = scope or {"mode": "full", "goal_id": None}
        if scope is not None:
            scope_payload = {
                "mode": scope["mode"],
                "goal_id": scope["goal_id"],
                "node_ids": list(scope["node_ids"]),
            }
        unscoped_node_ids = self._unscoped_node_ids()
        if scoped_node_ids is not None:
            unscoped_node_ids = [node_id for node_id in unscoped_node_ids if node_id in visible_node_ids]
        payload = {
            "schema": self._graph_agent_schema(),
            "graph": {
                "nodes": nodes,
                "edges": edges,
                "frames": frames,
                "selected_node_id": selected_node_id,
                "active_node_id": active_node_id,
                "root_goal_id": scope_goal_id if scoped_node_ids is not None else (self._root_goal_node().node_id if self._root_goal_node() is not None else None),
                "root_goal_ids": root_goal_ids,
                "scope": scope_payload,
                "unscoped_node_ids": unscoped_node_ids,
            },
            "cycles": self._payload_cycle_summaries(nodes, edges),
        }
        extension_context = self._canvas_extension_context_entries(
            kind="graph",
            graph=payload["graph"],
            scope_goal_id=scope_goal_id,
        )
        if extension_context:
            payload["canvas_extension_context"] = extension_context
        return payload

    def apply_graph_patch(self, patch: dict) -> dict:
        try:
            state, applied = self._patched_graph_state(patch)
        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "error": str(exc) or "Invalid graph patch.",
                "applied_operations": 0,
                "nodes": len(self._nodes),
                "edges": len(self._edges),
            }
        warning = self.restore_graph_state(state, reset_undo=False)
        if warning:
            return {
                "ok": False,
                "error": warning,
                "applied_operations": 0,
                "nodes": len(self._nodes),
                "edges": len(self._edges),
            }
        self._sync_counts()
        self._notify_graph_changed()
        self._set_mode(f"graph patch applied ({applied} ops)")
        self._advance_run_after_status_change()
        return {
            "ok": True,
            "summary": f"Applied {applied} graph patch operations.",
            "applied_operations": applied,
            "nodes": len(self._nodes),
            "edges": len(self._edges),
        }

    def _graph_agent_schema(self) -> dict:
        return {
            "tools": list(GRAPH_AGENT_TOOLS),
            "tool_contract": {
                "read_graph": "Returns this schema plus current nodes, edges, selected node, active node, master root goals, and cycle warnings.",
                "web_fetch": "Fetches one HTTP(S) URL for graph-planning research only. Use it to understand external product/domain context, examples, or docs before shaping nodes. Do not use fetched content as implementation proof or claim implementation work is done.",
                "propose_graph_patch": "Validate {'operations': [...]}. For new nodes, declare a client_id, include non-empty detail, and connect that client_id in the same patch. The tool autocorrects safe defaults such as missing operation crew and one obvious Design -> Implement handoff.",
                "apply_graph_patch": "Applies a proposed patch atomically. If any operation is invalid, deletes the source goal, adds an incoming edge to the source goal, disconnects from the selected goal scope, or creates a cycle, nothing changes.",
                "create_dod_fix_action": "DoD-only transition for Needs Changes reviews. Creates a corrective action from the review text and inserts it before the DoD. Use this instead of add_node(context), add_node(goal), or completing the DoD when a DoD review needs changes.",
                "ask_user": "Shows the user one modal question per call and returns their answer. Use multi_select=true only when several choices can be true at once, such as desired features or constraints; keep single-choice for direction/ownership/tradeoff decisions. Multiple ask_user calls are valid when each answer can change the graph shape. In Generate Steps, ask only design/product questions; do not ask the user to choose engines, frameworks, libraries, file paths, or technical approaches unless the goal is explicitly about that choice.",
            },
            "run_context_model": {
                "rule": "A run receives the selected node plus its direct graph inputs. Sibling nodes under the same goal do not share output or history.",
                "implication": "When a planning/design/research/spec action exists before implementation, connect that action directly to the implementation action.",
                "preferred_handoff_patch": {
                    "op": "connect",
                    "source": "design_or_architecture_action_client_id",
                    "target": "implementation_action_client_id",
                    "source_port": "implement",
                },
                "handoff_recipes": [
                    "Preferred: connect the planning/design/research/spec operation to the implementation operation with source_port='implement'.",
                    "Use operation.decision -> decision, then decision.guide -> operation only when a real decision/review gate exists.",
                    "Use operation.decision -> context, then context.context -> operation only when a decision becomes reusable durable context.",
                ],
                "not_handoffs": [
                    "goal.work -> operation siblings are independent unless connected",
                    "goal.context -> context only parks context; also connect context.context -> operation or context.context -> decision",
                    "evidence.context -> operation is invalid; use evidence.feedback -> operation or context.context -> operation",
                    "Do not connect implementation back into the upstream design/spec/context/evidence node it consumed; create a separate downstream evidence/proof node instead.",
                ],
            },
            "patch_operations": list(GRAPH_PATCH_OPERATIONS),
            "node_kinds": {
                kind: {
                    "title": component_spec(kind).title,
                    "role": component_spec(kind).role,
                    "detail_contract": self._graph_node_detail_contract(kind),
                    "inputs": [{"key": port.key, "label": port.label} for port in input_ports(kind)],
                    "outputs": [{"key": port.key, "label": port.label} for port in output_ports(kind)],
                }
                for kind in GRAPH_PATCH_NODE_KINDS
            },
            "component_playbook": self._graph_component_playbook(),
            "available_crew": [
                {"id": agent.id, "name": agent.name, "title": agent.title}
                for agent in canvas_agents()
            ],
            "operation_agent_contract": {
                "rule": "Every generated operation add_node should include agent_id and agent_name from available_crew. If omitted, the tool safely defaults based on the action title/detail.",
                "default": {"agent_id": "coder", "agent_name": "Coder"},
                "examples": [
                    {
                        "kind": "operation",
                        "title": "Implement focused change",
                        "agent_id": "coder",
                        "agent_name": "Coder",
                    },
                    {
                        "kind": "operation",
                        "title": "Research repo evidence",
                        "agent_id": "scout",
                        "agent_name": "Scout",
                    },
                    {
                        "kind": "operation",
                        "title": "Design architecture path",
                        "agent_id": "architect",
                        "agent_name": "Architect",
                    },
                    {
                        "kind": "operation",
                        "title": "Capture durable decision",
                        "agent_id": "archivist",
                        "agent_name": "Archivist",
                    },
                ],
                "selection_hint": "Use coder for implementation by default, scout for read-only research, architect for design/architecture/decomposition, and archivist for durable memory or summary work.",
                "ask_user_when": "Only ask_user for crew ownership when choosing the owner would change graph shape or accountability.",
            },
            "graph_value_contract": {
                "rule": "Linear is allowed when the chain carries real graph value. Reject plain action lists, not straight flows.",
                "valid_linear_examples": [
                    "Architect/design operation -> implementation operation with source_port='implement'",
                    "Context/scope feeds an operation that then produces evidence for DoD",
                    "Operation produces a decision contract, then that accepted decision gates the next operation",
                ],
                "invalid_linear_example": "Goal -> action -> action -> action with no consumed context/scope, no decision/proof/DoD, no branch, and no meaningful crew handoff.",
                "repair": "Add only the graph signal that is real: consumed context/scope, decision, evidence/proof, DoD, branch/fan-in, or distinct crew handoff. If none applies, use one action or ask_user.",
            },
            "generation_quality_gate": [
                "Every generated operation add_node should include agent_id and agent_name from available_crew. Missing crew is autocorrected when the owner is obvious; specify it yourself when accountability matters.",
                "A generated context node must feed at least one action or decision; goal -> context alone is not useful.",
                "Generated branches with multiple actions must provide graph value: consumed context/scope, explicit decision/proof/DoD, branch/fan-in, or a meaningful crew handoff. A straight chain is valid only when it carries one of those signals.",
                "Generated branches with multiple actions and DoD should include expected evidence/proof that can feed the DoD.",
                "If one generated action plans/designs/architects/researches/specs and another implements from it, add a connect op from the planning action to the implementation action with source_port='implement'. Goal -> planning and Goal -> implementation as siblings is not enough.",
                "Implementation must not connect back into the same upstream design/spec/context/evidence node it consumed. Create separate downstream evidence/proof for implementation output.",
                "If these rules feel forced, ask_user instead of creating a weak linear graph.",
            ],
            "generation_checklist": [
                "Start from the selected goal as the outcome, not from a transcript, checklist, or one-chat prompt.",
                "Every add_node must include a non-empty detail that follows the node_kinds[kind].detail_contract.",
                "Context detail must synthesize durable constraints or implications in prose, not merely concatenate answer labels. Example: 'Target: scientific calculator web app. UX priority: compact, keyboard-focused interaction. This constrains layout, shortcuts, and acceptance proof.'",
                "Files detail must be repo paths only, one per line. Do not put descriptions such as 'Calculator component, UI, and test files' in Files detail.",
                "Use the graph for mega-feature decomposition: responsibilities, unknowns, decision contracts, context boundaries, file scopes, review paths, and acceptance evidence.",
                "If the request collapses to Analyze -> Implement -> Verify, keep it minimal or ask what larger breakdown the user wants.",
                "Keep the graph as simple as possible. Add fewer nodes when existing nodes already express the plan.",
                "Use a straight flow for sequential work. Branch only for real parallel work, alternatives, dependencies, review paths, or separate acceptance evidence.",
                "Wire dependencies explicitly: downstream agents only receive direct graph inputs, not nearby sibling nodes or visual order. Architecture/design/research/spec actions should point forward into implementation with source_port='implement'.",
                "Add or reuse a DoD node as the terminal acceptance contract for meaningful goals.",
                "Use operation as a runnable work action by a selected crew member. Do not add ownership-only nodes for execution.",
                "Break down by distinct responsibility, not by generic phases. Use decision nodes for accepted choices produced by operations; use context for options, constraints, and tradeoff background.",
                "Before adding 3 or more work actions, decide what graph value exists: consumed context/scope, evidence, decision, DoD, branch/fan-in, or a meaningful crew handoff. Do not add fake structural nodes just to avoid a straight line.",
                "Ask the user concise design questions when missing product intent, UX behavior, acceptance criteria, constraints, risk tolerance, or business tradeoffs would change the graph shape. Ask one focused question per ask_user call; multiple calls are valid when answers unlock different graph decisions.",
                "Do not ask the user to choose engines, frameworks, libraries, file paths, or technical approaches during Generate Steps. Create architecture/research work for crew instead unless the goal explicitly asks for that technical choice.",
                "Use scope for files or code areas, evidence for proof or verification output, decision for accepted choices that downstream work must obey, DoD for acceptance, and context for durable constraints/options/tradeoff background.",
                "Evidence should feed DoD; DoD is the sink that closes the graph. Do not use evidence as the terminal node.",
                "Use exact source_port values from connection_rules. For proof driving more work, use evidence.feedback -> operation. For durable context driving work, use context.context -> operation. Never point implementation back to the upstream node it consumed.",
                "An all-action branch is acceptable only when every node is distinct runnable execution and no structural node clarifies input, proof, decision, or acceptance.",
            ],
            "generation_patch_patterns": {
                "good_design_to_implementation_handoff": [
                    {"op": "add_node", "client_id": "design", "kind": "operation", "title": "Design UX", "detail": "Define behavior and UI contract.", "agent_id": "architect", "agent_name": "Architect"},
                    {"op": "add_node", "client_id": "implement", "kind": "operation", "title": "Implement UX", "detail": "Build from the accepted design contract.", "agent_id": "coder", "agent_name": "Coder"},
                    {"op": "connect", "source": "selected_goal_id", "target": "design", "source_port": "work"},
                    {"op": "connect", "source": "design", "target": "implement", "source_port": "implement"},
                ],
                "bad_sibling_handoff": [
                    {"op": "connect", "source": "selected_goal_id", "target": "design", "source_port": "work"},
                    {"op": "connect", "source": "selected_goal_id", "target": "implement", "source_port": "work"},
                ],
                "why_bad": "The implementation action will not see the design output, because both actions are only siblings under the goal.",
            },
            "repair_patterns": {
                "proof_for_dod": [
                    "goal.work -> operation",
                    "operation.evidence -> evidence",
                    "evidence.supports -> dod",
                ],
                "context_feeds_work": [
                    "goal.context -> context",
                    "context.context -> operation",
                ],
                "design_to_implementation": [
                    "goal.work -> design operation",
                    "design operation.implement -> implementation operation",
                ],
                "proof_feedback_to_work": [
                    "evidence.feedback -> operation",
                ],
            },
            "statuses": list(NODE_STATUSES),
            "connection_rules": [
                {
                    "source_kind": rule.source_kind,
                    "source_port": rule.source_port,
                    "target_kind": rule.target_kind,
                    "target_port": rule.target_port,
                    "edge_kind": rule.kind,
                    "label": rule.label,
                }
                for rule in _CONNECTION_RULES
                if rule.source_kind in GRAPH_PATCH_NODE_KINDS and rule.target_kind in GRAPH_PATCH_NODE_KINDS
            ],
            "operation_shapes": {
                "add_node": {
                    "required": ["op", "kind", "title", "detail"],
                    "operation_preferred": ["agent_id", "agent_name"],
                    "optional": ["client_id", "id", "x", "y", "status", "status_note", "agent_id", "agent_name"],
                "note": "Use client_id for temporary references in the same patch. A string id such as 'op_1' is accepted as a client_id alias. detail must follow node_kinds[kind].detail_contract; the tool backfills only as a safety net. If kind is operation during Generate Steps, agent_id and agent_name are autocorrected when omitted, but explicit crew is preferred.",
                },
                "update_node": {
                    "required": ["op", "id"],
                    "optional": ["title", "detail", "status", "status_note", "agent_id", "agent_name"],
                },
                "delete_node": {"required": ["op", "id"]},
                "connect": {"required": ["op", "source", "target", "source_port"]},
                "delete_edge": {
                    "required": ["op"],
                    "optional": ["source", "target", "source_port", "target_port", "kind"],
                },
                "set_active": {"required": ["op"], "optional": ["id", "status", "status_note"]},
            },
            "patch_example": {
                "operations": [
                    {
                        "op": "add_node",
                        "client_id": "action1",
                        "kind": "operation",
                        "title": "Implement first action",
                        "detail": "Describe the concrete implement, decide, or proof action this selected crew member should run.",
                    },
                    {"op": "connect", "source": 1, "target": "action1", "source_port": "work"},
                    {"op": "set_active", "id": "action1", "status": "running"},
                ]
            },
            "restriction_model": {
                "hard_constraints": [
                    "Patch must be syntactically valid and atomic.",
                    "Node kind, title, status, and details must be valid for the component.",
                    "Connections must use a declared connection_rule and exact source_port.",
                    "Graph edits must stay inside the selected goal scope.",
                    "The source goal for the current generation cannot be deleted or given a new incoming edge.",
                    "Directed cycles are blocked because runs require an acyclic branch.",
                    "New nodes in a scoped edit must connect into that selected goal scope.",
                    "Files/scope detail must be repo-like paths, not prose descriptions.",
                ],
                "soft_quality_checks": [
                    "Generated context should feed an action or decision.",
                    "Generated multi-action plans should carry graph value, not just a generic task list.",
                    "Generated multi-action plans with DoD should include expected evidence/proof.",
                    "Planning/design/research/spec actions should feed implementation actions directly.",
                ],
                "autocorrections": [
                    "Missing generated operation crew is defaulted from title/detail: Scout for research, Architect for design/planning/spec, otherwise Coder.",
                    "A single obvious planning/design/research/spec action plus a single implementation action gets an operation.implement connection when missing.",
                ],
                "do_not_autocorrect": [
                    "Cycles, scope escapes, invalid connection kinds, deleting protected goals, fake file paths, and ambiguous multi-node handoffs.",
                ],
            },
        }

    @staticmethod
    def _graph_node_detail_contract(kind: str) -> dict:
        contracts = {
            "goal": {
                "field_label": "Description",
                "put": "Outcome, user value, constraints, and acceptance signal.",
                "avoid": "A generic task name with no definition of done.",
                "example": "Make calculator input fast and reliable for keyboard-heavy scientific use; accepted when core functions, history, and error states are verified.",
            },
            "operation": {
                "field_label": "Description",
                "put": "The runnable action for the selected crew member: what to do, expected output/artifact, and local acceptance criteria.",
                "avoid": "Crew name only, vague phase labels, or a duplicate of the title.",
                "example": "Design the calculator interaction contract: keyboard model, display states, history behavior, and acceptance checks. Output a design artifact consumed by implementation.",
            },
            "context": {
                "field_label": "Description",
                "put": "Durable facts, constraints, options, conventions, or synthesized user answers that should guide connected work.",
                "avoid": "Raw answer labels or comma-only lists without implications.",
                "example": "Target: scientific calculator web app. UX priority: compact, keyboard-focused interaction. This constrains layout density, shortcut behavior, and proof expectations.",
            },
            "scope": {
                "field_label": "Paths",
                "put": "Repo paths only: file or folder references, one per line. Existing paths are preferred; future files should still look like paths.",
                "avoid": "Descriptions of areas, component names, or phrases such as 'UI and test files'.",
                "example": "src/calculator/\ntests/test_calculator.py\nUX_UI_DESIGN.md",
            },
            "evidence": {
                "field_label": "Description",
                "put": "Expected proof or actual review artifact: tests, screenshots, diffs, errors, or acceptance evidence.",
                "avoid": "Claiming proof exists before a run has produced it.",
                "example": "Expected proof: calculator interaction tests pass, keyboard shortcuts are documented, and UI error states are screenshot-reviewed.",
            },
            "decision": {
                "field_label": "Description",
                "put": "Decision contract while idle, or accepted choice after its producer operation is approved: question, criteria, chosen path, reason, and downstream guidance.",
                "avoid": "Unresolved options/background with no producer operation, or a generic approval label with no reason.",
                "example": "Contract: choose inline history vs drawer history based on compact keyboard-first usage. Accepted result: use inline history because it preserves focus and avoids mode switching.",
            },
            "dod": {
                "field_label": "Acceptance Criteria",
                "put": "Terminal acceptance criteria for this goal. Evidence and decisions feed this node.",
                "avoid": "Implementation steps or proof notes.",
                "example": "Done when scientific functions, keyboard UX, history behavior, error states, and focused tests are accepted.",
            },
        }
        return contracts.get(kind, {
            "field_label": "Description",
            "put": "Purpose and expected outcome.",
            "avoid": "Ambiguous labels.",
            "example": "Describe the graph component clearly.",
        })

    @staticmethod
    def _graph_component_playbook() -> dict:
        return {
            "goal": {
                "use_for": "A desired outcome or sub-outcome.",
                "avoid": "Do not use as a generic step label.",
                "good_connections": ["goal.work [WORK] -> operation", "goal.context -> context", "goal.split -> goal", "decision.resolve -> goal"],
            },
            "operation": {
            "use_for": "A runnable work action: implement, decide, or produce proof. Crew is selected on the node, not represented by a separate node.",
            "avoid": "Do not make generic Plan/Implement/Verify filler chains or use action nodes for every component.",
            "good_connections": ["goal.work -> operation", "operation.implement -> operation", "scope.read -> operation", "context.context -> operation", "operation.evidence -> evidence", "operation.decision -> decision", "operation.implement -> dod"],
        },
            "scope": {
                "use_for": "Actual repo files or folders that ground the work. Put only paths in detail, one per line.",
                "avoid": "Do not use for descriptions like 'Calculator component, UI, and test files'. Do not invent precise paths unless known from graph context or user input.",
                "good_connections": ["scope.read -> operation", "scope.proof -> context"],
            },
            "context": {
                "use_for": "Durable constraints, docs, conventions, product context, or synthesized user answers that shape work.",
                "avoid": "Do not use for concrete files; use scope for those. Do not paste raw comma-separated answers without explaining what they imply.",
                "good_connections": ["goal.context -> context", "scope.proof -> context", "operation.decision -> context", "context.context -> operation"],
            },
            "evidence": {
                "use_for": "Expected proof, verification output, tests, screenshots, diffs, or acceptance signals.",
                "avoid": "Do not claim evidence exists before work runs; title it as expected proof. Do not use evidence as the final graph sink.",
                "good_connections": ["operation.evidence -> evidence", "evidence.supports -> dod", "evidence.feedback -> operation"],
            },
            "decision": {
                "use_for": "A choice, review gate, tradeoff, or approval that changes the path.",
                "avoid": "Do not create if there is no meaningful decision point.",
                "good_connections": ["operation.decision -> decision", "decision.guide -> operation", "decision.resolve -> goal", "decision.resolve -> dod"],
            },
            "dod": {
                "use_for": "Terminal acceptance criteria for a meaningful goal.",
                "avoid": "Do not use as work, proof, or a planning heading. DoD is a sink.",
                "good_connections": ["operation.implement -> dod", "evidence.supports -> dod", "decision.resolve -> dod"],
            },
        }

    def _patched_graph_state(self, patch: dict) -> tuple[dict, int]:
        if not isinstance(patch, dict):
            raise TypeError("Graph patch must be an object.")
        operations = patch.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("Graph patch must contain a non-empty operations list.")

        state = self.graph_state()
        nodes = [dict(node) for node in state["nodes"]]
        edges = [dict(edge) for edge in state["edges"]]
        node_by_id = {int(node["id"]): node for node in nodes}
        client_ids: dict[str, int] = {}
        next_id = max([int(state.get("next_node_id") or 1), *((node_id + 1) for node_id in node_by_id)])
        unpositioned_new_ids: set[int] = set()
        applied = 0

        for raw in operations:
            if not isinstance(raw, dict):
                raise ValueError("Every patch operation must be an object.")
            op = str(raw.get("op") or "").strip()
            if op not in GRAPH_PATCH_OPERATIONS:
                raise ValueError(f"Unsupported graph patch operation: {op or '<empty>'}.")
            if op == "add_node":
                node_id = next_id
                next_id += 1
                client_id = self._patch_add_node_client_id(raw)
                kind = str(raw.get("kind") or "").strip()
                title = str(raw.get("title") or "").strip()
                if kind not in GRAPH_PATCH_NODE_KINDS or not title:
                    raise ValueError("add_node requires a supported kind and non-empty title.")
                detail = self._patch_node_detail(kind, title, raw.get("detail"))
                self._validate_patch_node_detail(kind, detail)
                if client_id:
                    if client_id in client_ids:
                        raise ValueError(f"Duplicate patch client_id: {client_id}.")
                    client_ids[client_id] = node_id
                explicit_position = "x" in raw or "y" in raw
                node = {
                    "id": node_id,
                    "kind": kind,
                    "title": title,
                    "detail": detail,
                    "x": self._patch_float(raw.get("x"), self._next_patch_x(nodes)),
                    "y": self._patch_float(raw.get("y"), self._next_patch_y(nodes)),
                    "status": self._patch_status(raw.get("status"), default="idle"),
                    "status_note": str(raw.get("status_note") or ""),
                    "agent_id": str(raw.get("agent_id") or ""),
                    "agent_name": str(raw.get("agent_name") or ""),
                }
                if not explicit_position:
                    unpositioned_new_ids.add(node_id)
                nodes.append(node)
                node_by_id[node_id] = node
                applied += 1
            elif op == "update_node":
                node = node_by_id.get(self._resolve_patch_node_ref(raw.get("id"), client_ids))
                if node is None:
                    raise ValueError("update_node refers to a missing node.")
                for field in ("title", "detail", "status_note", "agent_id", "agent_name"):
                    if field in raw:
                        value = str(raw.get(field) or "").strip() if field == "title" else str(raw.get(field) or "")
                        if field == "title" and not value:
                            raise ValueError("update_node title cannot be empty.")
                        node[field] = value
                self._validate_patch_node_detail(str(node.get("kind") or ""), str(node.get("detail") or ""))
                if "status" in raw:
                    node["status"] = self._patch_status(raw.get("status"), default=str(node.get("status") or "idle"))
                applied += 1
            elif op == "delete_node":
                node_id = self._resolve_patch_node_ref(raw.get("id"), client_ids)
                if node_id not in node_by_id:
                    raise ValueError("delete_node refers to a missing node.")
                nodes = [node for node in nodes if int(node["id"]) != node_id]
                node_by_id.pop(node_id, None)
                edges = [
                    edge
                    for edge in edges
                    if int(edge["source_id"]) != node_id and int(edge["target_id"]) != node_id
                ]
                if state.get("active_node_id") == node_id:
                    state["active_node_id"] = None
                if state.get("selected_node_id") == node_id:
                    state["selected_node_id"] = None
                applied += 1
            elif op == "connect":
                source_id = self._resolve_patch_node_ref(raw.get("source"), client_ids)
                target_id = self._resolve_patch_node_ref(raw.get("target"), client_ids)
                if source_id == target_id or source_id not in node_by_id or target_id not in node_by_id:
                    raise ValueError("connect refers to missing or identical nodes.")
                source_port = str(raw.get("source_port") or "out")
                source_token = CanvasToken(str(node_by_id[source_id]["kind"]), str(node_by_id[source_id]["title"]))
                target_token = CanvasToken(str(node_by_id[target_id]["kind"]), str(node_by_id[target_id]["title"]))
                rule = connection_rule(source_token, target_token, source_port)
                if rule is None:
                    hint = self._invalid_connection_hint(source_token.kind, target_token.kind)
                    raise ValueError(
                        f"connect is not valid: {source_token.kind}.{source_port} -> {target_token.kind}. {hint}"
                    )
                candidate = {
                    "source_id": source_id,
                    "target_id": target_id,
                    "kind": rule.kind,
                    "source_port": rule.source_port,
                    "target_port": rule.target_port,
                }
                if any(self._same_edge(candidate, edge) for edge in edges):
                    raise ValueError("connect duplicates an existing connection.")
                edges.append(candidate)
                cycle = self._payload_cycle_summaries(nodes, edges)
                if cycle:
                    raise ValueError(
                        f"connect would create a cycle: {cycle[0]}. "
                        f"{self._cycle_connection_repair_hint(source_token.kind, target_token.kind)}"
                    )
                applied += 1
            elif op == "delete_edge":
                before = len(edges)
                edges = [
                    edge for edge in edges if not self._patch_edge_matches(edge, raw, client_ids)
                ]
                if len(edges) == before:
                    raise ValueError("delete_edge did not match any connection.")
                applied += 1
            elif op == "set_active":
                node_id = None if raw.get("id") is None else self._resolve_patch_node_ref(raw.get("id"), client_ids)
                if node_id is not None and node_id not in node_by_id:
                    raise ValueError("set_active refers to a missing node.")
                state["active_node_id"] = node_id
                if node_id is not None:
                    node = node_by_id[node_id]
                    if "status" in raw:
                        node["status"] = self._patch_status(raw.get("status"), default=str(node.get("status") or "idle"))
                    if "status_note" in raw:
                        node["status_note"] = str(raw.get("status_note") or "")
                applied += 1
        cycles = self._payload_cycle_summaries(nodes, edges)
        if cycles:
            raise ValueError(f"Graph patch creates a cycle: {cycles[0]}.")
        self._place_unpositioned_patch_nodes(nodes, edges, unpositioned_new_ids)
        state["nodes"] = nodes
        state["edges"] = edges
        state["next_node_id"] = next_id
        if state.get("selected_node_id") not in node_by_id:
            state["selected_node_id"] = next(iter(node_by_id), None)
        if state.get("active_node_id") not in node_by_id:
            state["active_node_id"] = None
        return state, applied

    @staticmethod
    def _invalid_connection_hint(source_kind: str, target_kind: str) -> str:
        exact = [
            f"{rule.source_kind}.{rule.source_port} -> {rule.target_kind}.{rule.target_port}"
            for rule in _CONNECTION_RULES
            if rule.source_kind == source_kind and rule.target_kind == target_kind
        ]
        if exact:
            return "Valid for this pair: " + ", ".join(exact) + "."
        from_source = [
            f"{rule.source_kind}.{rule.source_port} -> {rule.target_kind}.{rule.target_port}"
            for rule in _CONNECTION_RULES
            if rule.source_kind == source_kind
        ][:4]
        to_target = [
            f"{rule.source_kind}.{rule.source_port} -> {rule.target_kind}.{rule.target_port}"
            for rule in _CONNECTION_RULES
            if rule.target_kind == target_kind
        ][:4]
        hints = []
        if from_source:
            hints.append("Valid from source: " + ", ".join(from_source) + ".")
        if to_target:
            hints.append("Valid to target: " + ", ".join(to_target) + ".")
        return " ".join(hints).strip()

    @staticmethod
    def _cycle_connection_repair_hint(source_kind: str, target_kind: str) -> str:
        if source_kind == "operation" and target_kind in {"context", "evidence", "decision"}:
            return (
                "This looks like implementation writing back into an upstream artifact it consumed. "
                "Keep upstream design/spec/context/proof nodes as inputs, and create a separate downstream "
                "evidence/proof or decision node for implementation output."
            )
        return (
            "Graph edges must point forward through the work. Remove the backward edge; if new output is needed, "
            "create a separate downstream node instead of linking back to an upstream node."
        )

    @staticmethod
    def _patch_float(value: object, default: float) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError("Node positions must be numeric.") from None

    @staticmethod
    def _patch_status(value: object, *, default: str) -> str:
        status = str(value or default).strip() or default
        if status not in NODE_STATUSES:
            raise ValueError(f"Unsupported node status: {status}.")
        return status

    @staticmethod
    def _same_edge(left: dict, right: dict) -> bool:
        return (
            int(left["source_id"]) == int(right["source_id"])
            and int(left["target_id"]) == int(right["target_id"])
            and str(left["source_port"]) == str(right["source_port"])
            and str(left["target_port"]) == str(right["target_port"])
        )

    def _patch_edge_matches(self, edge: dict, raw: dict, client_ids: dict[str, int]) -> bool:
        checks = {
            "source": "source_id",
            "target": "target_id",
            "source_port": "source_port",
            "target_port": "target_port",
            "kind": "kind",
        }
        matched_any = False
        for raw_key, edge_key in checks.items():
            if raw_key not in raw:
                continue
            matched_any = True
            raw_value = raw.get(raw_key)
            if raw_key in {"source", "target"}:
                raw_value = self._resolve_patch_node_ref(raw_value, client_ids)
                if int(edge[edge_key]) != raw_value:
                    return False
            elif str(edge[edge_key]) != str(raw_value or ""):
                return False
        if not matched_any:
            raise ValueError("delete_edge requires at least one selector.")
        return True

    @staticmethod
    def _resolve_patch_node_ref(value: object, client_ids: dict[str, int]) -> int:
        if isinstance(value, str) and value in client_ids:
            return client_ids[value]
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid node reference: {value!r}.") from None

    @staticmethod
    def _next_patch_x(nodes: list[dict]) -> float:
        if not nodes:
            return -280.0
        return max(float(node.get("x", 0.0)) for node in nodes) + 340.0

    @staticmethod
    def _next_patch_y(nodes: list[dict]) -> float:
        return -90.0 + (len(nodes) % 6) * 145.0

    @staticmethod
    def _place_unpositioned_patch_nodes(nodes: list[dict], edges: list[dict], node_ids: set[int]):
        if not node_ids:
            return
        node_by_id = {int(node["id"]): node for node in nodes if "id" in node}
        incoming: dict[int, list[int]] = {node_id: [] for node_id in node_by_id}
        outgoing: dict[int, list[int]] = {node_id: [] for node_id in node_by_id}
        for edge in edges:
            try:
                source_id = int(edge["source_id"])
                target_id = int(edge["target_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if source_id in node_by_id and target_id in node_by_id:
                outgoing.setdefault(source_id, []).append(target_id)
                incoming.setdefault(target_id, []).append(source_id)

        x_gap = _GraphNode.WIDTH + 150
        y_gap = _GraphNode.HEIGHT + 54
        placed: set[int] = set()
        pending = set(node_ids)
        while pending:
            progressed = False
            for node_id in sorted(pending):
                node = node_by_id.get(node_id)
                if node is None:
                    pending.remove(node_id)
                    progressed = True
                    continue
                parents = [source_id for source_id in incoming.get(node_id, []) if source_id in node_by_id]
                children = [target_id for target_id in outgoing.get(node_id, []) if target_id in node_by_id]
                anchored_parents = [source_id for source_id in parents if source_id not in pending]
                anchored_children = [target_id for target_id in children if target_id not in pending]
                if anchored_parents:
                    parent_id = anchored_parents[0]
                    parent = node_by_id[parent_id]
                    siblings = [
                        child_id
                        for child_id in outgoing.get(parent_id, [])
                        if child_id in node_ids and child_id in node_by_id
                    ]
                    offset = AgentCanvasPanel._sibling_offset(node_id, siblings)
                    node["x"] = float(parent.get("x", 0.0)) + x_gap
                    node["y"] = float(parent.get("y", 0.0)) + offset * y_gap
                elif anchored_children:
                    child_id = anchored_children[0]
                    child = node_by_id[child_id]
                    siblings = [
                        source_id
                        for source_id in incoming.get(child_id, [])
                        if source_id in node_ids and source_id in node_by_id
                    ]
                    offset = AgentCanvasPanel._sibling_offset(node_id, siblings)
                    node["x"] = float(child.get("x", 0.0)) - x_gap
                    node["y"] = float(child.get("y", 0.0)) + offset * y_gap
                else:
                    continue
                pending.remove(node_id)
                placed.add(node_id)
                progressed = True
            if progressed:
                continue
            anchor = next(iter(placed), None)
            if anchor is None:
                existing = [node_id for node_id in node_by_id if node_id not in pending]
                anchor = existing[0] if existing else None
            base = node_by_id.get(anchor) if anchor is not None else None
            base_x = float(base.get("x", -280.0)) if base is not None else -280.0
            base_y = float(base.get("y", -90.0)) if base is not None else -90.0
            for idx, node_id in enumerate(sorted(pending)):
                node = node_by_id[node_id]
                node["x"] = base_x + x_gap
                node["y"] = base_y + (idx - (len(pending) - 1) / 2.0) * y_gap
            pending.clear()

    @staticmethod
    def _sibling_offset(node_id: int, siblings: list[int]) -> float:
        ordered = sorted(dict.fromkeys(siblings))
        if node_id not in ordered:
            return 0.0
        return ordered.index(node_id) - (len(ordered) - 1) / 2.0

    @staticmethod
    def _payload_cycle_summaries(nodes: list[dict], edges: list[dict]) -> list[str]:
        node_by_id = {int(node["id"]): node for node in nodes if "id" in node}
        adjacency: dict[int, list[int]] = {node_id: [] for node_id in node_by_id}
        for edge in edges:
            source_id = int(edge["source_id"])
            target_id = int(edge["target_id"])
            if source_id in node_by_id and target_id in node_by_id:
                adjacency.setdefault(source_id, []).append(target_id)

        visiting: set[int] = set()
        visited: set[int] = set()
        stack: list[int] = []

        def visit(node_id: int) -> list[int] | None:
            visiting.add(node_id)
            stack.append(node_id)
            for target_id in adjacency.get(node_id, []):
                if target_id in visiting:
                    start = stack.index(target_id)
                    return stack[start:] + [target_id]
                if target_id not in visited:
                    cycle = visit(target_id)
                    if cycle:
                        return cycle
            stack.pop()
            visiting.remove(node_id)
            visited.add(node_id)
            return None

        for node_id in sorted(node_by_id):
            if node_id in visited:
                continue
            cycle = visit(node_id)
            if cycle:
                names = [str(node_by_id[item].get("title") or item) for item in cycle if item in node_by_id]
                if len(names) > 5:
                    names = names[:5] + ["..."]
                return [" -> ".join(names)]
        return []

    def restore_graph_state(self, state: dict | None, *, reset_undo: bool = True) -> str:
        if not state:
            return ""
        self._restoring_graph = True
        warning = ""
        reconciled_run_state = False
        try:
            reconciled_run_state = self._restore_graph_state_strict(state)
        except (TypeError, ValueError) as exc:
            warning = str(exc) or "Saved canvas is not compatible with this version."
            self._clear_graph()
            self._seed_graph()
        finally:
            self._restoring_graph = False
            self._sync_counts()
            self._refresh_edges()
            self._sync_attention_state(force=True)
        if reset_undo:
            self._reset_undo_history()
            if reconciled_run_state and not warning:
                self.graph_changed.emit()
        return warning

    def reset_graph(self):
        self._clear_graph()
        self._seed_graph()
        self._notify_graph_changed()

    def record_file_activity(self, path: str, activity: str = "changed") -> _GraphNode:
        ref = self._relative_ref(path)
        scope = self._find_scope_node(ref)
        if scope is None:
            scope = self._create_node(
                CanvasToken("scope", self._scope_title(ref), ref),
                self._next_activity_point(),
            )
        operation = self._active_operation_node()
        if operation is None:
            operation = self._create_node(
                CanvasToken("operation", "Current Work", "Agent-maintained activity"),
                scope.pos() + QPointF(-280, 0),
            )
        self._set_active_node(operation, "running", f"working on {ref}")
        scope.set_status("changed", ref)
        self._select_node(scope)
        self._set_mode(f"agent touched: {ref}")
        self._notify_graph_changed()
        return scope

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("canvasHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)

        title = QLabel("Intent Graph")
        title.setObjectName("canvasTitle")
        self._goal = QLabel("Start with a goal. Drag out to split, assign crew, or create work.")
        self._goal.setObjectName("canvasGoal")
        self._provider_combo = QComboBox()
        self._provider_combo.setObjectName("canvasProviderCombo")
        self._provider_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._provider_combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._provider_combo.setToolTip("Provider used by the canvas graph agent.")
        self._provider_combo.currentTextChanged.connect(self._on_graph_provider_changed)
        self._model_combo = QComboBox()
        self._model_combo.setObjectName("canvasModelCombo")
        self._model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._model_combo.setMinimumContentsLength(10)
        self._model_combo.setMaximumWidth(220)
        self._model_combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._model_combo.setToolTip("Model used by the canvas graph agent.")
        self._model_combo.currentTextChanged.connect(self._on_graph_model_changed)
        self._add_goal_btn = QPushButton("New Goal")
        self._add_goal_btn.clicked.connect(self._add_goal)
        self._autoformat_btn = QPushButton("Autoformat")
        self._autoformat_btn.clicked.connect(self._autoformat_graph)
        self._fit_btn = QPushButton("Fit")
        self._fit_btn.clicked.connect(self._fit_graph)
        self._cycle_warning = QLabel("")
        self._cycle_warning.setObjectName("canvasCycleWarning")
        self._cycle_warning.setVisible(False)

        layout.addWidget(title)
        layout.addWidget(self._goal, 1)
        layout.addWidget(self._cycle_warning)
        layout.addWidget(self._provider_combo)
        layout.addWidget(self._model_combo)
        for button in (
            self._add_goal_btn,
            self._autoformat_btn,
            self._fit_btn,
        ):
            layout.addWidget(button)
        self.refresh_models()
        return header

    def _build_graph_chat(self) -> QFrame:
        chat = QFrame()
        chat.setObjectName("canvasGraphChat")
        layout = QVBoxLayout(chat)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self._graph_chat_title = QLabel("Graph Agent")
        self._graph_chat_title.setObjectName("canvasGraphChatTitle")
        self._graph_chat_meta = QLabel("Query and change the graph")
        self._graph_chat_meta.setObjectName("canvasGraphChatMeta")
        self._graph_chat_meta.setToolTip(self._graph_agent_prompt())
        self._graph_chat_clear_btn = QPushButton("Clear")
        self._graph_chat_clear_btn.setObjectName("canvasGraphChatClear")
        self._graph_chat_clear_btn.clicked.connect(self._clear_graph_chat)
        self._run_accept_btn = QPushButton("Accept")
        self._run_accept_btn.setObjectName("canvasRunAccept")
        self._run_accept_btn.clicked.connect(self._accept_selected_run_node)
        self._run_rerun_btn = QPushButton("Retry")
        self._run_rerun_btn.setObjectName("canvasRunRerun")
        self._run_rerun_btn.clicked.connect(self._rerun_selected_run_node)
        self._run_guidance_btn = QPushButton("Needs changes")
        self._run_guidance_btn.setObjectName("canvasRunGuidance")
        self._run_guidance_btn.clicked.connect(self._add_guidance_to_selected_run_node)
        self._run_extend_btn = QPushButton("Extend")
        self._run_extend_btn.setObjectName("canvasRunExtend")
        self._run_extend_btn.clicked.connect(self._extend_graph_from_selected_review)
        header.addWidget(self._graph_chat_title)
        header.addWidget(self._graph_chat_meta, 1)
        for button in (
            self._run_accept_btn,
            self._run_rerun_btn,
            self._run_guidance_btn,
            self._run_extend_btn,
        ):
            button.setVisible(False)
            header.addWidget(button)
        header.addWidget(self._graph_chat_clear_btn)
        layout.addLayout(header)

        self._graph_chat_transcript = _GraphTranscript()
        self._graph_chat_transcript.setObjectName("canvasGraphChatTranscript")
        self._graph_chat_transcript.setReadOnly(True)
        self._graph_chat_transcript.anchorClicked.connect(self._on_graph_chat_anchor_clicked)
        self._graph_chat_transcript.userScrollChanged.connect(self._on_graph_chat_user_scroll)
        self._graph_chat_transcript.verticalScrollBar().valueChanged.connect(self._on_graph_chat_scroll_value_changed)
        self._graph_chat_transcript.verticalScrollBar().rangeChanged.connect(self._on_graph_chat_scroll_range_changed)
        self._graph_chat_transcript.setToolTip("Transcript for the graph agent.")
        self._graph_chat_bottom_btn = QPushButton("↓")
        self._graph_chat_bottom_btn.setObjectName("canvasGraphChatBottom")
        self._graph_chat_bottom_btn.setFixedSize(30, 30)
        self._graph_chat_bottom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._graph_chat_bottom_btn.setToolTip("Jump to latest canvas chat output.")
        self._graph_chat_bottom_btn.hide()
        self._graph_chat_bottom_btn.clicked.connect(self._resume_graph_chat_auto_scroll)
        self._graph_chat_host = _TranscriptHost(self._graph_chat_transcript, self._graph_chat_bottom_btn)
        layout.addWidget(self._graph_chat_host)

        self._graph_chat_input_row = QWidget()
        row = QHBoxLayout(self._graph_chat_input_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._graph_chat_input = QLineEdit()
        self._graph_chat_input.setObjectName("canvasGraphChatInput")
        self._graph_chat_input.setPlaceholderText("Ask or instruct the graph agent...")
        self._graph_chat_input.returnPressed.connect(self._send_graph_chat)
        self._graph_chat_send_btn = QPushButton("Send")
        self._graph_chat_send_btn.setObjectName("canvasGraphChatSend")
        self._graph_chat_send_btn.clicked.connect(self._on_graph_chat_action)
        row.addWidget(self._graph_chat_input, 1)
        row.addWidget(self._graph_chat_send_btn)
        layout.addWidget(self._graph_chat_input_row)
        self._refresh_graph_chat_controls()
        return chat

    def _build_inspector(self) -> QFrame:
        inspector = AgentCanvasInspector(
            self._repo_root,
            apply_requested=self._apply_inspector_edits,
            cancel_requested=self._cancel_inspector_edits,
            generate_steps_requested=self._generate_steps_for_selected_goal,
            add_scope_path_requested=self._add_scope_path_from_field,
            open_scope_requested=self._open_selected_scope,
            frame_color_requested=self._choose_frame_color,
            parent=self,
        )
        self._selected = inspector.selected
        self._inspector_lines = inspector.lines
        self._edit_title = inspector.edit_title
        self._agent_label = inspector.agent_label
        self._agent_combo = inspector.agent_combo
        self._detail_label = inspector.detail_label
        self._edit_detail = inspector.edit_detail
        self._frame_color_label = inspector.frame_color_label
        self._frame_color_field = inspector.frame_color_field
        self._frame_color_btn = inspector.frame_color_button
        self._scope_path_label = inspector.scope_path_label
        self._scope_path_field = inspector.scope_path_field
        self._generate_steps_btn = inspector.generate_steps_btn
        self._open_scope_btn = inspector.open_scope_btn
        self._cancel_edit_btn = inspector.cancel_edit_btn
        self._apply_edit_btn = inspector.apply_edit_btn
        self._connect_inspector_auto_apply()
        return inspector

    def _connect_inspector_auto_apply(self):
        self._edit_title.textChanged.connect(self._schedule_inspector_auto_apply)
        self._edit_detail.textChanged.connect(self._schedule_inspector_auto_apply)
        self._frame_color_field.textChanged.connect(self._schedule_inspector_auto_apply)
        self._agent_combo.currentIndexChanged.connect(self._schedule_inspector_auto_apply)

    def _send_graph_chat(self):
        text = self._graph_chat_input.text().strip()
        if not text:
            return
        if self._is_graph_agent_running():
            self._set_mode("graph agent is already running")
            self._sync_graph_agent_controls()
            return
        context = self._selected_graph_chat_context()
        self._append_graph_chat_message("You", self._graph_chat_visible_user_text(text, context))
        if self._start_graph_agent(
            self._graph_chat_agent_prompt(text, context),
            scope_goal_id=self._graph_chat_context_scope_goal_id(context),
        ):
            self._graph_chat_input.clear()
        self._notify_graph_changed()

    def _on_graph_chat_action(self):
        if self._is_graph_agent_running():
            self._stop_graph_agent_run()
            return
        self._send_graph_chat()

    def _append_graph_chat_message(self, role: str, text: str) -> int:
        self._graph_chat_messages.append({"role": role, "text": text})
        self._render_graph_chat()
        return len(self._graph_chat_messages) - 1

    def _render_graph_chat(self):
        if not hasattr(self, "_graph_chat_transcript"):
            return
        node = self._selected_run_history_node()
        if node is not None:
            self._render_run_history(node)
            return
        blocks: list[str] = []
        for message in self._graph_chat_messages[-8:]:
            role = str(message.get("role") or "Message").strip() or "Message"
            text = str(message.get("text") or "").strip()
            if text:
                if role.casefold() in {"tool", "tools"}:
                    blocks.append(self._graph_tools_message_html(text))
                else:
                    blocks.append(self._graph_chat_message_html(role, text))
        blocks.extend(self._expanded_graph_tool_sections_html())
        self._graph_chat_title.setText("Graph Agent")
        context = self._selected_graph_chat_context()
        if context is not None:
            title = self._graph_chat_context_title(context)
            spec = component_spec(context.token.kind)
            self._graph_chat_meta.setText(f"Context: [{title}]")
            self._graph_chat_meta.setToolTip(
                f"Next message is anchored to selected {spec.title.lower()} #{context.node_id}. "
                "Ask to expand, rewrite, connect, or explain this component."
            )
        else:
            self._graph_chat_meta.setText("Query and change the graph")
            self._graph_chat_meta.setToolTip(self._graph_agent_prompt())
        self._set_graph_chat_html(
            "<html><body style='margin:0; padding:0;'>"
            + "".join(blocks)
            + "</body></html>"
        )
        self._refresh_graph_chat_controls()

    def _selected_run_history_node(self) -> _GraphNode | None:
        node = self._selected_node()
        if (
            node is not None
            and node.token.kind in {"operation", "dod"}
            and (self._node_run_history.get(node.node_id) or node.status in {"running", "paused", "review", "done", "blocked"})
        ):
            return node
        return None

    def _render_run_history(self, node: _GraphNode):
        history = self._node_run_history.get(node.node_id, [])
        self._graph_chat_title.setText("Run History")
        role = node.agent_name or ("Architect" if node.token.kind == "dod" else "Coder")
        self._graph_chat_meta.setText(f"{node.token.title} - {role} - {self._status_label(node)}")
        self._graph_chat_meta.setToolTip("Execution transcript for the selected graph node.")
        blocks = []
        if not history:
            blocks.append(self._graph_chat_message_html("Status", "No run history yet. Start from a goal to run this branch."))
        else:
            for attempt in history[-6:]:
                blocks.append(self._run_attempt_html(attempt, expanded_tools=self._expanded_run_tools))
        self._set_graph_chat_html(
            "<html><body style='margin:0; padding:0;'>"
            + "".join(blocks)
            + "</body></html>"
        )
        self._refresh_graph_chat_controls()

    def _set_graph_chat_html(self, html: str, *, force_bottom: bool = False):
        if not hasattr(self, "_graph_chat_transcript"):
            return
        html = self._graph_chat_document_with_link_style(html)
        bar = self._graph_chat_transcript.verticalScrollBar()
        should_pin = force_bottom or self._graph_chat_auto_scroll or self._graph_chat_is_at_bottom()
        previous = bar.value()
        self._graph_chat_programmatic_scroll = True
        self._graph_chat_transcript.setHtml(html)
        if should_pin:
            bar.setValue(bar.maximum())
            self._graph_chat_auto_scroll = True
            self._graph_chat_bottom_btn.hide()
            QTimer.singleShot(0, self._scroll_graph_chat_to_bottom)
        else:
            bar.setValue(min(previous, bar.maximum()))
            self._graph_chat_bottom_btn.setVisible(bar.maximum() > 0)
            self._graph_chat_bottom_btn.raise_()
        self._graph_chat_programmatic_scroll = False

    @staticmethod
    def _graph_chat_document_with_link_style(html: str) -> str:
        style = (
            "<style>"
            "a { color:#58a6ff; text-decoration:underline; }"
            "a:hover { color:#9fd9ff; text-decoration:underline; }"
            "</style>"
        )
        text = str(html or "")
        if "<head>" in text:
            return text.replace("<head>", f"<head>{style}", 1)
        if "<html>" in text:
            return text.replace("<html>", f"<html><head>{style}</head>", 1)
        return f"<html><head>{style}</head><body>{text}</body></html>"

    def _graph_chat_is_at_bottom(self, threshold: int = 32) -> bool:
        if not hasattr(self, "_graph_chat_transcript"):
            return True
        bar = self._graph_chat_transcript.verticalScrollBar()
        return bar.maximum() - bar.value() <= threshold

    def _on_graph_chat_scroll_value_changed(self, _value: int):
        if self._graph_chat_programmatic_scroll:
            return
        self._sync_graph_chat_scroll_state()

    def _on_graph_chat_scroll_range_changed(self, _minimum: int, _maximum: int):
        if self._graph_chat_auto_scroll:
            self._scroll_graph_chat_to_bottom()

    def _on_graph_chat_user_scroll(self):
        self._sync_graph_chat_scroll_state()

    def _sync_graph_chat_scroll_state(self):
        if not hasattr(self, "_graph_chat_bottom_btn"):
            return
        if self._graph_chat_is_at_bottom():
            self._graph_chat_auto_scroll = True
            self._graph_chat_bottom_btn.hide()
        else:
            self._graph_chat_auto_scroll = False
            self._graph_chat_bottom_btn.setVisible(self._graph_chat_transcript.verticalScrollBar().maximum() > 0)
            self._graph_chat_bottom_btn.raise_()

    def _resume_graph_chat_auto_scroll(self):
        self._graph_chat_auto_scroll = True
        self._graph_chat_bottom_btn.hide()
        self._scroll_graph_chat_to_bottom()
        QTimer.singleShot(0, self._scroll_graph_chat_to_bottom)
        QTimer.singleShot(50, self._scroll_graph_chat_to_bottom)

    def _scroll_graph_chat_to_bottom(self):
        if not hasattr(self, "_graph_chat_transcript"):
            return
        if not self._graph_chat_auto_scroll:
            return
        bar = self._graph_chat_transcript.verticalScrollBar()
        self._graph_chat_programmatic_scroll = True
        bar.setValue(bar.maximum())
        self._graph_chat_programmatic_scroll = False

    @staticmethod
    def _run_attempt_html(attempt: dict, *, expanded_tools: set[tuple[str, int]] | None = None) -> str:
        role = str(attempt.get("role") or "Agent").strip() or "Agent"
        status = str(attempt.get("status") or "running").strip() or "running"
        started = str(attempt.get("started_at") or "").strip()
        title = f"{role} - {status}"
        if started:
            title = f"{title} - {started}"
        label_color, text_color, border_color, background = AgentCanvasPanel._graph_chat_role_colors(role)
        prompt = str(attempt.get("prompt") or "").strip()
        content = str(attempt.get("content") or "").strip()
        if content and status == "running":
            rendered = f"<p style='white-space:pre-wrap;'>{escape(content)}</p>"
        elif content:
            rendered = assistant_markdown_html(content)
        else:
            rendered = "<p>No output yet.</p>"
        meta = ""
        if prompt:
            meta = AgentCanvasPanel._run_prompt_html(prompt, text_color)
        artifact_ref = str(attempt.get("artifact_ref") or "").strip()
        if artifact_ref:
            artifact_title = str(attempt.get("artifact_title") or "Run artifact").strip() or "Run artifact"
            safe_artifact = escape(artifact_ref)
            safe_artifact_title = escape(artifact_title)
            meta += (
                f"<div style='margin:0 0 8px 0; color:{text_color}; opacity:.9;'>"
                f"<span style='font-weight:700;'>Artifact:</span> "
                f"<code title='{safe_artifact_title}'>{safe_artifact}</code>"
                "</div>"
            )
        tools = AgentCanvasPanel._run_tools_html(
            attempt.get("tools") or [],
            attempt_id=str(attempt.get("id") or ""),
            expanded_tools=expanded_tools or set(),
        )
        return (
            "<div style='"
            "margin:0 0 6px 0; padding:6px 8px; border-radius:6px; "
            f"border-left:3px solid {border_color}; background:{background}; color:{text_color};"
            "'>"
            f"<div style='font-weight:700; color:{label_color}; margin-bottom:5px;'>{escape(title)}</div>"
            f"{meta}"
            f"{tools}"
            f"<div style='color:{text_color};'>{rendered}</div>"
            "</div>"
        )

    @staticmethod
    def _run_prompt_html(prompt: str, text_color: str) -> str:
        prompt = str(prompt or "").strip()
        if not prompt:
            return ""
        sections = AgentCanvasPanel._structured_run_prompt_sections(prompt)
        if not sections:
            safe_prompt = escape(prompt).replace("\n", "<br>")
            return (
                f"<div style='margin:0 0 8px 0; color:{text_color}; opacity:.78;'>"
                f"<span style='font-weight:700;'>Prompt:</span><br>{safe_prompt}"
                "</div>"
            )
        rows = []
        for label, body in sections:
            safe_label = escape(label)
            safe_body = escape(body.strip() or "(empty)").replace("\n", "<br>")
            rows.append(
                "<div style='margin:5px 0 0 0;'>"
                "<span style='display:inline-block; min-width:86px; margin-right:6px; "
                "padding:1px 5px; border-radius:4px; background:#162433; color:#9fd9ff; "
                f"font-weight:700;'>{safe_label}</span>"
                f"<span style='color:{text_color};'>{safe_body}</span>"
                "</div>"
            )
        return (
            "<div style='margin:0 0 8px 0; padding:6px 7px; border-radius:6px; "
            "background:#0d151d; border:1px solid #233142;'>"
            "<div style='font-size:10px; font-weight:700; color:#8fa3bb; margin-bottom:3px;'>Run prompt</div>"
            + "".join(rows)
            + "</div>"
        )

    @staticmethod
    def _structured_run_prompt_sections(prompt: str) -> list[tuple[str, str]]:
        headers = {
            "Operation",
            "Description",
            "Crew",
            "Inputs",
            "Direct inputs only",
            "Expected downstream consumers",
            "DoD",
            "Criteria",
            "Accepted upstream results",
            "Accepted upstream step titles",
        }
        sections: list[tuple[str, str]] = []
        current_label = ""
        current_lines: list[str] = []
        intro_lines: list[str] = []
        for raw_line in str(prompt or "").splitlines():
            match = re.match(r"^([A-Za-z][A-Za-z ]{1,40}):\s*(.*)$", raw_line)
            if match and match.group(1) in headers:
                if current_label:
                    sections.append((current_label, "\n".join(current_lines).strip()))
                elif intro_lines:
                    sections.append(("Task", "\n".join(intro_lines).strip()))
                current_label = match.group(1)
                current_lines = [match.group(2)] if match.group(2) else []
                intro_lines = []
            elif current_label:
                current_lines.append(raw_line)
            else:
                intro_lines.append(raw_line)
        if current_label:
            sections.append((current_label, "\n".join(current_lines).strip()))
        elif intro_lines:
            text = "\n".join(intro_lines).strip()
            if text.startswith(("Run this graph operation.", "Retry this graph operation", "Review whether this Definition of Done")):
                sections.append(("Task", text))
        return [(label, body) for label, body in sections if label and body]

    @staticmethod
    def _run_tools_html(
        tools: object,
        *,
        attempt_id: str = "",
        expanded_tools: set[tuple[str, int]] | None = None,
    ) -> str:
        if not isinstance(tools, list):
            return ""
        expanded_tools = expanded_tools or set()
        visible_tools = AgentCanvasPanel._compact_run_tools(tools)[-12:]
        rows = [
            AgentCanvasPanel._run_tool_html(
                tool,
                attempt_id=attempt_id,
                row_index=index,
                expanded=(attempt_id, index) in expanded_tools,
            )
            for index, tool in enumerate(visible_tools)
            if isinstance(tool, dict)
        ]
        rows = [row for row in rows if row]
        if not rows:
            return ""
        return (
            "<div style='margin:0 0 8px 0; padding:5px 6px; border-radius:6px; "
            "background:#0f141b; border:1px solid #263241;'>"
            "<div style='font-size:10px; font-weight:700; color:#8fa3bb; margin-bottom:4px;'>Tool activity</div>"
            + "".join(rows)
            + "</div>"
        )

    @staticmethod
    def _run_tool_html(tool: dict, *, attempt_id: str = "", row_index: int = 0, expanded: bool = False) -> str:
        name = str(tool.get("name") or "tool").strip() or "tool"
        status = str(tool.get("status") or "called").strip().lower() or "called"
        summary = AgentCanvasPanel._compact_run_text(str(tool.get("summary") or "").strip(), 120)
        count = int(tool.get("count") or 1)
        status_color, background = AgentCanvasPanel._run_tool_status_colors(status)
        safe_name = escape(name)
        safe_status = escape(status)
        safe_summary = escape(summary)
        count_html = (
            f"<span style='color:#8fa3bb; margin-left:6px;'>x{count}</span>"
            if count > 1
            else ""
        )
        summary_html = (
            f"<span style='color:#9aa9ba; margin-left:6px;'>{safe_summary}</span>"
            if safe_summary
            else ""
        )
        has_detail = bool(str(tool.get("inputs") or "").strip() or str(tool.get("output") or "").strip())
        toggle = ""
        if has_detail and attempt_id:
            symbol = "▾" if expanded else "▸"
            href = escape(f"run-tool:{attempt_id}:{row_index}")
            toggle = (
                f"<a href='{href}' style='float:right; color:#8fa3bb; text-decoration:none; "
                f"font-weight:700; padding:0 4px;'>{symbol}</a>"
            )
        return (
            "<div style='margin:3px 0; padding:4px 6px; border-radius:5px; "
            f"background:{background}; color:#d7dee8;'>"
            f"{toggle}"
            f"<span style='font-family:Consolas, monospace; color:#d7dee8;'>{safe_name}</span>"
            f"<span style='font-weight:700; color:{status_color}; margin-left:8px;'>{safe_status}</span>"
            f"{count_html}"
            f"{summary_html}"
            f"{AgentCanvasPanel._run_tool_detail_html(tool) if expanded else ''}"
            "</div>"
        )

    @staticmethod
    def _run_tool_detail_html(tool: dict) -> str:
        blocks = []
        inputs = str(tool.get("inputs") or "").strip()
        output = str(tool.get("output") or "").strip()
        if inputs:
            blocks.append(("Inputs", inputs))
        if output:
            blocks.append(("Output", output))
        if not blocks:
            return ""
        html = []
        for label, value in blocks:
            safe_label = escape(label)
            safe_value = escape(value).replace("\n", "<br>")
            html.append(
                "<div style='margin-top:5px; padding:5px 6px; border-radius:5px; "
                "background:#080c11; border:1px solid #263241;'>"
                f"<div style='font-size:10px; font-weight:700; color:#8fa3bb; margin-bottom:3px;'>{safe_label}</div>"
                f"<code style='white-space:pre-wrap; color:#d7dee8;'>{safe_value}</code>"
                "</div>"
            )
        return "".join(html)

    @staticmethod
    def _compact_run_tools(tools: list) -> list[dict]:
        compact: list[dict] = []
        for raw in tools:
            if not isinstance(raw, dict):
                continue
            tool = {
                "name": str(raw.get("name") or "tool"),
                "status": str(raw.get("status") or "called"),
                "summary": str(raw.get("summary") or ""),
                "inputs": str(raw.get("inputs") or ""),
                "output": str(raw.get("output") or ""),
            }
            if compact and all(compact[-1].get(key) == tool[key] for key in ("name", "status", "summary", "inputs", "output")):
                compact[-1]["count"] = int(compact[-1].get("count") or 1) + 1
            else:
                tool["count"] = 1
                compact.append(tool)
        return compact

    @staticmethod
    def _run_tool_status_colors(status: str) -> tuple[str, str]:
        normalized = str(status or "").strip().lower()
        if normalized in {"failed", "error", "blocked"}:
            return "#ff8a8a", "#251316"
        if normalized in {"running", "called"}:
            return "#f6c744", "#211c10"
        return "#76d48b", "#102018"

    @staticmethod
    def _compact_run_text(text: str, limit: int = 600) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 1)] + "..."

    @staticmethod
    def _compact_multiline_text(text: str, limit: int = 600) -> str:
        compact = str(text or "").strip()
        if len(compact) <= limit:
            return compact
        head_limit = max(0, int(limit * 0.82))
        tail_limit = max(0, limit - head_limit - 80)
        head = compact[:head_limit].rstrip()
        tail = compact[-tail_limit:].lstrip() if tail_limit else ""
        omitted = len(compact) - len(head) - len(tail)
        if tail:
            return f"{head}\n\n... omitted {omitted} chars from summarized DoD review context ...\n\n{tail}"
        return head + "\n\n... summarized DoD review context truncated ..."

    def _selected_graph_chat_context(self) -> _GraphNode | None:
        node = self._selected_node()
        if node is None and self._last_selected_node_id is not None:
            node = self._nodes.get(self._last_selected_node_id)
        if node is None or (node.token.kind in {"operation", "dod"} and self._selected_run_history_node() is not None):
            return None
        return node

    def _graph_chat_context_title(self, node: _GraphNode) -> str:
        return self._compact_run_text(node.token.title or component_spec(node.token.kind).title, 54)

    def _graph_chat_visible_user_text(self, text: str, context: _GraphNode | None) -> str:
        if context is None:
            return text
        return f"[{self._graph_chat_context_title(context)}] {text}"

    def _graph_chat_context_scope_goal_id(self, context: _GraphNode | None) -> int | None:
        if context is None:
            return None
        if context.token.kind == "goal":
            return context.node_id
        return self._owning_goal_id(context.node_id) or self._nearest_upstream_goal_id(context.node_id)

    def _nearest_upstream_goal_id(self, node_id: int) -> int | None:
        seen = {node_id}
        queue = [node_id]
        while queue:
            current_id = queue.pop(0)
            incoming = sorted(
                (edge.source_id for edge in self._edges if edge.target_id == current_id),
                key=self._node_sort_key,
            )
            for source_id in incoming:
                if source_id in seen:
                    continue
                seen.add(source_id)
                source = self._nodes.get(source_id)
                if source is None:
                    continue
                if source.token.kind == "goal":
                    return source.node_id
                queue.append(source_id)
        return None

    def _graph_chat_agent_prompt(self, text: str, context: _GraphNode | None) -> str:
        text = str(text or "").strip()
        if context is None:
            return text
        detail = self._compact_run_text(context.token.detail or "", 1600) or "(empty)"
        scope_goal_id = self._graph_chat_context_scope_goal_id(context)
        scope_line = f"- owning_goal_id: {scope_goal_id}" if scope_goal_id is not None else "- owning_goal_id: none"
        return (
            f"{text}\n\n"
            "Selected component context:\n"
            f"- id: {context.node_id}\n"
            f"- kind: {context.token.kind}\n"
            f"- title: {context.token.title}\n"
            f"{scope_line}\n"
            "- description:\n"
            f"{detail}\n\n"
            "Interpret this request as anchored to the selected component. "
            "If the user asks to generate, expand, rewrite, or improve, update the graph around this component and its owning goal scope. "
            "Do not regenerate unrelated goals or mutate other disconnected graphs. "
            "Call read_graph first, then use graph patches only when a change is needed."
        )

    def _refresh_graph_chat_controls(self):
        if not hasattr(self, "_graph_chat_send_btn"):
            return
        node = self._selected_run_history_node()
        history_mode = node is not None
        running = self._is_graph_agent_running()
        stopping = running and self._graph_agent_stop_requested
        has_messages = bool(self._graph_chat_messages)
        self._graph_chat_input_row.setVisible(not history_mode)
        self._graph_chat_clear_btn.setVisible(not history_mode)
        self._graph_chat_send_btn.setEnabled((not running or not stopping) and not history_mode)
        self._graph_chat_send_btn.setText("Stopping" if stopping else ("Stop" if running else "Send"))
        self._graph_chat_input.setEnabled(not running and not history_mode)
        self._graph_chat_clear_btn.setEnabled(has_messages and not running and not history_mode)
        self._refresh_run_history_controls(node)
        context = self._selected_graph_chat_context() if not history_mode else None
        if running:
            placeholder = "Graph agent is running..."
        elif context is not None:
            placeholder = f"Ask about [{self._graph_chat_context_title(context)}]..."
        else:
            placeholder = "Ask or instruct the graph agent..."
        self._graph_chat_input.setPlaceholderText(placeholder)

        if running:
            clear_tip = "Wait for the graph agent to finish before clearing the chat."
            send_tip = "Stopping the graph agent..." if stopping else "Stop the running graph agent."
            input_tip = "Graph agent is already running. Wait for this run to finish before sending another message."
        else:
            clear_tip = "Clear the canvas chat history." if has_messages else "No canvas chat messages to clear yet."
            send_tip = "Send this message to the graph agent."
            if context is not None:
                input_tip = (
                    f"Ask the graph agent about [{self._graph_chat_context_title(context)}]. "
                    "Use this for expand, rewrite, explain, or connect. Tools: "
                    + ", ".join(GRAPH_AGENT_TOOLS)
                )
            else:
                input_tip = "Ask or instruct the graph agent. Tools: " + ", ".join(GRAPH_AGENT_TOOLS)
        self._graph_chat_clear_btn.setToolTip(clear_tip)
        self._graph_chat_send_btn.setToolTip(send_tip)
        self._graph_chat_input.setToolTip(input_tip)

    def _refresh_run_history_controls(self, node: _GraphNode | None):
        buttons = (
            self._run_accept_btn,
            self._run_rerun_btn,
            self._run_guidance_btn,
            self._run_extend_btn,
        )
        for button in buttons:
            button.setVisible(False)
            button.setEnabled(False)
        if node is None:
            return
        busy = self._is_run_agent_running()
        is_dod = node.token.kind == "dod"
        latest_attempt = self._latest_run_attempt_status(node.node_id)
        attempt_count = len(self._node_run_history.get(node.node_id, []))
        node_label = node.token.title or "selected node"
        can_review = node.status == "review"
        node_thread = self._run_threads.get(node.node_id)
        node_busy = bool(node_thread is not None and node_thread.isRunning())
        can_accept_review = can_review and not node_busy and not self._is_graph_agent_running()
        can_rerun = node.token.kind in {"operation", "dod"} and node.status in {"review", "blocked", "done", "idle"}
        retrying_failure = can_rerun and self._latest_run_attempt_status(node.node_id) == "error"
        attempt_context = f"Attempt {attempt_count}" + (f" (last='{latest_attempt}')" if latest_attempt else "")
        self._run_accept_btn.setVisible(can_review)
        self._run_accept_btn.setEnabled(can_accept_review)
        self._run_accept_btn.setText("Approve DoD" if is_dod else "Approve result")
        if is_dod:
            self._run_accept_btn.setToolTip(
                f"{attempt_context}: Approve DoD '{node_label}', save acceptance evidence, and mark this goal scope as passing acceptance review."
            )
        else:
            self._run_accept_btn.setToolTip(
                f"{attempt_context}: Approve result for '{node_label}' and unblock downstream nodes."
            )
        self._run_rerun_btn.setVisible(can_rerun)
        self._run_rerun_btn.setEnabled(can_rerun and not busy)
        if is_dod:
            self._run_rerun_btn.setText("Re-evaluate DoD")
            if retrying_failure:
                self._run_rerun_btn.setToolTip(
                    f"{attempt_context}: Retry DoD review for '{node_label}' using compact context after a failed attempt."
                )
            elif latest_attempt == "blocked":
                self._run_rerun_btn.setToolTip(
                    f"{attempt_context}: Re-run DoD review for '{node_label}' after adding guidance or extending this branch."
                )
            else:
                self._run_rerun_btn.setToolTip(
                    f"{attempt_context}: Re-run DoD review for '{node_label}' against current evidence and status."
                )
        else:
            retrying_step = self._latest_run_attempt_status(node.node_id) == "error"
            self._run_rerun_btn.setText("Retry step" if retrying_step else "Rerun step")
            self._run_rerun_btn.setToolTip(
                f"{attempt_context}: Retry '{node_label}' with compact context because the previous attempt failed."
                if retrying_step
                else f"{attempt_context}: Run this operation again and append a new attempt."
            )
        can_add_guidance = node.status in {"review", "blocked"}
        self._run_guidance_btn.setVisible(can_add_guidance)
        self._run_guidance_btn.setEnabled(can_add_guidance and not busy and not self._is_graph_agent_running())
        self._run_guidance_btn.setText("Needs changes")
        if is_dod:
            if node.status == "review":
                self._run_guidance_btn.setToolTip(
                    f"{attempt_context}: Mark '{node_label}' as blocked, then add a decision update (what changed, what proof is required, or what changed the acceptance."
                )
            else:
                self._run_guidance_btn.setToolTip(
                    f"{attempt_context}: Add another guidance decision for '{node_label}' and keep the branch ready for a retry."
                )
        else:
            if node.status == "review":
                self._run_guidance_btn.setToolTip(
                    f"{attempt_context}: Mark '{node_label}' as blocked and add concrete context so the next run has enough guidance."
                )
            else:
                self._run_guidance_btn.setToolTip(
                    f"{attempt_context}: Add concrete context for '{node_label}' and keep this operation ready to retry."
                )
        can_extend = node.token.kind == "dod" and node.status in {"review", "blocked"}
        self._run_extend_btn.setVisible(can_extend)
        self._run_extend_btn.setEnabled(can_extend and not busy and not self._is_graph_agent_running())
        self._run_extend_btn.setText("Extend from review")
        self._run_extend_btn.setToolTip(
            f"{attempt_context}: Ask the graph agent to append follow-up work for '{node_label}' from this DoD review."
        )

    def _on_graph_chat_anchor_clicked(self, url: QUrl):
        text = url.toString()
        if text.startswith("graph-tool:"):
            name = text.split(":", 1)[1]
            if name == self._expanded_graph_tool:
                self._expanded_graph_tool = None
            else:
                self._expanded_graph_tool = name
            self._render_graph_chat()
            return
        if not text.startswith("run-tool:"):
            return
        _, attempt_id, row_text = text.split(":", 2)
        try:
            row_index = int(row_text)
        except ValueError:
            return
        key = (attempt_id, row_index)
        if key in self._expanded_run_tools:
            self._expanded_run_tools.remove(key)
        else:
            self._expanded_run_tools.add(key)
        self._render_graph_chat()

    def _graph_tools_message_html(self, text: str) -> str:
        role = "Tools"
        text = str(text or "").strip()
        label_color, text_color, border_color, background = AgentCanvasPanel._graph_chat_role_colors(role)
        safe_role = escape(role)
        safe_text = escape(text).replace("\n", "<br>")
        label_to_tool = {self._graph_tool_label(name): name for name in GRAPH_AGENT_TOOLS}

        def replace_summary(match: re.Match[str]) -> str:
            label = match.group("label")
            name = label_to_tool.get(label)
            if not name:
                return match.group(0)
            safe_href = escape(f"graph-tool:{name}")
            safe_label = escape(match.group(0))
            return (
                f"<a href='{safe_href}' style='font-weight:700;'>{safe_label}</a>"
            )

        labels = "|".join(re.escape(label) for label in label_to_tool)
        if labels:
            safe_text = re.sub(
                rf"\b(?P<label>{labels})\s+\d+/\d+(?:,\s+\d+\s+failed)?",
                replace_summary,
                safe_text,
            )
        return (
            "<div style='"
            "margin:0 0 6px 0; padding:6px 8px; border-radius:6px; "
            f"border-left:3px solid {border_color}; background:{background}; color:{text_color};"
            "'>"
            f"<span style='font-weight:700; color:{label_color};'>{safe_role}:</span> "
            f"<span>{safe_text}</span>"
            "</div>"
        )

    def _expanded_graph_tool_sections_html(self) -> list[str]:
        sections = []
        for name in GRAPH_AGENT_TOOLS:
            if name == self._expanded_graph_tool:
                sections.append(self._graph_tool_activity_html(name))
        return [section for section in sections if section]

    def _graph_tool_activity_html(self, name: str) -> str:
        name = str(name or "tool")
        label = self._graph_tool_label(name)
        events = [event for event in self._graph_tool_events.get(name, []) if isinstance(event, dict)]
        rows = []
        if name == "propose_graph_patch" and self._graph_check_failures:
            rows.append(self._graph_tool_activity_row_html("failed", self._graph_check_failures_text(), ""))
        for event in events[-20:]:
            if name == "propose_graph_patch" and self._graph_check_failures and str(event.get("status") or "").lower() == "failed":
                continue
            rows.append(
                self._graph_tool_activity_row_html(
                    str(event.get("status") or "done"),
                    str(event.get("summary") or ""),
                    str(event.get("detail") or ""),
                )
            )
        if not rows:
            rows.append(self._graph_tool_activity_row_html("idle", "No activity recorded for this tool.", ""))
        label_color, text_color, border_color, background = AgentCanvasPanel._graph_chat_role_colors(
            "Check Failures" if name == "propose_graph_patch" else "Tools"
        )
        safe_label = escape(label)
        return (
            "<div style='"
            "margin:4px 0 10px 14px; padding:8px 9px; border-radius:7px; "
            f"border:1px solid {border_color}; border-left:4px solid {border_color}; "
            f"background:#0b1118; color:{text_color};"
            "'>"
            "<div style='display:block; margin:-2px 0 7px 0; padding-bottom:5px; "
            "border-bottom:1px solid #263241;'>"
            "<span style='font-size:10px; text-transform:uppercase; letter-spacing:.6px; "
            "color:#8fa3bb; font-weight:700;'>Expanded tool</span>"
            f"<span style='font-weight:700; color:{label_color}; margin-left:8px;'>{safe_label} activity</span>"
            "</div>"
            + "".join(rows)
            + "</div>"
        )

    @staticmethod
    def _graph_tool_activity_row_html(status: str, summary: str, detail: str) -> str:
        status = str(status or "done").strip().lower() or "done"
        summary = str(summary or "").strip()
        detail = str(detail or "").strip()
        status_color, background = AgentCanvasPanel._run_tool_status_colors(status)
        safe_status = escape(status)
        safe_summary = escape(summary).replace("\n", "<br>")
        safe_detail = escape(detail).replace("\n", "<br>")
        detail_html = (
            f"<div style='margin-top:3px; color:#9aa9ba; white-space:pre-wrap;'>{safe_detail}</div>"
            if safe_detail
            else ""
        )
        return (
            "<div style='margin:3px 0; padding:4px 6px; border-radius:5px; "
            f"background:{background}; color:#d7dee8;'>"
            f"<span style='font-weight:700; color:{status_color};'>{safe_status}:</span> "
            f"<span style='margin-left:8px;'>{safe_summary}</span>"
            f"{detail_html}"
            "</div>"
        )

    @staticmethod
    def _graph_chat_message_html(role: str, text: str) -> str:
        role = str(role or "Message").strip() or "Message"
        text = str(text or "").strip()
        label_color, text_color, border_color, background = AgentCanvasPanel._graph_chat_role_colors(role)
        safe_role = escape(role)
        safe_text = escape(text).replace("\n", "<br>")
        return (
            "<div style='"
            "margin:0 0 6px 0; padding:6px 8px; border-radius:6px; "
            f"border-left:3px solid {border_color}; background:{background}; color:{text_color};"
            "'>"
            f"<span style='font-weight:700; color:{label_color};'>{safe_role}:</span> "
            f"<span>{safe_text}</span>"
            "</div>"
        )

    @staticmethod
    def _graph_chat_role_colors(role: str) -> tuple[str, str, str, str]:
        p = palette()
        normalized = str(role or "").strip().lower()
        if normalized in {"tool", "tools"}:
            return ("#f6c744", p["TEXT"], "#a86f00", "#18150c")
        if normalized in {"check failures", "checks"}:
            return ("#ffb86b", p["TEXT"], "#b45309", "#211409")
        if normalized in {"system", "status"}:
            return ("#a7b1c2", p["TEXT_DIM"], "#4b5563", "#14161b")
        if normalized in {"error", "graph agent error"}:
            return ("#ff8a8a", p["TEXT"], "#b23b3b", "#211111")
        if normalized in {"you", "user"}:
            return ("#8ab4ff", p["TEXT"], "#315fbd", "#101826")
        if normalized in {"graph agent", "agent"}:
            return ("#67e8f9", p["TEXT"], "#1f9aaa", "#0e1b20")
        return (p["TEXT_DIM"], p["TEXT"], p["BORDER"], p["BG2"])

    def _clear_graph_chat(self):
        if self._is_graph_agent_running():
            self._set_mode("graph agent is running")
            self._sync_graph_agent_controls()
            return
        if not self._graph_chat_messages:
            return
        self._reset_graph_chat_output()
        self._set_mode("cleared canvas chat")
        self._notify_graph_changed()

    def _reset_graph_chat_output(self):
        self._graph_chat_messages = []
        self._graph_agent_stream_index = None
        self._graph_agent_stream_text = ""
        self._stop_graph_agent_thinking()
        self._reset_graph_tool_status()
        self._render_graph_chat()

    def _start_graph_agent(
        self,
        prompt: str,
        *,
        scope_goal_id: int | None = None,
        generation_mode: bool = False,
    ):
        if self._graph_agent_thread is not None and self._graph_agent_thread.isRunning():
            self._set_mode("graph agent is already running")
            self._sync_graph_agent_controls()
            return False
        self._graph_agent_scope_goal_id = scope_goal_id
        self._graph_agent_generation_mode = generation_mode
        self._graph_agent_applied_patches = 0
        self._graph_agent_stop_requested = False
        self._graph_agent_stream_text = ""
        self._reset_graph_tool_status()
        self._graph_agent_stream_index = self._append_graph_chat_message(
            "Graph Agent",
            self._graph_agent_thinking_text(),
        )
        self._start_graph_agent_thinking()
        tools = self._graph_tool_schemas()

        if self._graph_agent_runner is not None:
            try:
                result = self._graph_agent_runner(prompt, tools, self._execute_graph_tool)
            except Exception as exc:
                result = f"Graph agent failed: {exc}"
            finally:
                finish_scope_goal_id = self._graph_agent_scope_goal_id
            try:
                self._finish_graph_agent_response(str(result or "Done."), scope_goal_id=finish_scope_goal_id)
            finally:
                self._graph_agent_scope_goal_id = None
                self._graph_agent_generation_mode = False
                self._graph_agent_generation_goal_id = None
            return True

        model = self._graph_agent_model()
        if not model:
            self._stop_graph_agent_thinking()
            self._set_graph_agent_stream_text("No model is configured for the Graph Agent.")
            self._finish_goal_generation_status(error=True)
            self._graph_agent_scope_goal_id = None
            self._graph_agent_generation_mode = False
            self._graph_agent_generation_goal_id = None
            self._notify_graph_changed()
            return True

        history = [{"role": "user", "content": self._graph_agent_user_prompt(prompt)}]
        canvas_tool_names = self._canvas_extension_tool_names()
        thread = ChatThread(
            model,
            history,
            self._graph_agent_system_prompt,
            self._repo_root,
            allowed_tools=list(GRAPH_AGENT_TOOLS) + canvas_tool_names,
            enable_crew_tool=False,
            crew_settings=self._settings.load(),
            configured_providers=set(configured_provider_ids(self._settings.load())),
            extra_tools=tools,
            extra_tool_executor=self._execute_graph_tool_threadsafe,
            tool_surface="canvas",
        )
        self._graph_agent_thread = thread
        thread.chunk.connect(self._on_graph_agent_chunk)
        thread.tool_called.connect(self._on_graph_agent_tool_called)
        thread.tool_result.connect(self._on_graph_agent_tool_result)
        thread.done.connect(self._on_graph_agent_done)
        thread.error.connect(self._on_graph_agent_error)
        thread.finished.connect(lambda t=thread: self._on_graph_agent_finished(t))
        thread.start()
        self._sync_run_controls()
        self._sync_graph_agent_controls()
        return True

    def _is_graph_agent_running(self) -> bool:
        return self._graph_agent_thread is not None and self._graph_agent_thread.isRunning()

    def _stop_graph_agent_run(self):
        thread = self._graph_agent_thread
        if thread is None or not thread.isRunning():
            self._sync_graph_agent_controls()
            return
        self._graph_agent_stop_requested = True
        thread.cancel()
        self._stop_graph_agent_thinking()
        self._set_mode("stopping graph agent")
        self._sync_graph_agent_controls()

    def _sync_graph_agent_controls(self):
        self._refresh_graph_chat_controls()
        running = self._is_graph_agent_running()
        if hasattr(self, "_generate_steps_btn"):
            selected = self._selected_node()
            existing_steps = self._outgoing_operation_nodes(selected.node_id) if selected is not None and selected.token.kind == "goal" else []
            if (
                running
                and selected is not None
                and selected.node_id == self._graph_agent_generation_goal_id
            ):
                self._generate_steps_btn.setText("Thinking...")
            elif selected is not None and selected.token.kind == "goal":
                self._generate_steps_btn.setText("Show Steps" if existing_steps else "Generate Steps")
            self._generate_steps_btn.setEnabled(
                not running and selected is not None and selected.token.kind == "goal"
            )

    def _graph_agent_system_prompt(self) -> str:
        prompt = (
            self._graph_agent_prompt()
            + "\n\nYou have these graph tools: read_graph, web_fetch, propose_graph_patch, apply_graph_patch, ask_user. Explicit canvas extension tools may also be available.\n"
            + "Always call read_graph before proposing or applying changes.\n"
            + "Use web_fetch only for graph-planning research: external product/domain context, public docs, examples, or constraints that help shape goals, context, decisions, evidence, or DoD. Do not use web_fetch to implement, inspect local code, verify completed work, or cite proof that a run is done.\n"
            + "Graph tools default to the selected goal scope. If a goal or owned node is selected, do not edit outside that scope; connect every new node into that scoped graph.\n"
            + "Treat the selected/source goal as the anchor for this run: do not delete it and do not add new incoming connections into it. Expand outward from it. Other subgoals may have inputs when they are not the source goal.\n"
            + "For graph changes, call propose_graph_patch first, then apply_graph_patch if valid.\n"
            + "In Generate Steps mode, a successful answer must apply a graph patch. Writing JSON, a patch plan, or 'let me apply it' in assistant text does not change the graph. After ask_user returns an answer, continue in the same turn to propose_graph_patch and apply_graph_patch unless another user answer is truly required. If you cannot apply a graph patch, say BLOCKED: followed by the reason.\n"
            + self._generation_strategy_instructions()
            + "\n"
            + "When a patch creates nodes, each add_node needs a client_id, a meaningful non-empty detail, and every new node must be connected in that same patch. Reuse the client_id in connect/source/target; do not reference undeclared names.\n"
            + "Node details must follow read_graph.node_kinds[kind].detail_contract. Files/scope detail is repo paths only, one per line. Context detail is synthesized durable facts/constraints/options with implications, not raw comma-separated answer labels. Decision detail is an output contract or accepted choice; do not use decision for unresolved options/background.\n"
            + "Use ask_user only when a design/product ambiguity would change the graph shape: user-facing behavior, product intent, UX priority, acceptance criteria, constraints, risk tolerance, business tradeoff, or responsible crew. Ask one focused question per ask_user call, with clear choices when possible. Set multi_select=true only when several choices can be valid together, such as needed features or constraints. Use single-choice for direction, ownership, priority, or tradeoff decisions. You may call ask_user multiple times when each answer can change a different graph decision.\n"
            + "During Generate Steps, do not ask the user to choose implementation details such as engines, frameworks, libraries, file paths, or technical approaches. Represent those as architecture/research work for the crew unless the user explicitly made that technical choice the goal. If an operation must produce a chosen result, connect operation.decision -> decision and write the decision node detail as the decision contract.\n"
            + "Use read_graph's component_playbook, generation_checklist, and generation_patch_patterns. Prefer a graph-native branch over a task list: operation means a runnable work action by selected crew; scope, evidence, decision, DoD, and context are structural components.\n"
            + "For every operation add_node during Generate Steps, include agent_id and agent_name from read_graph.available_crew. If omitted, the tool will autocorrect an obvious default, but explicit crew is better. Use coder/Coder for implementation by default, scout/Scout for read-only research, architect/Architect for design/architecture/decomposition, and archivist/Archivist for durable memory or summary work. Do not create ownership-only nodes. Ask_user about crew only if ownership changes graph shape or accountability.\n"
            + "This graph mode is for mega-feature decomposition. If the plan would just be one chat prompt or Analyze -> Implement -> Verify, keep the graph minimal or ask what larger breakdown the user wants.\n"
            + "Do not overcomplicate the graph. Use the smallest graph that makes the work easier to understand and run.\n"
            + "Follow the selected generation strategy, but do not create fake structure. Use a straight flow for sequential work when it has real graph value; branch when the strategy and real workflow independence justify it. Avoid plain action lists with no graph signal.\n"
            + "When generated operations depend on previous planning, design, research, spec, or architecture work, encode that dependency with a concrete connect operation from the planning action to the implementation action: {\"op\":\"connect\",\"source\":\"design\",\"target\":\"implement\",\"source_port\":\"implement\"}. A downstream run only receives direct graph inputs; it will not see sibling outputs just because both nodes connect from the goal.\n"
            + "Break down by distinct responsibilities and real decision output contracts, not generic phases. Prefer product/UX, architecture, implementation surface, state/persistence, integration, validation, review, and rollout only when those boundaries are real.\n"
            + "When proposing 3 or more new work actions, first identify the graph signal that makes this better than one chat: consumed context/scope, decision, evidence/proof, DoD, branch/fan-in, or meaningful crew handoff. Do not add fake structural nodes just to avoid a straight line.\n"
            + "When asked to generate steps, plan the graph for how agents should use context, implement actions, produce accepted decisions, produce proof, and close against DoD. Do not research the code, inspect files, implement changes, or verify results during step generation.\n"
            + "Create a meaningful runnable graph branch whose action nodes are future actions, not completed findings. Keep it compact: prefer 3-5 new nodes total, never more than 6 unless the user explicitly asks for depth.\n"
            + "Reuse existing nodes where possible. Do not create duplicate context, scope, evidence, decisions, or DoD. Add or reuse DoD only when the branch needs an explicit terminal acceptance contract; evidence and decisions should feed DoD.\n"
            + "Use exact source_port values from read_graph.connection_rules. evidence.context -> operation is invalid; use evidence.feedback -> operation for proof-driven follow-up or context.context -> operation for durable context. Never connect implementation back into an upstream design/spec/context/evidence node it consumed; create separate downstream evidence/proof instead.\n"
            + "Avoid a fixed generic Plan/Implement/Verify chain unless that is truly sufficient.\n"
            + "Keep the final answer short and describe what changed."
        )
        return self._with_canvas_extension_context(
            prompt,
            model=self._graph_agent_model(),
            kind="graph",
        )

    def _canvas_extension_tool_names(self) -> list[str]:
        tools, _errors = extension_canvas_tools(self._repo_root)
        return sorted({tool.name for tool in tools if tool.source != "builtin"})

    def _with_canvas_extension_context(
        self,
        system: str,
        *,
        model: str = "",
        kind: str = "graph",
        node: _GraphNode | None = None,
    ) -> str:
        clean = [
            (entry["name"], entry["text"])
            for entry in self._canvas_extension_context_entries(
                model=model,
                kind=kind,
                node=node,
                scope_goal_id=self._graph_agent_scope_goal_id,
            )
        ]
        if not clean:
            return system
        parts = ["## Canvas Extension Context"]
        for name, text in clean:
            heading = name or "Extension"
            parts.append(f"### {heading}\n{text}")
        return system + "\n\n" + "\n\n".join(parts)

    def _canvas_extension_context_entries(
        self,
        *,
        model: str = "",
        kind: str = "graph",
        node: _GraphNode | None = None,
        graph: dict | None = None,
        scope_goal_id: int | None = None,
    ) -> list[dict[str, str]]:
        snippets, _errors = extension_canvas_context_snippets(
            self._repo_root,
            model=model,
            canvas=self._canvas_extension_payload(
                kind=kind,
                node=node,
                graph=graph,
                scope_goal_id=scope_goal_id,
            ),
        )
        return [
            {"name": str(name).strip(), "text": str(text).strip()}
            for name, text in snippets
            if str(text).strip()
        ]

    def _canvas_extension_payload(
        self,
        *,
        kind: str,
        node: _GraphNode | None = None,
        graph: dict | None = None,
        scope_goal_id: int | None = None,
    ) -> dict:
        payload = {
            "surface": "canvas",
            "kind": str(kind or "graph"),
            "graph": graph if graph is not None else self.graph_state(),
            "scope_goal_id": scope_goal_id if scope_goal_id is not None else self._graph_agent_scope_goal_id,
            "active_node_id": self._active_node_id,
        }
        if node is not None:
            payload["node_id"] = node.node_id
            payload["node"] = {
                "id": node.node_id,
                "kind": node.token.kind,
                "title": node.token.title,
                "detail": node.token.detail,
                "agent_id": node.agent_id,
                "agent_name": node.agent_name,
                "status": node.status,
            }
        return payload

    def _graph_agent_user_prompt(self, prompt: str) -> str:
        return (
            str(prompt or "").strip()
            + "\n\nCurrent graph tool contract is available from read_graph. Use graph patches for any change. If the graph scope is goal-scoped, treat nodes outside that scope as off-limits."
        )

    def refresh_models(self):
        if not hasattr(self, "_provider_combo"):
            return
        provider = self._provider_combo.currentText()
        model = self._model_combo.currentText()
        providers = self._configured_graph_providers()

        self._provider_combo.blockSignals(True)
        self._provider_combo.clear()
        self._provider_combo.addItems(providers)
        self._provider_combo.blockSignals(False)

        if provider in providers:
            self._provider_combo.setCurrentText(provider)
        elif providers:
            self._provider_combo.setCurrentText(providers[0])
        self._on_graph_provider_changed(self._provider_combo.currentText())
        if model and model in MODELS.get(self._provider_combo.currentText(), []):
            self._model_combo.setCurrentText(model)

    def _configured_graph_providers(self) -> list[str]:
        data = self._settings.load()
        providers = list(configured_provider_ids(data))
        return providers or list(MODELS)

    def _on_graph_provider_changed(self, provider: str):
        provider = str(provider or "")
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(MODELS.get(provider, []))
        self._model_combo.blockSignals(False)
        self._apply_graph_default_model(provider)

    def _on_graph_model_changed(self, model: str):
        model = str(model or "").strip()
        provider = self._provider_combo.currentText() if hasattr(self, "_provider_combo") else ""
        if not provider or not model:
            return
        data = self._settings.load()
        defaults = data.get("default_models", {}) if isinstance(data, dict) else {}
        defaults = dict(defaults) if isinstance(defaults, dict) else {}
        if defaults.get(provider) == model:
            return
        defaults[provider] = model
        if hasattr(self._settings, "update"):
            self._settings.update({"default_models": defaults})

    def _apply_graph_default_model(self, provider: str):
        data = self._settings.load()
        defaults = data.get("default_models", {}) if isinstance(data, dict) else {}
        model = defaults.get(provider) if isinstance(defaults, dict) else ""
        if model and model in MODELS.get(provider, []):
            self._model_combo.blockSignals(True)
            self._model_combo.setCurrentText(model)
            self._model_combo.blockSignals(False)

    def _graph_agent_model(self) -> str:
        if hasattr(self, "_model_combo"):
            model = self._model_combo.currentText()
            provider = self._provider_combo.currentText()
            if model and model in MODELS.get(provider, []):
                return model
        data = self._settings.load()
        defaults = data.get("default_models", {}) if isinstance(data, dict) else {}
        providers = self._configured_graph_providers()
        for provider in providers:
            models = MODELS.get(provider, [])
            if not models:
                continue
            default = defaults.get(provider) if isinstance(defaults, dict) else ""
            if default in models:
                return default
            return models[0]
        for models in MODELS.values():
            if models:
                return models[0]
        return ""

    def _set_graph_agent_stream_text(self, text: str):
        if self._graph_agent_stream_index is None:
            self._graph_agent_stream_index = self._append_graph_chat_message("Graph Agent", text)
            return
        if 0 <= self._graph_agent_stream_index < len(self._graph_chat_messages):
            self._graph_chat_messages[self._graph_agent_stream_index]["text"] = text
            self._render_graph_chat()

    def _graph_agent_thinking_text(self) -> str:
        dots = "." * (self._graph_agent_thinking_step + 1)
        return f"Thinking{dots}"

    def _start_graph_agent_thinking(self):
        self._graph_agent_thinking_step = 2
        self._set_graph_agent_stream_text(self._graph_agent_thinking_text())
        self._graph_agent_thinking_timer.start()

    def _stop_graph_agent_thinking(self):
        self._graph_agent_thinking_timer.stop()
        self._graph_agent_thinking_step = 0

    def _advance_graph_agent_thinking(self):
        if self._graph_agent_stream_index is None or self._graph_agent_stream_text:
            self._stop_graph_agent_thinking()
            return
        self._graph_agent_thinking_step = (self._graph_agent_thinking_step + 1) % 3
        self._set_graph_agent_stream_text(self._graph_agent_thinking_text())

    def _on_graph_agent_chunk(self, text: str):
        chunk = str(text or "")
        if chunk:
            self._stop_graph_agent_thinking()
        self._graph_agent_stream_text += chunk
        self._set_graph_agent_stream_text(self._graph_agent_stream_text or self._graph_agent_thinking_text())

    def _on_graph_agent_tool_called(self, name: str, inputs: dict):
        self._set_mode(f"graph agent using {name}")
        self._record_graph_tool_call(name, inputs)

    def _on_graph_agent_tool_result(self, name: str, output: str):
        if str(output or "").startswith("[tool error]"):
            self._record_graph_tool_result(name, output, failed=True)
            return
        self._set_mode(f"graph agent finished {name}")
        self._record_graph_tool_result(name, output)

    def _on_graph_agent_done(self, text: str):
        final = "Stopped by user." if self._graph_agent_stop_requested else str(text or "").strip()
        self._finish_graph_agent_response(
            final or self._graph_agent_stream_text or "Done.",
            scope_goal_id=self._graph_agent_scope_goal_id,
        )

    def _finish_graph_agent_response(self, text: str, *, scope_goal_id: int | None = None):
        self._stop_graph_agent_thinking()
        final = self._graph_agent_final_text(str(text or "Done."))
        self._set_graph_agent_stream_text(final)
        self._graph_agent_stream_index = None
        self._graph_agent_stream_text = ""
        self._finish_goal_generation_status(error=False)
        if not self._graph_agent_generation_mode or self._graph_agent_applied_patches:
            self._autoformat_graph(scope_goal_id=scope_goal_id)
        self._notify_graph_changed()

    def _graph_agent_final_text(self, text: str) -> str:
        final = str(text or "Done.").strip() or "Done."
        if (
            self._graph_agent_generation_mode
            and not self._graph_agent_stop_requested
            and self._graph_agent_applied_patches <= 0
        ):
            patch_detected = self._graph_patch_text_detected(final)
            notice = (
                "No graph changes were applied. Generate Steps is incomplete: the graph agent must call "
                "propose_graph_patch and apply_graph_patch after planning."
            )
            if patch_detected:
                notice += (
                    " A JSON patch was detected in the assistant message, but chat text alone does not mutate "
                    "the canvas."
                )
            self._set_mode("generation ended without graph changes")
            if notice not in final:
                final = f"{final}\n\n{notice}"
            return final
        return final

    @staticmethod
    def _graph_patch_text_detected(text: str) -> bool:
        raw = str(text or "")
        if not raw:
            return False
        lower = raw.casefold()
        if "operations" in lower and "propose_graph_patch" in lower:
            return True
        decoder = json.JSONDecoder()
        for candidate in [raw]:
            candidate = candidate.strip()
            if not candidate:
                continue
            start = 0
            while True:
                idx = candidate.find("{", start)
                if idx < 0:
                    break
                try:
                    parsed, _ = decoder.raw_decode(candidate[idx:])
                except ValueError:
                    start = idx + 1
                    continue
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("operations"), list):
                        return True
                    patch = parsed.get("patch")
                    if isinstance(patch, dict) and isinstance(patch.get("operations"), list):
                        return True
                start = idx + 1
        for match in re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.IGNORECASE):
            try:
                payload = json.loads(match.group(1))
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if isinstance(payload.get("operations"), list) or (
                isinstance(payload.get("patch"), dict)
                and isinstance(payload["patch"].get("operations"), list)
            ):
                return True
        return False

    def _finish_goal_generation_status(self, *, error: bool):
        goal = self._nodes.get(self._graph_agent_generation_goal_id or -1)
        if goal is None or goal.token.kind != "goal" or goal.status != "thinking":
            return
        if self._graph_agent_stop_requested:
            goal.set_status("idle", "generation stopped")
        elif error or self._graph_agent_applied_patches <= 0:
            goal.set_status("blocked", "generation incomplete")
        else:
            goal.set_status("planned", "steps generated")
        self._refresh_edges()
        self._sync_counts()
        if self._selected_node() is goal:
            self._populate_inspector(goal)

    def _on_graph_agent_error(self, message: str):
        self._stop_graph_agent_thinking()
        if self._graph_agent_stop_requested:
            self._set_graph_agent_stream_text("Stopped by user.")
        else:
            self._set_graph_agent_stream_text(f"Graph agent error: {message}")
        self._graph_agent_stream_index = None
        self._graph_agent_stream_text = ""
        self._finish_goal_generation_status(error=True)
        self._notify_graph_changed()

    def _on_graph_agent_finished(self, thread: ChatThread):
        if self._graph_agent_thread is thread:
            self._stop_graph_agent_thinking()
            self._graph_agent_thread = None
            self._graph_agent_scope_goal_id = None
            self._graph_agent_generation_mode = False
            self._graph_agent_generation_goal_id = None
            self._graph_agent_stop_requested = False
            self._sync_run_controls()
            self._sync_graph_agent_controls()

    def _reset_graph_tool_status(self):
        self._graph_tool_status_index = None
        self._graph_tool_stats = {}
        self._graph_tool_events = {}
        self._expanded_graph_tool = None
        self._graph_tool_last = ""
        self._graph_check_failures = []

    def _record_graph_tool_call(self, name: str, inputs: dict):
        name = str(name or "tool")
        stats = self._graph_tool_stats.setdefault(name, {"calls": 0, "done": 0, "failed": 0})
        stats["calls"] += 1
        self._graph_tool_last = self._graph_tool_call_notice(name, inputs)
        self._append_graph_tool_event(name, "called", self._graph_tool_last, self._graph_tool_inputs_summary(inputs))
        self._set_graph_tool_status_text(self._graph_tool_status_text(running=name))

    def _record_graph_tool_result(self, name: str, output: str, *, failed: bool = False):
        name = str(name or "tool")
        stats = self._graph_tool_stats.setdefault(name, {"calls": 0, "done": 0, "failed": 0})
        payload = self._graph_tool_payload(output)
        payload_failed = (
            isinstance(payload, dict)
            and payload.get("ok") is False
            and name in {"web_fetch", "propose_graph_patch", "apply_graph_patch"}
        )
        if failed or payload_failed:
            stats["failed"] += 1
            self._graph_tool_last = (
                self._graph_tool_result_notice(name, output)
                if payload_failed
                else f"{name} failed: {output}"
            )
            status = "failed"
        else:
            stats["done"] += 1
            self._graph_tool_last = self._graph_tool_result_notice(name, output)
            status = "done"
        if name == "propose_graph_patch" and isinstance(payload, dict) and payload.get("ok") is not True:
            self._record_graph_check_failure(payload)
        self._append_graph_tool_event(name, status, self._graph_tool_last, self._graph_tool_output_summary(name, output, payload))
        self._set_graph_tool_status_text(self._graph_tool_status_text())

    def _append_graph_tool_event(self, name: str, status: str, summary: str, detail: str):
        name = str(name or "tool")
        events = self._graph_tool_events.setdefault(name, [])
        events.append(
            {
                "status": str(status or "done")[:20],
                "summary": self._compact_run_text(summary, 180),
                "detail": self._compact_run_text(detail, 500),
            }
        )
        self._graph_tool_events[name] = events[-80:]

    def _graph_tool_inputs_summary(self, inputs: dict) -> str:
        if not isinstance(inputs, dict) or not inputs:
            return ""
        try:
            return json.dumps(inputs, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(inputs)

    def _graph_tool_output_summary(self, name: str, output: str, payload: dict | None) -> str:
        if not isinstance(payload, dict):
            return self._compact_run_text(str(output or ""), 500)
        if name in {"propose_graph_patch", "apply_graph_patch"} and payload.get("ok") is not True:
            error = str(payload.get("error") or payload.get("detail") or "").strip()
            return self._compact_run_text(error, 500)
        if name == "web_fetch" and payload.get("ok") is not True:
            return self._compact_run_text(str(payload.get("error") or ""), 500)
        if name == "ask_user":
            answer = str(payload.get("answer") or "").strip()
            return f"answer: {answer}" if answer else ""
        return ""

    def _record_graph_check_failure(self, payload: dict):
        summary = str(payload.get("summary") or "").strip() or "invalid patch"
        detail = self._graph_patch_failure_notice(payload)
        error = str(payload.get("error") or payload.get("detail") or "").strip()
        self._graph_check_failures.append(
            {
                "summary": summary[:120],
                "detail": self._compact_run_text(detail, 260),
                "error": self._compact_run_text(error, 500),
            }
        )
        self._graph_check_failures = self._graph_check_failures[-80:]

    def _graph_check_failures_text(self) -> str:
        failures = [failure for failure in self._graph_check_failures if isinstance(failure, dict)]
        if not failures:
            return ""
        grouped: dict[str, dict[str, object]] = {}
        order: list[str] = []
        for failure in failures:
            summary = str(failure.get("summary") or "invalid patch").strip() or "invalid patch"
            detail = str(failure.get("detail") or failure.get("error") or "").strip()
            if summary not in grouped:
                grouped[summary] = {"count": 0, "detail": detail}
                order.append(summary)
            grouped[summary]["count"] = int(grouped[summary].get("count") or 0) + 1
            if detail:
                grouped[summary]["detail"] = detail
        ordered = sorted(order, key=lambda item: (-int(grouped[item].get("count") or 0), order.index(item)))
        total = len(failures)
        lines = [f"{total} check failure{'s' if total != 1 else ''} stored during this graph-agent run:"]
        for summary in ordered[:8]:
            entry = grouped[summary]
            count = int(entry.get("count") or 0)
            detail = self._compact_run_text(str(entry.get("detail") or ""), 140)
            if detail:
                summary_prefix = f"{summary}:"
                if detail.casefold().startswith(summary_prefix.casefold()):
                    detail = detail[len(summary_prefix):].strip()
            suffix = f" x{count}" if count > 1 else ""
            line = f"- {summary}{suffix}"
            if detail and detail.casefold() != summary.casefold():
                line += f": {detail}"
            lines.append(line)
        if len(ordered) > 8:
            lines.append(f"- ... {len(ordered) - 8} more failure type(s)")
        return "\n".join(lines)

    def _set_graph_tool_status_text(self, text: str):
        if self._graph_tool_status_index is None:
            self._graph_tool_status_index = self._append_graph_chat_message("Tools", text)
            return
        if 0 <= self._graph_tool_status_index < len(self._graph_chat_messages):
            self._graph_chat_messages[self._graph_tool_status_index]["text"] = text
            self._render_graph_chat()
            return
        self._graph_tool_status_index = self._append_graph_chat_message("Tool", text)

    def _graph_tool_status_text(self, *, running: str = "") -> str:
        parts = []
        for name in GRAPH_AGENT_TOOLS:
            stats = self._graph_tool_stats.get(name)
            if not stats:
                continue
            calls = int(stats.get("calls") or 0)
            done = int(stats.get("done") or 0)
            failed = int(stats.get("failed") or 0)
            label = self._graph_tool_label(name)
            if failed:
                label += f" {done}/{calls}, {failed} failed"
            elif done < calls:
                label += f" {done}/{calls}"
            else:
                label += f" {done}/{calls}"
            parts.append(label)
        prefix = "  ".join(parts) if parts else "waiting"
        if running:
            return f"{prefix} | Running {self._graph_tool_label(running)}"
        if self._graph_tool_last:
            return f"{prefix} | {self._graph_tool_last}"
        return prefix

    @staticmethod
    def _graph_tool_label(name: str) -> str:
        return {
            "read_graph": "Read",
            "web_fetch": "Fetch",
            "propose_graph_patch": "Check",
            "apply_graph_patch": "Apply",
            "ask_user": "Ask",
        }.get(str(name or "tool"), "Tool")

    def _graph_tool_call_notice(self, name: str, inputs: dict) -> str:
        name = str(name or "tool")
        inputs = inputs if isinstance(inputs, dict) else {}
        if name == "read_graph":
            return "Reading graph"
        if name == "web_fetch":
            url = str(inputs.get("url") or "").strip()
            return f"Fetching {url[:72]}" if url else "Fetching URL"
        patch = self._graph_patch_from_tool_inputs(inputs)
        operations = patch.get("operations") if isinstance(patch, dict) else None
        count = len(operations) if isinstance(operations, list) else 0
        if name == "propose_graph_patch":
            return f"Checking {count} ops"
        if name == "apply_graph_patch":
            return f"Applying {count} ops"
        if name == "ask_user":
            return "Asking user"
        return f"Running {self._graph_tool_label(name)}"

    def _graph_tool_result_notice(self, name: str, output: str) -> str:
        payload = self._graph_tool_payload(output)
        if payload is None:
            return f"{name} finished"
        if name == "read_graph":
            graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else {}
            nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
            edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
            cycles = payload.get("cycles") if isinstance(payload.get("cycles"), list) else []
            suffix = f", {len(cycles)} cycle warnings" if cycles else ""
            return f"Read {len(nodes)} nodes, {len(edges)} links{suffix}"
        if name == "web_fetch":
            if payload.get("ok") is True:
                chars = int(payload.get("chars") or 0)
                title = str(payload.get("title") or payload.get("url") or "page").strip()
                return f"Fetched {title[:32]} ({chars} chars)"
            error = str(payload.get("error") or "fetch failed").strip()
            return f"Fetch failed: {error[:48]}"
        if name == "propose_graph_patch":
            if payload.get("ok") is True:
                patch = payload.get("patch") if isinstance(payload.get("patch"), dict) else {}
                operations = patch.get("operations") if isinstance(patch.get("operations"), list) else []
                return f"Checked {len(operations)} ops"
            summary = str(payload.get("summary") or "invalid patch").strip() or "invalid patch"
            return f"Check failed: {summary}"
        if name == "apply_graph_patch":
            if payload.get("ok") is True:
                return f"Applied {payload.get('applied_operations', 0)} ops"
            summary = str(payload.get("summary") or "invalid patch").strip() or "invalid patch"
            return f"Apply failed: {summary}"
        if name == "ask_user":
            if payload.get("cancelled"):
                return "Question cancelled"
            answer = str(payload.get("answer") or "").strip()
            if not answer:
                return "Question answered"
            return f"Answered: {answer[:32]}"
        return f"{self._graph_tool_label(name)} done"

    @staticmethod
    def _graph_patch_failure_notice(payload: dict) -> str:
        summary = str(payload.get("summary") or "").strip()
        error = str(payload.get("error") or "").strip()
        detail = AgentCanvasPanel._graph_patch_error_detail(error, summary)
        if not summary:
            summary = "invalid patch"
        if detail and detail.casefold() != summary.casefold():
            return f"{summary}: {detail}"
        return summary

    @staticmethod
    def _graph_tool_payload(output: str) -> dict | None:
        if isinstance(output, dict):
            return output
        try:
            parsed = json.loads(str(output or ""))
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _graph_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "read_graph",
                "description": "Read the current Intent Graph, including schema, nodes, edges, selection, active node, and cycle warnings.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "scope_goal_id": {
                            "type": "integer",
                            "description": "Optional goal id to read as the active graph scope. Omit to use the selected goal or selected node owner.",
                        }
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "web_fetch",
                "description": (
                    "Fetch one HTTP(S) URL for graph-planning research only. "
                    "Use the excerpt to shape context, decision, evidence, or DoD nodes; "
                    "do not treat it as implementation work or proof that a run is done."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "HTTP(S) URL to fetch for planning context.",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum excerpt characters to return. Default 6000, max 12000.",
                        },
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "propose_graph_patch",
                "description": "Validate a graph patch without mutating the canvas. Use before apply_graph_patch.",
                "input_schema": self._graph_patch_input_schema(),
            },
            {
                "name": "apply_graph_patch",
                "description": "Apply a graph patch atomically. Invalid patches and cycles fail without changing the graph.",
                "input_schema": self._graph_patch_input_schema(),
            },
            {
                "name": "create_dod_fix_action",
                "description": "DoD-only Needs Changes transition. Creates a corrective action from review feedback and inserts it before the DoD.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "dod_node_id": {
                            "type": "integer",
                            "description": "The DoD node that received a Needs Changes review.",
                        },
                        "changes": {
                            "type": "string",
                            "description": "The parsed requested changes from the DoD review.",
                        },
                        "source_action_id": {
                            "type": "integer",
                            "description": "Optional previous action to extend from. If omitted, the canvas picks the nearest action feeding this DoD.",
                        },
                    },
                    "required": ["dod_node_id", "changes"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ask_user",
                "description": "Ask the user one focused question when a graph decision is blocked by ambiguity. You may call this tool multiple times, one question at a time.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The concise question to show the user.",
                        },
                        "choices": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional short choices. Prefer 2-5 choices when the decision is bounded.",
                        },
                        "allow_free_text": {
                            "type": "boolean",
                            "description": "Allow the user to type a custom answer. Defaults to true so bounded choices still include Other.",
                        },
                        "multi_select": {
                            "type": "boolean",
                            "description": "Render choices as checkboxes and allow multiple answers. Use only when several choices can be true at once.",
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
        ]

    @staticmethod
    def _graph_patch_input_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "description": "Patch operations such as add_node, update_node, delete_node, connect, delete_edge, set_active.",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "patch": {
                    "type": "object",
                    "description": "Alternative wrapper containing {'operations': [...]}",
                    "additionalProperties": True,
                },
                "reason": {"type": "string"},
            },
            "additionalProperties": True,
        }

    def _execute_graph_tool_threadsafe(self, name: str, inputs: dict, cancel) -> dict | str:
        if QThread.currentThread() == self.thread():
            return self._execute_graph_tool(name, inputs)
        event = threading.Event()
        request = {"name": name, "inputs": inputs, "event": event, "result": None}
        self._graph_tool_dispatcher.request.emit(request)
        while not event.wait(0.05):
            if cancel is not None and cancel.is_set():
                return "[cancelled]"
        return request["result"]

    def _handle_graph_tool_request(self, request: dict):
        try:
            request["result"] = self._execute_graph_tool(request.get("name"), request.get("inputs") or {})
        except Exception as exc:
            request["result"] = f"[tool error] graph tool failed: {exc}"
        finally:
            request["event"].set()

    def _execute_graph_tool(self, name: str, inputs: dict) -> dict:
        name = str(name or "")
        inputs = inputs if isinstance(inputs, dict) else {}
        if name == "ask_user":
            return self._ask_user_tool(inputs)
        if name == "web_fetch":
            return self._web_fetch_tool(inputs)
        scope_goal_id = self._graph_tool_scope_goal_id(inputs)
        if name == "read_graph":
            return self.read_graph_tool(scope_goal_id=scope_goal_id)
        if name == "propose_graph_patch":
            patch = self._graph_patch_from_tool_inputs(inputs)
            patch, autocorrections = self._autocorrect_graph_patch(patch)
            try:
                self._validate_graph_patch_scope(patch, scope_goal_id)
                after, _applied = self._patched_graph_state(patch)
                self._validate_generated_steps_patch_quality(patch, after)
            except (TypeError, ValueError) as exc:
                return self._graph_patch_error_payload(exc, scope_goal_id, mutated=False)
            return {
                "ok": True,
                "message": "Patch is valid within the active goal scope.",
                "mutated": False,
                "patch": patch,
                "autocorrections": autocorrections,
            }
        if name == "apply_graph_patch":
            patch = self._graph_patch_from_tool_inputs(inputs)
            patch, autocorrections = self._autocorrect_graph_patch(patch)
            try:
                self._validate_graph_patch_scope(patch, scope_goal_id)
                after, _applied = self._patched_graph_state(patch)
                self._validate_generated_steps_patch_quality(patch, after)
            except (TypeError, ValueError) as exc:
                payload = self._graph_patch_error_payload(exc, scope_goal_id, mutated=False)
                payload.update({
                    "ok": False,
                    "applied_operations": 0,
                    "nodes": len(self._nodes),
                    "edges": len(self._edges),
                })
                return payload
            result = self.apply_graph_patch(patch)
            result["autocorrections"] = autocorrections
            if result.get("ok") is True:
                self._graph_agent_applied_patches += 1
                self._autoformat_graph(scope_goal_id=scope_goal_id)
            return result
        if name == "create_dod_fix_action":
            return self._create_dod_fix_action_tool(inputs, scope_goal_id=scope_goal_id)
        return {"ok": False, "error": f"Unknown graph tool: {name}"}

    def _autocorrect_graph_patch(self, patch: dict) -> tuple[dict, list[str]]:
        if not isinstance(patch, dict):
            return patch, []
        operations = patch.get("operations")
        if not isinstance(operations, list):
            return patch, []
        corrected = {
            key: value
            for key, value in patch.items()
            if key != "operations"
        }
        corrected_operations = [dict(raw) if isinstance(raw, dict) else raw for raw in operations]
        corrected["operations"] = corrected_operations
        if not self._graph_agent_generation_mode:
            return corrected, []

        autocorrections: list[str] = []
        for raw in corrected_operations:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("op") or "") != "add_node" or str(raw.get("kind") or "") != "operation":
                continue
            if str(raw.get("agent_id") or "").strip() and str(raw.get("agent_name") or "").strip():
                continue
            agent_id, agent_name = self._default_generated_operation_agent(raw)
            raw["agent_id"] = agent_id
            raw["agent_name"] = agent_name
            title = str(raw.get("title") or "operation").strip() or "operation"
            autocorrections.append(f"defaulted crew for '{title}' to {agent_name}")

        handoff = self._autocorrect_single_design_handoff(corrected_operations)
        if handoff is not None:
            corrected_operations.append(handoff)
            autocorrections.append("connected the single planning/design action to the single implementation action")
        return corrected, autocorrections

    def _default_generated_operation_agent(self, raw: dict) -> tuple[str, str]:
        text = self._generated_operation_text(raw)
        if any(word in text for word in ("research", "survey", "investigate", "compare")):
            return "scout", "Scout"
        if self._generated_operation_is_implementation(raw):
            return "coder", "Coder"
        if self._generated_operation_is_planning(raw):
            return "architect", "Architect"
        return "coder", "Coder"

    def _autocorrect_single_design_handoff(self, operations: list[object]) -> dict | None:
        added_operations = [
            raw
            for raw in operations
            if isinstance(raw, dict)
            and str(raw.get("op") or "") == "add_node"
            and str(raw.get("kind") or "") == "operation"
        ]
        planners = [
            raw
            for raw in added_operations
            if self._generated_operation_is_planning(raw)
            and not self._generated_operation_is_implementation(raw)
        ]
        implementers = [raw for raw in added_operations if self._generated_operation_is_implementation(raw)]
        if len(planners) != 1 or len(implementers) != 1 or planners[0] is implementers[0]:
            return None
        source = self._patch_add_node_client_id(planners[0])
        target = self._patch_add_node_client_id(implementers[0])
        if not source or not target:
            return None
        for raw in operations:
            if not isinstance(raw, dict) or str(raw.get("op") or "") != "connect":
                continue
            if (
                raw.get("source") == source
                and raw.get("target") == target
                and str(raw.get("source_port") or "") == "implement"
            ):
                return None
        return {"op": "connect", "source": source, "target": target, "source_port": "implement"}

    def _graph_patch_error_payload(self, exc: Exception, scope_goal_id: int | None, *, mutated: bool) -> dict:
        message = str(exc) or "Invalid graph patch."
        summary = self._graph_patch_error_summary(message)
        payload = {
            "ok": False,
            "error": message,
            "summary": summary,
            "detail": self._graph_patch_error_detail(message, summary),
            "mutated": mutated,
        }
        repair = self._graph_patch_repair(message, summary)
        if repair:
            payload.update(repair)
        if summary == "cycle blocked":
            payload["repair_hint"] = (
                "Remove the backward edge that closes the loop. If an implementation consumed an upstream "
                "design/spec/context/evidence node, do not connect implementation back into that same node; "
                "create a separate downstream evidence/proof, decision, or DoD node instead."
            )
        if scope_goal_id is not None:
            scope = self._graph_scope_for_goal(scope_goal_id)
            if scope is not None:
                scope_ids = list(scope.get("node_ids") or [])
                payload["active_scope"] = {
                    "goal_id": scope_goal_id,
                    "node_ids": scope_ids,
                    "nodes": [
                        {
                            "id": node_id,
                            "kind": self._nodes[node_id].token.kind,
                            "title": self._nodes[node_id].token.title,
                        }
                        for node_id in scope_ids
                        if node_id in self._nodes
                    ],
                }
                payload["hint"] = (
                    "Retry using only node ids in active_scope.node_ids. "
                    "If you need another goal graph, ask_user which graph to edit or ask the user to select it."
                )
        return payload

    @staticmethod
    def _graph_patch_repair(message: str, summary: str) -> dict:
        text = str(message or "")
        folded = text.casefold()
        if summary == "invalid connection" and "-> evidence" in folded:
            return {
                "repair_pattern": "proof_for_dod",
                "repair_hint": (
                    "Goals and context do not directly produce evidence. Add or reuse an operation, connect "
                    "goal.work -> operation, then operation.evidence -> evidence. If the proof closes acceptance, "
                    "connect evidence.supports -> dod."
                ),
            }
        if summary == "invalid connection" and "context" in folded and "-> operation" in folded:
            return {
                "repair_pattern": "context_feeds_work",
                "repair_hint": (
                    "Durable context feeds work through context.context -> operation. If you have evidence/proof "
                    "driving follow-up work, use evidence.feedback -> operation instead."
                ),
            }
        if summary == "new node not connected":
            return {
                "repair_pattern": "context_feeds_work",
                "repair_hint": (
                    "Every add_node in a scoped patch must be connected in the same patch. Connect runnable work with "
                    "goal.work -> operation, durable context with goal.context -> context and context.context -> operation, "
                    "and proof with operation.evidence -> evidence."
                ),
            }
        if summary == "weak generated graph" and "dod" in folded and "evidence/proof" in folded:
            return {
                "repair_pattern": "proof_for_dod",
                "repair_hint": (
                    "A multi-action branch with DoD needs expected proof. Add an evidence node, connect the operation "
                    "that will verify the work with operation.evidence -> evidence, then connect evidence.supports -> dod."
                ),
            }
        if summary == "missing design link":
            return {
                "repair_pattern": "design_to_implementation",
                "repair_hint": (
                    "Implementation will not receive sibling planning output. Connect the planning/design/research/spec "
                    "operation directly to implementation with source_port='implement'."
                ),
            }
        return {}

    @staticmethod
    def _graph_patch_error_summary(message: str) -> str:
        text = str(message or "")
        folded = text.casefold()
        if "non-empty operations list" in folded:
            return "empty patch"
        if "graph patch must be an object" in folded:
            return "patch is not an object"
        if "every patch operation must be an object" in folded:
            return "operation is not an object"
        if "unsupported graph patch operation" in folded:
            return "unsupported operation"
        if "add_node requires" in folded:
            return "invalid add_node"
        if "files node detail must contain repo paths" in folded:
            return "invalid files paths"
        if "duplicate patch client_id" in folded:
            return "duplicate client_id"
        if "update_node refers to a missing node" in folded:
            return "missing update node"
        if "update_node title cannot be empty" in folded:
            return "empty update title"
        if "delete_node refers to a missing node" in folded:
            return "missing delete node"
        if "connect refers to missing or identical nodes" in folded:
            return "bad connection endpoints"
        if "connect is not valid" in folded:
            return "invalid connection"
        if "connect duplicates" in folded:
            return "duplicate connection"
        if "delete_edge did not match" in folded:
            return "edge not found"
        if "set_active refers to a missing node" in folded:
            return "missing active node"
        if "node positions must be numeric" in folded:
            return "invalid node position"
        if "unsupported node status" in folded:
            return "unsupported status"
        if "delete_edge requires" in folded:
            return "missing edge selector"
        if "patch disconnects" in folded:
            return "scope disconnected"
        if "outside selected goal scope" in text:
            return "outside selected goal"
        if "New graph nodes must be connected" in text:
            return "new node not connected"
        if "need agent_id and agent_name" in folded or "need crew assignment" in folded:
            return "missing crew"
        if "just an action list" in folded:
            return "missing graph signal"
        if "direct design -> implement link" in folded or "implementation actions must consume upstream planning/design outputs" in folded:
            return "missing design link"
        if "generated steps" in folded:
            return "weak generated graph"
        if "Invalid node reference" in text:
            return "unknown node reference"
        if "cycle" in text.casefold():
            return "cycle blocked"
        return "invalid patch"

    @staticmethod
    def _graph_patch_error_detail(message: str, summary: str = "") -> str:
        text = " ".join(str(message or "").split())
        if not text:
            return ""
        if "outside selected goal scope" in text:
            return ""
        prefixes = (
            "Graph patch ",
            "Patch ",
        )
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if text.startswith("must "):
            text = f"patch {text}"
        if summary and text.casefold() == summary.casefold():
            return ""
        return text[:96]

    def _ask_user_tool(self, inputs: dict) -> dict:
        question = str(inputs.get("question") or "").strip()
        if not question:
            return {"ok": False, "cancelled": False, "answer": "", "error": "ask_user requires a question."}
        raw_choices = inputs.get("choices")
        choices = [
            str(choice).strip()
            for choice in raw_choices
            if str(choice).strip()
        ] if isinstance(raw_choices, list) else []
        allow_free_text = inputs.get("allow_free_text")
        if allow_free_text is None:
            allow_free_text = True
        allow_free_text = bool(allow_free_text)
        multi_select = bool(inputs.get("multi_select"))
        self._set_question_attention(True)
        try:
            if choices:
                if multi_select:
                    answer, ok = self._ask_user_choice_dialog(
                        question,
                        choices,
                        allow_free_text,
                        multi_select=True,
                    )
                else:
                    answer, ok = self._ask_user_choice_dialog(question, choices, allow_free_text)
            else:
                answer, ok = QInputDialog.getMultiLineText(
                    self,
                    "Graph Agent Question",
                    question,
                    "",
                )
        finally:
            self._set_question_attention(False)
        answer = str(answer or "").strip()
        if not ok:
            return {"ok": False, "cancelled": True, "answer": ""}
        result = {"ok": True, "cancelled": False, "answer": answer}
        if choices and multi_select:
            result["answers"] = [part.strip() for part in answer.split(";") if part.strip()]
        return result

    def _ask_user_choice_dialog(
        self,
        question: str,
        choices: list[str],
        allow_free_text: bool,
        *,
        multi_select: bool = False,
    ) -> tuple[str, bool]:
        dialog = QDialog(self)
        dialog.setObjectName("graphQuestionDialog")
        dialog.setWindowTitle("Graph Agent Question")
        dialog.setStyleSheet(graph_question_dialog_style())
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        label = QLabel(question)
        label.setObjectName("graphQuestionPrompt")
        label.setWordWrap(True)
        layout.addWidget(label)

        group = QButtonGroup(dialog)
        group.setExclusive(not multi_select)
        buttons: list[QRadioButton | QCheckBox] = []
        for index, choice in enumerate(choices):
            button = QCheckBox(choice) if multi_select else QRadioButton(choice)
            button.setObjectName("graphQuestionChoice")
            group.addButton(button, index)
            layout.addWidget(button)
            buttons.append(button)
        if buttons and not multi_select:
            buttons[0].setChecked(True)

        other_id = -1000
        other_field = QTextEdit()
        other_field.setObjectName("graphQuestionOther")
        other_field.setAcceptRichText(False)
        other_field.setPlaceholderText("Specify another answer")
        other_field.setFixedHeight(76)
        if allow_free_text:
            other_button = QCheckBox("Other (specify)") if multi_select else QRadioButton("Other (specify)")
            other_button.setObjectName("graphQuestionChoice")
            group.addButton(other_button, other_id)
            layout.addWidget(other_button)
            layout.addWidget(other_field)
            other_field.setEnabled(False)

            def sync_other_field():
                other_field.setEnabled(other_button.isChecked())
                if other_field.isEnabled():
                    other_field.setFocus()

            group.buttonToggled.connect(lambda _button, _checked: sync_other_field())
        else:
            other_field.hide()

        buttons_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons_box.accepted.connect(dialog.accept)
        buttons_box.rejected.connect(dialog.reject)
        layout.addWidget(buttons_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return "", False

        if allow_free_text and not multi_select and group.checkedId() == other_id:
            return other_field.toPlainText().strip(), True
        if multi_select:
            selected_answers = [button.text().strip() for button in buttons if button.isChecked()]
            if allow_free_text and other_button.isChecked():
                custom = other_field.toPlainText().strip()
                if custom:
                    selected_answers.append(custom)
            return "; ".join(answer for answer in selected_answers if answer), True
        selected = group.checkedButton()
        return (selected.text().strip() if selected is not None else ""), True

    def _web_fetch_tool(self, inputs: dict) -> dict:
        url = str(inputs.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "web_fetch requires a url."}
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"ok": False, "error": "web_fetch only supports absolute http(s) URLs."}
        try:
            max_chars = int(inputs.get("max_chars") or 6000)
        except (TypeError, ValueError):
            max_chars = 6000
        max_chars = max(500, min(max_chars, 12000))
        max_bytes = max(max_chars * 4, 64_000)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AICHS Intent Graph/1.0 (+graph-planning-research)",
                "Accept": "text/html,text/plain,application/xhtml+xml,application/xml;q=0.8,*/*;q=0.5",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                raw = response.read(max_bytes + 1)
                headers = getattr(response, "headers", None)
                content_type = ""
                charset = ""
                if headers is not None:
                    content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
                    charset = str(getattr(headers, "get_content_charset", lambda: "")() or "")
                final_url = str(getattr(response, "url", "") or url)
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"HTTP {exc.code} while fetching URL.", "url": url}
        except urllib.error.URLError as exc:
            return {"ok": False, "error": f"Could not fetch URL: {exc.reason}", "url": url}
        except TimeoutError:
            return {"ok": False, "error": "Timed out while fetching URL.", "url": url}

        truncated = len(raw) > max_bytes
        if content_type and "text/" not in content_type and "html" not in content_type and "xml" not in content_type:
            return {
                "ok": False,
                "error": f"Fetched content is not readable text ({content_type or 'unknown content type'}).",
                "url": final_url,
            }
        text = raw[:max_bytes].decode(charset or "utf-8", errors="replace")
        title, content = self._readable_web_text(text, content_type)
        if len(content) > max_chars:
            content = content[:max_chars].rstrip()
            truncated = True
        return {
            "ok": True,
            "url": final_url,
            "title": title,
            "content_type": content_type,
            "chars": len(content),
            "truncated": truncated,
            "planning_note": (
                "Use this fetched content only to shape the Intent Graph plan: context, "
                "decisions, research actions, evidence expectations, or DoD. It is not implementation proof."
            ),
            "content": content,
        }

    @staticmethod
    def _readable_web_text(text: str, content_type: str = "") -> tuple[str, str]:
        source = str(text or "")
        title = ""
        if "html" in str(content_type or "").casefold() or "<html" in source[:500].casefold():
            title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", source)
            if title_match:
                title = AgentCanvasPanel._collapse_web_text(title_match.group(1))
            source = re.sub(r"(?is)<(script|style|noscript|svg|canvas)[^>]*>.*?</\1>", " ", source)
            source = re.sub(r"(?i)<br\s*/?>", "\n", source)
            source = re.sub(r"(?i)</(p|div|section|article|header|footer|main|li|h[1-6]|tr)>", "\n", source)
            source = re.sub(r"(?is)<[^>]+>", " ", source)
        content = AgentCanvasPanel._collapse_web_text(source)
        return title, content

    @staticmethod
    def _collapse_web_text(text: str) -> str:
        text = unescape(str(text or ""))
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _graph_tool_scope_goal_id(self, inputs: dict) -> int | None:
        if self._graph_agent_scope_goal_id is not None:
            return self._graph_agent_scope_goal_id
        if "scope_goal_id" in inputs:
            try:
                goal_id = int(inputs.get("scope_goal_id"))
            except (TypeError, ValueError):
                return None
            node = self._nodes.get(goal_id)
            return goal_id if node is not None and node.token.kind == "goal" else None
        selected = self._selected_node()
        if selected is not None:
            if selected.token.kind == "goal":
                return selected.node_id
            owner = self._goal_for_node_scope(selected.node_id)
            if owner is not None:
                return owner
        return None

    def _goal_for_node_scope(self, node_id: int) -> int | None:
        adjacency = self._weak_adjacency()
        seen: set[int] = set()
        pending = [node_id]
        goals: list[int] = []
        while pending:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            node = self._nodes.get(current)
            if node is not None and node.token.kind == "goal":
                goals.append(current)
            for other_id in adjacency.get(current, ()):
                if other_id not in seen:
                    pending.append(other_id)
        return min(goals) if goals else None

    def _validate_graph_patch_scope(self, patch: dict, scope_goal_id: int | None):
        if scope_goal_id is None:
            return
        before = self.graph_state()
        before_scope = self._graph_scope_for_goal(scope_goal_id, before)
        if before_scope is None:
            raise ValueError("Active graph scope is missing. Select a goal before editing the graph.")
        before_scope_ids = set(before_scope["node_ids"])
        before_node_ids = {int(node["id"]) for node in before["nodes"]}
        client_ids = self._patch_client_ids(patch)
        self._validate_graph_patch_keeps_source_goal_anchor(patch, scope_goal_id, client_ids)
        for node_id in self._existing_patch_references(patch, client_ids):
            if node_id in before_node_ids and node_id not in before_scope_ids:
                active_title = self._nodes[scope_goal_id].token.title if scope_goal_id in self._nodes else str(scope_goal_id)
                raise ValueError(
                    "Patch touches node outside selected goal scope: "
                    f"node id {node_id}. Active goal is {active_title} "
                    f"(id {scope_goal_id}). Use only node ids returned in active_scope.node_ids."
                )
        after, _applied = self._patched_graph_state(patch)
        after_scope = self._graph_scope_for_goal(scope_goal_id, after)
        if after_scope is None:
            raise ValueError("Patch disconnects the selected goal scope.")
        after_scope_ids = set(after_scope["node_ids"])
        after_node_ids = {int(node["id"]) for node in after["nodes"]}
        new_ids = after_node_ids - before_node_ids
        outside_new = new_ids - after_scope_ids
        if outside_new:
            raise ValueError(
                "New graph nodes must be connected into the selected goal scope. "
                "Include connect operations in the same patch using each add_node client_id."
            )

    def _validate_generated_steps_patch_quality(self, patch: dict, after: dict):
        if not self._graph_agent_generation_mode:
            return
        before_ids = {
            int(node["id"])
            for node in self.graph_state().get("nodes", [])
            if isinstance(node, dict) and "id" in node
        }
        after_nodes = [
            node
            for node in after.get("nodes", [])
            if isinstance(node, dict) and "id" in node
        ]
        node_by_id = {int(node["id"]): node for node in after_nodes}
        added_nodes = [node for node in after_nodes if int(node["id"]) not in before_ids]
        if not added_nodes:
            return

        added_kinds = {str(node.get("kind") or "") for node in added_nodes}
        added_operations = [node for node in added_nodes if str(node.get("kind") or "") == "operation"]
        missing_crew = [
            str(node.get("title") or node.get("id") or "action")
            for node in added_operations
            if not str(node.get("agent_id") or "").strip()
            or not str(node.get("agent_name") or "").strip()
        ]
        if missing_crew:
            names = ", ".join(missing_crew[:3])
            raise ValueError(
                "Generated operation nodes need agent_id and agent_name. "
                "Add both fields to each operation add_node using read_graph.available_crew, "
                'for example {"agent_id":"coder","agent_name":"Coder"}. '
                f"Missing for: {names}."
            )

        if len(added_operations) < 2:
            return

        self._validate_generated_operation_handoffs(added_operations, after)

        if not self._generated_steps_has_graph_value(added_operations, added_nodes, after):
            raise ValueError(
                "Generated steps are just an action list. Add one real graph signal: consumed context/scope, "
                "decision, evidence/proof, DoD, branch/fan-in, or a meaningful crew handoff. "
                "If none applies, keep one action or ask_user what larger breakdown the user wants."
            )

        if "dod" in added_kinds and "evidence" not in added_kinds:
            raise ValueError(
                "Generated steps with multiple actions and DoD need expected evidence/proof that can feed the DoD."
            )

        context_ids = {
            int(node["id"])
            for node in added_nodes
            if str(node.get("kind") or "") == "context"
        }
        if not context_ids:
            return
        for edge in after.get("edges", []):
            if not isinstance(edge, dict):
                continue
            try:
                source_id = int(edge.get("source_id"))
                target_id = int(edge.get("target_id"))
            except (TypeError, ValueError):
                continue
            target = node_by_id.get(target_id)
            if (
                source_id in context_ids
                and str(edge.get("source_port") or "") == "context"
                and target is not None
                and str(target.get("kind") or "") in {"operation", "decision"}
            ):
                return
        raise ValueError(
            "Generated steps added context that only hangs from the goal. "
            "Connect context.context into at least one action or decision."
        )

    def _generated_steps_has_graph_value(self, added_operations: list[dict], added_nodes: list[dict], after: dict) -> bool:
        added_kinds = {str(node.get("kind") or "") for node in added_nodes}
        if added_kinds & {"scope", "context", "decision", "evidence", "dod"}:
            return True

        operation_by_id: dict[int, dict] = {}
        for node in added_operations:
            try:
                operation_by_id[int(node["id"])] = node
            except (KeyError, TypeError, ValueError):
                continue
        operation_ids = set(operation_by_id)
        outgoing: dict[int, set[int]] = {}
        incoming: dict[int, set[int]] = {}
        for edge in after.get("edges", []):
            if not isinstance(edge, dict):
                continue
            try:
                source_id = int(edge.get("source_id"))
                target_id = int(edge.get("target_id"))
            except (TypeError, ValueError):
                continue
            if source_id not in operation_ids or target_id not in operation_ids:
                continue
            outgoing.setdefault(source_id, set()).add(target_id)
            incoming.setdefault(target_id, set()).add(source_id)
            source = operation_by_id[source_id]
            target = operation_by_id[target_id]
            if self._generated_operation_is_planning(source) and self._generated_operation_is_implementation(target):
                return True
            if self._generated_operation_crew_key(source) != self._generated_operation_crew_key(target):
                return True
        return any(len(targets) > 1 for targets in outgoing.values()) or any(
            len(sources) > 1 for sources in incoming.values()
        )

    def _validate_generated_operation_handoffs(self, added_operations: list[dict], after: dict):
        planners = [
            int(node["id"])
            for node in added_operations
            if self._generated_operation_is_planning(node)
            and not self._generated_operation_is_implementation(node)
        ]
        implementers = [
            int(node["id"])
            for node in added_operations
            if self._generated_operation_is_implementation(node)
        ]
        if not planners or not implementers:
            return
        missing_handoff: list[int] = []
        for implementer_id in implementers:
            if not any(
                planner_id != implementer_id and self._graph_has_path(after, planner_id, implementer_id)
                for planner_id in planners
            ):
                missing_handoff.append(implementer_id)
        if not missing_handoff:
            return
        names = self._generated_handoff_names(missing_handoff, added_operations)
        raise ValueError(
            "Generated steps need a direct Design -> Implement link. "
            "Add a connect operation from the planning/design/research/spec action to the implementation action "
            "{\"op\":\"connect\",\"source\":\"<design_client_id>\",\"target\":\"<implement_client_id>\",\"source_port\":\"implement\"}. "
            "Goal -> design and Goal -> implement as siblings is not enough. "
            f"Missing for: {names}."
        )

    @staticmethod
    def _generated_handoff_names(missing_ids: list[int], added_operations: list[dict]) -> str:
        titles = []
        for node in added_operations:
            try:
                node_id = int(node.get("id"))
            except (TypeError, ValueError):
                continue
            if node_id in missing_ids:
                titles.append(str(node.get("title") or node_id))
        return ", ".join(titles[:3]) or "implementation action"

    def _generated_operation_is_planning(self, node: dict) -> bool:
        text = self._generated_operation_text(node)
        agent = str(node.get("agent_id") or node.get("agent_name") or "").lower()
        planning_words = (
            "architect",
            "architecture",
            "design",
            "define",
            "plan",
            "research",
            "spec",
            "requirements",
            "ux",
            "ui",
            "model",
            "strategy",
        )
        return "architect" in agent or self._generated_text_has_terms(text, planning_words)

    def _generated_operation_is_implementation(self, node: dict) -> bool:
        text = self._generated_operation_text(node)
        implementation_words = (
            "implement",
            "build",
            "wire",
            "code",
            "engine",
            "state",
            "integration",
            "frontend",
            "backend",
            "parser",
            "evaluator",
            "persist",
        )
        return self._generated_text_has_terms(text, implementation_words)

    @staticmethod
    def _generated_text_has_terms(text: str, terms: tuple[str, ...]) -> bool:
        normalized = str(text or "").casefold()
        for term in terms:
            escaped = re.escape(str(term or "").casefold())
            if escaped and re.search(rf"\b{escaped}\b", normalized):
                return True
        return False

    @staticmethod
    def _generated_operation_crew_key(node: dict) -> str:
        return str(node.get("agent_id") or node.get("agent_name") or "").strip().casefold()

    def _generated_operation_text(self, node: dict) -> str:
        detail = str(node.get("detail") or "")
        if detail.startswith("Work action for "):
            detail = ""
        return (
            str(node.get("title") or "")
            + "\n"
            + detail
        ).lower()

    def _graph_has_path(self, state: dict, source_id: int, target_id: int) -> bool:
        adjacency: dict[int, set[int]] = {}
        for edge in state.get("edges", []):
            if not isinstance(edge, dict):
                continue
            try:
                edge_source = int(edge.get("source_id"))
                edge_target = int(edge.get("target_id"))
            except (TypeError, ValueError):
                continue
            adjacency.setdefault(edge_source, set()).add(edge_target)
        pending = list(adjacency.get(source_id, ()))
        seen: set[int] = set()
        while pending:
            current = pending.pop(0)
            if current == target_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(set(adjacency.get(current, ())) - seen)
        return False

    def _validate_graph_patch_keeps_source_goal_anchor(
        self,
        patch: dict,
        scope_goal_id: int,
        client_ids: dict[str, int],
    ):
        operations = patch.get("operations") if isinstance(patch, dict) else None
        if not isinstance(operations, list):
            return
        for raw in operations:
            if not isinstance(raw, dict):
                continue
            op = str(raw.get("op") or "")
            if op == "delete_node":
                try:
                    node_id = self._resolve_patch_node_ref(raw.get("id"), client_ids)
                except ValueError:
                    continue
                if node_id == scope_goal_id:
                    title = self._nodes[node_id].token.title if node_id in self._nodes else str(node_id)
                    raise ValueError(
                        "Patch cannot delete the source goal for this run: "
                        f"{title} (id {node_id}). Update it, append to it, or ask_user before replacing it."
                    )
            elif op == "connect":
                try:
                    target_id = self._resolve_patch_node_ref(raw.get("target"), client_ids)
                except ValueError:
                    continue
                if target_id == scope_goal_id:
                    title = self._nodes[target_id].token.title if target_id in self._nodes else str(target_id)
                    raise ValueError(
                        "Patch cannot add an incoming connection to the source goal for this run: "
                        f"{title} (id {target_id}). Expand from the goal outward instead."
                    )

    def _patch_client_ids(self, patch: dict) -> dict[str, int]:
        operations = patch.get("operations") if isinstance(patch, dict) else None
        if not isinstance(operations, list):
            return {}
        next_id = self._next_node_id
        client_ids: dict[str, int] = {}
        for raw in operations:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("op") or "") != "add_node":
                continue
            client_id = self._patch_add_node_client_id(raw)
            if client_id:
                client_ids[client_id] = next_id
            next_id += 1
        return client_ids

    @staticmethod
    def _patch_add_node_client_id(raw: dict) -> str:
        client_id = str(raw.get("client_id") or "").strip()
        if client_id:
            return client_id
        raw_id = raw.get("id")
        if raw_id is None:
            return ""
        try:
            int(raw_id)
        except (TypeError, ValueError):
            return str(raw_id).strip()
        return ""

    @staticmethod
    def _patch_node_detail(kind: str, title: str, detail: object) -> str:
        text = str(detail or "").strip()
        if text:
            return text
        title = str(title or "").strip() or component_spec(kind).title
        fallbacks = {
            "goal": f"Define the desired outcome and acceptance signal for {title}.",
            "operation": f"Work action for {title}; specify whether it implements, decides, or proves, plus expected output.",
            "context": f"Durable project context for {title}; record constraints that should guide connected work.",
            "scope": f"Files, folders, or code areas relevant to {title}.",
            "evidence": f"Expected proof for {title}; connect concrete results into the DoD.",
            "decision": f"Decision contract for {title}; record the choice needed, criteria, producer owner, and downstream guidance expected.",
            "dod": f"Acceptance criteria for {title}; mark done only after connected proof satisfies it.",
        }
        return fallbacks.get(kind, f"Describe the purpose and expected outcome for {title}.")

    def _validate_patch_node_detail(self, kind: str, detail: str):
        if kind != "scope":
            return
        refs = self._scope_refs(detail)
        if not refs:
            raise ValueError(
                "Files node detail must contain repo paths, one per line. "
                "Use context for descriptions or product constraints."
            )
        invalid = [ref for ref in refs if not self._scope_ref_looks_like_path(ref)]
        if invalid:
            sample = ", ".join(invalid[:3])
            raise ValueError(
                "Files node detail must contain repo paths, one per line. "
                f"These entries do not look like paths: {sample}. "
                "Use context for descriptions such as affected areas, components, or constraints."
            )

    def _scope_ref_looks_like_path(self, ref: str) -> bool:
        normalized = self._normalize_scope_ref(ref)
        if not normalized:
            return False
        candidates = {
            self._normalize_scope_ref(candidate).casefold()
            for candidate in repo_path_candidates(self._repo_root)
        }
        if normalized.casefold() in candidates:
            return True
        if "/" in normalized or "\\" in str(ref):
            return True
        basename = normalized.rsplit("/", 1)[-1]
        return "." in basename and " " not in basename

    def _existing_patch_references(self, patch: dict, client_ids: dict[str, int]) -> set[int]:
        operations = patch.get("operations") if isinstance(patch, dict) else None
        if not isinstance(operations, list):
            return set()
        refs: set[int] = set()
        for raw in operations:
            if not isinstance(raw, dict):
                continue
            op = str(raw.get("op") or "")
            keys = {
                "update_node": ("id",),
                "delete_node": ("id",),
                "connect": ("source", "target"),
                "delete_edge": ("source", "target"),
                "set_active": ("id",),
            }.get(op, ())
            for key in keys:
                if key not in raw or raw.get(key) is None:
                    continue
                try:
                    node_id = self._resolve_patch_node_ref(raw.get(key), client_ids)
                except ValueError:
                    continue
                if node_id not in client_ids.values():
                    refs.add(node_id)
        return refs

    @staticmethod
    def _graph_patch_from_tool_inputs(inputs: dict) -> dict:
        patch = inputs.get("patch")
        if isinstance(patch, dict):
            return patch
        operations = inputs.get("operations")
        if isinstance(operations, list):
            return {"operations": operations}
        return {"operations": []}

    def _graph_agent_prompt(self) -> str:
        return graph_agent_prompt(self._settings.load())

    def _generation_strategy(self) -> str:
        return graph_generation_strategy(self._settings.load())

    def _canvas_run_mode(self) -> str:
        return canvas_run_mode(self._settings.load())

    def _canvas_run_parallel_limit(self) -> int:
        mode = self._canvas_run_mode()
        return canvas_parallel_limit(self._settings.load()) if mode == "parallel" else 1

    def _canvas_action_auto_approve(self) -> str:
        return canvas_action_auto_approve(self._settings.load())

    @staticmethod
    def _generation_strategy_label(strategy: str) -> str:
        return "Prefer atomicity" if strategy == "atomicity" else "Prefer parallelism"

    @staticmethod
    def _generation_strategy_tooltip(strategy: str) -> str:
        if strategy == "atomicity":
            return "Generate Steps prefers a long sequential trail; branches only when sequence would be misleading."
        return "Generate Steps prefers meaningful parallel branches for independent non-trivial work."

    def _generation_strategy_instructions(self) -> str:
        strategy = self._generation_strategy()
        if strategy == "atomicity":
            return (
                "Generation strategy: Prefer atomicity. In Generate Steps mode, prefer a long, clear sequential trail of actions. "
                "Use branches only when sequential ordering would be misleading or impossible, such as true alternatives, fan-in review, or independent acceptance evidence. "
                "Keep each action self-contained and make dependencies explicit with direct connections."
            )
        return (
            "Generation strategy: Prefer parallelism. In Generate Steps mode, split non-trivial independent work into sibling branches when those branches can run concurrently. "
            "Prefer parallel branches for distinct responsibilities, surfaces, unknowns, validation paths, or implementation areas. "
            "Do not split trivial work just to create more nodes; every branch needs a distinct output, owner, dependency, or acceptance signal."
        )

    def _seed_graph(self):
        self._populate_empty_inspector()
        self._graph.ensure_scene_rect_contains(QPointF(0, 0))
        self._goal.setText("Start with a goal. Drag out to split, assign crew, or create work.")

    def _create_node(
        self,
        token: CanvasToken,
        point: QPointF,
        *,
        node_id: int | None = None,
        sync_visibility: bool = True,
        sync_layout: bool = True,
    ) -> _GraphNode:
        if node_id is None:
            node_id = self._next_node_id
            self._next_node_id += 1
        else:
            self._next_node_id = max(self._next_node_id, node_id + 1)
        node = _GraphNode(
            node_id,
            token,
            moved=self._on_node_moved,
            selected=self._select_node,
            activated=self._activate_node,
            menu_requested=self._show_node_menu,
            file_open_requested=self._activate_file_node,
            run_requested=self._control_node,
            output_drag_started=self._begin_output_drag,
            output_drag_moved=self._move_output_drag,
            output_drag_finished=self._finish_output_drag,
            input_drag_started=self._begin_input_drag,
            input_drag_moved=self._move_input_drag,
            input_drag_finished=self._finish_input_drag,
        )
        node.setPos(point)
        self._scene.addItem(node)
        self._nodes[node.node_id] = node
        if sync_layout:
            self._sync_root_goal()
            self._ensure_scene_for_items()
            if sync_visibility:
                self._apply_goal_collapse_visibility()
            self._sync_graph_frames()
            self._sync_counts()
            self._notify_graph_changed()
        return node

    def _create_frame(
        self,
        title: str,
        color: str,
        rect: QRectF,
        *,
        root_id: int | None,
        node_ids: set[int],
        frame_id: int | None = None,
    ) -> _GraphFrame:
        if frame_id is None:
            frame_id = self._next_frame_id
            self._next_frame_id += 1
        else:
            self._next_frame_id = max(self._next_frame_id, frame_id + 1)
        frame = _GraphFrame(
            frame_id,
            title,
            color,
            rect,
            root_id=root_id,
            node_ids=node_ids,
            selected=self._select_frame,
            activated=self.edit_frame,
        )
        self._scene.addItem(frame)
        self._frames[frame.frame_id] = frame
        return frame

    def _add_goal(self, scene_pos: QPointF | None = None):
        point = self._next_spawn_point() if scene_pos is None else scene_pos
        node = self._create_node(CanvasToken("goal", "New Goal", "Describe the desired outcome"), point)
        self._goal.setText("New goal placed. Drag from it to add context, split, or an action.")
        self._select_node(node)

    def _show_canvas_context_menu(self, scene_pos: QPointF):
        menu = QMenu(self)
        action = menu.addAction("New Goal")
        chosen = menu.exec(self._scene_pos_to_global(scene_pos))
        if chosen == action:
            self._add_goal(scene_pos)

    def _break_down_selected(self):
        node = self._selected_node()
        if node is None:
            return
        if node.token.kind != "goal":
            self._set_mode("split starts from a goal")
            return
        base = node.pos()
        children = [
            CanvasToken("goal", "Explore", "Understand the current flow"),
            CanvasToken("goal", "Change the model", "Make the graph reduce work"),
            CanvasToken("goal", "Verify", "Use evidence to close the loop"),
        ]
        for idx, token in enumerate(children):
            child = self._create_node(token, base + QPointF(270, -120 + idx * 120))
            self.connect_nodes(node.node_id, child.node_id)
        self._goal.setText("Goal broken into connected child goals.")
        self._fit_graph()

    def _run_selected(self):
        node = self._selected_node()
        if node is None:
            self._set_mode("select a goal to run")
            return
        self._run_node(node)

    def _control_node(self, node: _GraphNode, action: str = "run"):
        if action == "pause":
            self._pause_run(node)
            return
        if action == "stop":
            self._cancel_run(node)
            return
        if node.status == "paused" and self._run_session is not None:
            self._resume_run(node)
            return
        self._run_node(node)

    def _run_node(self, node: _GraphNode):
        if self._is_graph_agent_running():
            self._set_mode("wait for graph generation to finish before running")
            self._sync_run_controls()
            return
        if node.token.kind != "goal":
            self._set_mode("runs start from goals")
            self._sync_run_controls()
            return
        try:
            plan = self._run_engine.compile(self.graph_state(), node.node_id)
        except GraphRunError as exc:
            self._set_mode(str(exc))
            self._sync_run_controls()
            return
        self._prepare_run_restart(plan)
        self._run_session = GraphRunSession(plan=plan)
        self._select_node(node)
        if node.token.kind == "goal":
            for goal_id in plan.goal_ids:
                goal = self._nodes.get(goal_id)
                if goal is not None and goal.status == "idle":
                    goal.set_status("queued", "in run branch")
            node.set_status("running", "orchestrating branch")
            self._active_node_id = node.node_id
            self._append_graph_chat_message("Graph Agent", f"Run started from goal: {node.token.title}.")
        self._start_next_run_operation()

    def _start_next_run_operation(self):
        session = self._run_session
        if session is None or session.paused:
            self._sync_run_controls()
            return
        self._prune_run_session_running_nodes(session)
        run_mode = self._canvas_run_mode()
        if run_mode != "parallel" and session.running_node_id is not None:
            running = self._nodes.get(session.running_node_id)
            if running is not None and running.status in {"running", "review"}:
                self._sync_run_controls()
                return
        if not session.running_node_ids and self._run_engine.plan_complete(self.graph_state(), session.plan):
            self._finish_run_session(success=True)
            return
        ready_ids = self._run_engine.ready_operation_ids(self.graph_state(), session.plan)
        ready_ids = tuple(node_id for node_id in ready_ids if node_id not in session.running_node_ids)
        limit = self._canvas_run_parallel_limit()
        slots = max(0, limit - len(session.running_node_ids))
        if ready_ids and slots > 0:
            started = []
            for ready_id in ready_ids[:slots]:
                node = self._nodes.get(ready_id)
                if node is None:
                    continue
                if node.status in {"running", "review", "done", "blocked", "paused"}:
                    continue
                session.running_node_ids.add(node.node_id)
                session.running_node_id = node.node_id
                owner = f"{node.agent_name} working" if node.agent_name else "Coder working"
                self._set_active_node(node, "running", owner)
                self._append_graph_chat_message("Graph Agent", f"Running agent action: {node.token.title}.")
                self._start_node_agent(node, kind="operation")
                if self._run_session is not session:
                    self._sync_run_controls()
                    return
                if node.status == "running":
                    started.append(node.token.title)
            if started:
                if run_mode == "parallel" and len(started) > 1:
                    self._set_mode(f"running {len(started)} actions")
                else:
                    self._set_mode(f"running {started[-1]}")
                self._sync_run_controls()
                return
        if session.running_node_ids:
            self._sync_run_controls()
            return
        if not ready_ids:
            dod = self._pending_dod_review_node(session)
            if dod is not None:
                session.running_node_ids.add(dod.node_id)
                session.running_node_id = dod.node_id
                self._set_active_node(dod, "running", "Architect reviewing acceptance")
                self._append_graph_chat_message("Graph Agent", f"Reviewing DoD: {dod.token.title}.")
                self._start_node_agent(dod, kind="dod_review")
            else:
                waits = self._run_engine.waiting_operations(self.graph_state(), session.plan)
                if waits:
                    first = waits[0]
                    blocked = [self._nodes[item].token.title for item in first.blocker_ids if item in self._nodes]
                    self._set_mode("run waiting for " + ", ".join(blocked[:3]))
                else:
                    self._set_mode("run waiting for upstream verification")
            self._sync_run_controls()
            return

    def _advance_run_after_status_change(self):
        session = self._run_session
        if session is None:
            self._sync_run_controls()
            return
        self._prune_run_session_running_nodes(session)
        if any(self._nodes.get(node_id) is not None and self._nodes[node_id].status == "blocked" for node_id in session.plan.node_ids):
            self._finish_run_session(success=False)
            return
        if self._canvas_run_mode() != "parallel":
            running = self._nodes.get(session.running_node_id) if session.running_node_id is not None else None
            if running is not None and running.status in {"running", "review"}:
                self._sync_run_controls()
                return
            session.running_node_id = None
        elif session.running_node_ids:
            self._sync_run_controls()
            return
        if session.paused:
            self._set_mode("run paused")
            self._sync_run_controls()
            return
        self._start_next_run_operation()

    def _prune_run_session_running_nodes(self, session: GraphRunSession):
        session.running_node_ids = {
            node_id
            for node_id in session.running_node_ids
            if self._nodes.get(node_id) is not None and self._nodes[node_id].status == "running"
        }

    def _finish_run_session(self, *, success: bool):
        session = self._run_session
        if session is None:
            return
        if not success:
            self._stop_running_node_attempts(
                session.plan.node_ids,
                reason="Run blocked before the provider returned a result.",
            )
        for goal_id in session.plan.goal_ids:
            goal = self._nodes.get(goal_id)
            if goal is None:
                continue
            if success:
                goal.set_status("done", "branch verified")
            elif goal.status in {"queued", "running"}:
                goal.set_status("blocked", "run blocked")
        self._active_node_id = None
        self._run_session = None
        self._run_threads.clear()
        self._run_thread = None
        self._refresh_edges()
        self._sync_counts()
        self._sync_run_controls()
        failed = self._selected_node()
        if success:
            self._set_mode("run complete")
            message = "Run complete."
        elif failed is not None and self._latest_run_attempt_status(failed.node_id) == "error":
            self._set_mode(f"{failed.token.title} needs retry or guidance")
            message = f"Run stopped at failed step: {failed.token.title}. Retry the step or add guidance."
        else:
            self._set_mode("run blocked")
            message = "Run blocked."
        self._append_graph_chat_message("Graph Agent", message)
        self._notify_graph_changed()

    def _run_waiting_for_dod(self, session: GraphRunSession) -> bool:
        return self._pending_dod_review_node(session) is not None

    def _pending_dod_review_node(self, session: GraphRunSession) -> _GraphNode | None:
        operations_done = all(
            self._nodes.get(node_id) is not None and self._nodes[node_id].status == "done"
            for node_id in session.plan.operation_ids
        )
        if not operations_done:
            return None
        for node_id in session.plan.node_ids:
            node = self._nodes.get(node_id)
            if node is not None and node.token.kind == "dod" and node.status not in {"done", "running", "review"}:
                return node
        return None

    def _is_run_agent_running(self) -> bool:
        return any(thread.isRunning() for thread in self._run_threads.values()) or (
            self._run_thread is not None and self._run_thread.isRunning()
        )

    def _start_node_agent(self, node: _GraphNode, *, kind: str, compact_retry: bool = False):
        attempt = self._new_run_attempt(node, kind, compact_retry=compact_retry)
        if self._run_agent_runner is not None:
            if kind == "dod_review":
                attempt["conversation_id"] = f"{ConversationStore.new_id()}_canvas_{node.node_id}_{uuid4().hex[:8]}"
            self._ensure_run_conversation(node, attempt, model=self._graph_agent_model(), system="")
            self._notify_graph_changed()
            try:
                result = self._run_agent_runner(node, attempt["prompt"], kind)
            except Exception as exc:
                self._finish_node_attempt(node.node_id, attempt["id"], f"Run failed: {exc}", error=True)
                return
            if result is None:
                self._render_graph_chat()
                self._notify_graph_changed()
                return
            self._finish_node_attempt(node.node_id, attempt["id"], str(result or "Done."))
            return

        model, system, allowed_tools, write_roots = self._run_agent_config(node, kind)
        if not model:
            self._finish_node_attempt(node.node_id, attempt["id"], "No model is configured for graph runs.", error=True)
            return
        if kind == "dod_review":
            allowed_tools = [
                tool_name
                for tool_name in (allowed_tools or [])
                if str(tool_name or "") in {"read_file", "list_files", "search_files"}
            ]
            write_roots = []
        canvas_tool_names = [] if kind == "dod_review" else self._canvas_extension_tool_names()
        if allowed_tools is not None:
            allowed_tools = list(dict.fromkeys(list(allowed_tools) + canvas_tool_names))
        run = self._conversation_run_manager.start(
            conv_id="" if kind == "dod_review" else self._latest_run_conversation_id(node.node_id),
            title=self._run_conversation_title(node),
            prompt=attempt["prompt"],
            model=model,
            system=system,
            allowed_tools=allowed_tools,
            tool_policy=self._tool_policy,
            approval_bus=self._approval_bus,
            write_roots=write_roots,
            crew_settings=self._settings.load(),
            configured_providers=set(configured_provider_ids(self._settings.load())),
            metadata=self._run_conversation_metadata(node, attempt),
        )
        attempt["conversation_id"] = run.conv_id
        attempt["conversation_created_at"] = str(run.data.get("created_at") or datetime.now().isoformat())
        attempt["conversation_model"] = model
        attempt["conversation_system"] = system
        attempt["conversation_canonical"] = True
        self._conversation_run_nodes[run.run_id] = (node.node_id, attempt["id"])
        thread = run.thread
        self._run_thread = thread
        self._run_threads[node.node_id] = thread
        self._run_thread_attempt_id = attempt["id"]
        self._run_last_edit_path = ""
        thread.finished.connect(lambda t=thread: self._on_run_agent_finished(t))
        self._render_graph_chat()
        self._notify_graph_changed()
        self._advance_run_after_status_change()

    def _new_run_attempt(self, node: _GraphNode, kind: str, *, compact_retry: bool = False) -> dict:
        role = "Architect" if kind == "dod_review" else (node.agent_name or "Coder")
        attempt = {
            "id": uuid4().hex,
            "kind": kind,
            "role": role,
            "status": "running",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "prompt": self._run_agent_prompt(node, kind, compact=compact_retry),
            "content": "",
            "tools": [],
            "touched_files": [],
            "compact_retry": compact_retry,
        }
        self._node_run_history.setdefault(node.node_id, []).append(attempt)
        return attempt

    def _ensure_run_conversation(self, node: _GraphNode, attempt: dict, *, model: str = "", system: str = "") -> str:
        conv_id = str(attempt.get("conversation_id") or "").strip()
        created = False
        if not conv_id:
            conv_id = self._latest_run_conversation_id(node.node_id)
        if not conv_id:
            conv_id = f"{ConversationStore.new_id()}_canvas_{node.node_id}_{uuid4().hex[:8]}"
            attempt["conversation_created_at"] = datetime.now().isoformat()
            created = True
        attempt["conversation_id"] = conv_id
        if not str(attempt.get("conversation_created_at") or "").strip():
            for previous in self._node_run_history.get(node.node_id, []):
                if str(previous.get("conversation_id") or "").strip() == conv_id:
                    created_at = str(previous.get("conversation_created_at") or "").strip()
                    if created_at:
                        attempt["conversation_created_at"] = created_at
                        break
        if model:
            attempt["conversation_model"] = str(model)
        if system:
            attempt["conversation_system"] = str(system)
        self._save_run_conversation(node, attempt)
        if created:
            self.conversation_created.emit(conv_id)
        return conv_id

    def _save_run_conversation(self, node: _GraphNode, attempt: dict, *, final: bool = False):
        conv_id = str(attempt.get("conversation_id") or "").strip()
        if not conv_id:
            return
        now = datetime.now().isoformat()
        created_at = str(attempt.get("conversation_created_at") or now)
        attempt["conversation_created_at"] = created_at
        if bool(attempt.get("conversation_canonical")):
            try:
                data = self._conversation_store.load_by_id(conv_id)
            except (FileNotFoundError, OSError, ValueError) as exc:
                attempt["conversation_error"] = str(exc)[:500]
                return
            data["title"] = self._run_conversation_title(node)
            data["title_auto"] = False
            data["updated_at"] = now
            data["model"] = str(attempt.get("conversation_model") or data.get("model") or "")
            data["cwd"] = self._repo_root
            data["canvas"] = self._run_conversation_metadata(node, attempt)
            try:
                self._conversation_store.save(conv_id, data)
            except OSError as exc:
                attempt["conversation_error"] = str(exc)[:500]
                return
            self.conversation_updated.emit(conv_id)
            return
        data = {
            "id": conv_id,
            "title": self._run_conversation_title(node),
            "title_auto": False,
            "created_at": created_at,
            "updated_at": now,
            "model": str(attempt.get("conversation_model") or ""),
            "cwd": self._repo_root,
            "messages": prepare_for_storage(self._run_node_conversation_messages(node.node_id, conv_id, final_attempt_id=str(attempt.get("id") or ""), final=final)),
            "canvas": {
                "node_id": node.node_id,
                "node_kind": node.token.kind,
                "node_title": node.token.title,
                "attempt_id": str(attempt.get("id") or ""),
                "attempt_status": str(attempt.get("status") or ""),
                "run_kind": str(attempt.get("kind") or ""),
                "surface": "canvas",
            },
        }
        try:
            self._conversation_store.save(conv_id, data)
        except OSError as exc:
            attempt["conversation_error"] = str(exc)[:500]
            return
        self.conversation_updated.emit(conv_id)

    def _run_conversation_metadata(self, node: _GraphNode, attempt: dict) -> dict:
        return {
            "node_id": node.node_id,
            "node_kind": node.token.kind,
            "node_title": node.token.title,
            "attempt_id": str(attempt.get("id") or ""),
            "attempt_status": str(attempt.get("status") or ""),
            "run_kind": str(attempt.get("kind") or ""),
            "surface": "canvas",
        }

    def _run_conversation_title(self, node: _GraphNode) -> str:
        action = " ".join(str(node.token.title or component_spec(node.token.kind).title).split()) or "Action"
        goal_ref = self._goal_reference_for_node(node.node_id)
        if not goal_ref:
            return action[:80]
        return f"{goal_ref} / {action}"[:100]

    def _goal_reference_for_node(self, node_id: int) -> str:
        node = self._nodes.get(node_id)
        goal_ids: list[int] = []
        if node is not None and node.token.kind == "goal":
            goal_ids.append(node_id)
        goal_ids.extend(self._owning_goal_ids(node_id))
        best_path: list[int] = []
        for goal_id in goal_ids:
            path = self._goal_reference_path(goal_id)
            if not path:
                continue
            if not best_path or len(path) > len(best_path) or (len(path) == len(best_path) and path < best_path):
                best_path = path
        return "G" + ".".join(str(part) for part in best_path) if best_path else ""

    def _goal_reference_path(self, goal_id: int, seen: set[int] | None = None) -> list[int]:
        goal = self._nodes.get(goal_id)
        if goal is None or goal.token.kind != "goal":
            return []
        seen = set(seen or ())
        if goal_id in seen:
            return []
        seen.add(goal_id)
        parents = self._parent_goal_ids(goal_id)
        if parents:
            parent_id = parents[0]
            siblings = self._child_goal_ids(parent_id)
            try:
                child_index = siblings.index(goal_id) + 1
            except ValueError:
                child_index = 1
            parent_path = self._goal_reference_path(parent_id, seen)
            return [*parent_path, child_index] if parent_path else [child_index]
        roots = self._root_goal_reference_ids()
        try:
            return [roots.index(goal_id) + 1]
        except ValueError:
            return [1]

    def _parent_goal_ids(self, goal_id: int) -> list[int]:
        parents = []
        for edge in self._edges:
            if edge.target_id != goal_id:
                continue
            if self._edge_makes_goal_child(edge):
                parents.append(edge.source_id)
        parents.sort(key=self._node_sort_key)
        return parents

    def _child_goal_ids(self, goal_id: int) -> list[int]:
        children = []
        for edge in self._edges:
            if edge.source_id != goal_id:
                continue
            if self._edge_makes_goal_child(edge):
                children.append(edge.target_id)
        children.sort(key=self._node_sort_key)
        return children

    def _root_goal_reference_ids(self) -> list[int]:
        child_ids = {
            edge.target_id
            for edge in self._edges
            if self._edge_makes_goal_child(edge)
        }
        roots = [
            node.node_id
            for node in self._nodes.values()
            if node.token.kind == "goal" and node.node_id not in child_ids
        ]
        roots.sort(key=self._node_sort_key)
        return roots

    @staticmethod
    def _goal_reference_sort_key(node: _GraphNode | int) -> tuple[float, float, int]:
        if isinstance(node, int):
            return (0.0, 0.0, node)
        return (float(node.pos().y()), float(node.pos().x()), node.node_id)

    def _run_node_conversation_messages(
        self,
        node_id: int,
        conv_id: str,
        *,
        final_attempt_id: str = "",
        final: bool = False,
    ) -> list[dict]:
        messages: list[dict] = []
        for attempt in self._node_run_history.get(node_id, []):
            if str(attempt.get("conversation_id") or "").strip() != conv_id:
                continue
            attempt_final = final if str(attempt.get("id") or "") == final_attempt_id else str(attempt.get("status") or "") != "running"
            messages.extend(self._run_conversation_messages(attempt, final=attempt_final))
        return messages

    def _run_conversation_messages(self, attempt: dict, *, final: bool = False) -> list[dict]:
        created_at = str(attempt.get("conversation_created_at") or datetime.now().isoformat())
        messages = [
            {
                "role": "user",
                "content": str(attempt.get("prompt") or ""),
                "created_at": created_at,
                "synthetic": "canvas_run_prompt",
            }
        ]
        for tool in attempt.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            messages.extend(self._run_tool_chat_messages(attempt, tool))
        content = str(attempt.get("content") or "").strip()
        if content or final:
            messages.append(
                {
                    "role": "assistant",
                    "content": content or "(no response)",
                    "created_at": datetime.now().isoformat(),
                    "synthetic": "canvas_run_result" if final else "canvas_run_partial",
                }
            )
        for raw in attempt.get("guidance") or []:
            if not isinstance(raw, dict):
                continue
            guidance = str(raw.get("content") or "").strip()
            if not guidance:
                continue
            messages.append(
                {
                    "role": "user",
                    "content": guidance,
                    "created_at": str(raw.get("created_at") or datetime.now().isoformat()),
                    "synthetic": "canvas_guidance",
                }
            )
        return messages

    def _run_tool_chat_messages(self, attempt: dict, tool: dict) -> list[dict]:
        name = str(tool.get("name") or "tool").strip() or "tool"
        tool_id = str(tool.get("tool_use_id") or "").strip()
        if not tool_id:
            attempt_id = self._safe_artifact_segment(str(attempt.get("id") or "attempt"))
            index = len([item for item in attempt.get("tools") or [] if isinstance(item, dict)])
            tool_id = f"canvas_{attempt_id}_{index}_{uuid4().hex[:6]}"
            tool["tool_use_id"] = tool_id
        inputs = self._tool_chat_inputs(tool)
        output = str(tool.get("output") or "").strip()
        created_at = datetime.now().isoformat()
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": name,
                        "input": inputs,
                    }
                ],
                "created_at": created_at,
            }
        ]
        if output or str(tool.get("status") or "") in {"done", "failed"}:
            messages.append(
                {
                    "role": "user",
                    "synthetic": "tool_results",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": output,
                        }
                    ],
                    "created_at": created_at,
                }
            )
        return messages

    @staticmethod
    def _tool_chat_inputs(tool: dict) -> dict:
        raw = str(tool.get("inputs") or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        summary = str(tool.get("summary") or "").strip()
        if summary:
            return {"path": summary}
        return {}

    def _run_agent_config(self, node: _GraphNode, kind: str):
        settings = self._settings.load()
        base = build_system(self._repo_root, str(settings.get("system_prompt") or "").strip() or None)
        member = get_crew_member("architect") if kind == "dod_review" else get_crew_member(node.agent_id)
        if member is not None:
            saved = {member.id: crew_settings(settings, member).get("model", "")}
            model = crew_model_choice(member, self._graph_agent_model(), saved, set(configured_provider_ids(settings)))
            system = crew_system_prompt(member, base, crew_prompt(member, settings))
            system = self._run_system_suffix(system, kind)
            if kind != "dod_review":
                system = self._with_canvas_extension_context(system, model=model, kind=kind, node=node)
            return model, system, list(member.tools), list(member.write_roots)
        model = self._graph_agent_model()
        system = self._run_system_suffix(base, kind)
        if kind != "dod_review":
            system = self._with_canvas_extension_context(system, model=model, kind=kind, node=node)
        return model, system, None, None

    def _run_system_suffix(self, system: str, kind: str) -> str:
        if kind == "dod_review":
            return (
            system
            + "\n\nYou are reviewing an Intent Graph Definition of Done. "
            + "Do not implement new work. Inspect only what is needed. Return a concise acceptance review: pass/fail, evidence checked, and gaps. "
            + "If you cannot judge because required information is missing, ask exactly one concrete guidance question instead of guessing. "
            + "If read_file returns File does not exist, do not retry that same path; inspect nearby existing files or ask for guidance. "
            + "The UI will ask the user to accept or extend the graph."
        )
        return (
            system
            + "\n\nYou are executing one Intent Graph operation. "
            + "Stay inside this operation's scope. Do not continue to downstream graph nodes. "
            + "If read_file returns File does not exist, do not retry that same path. If the task is to create that file, use edit_file with content; otherwise search/list existing files or ask one concrete guidance question. "
            + "For edit_file, content must be a plain string, not an object or list; for existing files prefer edits with oldText/newText strings. If edit_file returns a schema or type error, fix the tool arguments once instead of retrying the same shape. "
            + "If a direct graph input includes an Artifact path, read that file when the full upstream plan, decision, or result matters. "
            + "The only valid outcomes are: complete the step with a reviewable summary, or ask exactly one concrete guidance question if required context is missing. "
            + "If the prompt lists expected output decisions, explicitly provide the chosen decision, reasoning, and downstream guidance in your final answer. "
            + "When finished, summarize what changed, decisions made, proof produced, files touched, and remaining blockers. "
            + "The graph will move to human review after your response."
        )

    def _run_agent_prompt(self, node: _GraphNode, kind: str, *, compact: bool = False) -> str:
        if kind == "dod_review":
            return self._dod_review_prompt(node, compact=compact)
        upstream = self._neighbor_lines(node.node_id, incoming=True)
        downstream = self._neighbor_lines(node.node_id, incoming=False)
        output_contracts = self._operation_output_contract_lines(node)
        if compact:
            return (
                "Retry this graph operation with minimal context. The previous provider call failed before generation, likely because the prompt/tool context was too large.\n\n"
                f"Operation: {node.token.title}\n"
                f"Description:\n{node.token.detail or '(none)'}\n\n"
                f"Crew: {node.agent_name or 'Coder'}\n\n"
                f"Direct inputs only:\n{self._compact_run_text(upstream or '- None', 1200)}\n\n"
                f"Expected output contracts:\n{self._compact_run_text(output_contracts or '- None', 900)}\n\n"
                "Do only this step. If the available context is insufficient, ask the user one specific question instead of guessing."
            )
        return (
            f"Run this graph operation.\n\n"
            f"Operation: {node.token.title}\n"
            f"Description:\n{node.token.detail or '(none)'}\n\n"
            f"Crew: {node.agent_name or 'Coder'}\n\n"
            f"Inputs:\n{upstream or '- None'}\n\n"
            f"Expected output contracts:\n{output_contracts or '- None'}\n\n"
            f"Expected downstream consumers:\n{downstream or '- None'}\n\n"
            "Execute only this operation. Use tools as needed. Do not mark the graph done yourself."
        )

    def _operation_output_contract_lines(self, node: _GraphNode) -> str:
        contracts = []
        for output in self._operation_output_contract_nodes(node):
            contracts.append(
                f"- Decision: {output.token.title} [{output.status}]\n"
                f"  Contract: {output.token.detail or '(no decision contract detail)'}\n"
                "  Final answer must include the chosen decision, reasoning, and downstream guidance."
            )
        return "\n".join(contracts)

    def _operation_output_contract_nodes(self, node: _GraphNode) -> list[_GraphNode]:
        if node.token.kind != "operation":
            return []
        outputs = []
        for edge in self._edges:
            if edge.source_id != node.node_id or edge.source_port != "decision":
                continue
            target = self._nodes.get(edge.target_id)
            if target is not None and target.token.kind == "decision":
                outputs.append(target)
        return sorted(outputs, key=lambda item: self._node_sort_key(item.node_id))

    def _dod_review_prompt(self, node: _GraphNode, *, compact: bool = False) -> str:
        session = self._run_session
        plan_ids = set(session.plan.node_ids) if session is not None else set(self._nodes)
        accepted = []
        for node_id in sorted(plan_ids, key=self._node_sort_key):
            item = self._nodes.get(node_id)
            if item is None or item.node_id == node.node_id:
                continue
            if item.status == "done":
                accepted.append(self._dod_review_item_summary(item, compact=compact))
                if len(accepted) >= _DOD_REVIEW_MAX_ITEMS:
                    break
        prefix = (
            "Retry this DoD review with summarized context. The previous provider call failed before generation, likely because context was too large.\n\n"
            if compact
            else ""
        )
        prompt = "".join(
            [
                prefix,
                "Review whether this Definition of Done is satisfied.\n\n",
                f"DoD: {node.token.title}\n",
                f"Criteria:\n{self._compact_run_text(node.token.detail or '(none)', 1400)}\n\n",
                "Summarized accepted upstream results:\n",
                "\n".join(accepted) if accepted else "- None",
                "\n\nReturn PASS or NEEDS_CHANGES. If NEEDS_CHANGES, list only concrete missing evidence or changes that should become follow-up graph work. If you cannot judge, ask one specific question.",
            ]
        )
        return self._compact_multiline_text(prompt, _DOD_REVIEW_PROMPT_LIMIT)

    def _dod_review_item_summary(self, item: _GraphNode, *, compact: bool = False) -> str:
        parts = [f"- {item.token.kind}: {item.token.title} [{item.status}]"]
        if item.token.kind in {"context", "decision", "evidence", "scope"} and item.token.detail:
            parts.append(f"  Detail: {self._compact_run_text(item.token.detail, 240 if compact else 360)}")
        output = self._last_run_output(item.node_id, limit=180 if compact else _DOD_REVIEW_ITEM_LIMIT)
        if output and output != item._status_note:
            parts.append(f"  Result: {output}")
        artifact_ref = self._last_run_artifact_ref(item.node_id)
        if artifact_ref:
            parts.append(f"  Artifact: {artifact_ref}")
        return "\n".join(parts)

    def _neighbor_lines(self, node_id: int, *, incoming: bool) -> str:
        lines: list[str] = []
        for edge in self._edges:
            if incoming and edge.target_id != node_id:
                continue
            if not incoming and edge.source_id != node_id:
                continue
            other_id = edge.source_id if incoming else edge.target_id
            other = self._nodes.get(other_id)
            if other is None:
                continue
            line = (
                f"- {edge.kind}: {other.token.kind} '{other.token.title}' [{other.status}]"
                + (f" - {other.token.detail}" if other.token.detail else "")
            )
            if incoming and other.status == "done":
                result = self._last_run_output(other.node_id)
                if result and result != other._status_note:
                    line += f"\n  Result: {result}"
                artifact_ref = self._last_run_artifact_ref(other.node_id)
                if artifact_ref:
                    line += f"\n  Artifact: {artifact_ref}"
            lines.append(line)
        return "\n".join(lines)

    def _last_run_output(self, node_id: int, *, limit: int = 500) -> str:
        for attempt in reversed(self._node_run_history.get(node_id, [])):
            content = str(attempt.get("content") or "").strip()
            if content:
                return self._compact_run_text(content, limit)
        node = self._nodes.get(node_id)
        return node._status_note if node is not None else ""

    def _last_run_artifact_ref(self, node_id: int) -> str:
        for attempt in reversed(self._node_run_history.get(node_id, [])):
            artifact_ref = str(attempt.get("artifact_ref") or "").strip()
            content = str(attempt.get("content") or "").strip()
            if artifact_ref and content and str(attempt.get("status") or "") in {"done", "review"}:
                return artifact_ref
        return ""

    def _latest_run_conversation_id(self, node_id: int) -> str:
        for attempt in reversed(self._node_run_history.get(node_id, [])):
            conv_id = str(attempt.get("conversation_id") or "").strip()
            if conv_id:
                return conv_id
        return ""

    def _run_attempt(self, node_id: int, attempt_id: str) -> dict | None:
        for attempt in self._node_run_history.get(node_id, []):
            if attempt.get("id") == attempt_id:
                return attempt
        return None

    def _conversation_run_attempt(self, run_id: str) -> tuple[int, str] | None:
        return self._conversation_run_nodes.get(str(run_id or ""))

    def _on_conversation_run_chunk(self, _conv_id: str, run_id: str, text: str):
        mapped = self._conversation_run_attempt(run_id)
        if mapped is None:
            return
        self.conversation_chunk.emit(str(_conv_id or ""), str(text or ""))
        node_id, attempt_id = mapped
        self._on_run_agent_chunk(node_id, attempt_id, text)

    def _on_conversation_run_tool_called(self, _conv_id: str, run_id: str, name: str, inputs: dict):
        mapped = self._conversation_run_attempt(run_id)
        if mapped is None:
            return
        self.conversation_tool_called.emit(str(_conv_id or ""), str(name or "tool"), dict(inputs or {}))
        node_id, attempt_id = mapped
        self._on_run_agent_tool_called(node_id, attempt_id, name, inputs)

    def _on_conversation_run_tool_result(self, _conv_id: str, run_id: str, name: str, output: str):
        mapped = self._conversation_run_attempt(run_id)
        if mapped is None:
            return
        self.conversation_tool_result.emit(str(_conv_id or ""), str(name or "tool"), str(output or ""))
        node_id, attempt_id = mapped
        self._on_run_agent_tool_result(node_id, attempt_id, name, output)

    def _on_conversation_run_approval_required(self, _conv_id: str, run_id: str, approval_bus, pending):
        mapped = self._conversation_run_attempt(run_id)
        if mapped is not None:
            node_id, _attempt_id = mapped
            node = self._nodes.get(node_id)
            if node is not None:
                tool_name = str(getattr(pending, "tool_name", "") or getattr(pending, "kind", "") or "tool")
                node.set_status("running", f"approval required: {tool_name}")
                self._active_node_id = node.node_id
                self._refresh_edges()
                self._sync_counts()
                if self._selected_node() is node:
                    self._populate_inspector(node)
                self._set_run_attention(True)
                self._set_mode(f"{node.token.title} needs approval")
        handle_pending_approval(self, approval_bus, pending)

    def _on_conversation_run_done(self, _conv_id: str, run_id: str, text: str):
        mapped = self._conversation_run_attempt(run_id)
        if mapped is None:
            return
        self._set_run_attention(False)
        node_id, attempt_id = mapped
        self._finish_node_attempt(node_id, attempt_id, text)

    def _on_conversation_run_error(self, _conv_id: str, run_id: str, message: str):
        mapped = self._conversation_run_attempt(run_id)
        if mapped is None:
            return
        self._set_run_attention(False)
        node_id, attempt_id = mapped
        self._finish_node_attempt(node_id, attempt_id, message, error=True)

    def _on_conversation_run_finished(self, _conv_id: str, run_id: str):
        self._conversation_run_nodes.pop(str(run_id or ""), None)
        self.conversation_run_finished.emit(str(_conv_id or ""))

    def _on_run_agent_chunk(self, node_id: int, attempt_id: str, text: str):
        attempt = self._run_attempt(node_id, attempt_id)
        if attempt is None:
            return
        attempt["content"] = str(attempt.get("content") or "") + str(text or "")
        if not bool(attempt.get("conversation_canonical")):
            self._schedule_run_conversation_save(node_id, attempt_id)
        self._schedule_run_chat_render()

    def _schedule_run_chat_render(self):
        self._run_chat_render_pending = True
        if not self._run_chat_render_timer.isActive():
            self._run_chat_render_timer.start()

    def _schedule_run_conversation_save(self, node_id: int, attempt_id: str):
        self._pending_run_conversation_saves.add((node_id, str(attempt_id or "")))
        if not self._run_conversation_save_timer.isActive():
            self._run_conversation_save_timer.start()

    def _flush_run_conversation_saves(self):
        pending = list(self._pending_run_conversation_saves)
        self._pending_run_conversation_saves.clear()
        for node_id, attempt_id in pending:
            node = self._nodes.get(node_id)
            attempt = self._run_attempt(node_id, attempt_id)
            if node is not None and attempt is not None:
                self._save_run_conversation(node, attempt)

    def _flush_run_chat_render(self):
        if not self._run_chat_render_pending:
            return
        self._run_chat_render_pending = False
        self._render_graph_chat()

    def _on_run_agent_tool_called(self, node_id: int, attempt_id: str, name: str, inputs: dict):
        attempt = self._run_attempt(node_id, attempt_id)
        if attempt is None:
            return
        node = self._nodes.get(node_id)
        self._set_run_attention(False)
        name = str(name or "tool")
        summary = self._tool_summary(name, inputs)
        attempt.setdefault("tools", []).append(
            {
                "tool_use_id": f"canvas_{self._safe_artifact_segment(attempt_id)}_{len(attempt.get('tools') or [])}",
                "name": name,
                "status": "running",
                "summary": summary,
                "inputs": self._tool_detail_text(inputs),
                "output": "",
            }
        )
        if name == "edit_file":
            self._run_last_edit_path = str((inputs or {}).get("path") or "")
        self._set_mode(f"run using {name}")
        if node is not None:
            self._save_run_conversation(node, attempt)
        self._render_graph_chat()

    def _on_run_agent_tool_result(self, node_id: int, attempt_id: str, name: str, output: str):
        attempt = self._run_attempt(node_id, attempt_id)
        if attempt is None:
            return
        node = self._nodes.get(node_id)
        tools = attempt.setdefault("tools", [])
        for tool in reversed(tools):
            if tool.get("name") == name and tool.get("status") == "running":
                output_text = str(output or "")
                failed = output_text.startswith("[tool error]")
                tool["status"] = "failed" if failed else "done"
                tool["output"] = self._tool_detail_text(output_text)
                if failed:
                    tool["summary"] = self._tool_result_summary(output_text) or str(tool.get("summary") or "")
                break
        recorded_file_activity = False
        if name == "edit_file" and self._run_last_edit_path and not str(output or "").startswith("[tool error]"):
            attempt.setdefault("touched_files", []).append(self._run_last_edit_path)
            self._record_run_file_activity(node_id, self._run_last_edit_path)
            self._run_last_edit_path = ""
            recorded_file_activity = True
        elif name == "edit_file":
            self._run_last_edit_path = ""
        if not recorded_file_activity:
            self._set_mode(f"run finished {name}")
        if node is not None:
            self._save_run_conversation(node, attempt)
        self._render_graph_chat()

    @staticmethod
    def _tool_summary(name: str, inputs: dict | None) -> str:
        inputs = inputs if isinstance(inputs, dict) else {}
        for key in ("path", "query", "command", "url"):
            value = str(inputs.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _tool_result_summary(output: str) -> str:
        text = str(output or "").strip()
        if text.startswith("[tool error]"):
            text = text[len("[tool error]") :].strip()
        return AgentCanvasPanel._compact_run_text(text, 220)

    @staticmethod
    def _tool_detail_text(value: object, limit: int = 6000) -> str:
        if isinstance(value, (dict, list)):
            try:
                text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(value)
        else:
            text = str(value or "")
        text = text.strip()
        if len(text) > limit:
            return text[: max(0, limit - 1)] + "..."
        return text

    def _record_run_file_activity(self, operation_id: int, path: str):
        ref = self._relative_ref(path)
        operation = self._nodes.get(operation_id)
        scope = self._run_file_scope_node(operation, ref)
        if scope is None:
            base = operation.pos() if operation is not None else self._next_spawn_point()
            offset = sum(1 for node in self._nodes.values() if node.token.kind == "scope") * 34
            scope = self._create_node(
                CanvasToken("scope", "Changed file", ref),
                base + QPointF(300, -80 + offset),
            )
        else:
            self._append_changed_scope_ref(scope, ref)
        scope.set_status("changed", ref)
        if operation is not None and not any(
            edge.source_id == scope.node_id and edge.target_id == operation.node_id
            and edge.source_port == "read"
            for edge in self._edges
        ):
            self.connect_nodes(scope.node_id, operation.node_id, "read")
        scope_goal_id = self._run_scope_goal_id(operation_id)
        if scope_goal_id is not None:
            self._autoformat_graph(scope_goal_id=scope_goal_id)
        else:
            self._autoformat_graph()
        self._set_mode(f"agent touched: {ref}; autoformatted active graph")
        self._notify_graph_changed()

    def _run_file_scope_node(self, operation: _GraphNode | None, ref: str) -> _GraphNode | None:
        if operation is not None:
            connected_scopes = [
                self._nodes[edge.source_id]
                for edge in self._edges
                if edge.target_id == operation.node_id
                and edge.source_id in self._nodes
                and self._nodes[edge.source_id].token.kind == "scope"
                and edge.source_port == "read"
            ]
            for scope in connected_scopes:
                if scope.status == "changed" or scope.token.title.lower().startswith("changed"):
                    return scope
            if connected_scopes:
                return connected_scopes[0]
        return self._find_scope_node(ref)

    def _append_changed_scope_ref(self, scope: _GraphNode, ref: str):
        refs = self._scope_refs(scope.token.detail)
        normalized = self._normalize_scope_ref(ref).casefold()
        if not any(self._normalize_scope_ref(existing).casefold() == normalized for existing in refs):
            refs.append(ref)
        if not refs:
            refs = [ref]
        title = "Changed file" if len(refs) == 1 else f"{len(refs)} changed files"
        scope.set_token(CanvasToken("scope", title, "\n".join(refs)))

    def _run_scope_goal_id(self, operation_id: int) -> int | None:
        if self._run_session is not None and operation_id in self._run_session.plan.node_ids:
            return self._run_session.plan.start_node_id
        return self._owning_goal_id(operation_id)

    def _finish_node_attempt(self, node_id: int, attempt_id: str, text: str, *, error: bool = False):
        node = self._nodes.get(node_id)
        attempt = self._run_attempt(node_id, attempt_id)
        if node is None or attempt is None:
            return
        if self._run_session is not None:
            self._run_session.running_node_ids.discard(node_id)
        final = str(text or "").strip()
        if final:
            attempt["content"] = final
        if final and not error:
            self._write_run_artifact(node, attempt, final)
        attempt["status"] = "error" if error else "review"
        self._save_run_conversation(node, attempt, final=True)
        node.set_status("blocked" if error else "review", "provider error; retry or add guidance" if error else "awaiting acceptance")
        self._active_node_id = node.node_id
        self._select_node(node)
        self._refresh_edges()
        self._sync_counts()
        if self._selected_node() is node:
            self._populate_inspector(node)
        self._set_mode(
            f"{node.token.title} needs retry or guidance" if error else f"{node.token.title} ready for review"
        )
        self._render_graph_chat()
        self._notify_graph_changed()
        if not error and self._should_auto_approve_action(node):
            self._accept_run_node(node, advance=True)
            return
        if error or self._canvas_run_mode() == "parallel":
            self._advance_run_after_status_change()

    def _stop_running_node_attempts(self, node_ids=None, *, reason: str) -> bool:
        changed = False
        target_ids = list(node_ids) if node_ids is not None else list(self._node_run_history)
        for raw_node_id in target_ids:
            try:
                node_id = int(raw_node_id)
            except (TypeError, ValueError):
                continue
            node = self._nodes.get(node_id)
            for attempt in self._node_run_history.get(node_id, []):
                if str(attempt.get("status") or "") != "running":
                    continue
                attempt["status"] = "stopped"
                if not str(attempt.get("content") or "").strip():
                    attempt["content"] = reason
                if node is not None:
                    self._save_run_conversation(node, attempt, final=True)
                changed = True
        return changed

    def _write_run_artifact(self, node: _GraphNode, attempt: dict, content: str):
        artifact_ref = str(attempt.get("artifact_ref") or "").strip()
        if not artifact_ref:
            artifact_ref = self._run_artifact_ref(node, attempt)
            attempt["artifact_ref"] = artifact_ref
        attempt.setdefault("artifact_title", f"{node.token.title} output")
        path = Path(self._repo_root).expanduser().resolve() / artifact_ref
        body = self._run_artifact_markdown(node, attempt, content)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        except OSError as exc:
            attempt["artifact_error"] = str(exc)[:500]

    def _run_artifact_ref(self, node: _GraphNode, attempt: dict) -> str:
        attempt_id = self._safe_artifact_segment(str(attempt.get("id") or uuid4().hex))
        filename = f"node_{node.node_id}_{attempt_id}.md"
        path = canvas_artifacts_dir(self._repo_root) / filename
        try:
            return path.resolve().relative_to(Path(self._repo_root).expanduser().resolve()).as_posix()
        except ValueError:
            return f".aichs/canvas/default/artifacts/{filename}"

    @staticmethod
    def _safe_artifact_segment(value: str) -> str:
        safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})
        return safe[:80] or uuid4().hex

    @staticmethod
    def _run_artifact_markdown(node: _GraphNode, attempt: dict, content: str) -> str:
        title = node.token.title.strip() or f"Node {node.node_id}"
        role = str(attempt.get("role") or "Agent").strip() or "Agent"
        started = str(attempt.get("started_at") or "").strip()
        header = [
            f"# {title}",
            "",
            f"- Node: {node.node_id}",
            f"- Type: {node.token.kind}",
            f"- Role: {role}",
        ]
        if started:
            header.append(f"- Run started: {started}")
        header.extend(["", "---", ""])
        return "\n".join(header) + str(content or "").strip() + "\n"

    def _on_run_agent_finished(self, thread: ChatThread):
        finished_node_ids: list[int] = []
        for node_id, running_thread in list(self._run_threads.items()):
            if running_thread is thread:
                finished_node_ids.append(node_id)
                self._run_threads.pop(node_id, None)
        if self._run_thread is thread:
            self._run_thread = next(iter(self._run_threads.values()), None)
            self._run_thread_attempt_id = ""
            self._run_last_edit_path = ""
        if finished_node_ids and self._stop_running_node_attempts(
            finished_node_ids,
            reason="Run thread finished before the provider returned a result.",
        ):
            for node_id in finished_node_ids:
                if self._run_session is not None:
                    self._run_session.running_node_ids.discard(node_id)
                node = self._nodes.get(node_id)
                if node is not None and node.status == "running":
                    node.set_status("blocked", "run stopped")
            self._advance_run_after_status_change()
            self._notify_graph_changed()
        else:
            self._sync_run_controls()
            self._render_graph_chat()

    def _accept_selected_run_node(self):
        node = self._selected_run_history_node()
        self._accept_run_node(node)

    def _accept_run_node(self, node: _GraphNode | None, *, advance: bool = True):
        if node is None or node.status != "review":
            return
        latest_attempt = self._node_run_history.get(node.node_id, [])
        attempt = latest_attempt[-1] if latest_attempt else None
        is_dod = node.token.kind == "dod"
        status_note = "approved"
        if is_dod and attempt is not None:
            attempt["artifact_title"] = "DoD acceptance evidence"
            review_output = str(attempt.get("content") or "").strip()
            if not review_output:
                review_output = "No explicit DoD review output was captured before approval."
            artifact_body = (
                "## DoD acceptance\n\n"
                f"Decision: approved\n\n"
                f"Review summary:\n\n{review_output}"
            )
            self._write_run_artifact(node, attempt, artifact_body)
            status_note = "approved; project evidence saved"
        node.set_status("done", status_note)
        self._mark_latest_attempt_status(node.node_id, "done")
        if attempt is not None:
            self._save_run_conversation(node, attempt, final=True)
        if not is_dod:
            self._apply_operation_output_contracts(node, attempt)
        if self._run_session is not None and self._run_session.running_node_id == node.node_id:
            self._run_session.running_node_id = None
        if self._run_session is not None:
            self._run_session.running_node_ids.discard(node.node_id)
        self._active_node_id = None
        self._refresh_edges()
        self._sync_counts()
        self._populate_inspector(node)
        self._set_mode(f"accepted {node.token.title}")
        self._render_graph_chat()
        self._notify_graph_changed()
        if advance:
            self._advance_run_after_status_change()

    def _should_auto_approve_action(self, node: _GraphNode) -> bool:
        if node.token.kind != "operation":
            return False
        mode = self._canvas_action_auto_approve()
        if mode == "all":
            return True
        if mode == "coder":
            return str(node.agent_id or "").strip().lower() == "coder"
        return False

    def _apply_operation_output_contracts(self, node: _GraphNode, attempt: dict | None) -> int:
        if node.token.kind != "operation":
            return 0
        outputs = self._operation_output_contract_nodes(node)
        if not outputs:
            return 0
        content = str((attempt or {}).get("content") or "").strip()
        if not content:
            content = "The producing operation was approved, but no explicit decision output was captured."
        artifact_ref = str((attempt or {}).get("artifact_ref") or "").strip()
        artifact_title = str((attempt or {}).get("artifact_title") or f"{node.token.title} output").strip()
        produced = 0
        for output in outputs:
            contract = self._decision_contract_text(output.token.detail)
            detail = (
                f"Decision contract:\n{contract or '(none)'}\n\n"
                f"Produced by operation '{node.token.title}' after approval:\n{content}"
            )
            output.set_token(CanvasToken(output.token.kind, output.token.title, detail))
            output.set_status("done", f"produced by {node.token.title}")
            decision_attempt = {
                "id": uuid4().hex,
                "kind": "decision_output",
                "role": node.agent_name or "Agent",
                "status": "done",
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "prompt": f"Produced by operation {node.node_id}: {node.token.title}",
                "content": content,
                "artifact_ref": artifact_ref,
                "artifact_title": artifact_title,
                "tools": [],
                "touched_files": list((attempt or {}).get("touched_files") or []),
            }
            history = self._node_run_history.setdefault(output.node_id, [])
            if history and str(history[-1].get("content") or "").strip() == content:
                history[-1] = decision_attempt
            else:
                history.append(decision_attempt)
            produced += 1
        return produced

    @staticmethod
    def _decision_contract_text(detail: str) -> str:
        text = str(detail or "").strip()
        marker = "Produced by operation "
        if marker in text and text.startswith("Decision contract:"):
            text = text.split(marker, 1)[0].strip()
            text = text.removeprefix("Decision contract:").strip()
        return text

    def _rerun_selected_run_node(self):
        node = self._selected_run_history_node()
        if node is None or node.token.kind not in {"operation", "dod"} or self._is_run_agent_running():
            return
        session = self._run_session
        if session is None:
            owner_id = self._owning_goal_id(node.node_id)
            try:
                plan = self._run_engine.compile(self.graph_state(), owner_id) if owner_id is not None else None
            except GraphRunError:
                plan = None
            if plan is not None:
                session = GraphRunSession(plan=plan)
                self._run_session = session
        if session is not None:
            session.running_node_id = node.node_id
            session.running_node_ids.add(node.node_id)
        compact_retry = self._latest_run_attempt_status(node.node_id) == "error"
        self._set_active_node(node, "running", "retrying" if compact_retry else "rerunning")
        self._start_node_agent(node, kind="dod_review" if node.token.kind == "dod" else "operation", compact_retry=compact_retry)

    def _add_guidance_to_selected_run_node(self):
        node = self._selected_run_history_node()
        if node is None or node.status not in {"review", "blocked"}:
            return
        was_review = node.status == "review"
        text, ok = QInputDialog.getMultiLineText(
            self,
            "Needs changes",
            f"Tell the agent what to change or validate for '{node.token.title}':",
            "",
        )
        guidance = str(text or "").strip()
        if not ok or not guidance:
            self._set_mode("guidance cancelled")
            return
        latest_attempts = self._node_run_history.get(node.node_id, [])
        attempt = latest_attempts[-1] if latest_attempts else None
        if was_review:
            node.set_status("blocked", "needs changes")
            self._mark_latest_attempt_status(node.node_id, "blocked")
            self._active_node_id = None
        if attempt is not None:
            attempt.setdefault("guidance", []).append(
                {
                    "content": guidance,
                    "created_at": datetime.now().isoformat(),
                }
            )
            self._save_run_conversation(node, attempt, final=True)
        selection_after_guidance = node
        if node.token.kind == "dod":
            fix_node = self._create_dod_fix_action(node, guidance)
            if fix_node is None:
                return
            selection_after_guidance = fix_node
        else:
            guidance_node = self._create_node(
                CanvasToken("context", f"Guidance for {node.token.title}", guidance),
                node.pos() + QPointF(-300, 120),
            )
            self.connect_nodes(guidance_node.node_id, node.node_id, "context")
        if node.status == "blocked":
            node.set_status("idle", "guidance added; ready to retry")
            for goal_id in self._owning_goal_ids(node.node_id):
                goal = self._nodes.get(goal_id)
                if goal is not None and goal.status == "blocked" and goal._status_note == "run blocked":
                    goal.set_status("idle", "guidance added; ready to resume")
        scope_goal_id = self._owning_goal_id(node.node_id)
        if scope_goal_id is not None:
            self._autoformat_graph(scope_goal_id=scope_goal_id)
        self._refresh_edges()
        self._sync_counts()
        self._populate_inspector(selection_after_guidance)
        self._set_mode(f"needs changes added for {node.token.title}")
        self._select_node(selection_after_guidance)
        if was_review:
            self._advance_run_after_status_change()
        self._render_graph_chat()
        self._notify_graph_changed()

    def _extend_graph_from_selected_review(self):
        node = self._selected_run_history_node()
        if node is None or node.token.kind != "dod" or self._is_graph_agent_running():
            return
        review = self._last_run_output(node.node_id).strip()
        if not review:
            self._set_mode("no DoD review output to extend")
            return
        fix_node = self._create_dod_fix_action(node, review)
        if fix_node is None:
            return
        self._mark_latest_attempt_status(node.node_id, "blocked")
        self._append_graph_chat_message(
            "Graph Agent",
            f"Extended DoD review into corrective action: {fix_node.token.title}.",
        )
        self._render_graph_chat()
        self._notify_graph_changed()

    def _create_dod_fix_action_tool(self, inputs: dict, *, scope_goal_id: int | None = None) -> dict:
        try:
            dod_id = int(inputs.get("dod_node_id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "create_dod_fix_action requires dod_node_id."}
        changes = str(inputs.get("changes") or "").strip()
        if not changes:
            return {"ok": False, "error": "create_dod_fix_action requires changes."}
        dod = self._nodes.get(dod_id)
        if dod is None or dod.token.kind != "dod":
            return {"ok": False, "error": "create_dod_fix_action only works on DoD nodes."}
        if scope_goal_id is not None and dod_id not in self._scope_node_ids_for_goal(scope_goal_id):
            return {"ok": False, "error": "DoD is outside the active goal scope."}
        source_action_id = None
        if inputs.get("source_action_id") is not None:
            try:
                source_action_id = int(inputs.get("source_action_id"))
            except (TypeError, ValueError):
                return {"ok": False, "error": "source_action_id must be an operation node id."}
        fix_node = self._create_dod_fix_action(dod, changes, source_action_id=source_action_id)
        if fix_node is None:
            return {"ok": False, "error": "Could not create a DoD fix action. Connect an action into the DoD first."}
        self._graph_agent_applied_patches += 1
        self._autoformat_graph(scope_goal_id=scope_goal_id or self._owning_goal_id(dod.node_id))
        return {
            "ok": True,
            "summary": f"Created corrective action before DoD: {fix_node.token.title}.",
            "node_id": fix_node.node_id,
            "dod_node_id": dod.node_id,
        }

    def _create_dod_fix_action(
        self,
        dod: _GraphNode,
        changes: str,
        *,
        source_action_id: int | None = None,
    ) -> _GraphNode | None:
        if dod.token.kind != "dod":
            return None
        source_action = self._dod_fix_source_action(dod, source_action_id=source_action_id)
        if source_action is None:
            self._set_mode("connect an action into the DoD before requesting changes")
            return None
        title = self._dod_fix_action_title(changes)
        detail = self._dod_fix_action_detail(dod, changes)
        fix_node = self._create_node(
            CanvasToken("operation", title, detail),
            QPointF((source_action.pos().x() + dod.pos().x()) / 2.0, dod.pos().y() + 120),
        )
        fix_node.set_agent("coder", "Coder")
        for edge in list(self._edges):
            if (
                edge.source_id == source_action.node_id
                and edge.target_id == dod.node_id
                and edge.source_port == "implement"
            ):
                self._remove_edge(edge)
        if not self.connect_nodes(source_action.node_id, fix_node.node_id, "implement"):
            self._delete_node(fix_node)
            self._set_mode("could not connect corrective action to previous action")
            return None
        if not self.connect_nodes(fix_node.node_id, dod.node_id, "implement"):
            self._delete_node(fix_node)
            self._set_mode("could not connect corrective action to DoD")
            return None
        dod.set_status("idle", "needs changes; fix action added")
        fix_node.set_status("idle", "from DoD review")
        self._active_node_id = None
        goal_id = self._owning_goal_id(dod.node_id)
        self._refresh_run_session_plan(goal_id)
        if self._run_session is not None:
            self._run_session.running_node_ids.discard(dod.node_id)
            if self._run_session.running_node_id == dod.node_id:
                self._run_session.running_node_id = None
        if goal_id is not None:
            self._autoformat_graph(scope_goal_id=goal_id)
        self._refresh_edges()
        self._sync_counts()
        self._populate_inspector(dod)
        self._select_node(fix_node)
        if fix_node.token.title != title or fix_node.token.detail != detail:
            fix_node.set_token(CanvasToken("operation", title, detail))
            fix_node.set_agent("coder", "Coder")
            self._populate_inspector(fix_node)
        self._set_mode(f"created fix action for {dod.token.title}")
        return fix_node

    def _dod_fix_source_action(self, dod: _GraphNode, *, source_action_id: int | None = None) -> _GraphNode | None:
        if source_action_id is not None:
            source = self._nodes.get(source_action_id)
            return source if source is not None and source.token.kind == "operation" else None
        direct_sources = []
        for edge in self._edges:
            if edge.target_id != dod.node_id:
                continue
            source = self._nodes.get(edge.source_id)
            if source is not None and source.token.kind == "operation":
                direct_sources.append(source)
        if direct_sources:
            return sorted(direct_sources, key=lambda node: (float(node.pos().x()), float(node.pos().y()), node.node_id))[-1]
        evidence_sources = []
        for edge in self._edges:
            if edge.target_id != dod.node_id:
                continue
            evidence = self._nodes.get(edge.source_id)
            if evidence is None or evidence.token.kind != "evidence":
                continue
            for upstream in self._edges:
                if upstream.target_id != evidence.node_id:
                    continue
                source = self._nodes.get(upstream.source_id)
                if source is not None and source.token.kind == "operation":
                    evidence_sources.append(source)
        if evidence_sources:
            return sorted(evidence_sources, key=lambda node: (float(node.pos().x()), float(node.pos().y()), node.node_id))[-1]
        goal_id = self._owning_goal_id(dod.node_id)
        if goal_id is None:
            return None
        try:
            plan = self._run_engine.compile(self.graph_state(), goal_id)
        except GraphRunError:
            return None
        for node_id in reversed(plan.ordered_operation_ids):
            node = self._nodes.get(node_id)
            if node is not None:
                return node
        return None

    @staticmethod
    def _dod_fix_action_title(changes: str) -> str:
        first_line = " ".join(str(changes or "").strip().splitlines()[:1]).strip()
        if not first_line:
            return "Address DoD review"
        return ("Address: " + first_line)[:80]

    @staticmethod
    def _dod_fix_action_detail(dod: _GraphNode, changes: str) -> str:
        return (
            "Address the DoD review feedback before this acceptance gate can pass.\n\n"
            "Requested changes:\n"
            f"{str(changes or '').strip()}\n\n"
            "DoD acceptance target:\n"
            f"{dod.token.detail.strip() or dod.token.title.strip()}"
        )

    def _refresh_run_session_plan(self, goal_id: int | None):
        if goal_id is None or self._run_session is None:
            return
        try:
            plan = self._run_engine.compile(self.graph_state(), goal_id)
        except GraphRunError:
            return
        self._run_session.plan = plan
        self._run_session.running_node_ids = {
            node_id
            for node_id in self._run_session.running_node_ids
            if node_id in plan.node_ids
            and self._nodes.get(node_id) is not None
            and self._nodes[node_id].status == "running"
        }
        running = (
            self._nodes.get(self._run_session.running_node_id)
            if self._run_session.running_node_id is not None
            else None
        )
        if (
            self._run_session.running_node_id not in plan.node_ids
            or running is None
            or running.status not in {"running", "review"}
        ):
            self._run_session.running_node_id = None

    def _mark_latest_attempt_status(self, node_id: int, status: str):
        history = self._node_run_history.get(node_id, [])
        if history:
            history[-1]["status"] = status

    def _latest_run_attempt_status(self, node_id: int) -> str:
        history = self._node_run_history.get(node_id, [])
        if not history:
            return ""
        return str(history[-1].get("status") or "")

    def _prepare_run_restart(self, plan):
        for node_id in plan.node_ids:
            node = self._nodes.get(node_id)
            if node is None:
                continue
            if node.token.kind == "operation" and node.status == "done":
                history = self._node_run_history.get(node.node_id, [])
                if history:
                    self._apply_operation_output_contracts(node, history[-1])
            if node.token.kind == "goal" and node.status == "blocked" and node._status_note == "run blocked":
                node.set_status("queued", "retrying branch")
                continue
            if node.status == "blocked" and self._latest_run_attempt_status(node.node_id) == "error":
                node.set_status("idle", "retryable after provider error")

    def _owning_goal_id(self, node_id: int) -> int | None:
        goals = self._owning_goal_ids(node_id)
        return goals[0] if goals else None

    def _owning_goal_ids(self, node_id: int) -> list[int]:
        owners: list[int] = []
        for goal in self._root_goal_nodes():
            try:
                plan = self._run_engine.compile(self.graph_state(), goal.node_id)
            except GraphRunError:
                continue
            if node_id in plan.node_ids:
                owners.append(goal.node_id)
        for node in self._nodes.values():
            if node.token.kind != "goal":
                continue
            try:
                plan = self._run_engine.compile(self.graph_state(), node.node_id)
            except GraphRunError:
                continue
            if node_id in plan.node_ids:
                owners.append(node.node_id)
        deduped: list[int] = []
        for owner in owners:
            if owner not in deduped:
                deduped.append(owner)
        return deduped

    def _start_connect_selected(self):
        selected = self._selected_nodes()
        if len(selected) >= 2:
            self.connect_nodes(selected[0].node_id, selected[1].node_id)
            self._set_mode("connected selected nodes")
            return
        node = selected[0] if selected else self._selected_node()
        if node is None:
            self._set_mode("select a node first")
            return
        self._connect_anchor = node
        self._set_mode(f"connect from {node.token.title}")

    def delete_selected(self):
        selected_nodes = self._selected_nodes()
        selected_frames = [item for item in self._scene.selectedItems() if isinstance(item, _GraphFrame)]
        selected_edge_items = {
            item for item in self._scene.selectedItems() if isinstance(item, _GraphEdge)
        }
        if not selected_nodes and not selected_edge_items and not selected_frames:
            return
        if selected_frames and not selected_nodes and not selected_edge_items:
            self._set_mode("graph frame is layout; select nodes or connections to delete")
            return
        if selected_nodes and not self._confirm_delete_nodes(selected_nodes):
            self._set_mode("delete cancelled")
            return
        for node in selected_nodes:
            self._delete_node(node)
        for frame in selected_frames:
            frame.setSelected(False)
        for edge in list(self._edges):
            if edge.item in selected_edge_items:
                self._remove_edge(edge)
        self._sync_counts()
        if not self._nodes:
            self._populate_empty_inspector()
        self._set_mode("deleted selection")
        self._notify_graph_changed()

    def _confirm_delete_nodes(self, nodes: list[_GraphNode]) -> bool:
        count = len(nodes)
        if count == 1:
            title = nodes[0].token.title
            text = f"Delete '{title}' and its connections?"
        else:
            text = f"Delete {count} nodes and their connections?"
        answer = QMessageBox.question(
            self,
            "Delete node",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _delete_node(self, node: _GraphNode):
        incident_edges = [edge for edge in self._edges if edge.source_id == node.node_id or edge.target_id == node.node_id]
        for edge in incident_edges:
            self._remove_edge(edge)
        self._nodes.pop(node.node_id, None)
        self._node_run_history.pop(node.node_id, None)
        self._scene.removeItem(node)
        if self._last_selected_node_id == node.node_id:
            self._last_selected_node_id = None
        if self._connect_anchor is node:
            self._connect_anchor = None
        if self._active_node_id == node.node_id:
            self._active_node_id = None
        self._sync_root_goal()
        self._apply_goal_collapse_visibility()
        self._sync_graph_frames()
        self._notify_graph_changed()

    def _remove_edge(self, edge: CanvasEdge):
        self._edges = [item for item in self._edges if item is not edge]
        self._remove_edge_adjacency(edge.source_id, edge.target_id)
        self._scene.removeItem(edge.item)
        self._refresh_goal_subgoal_children(edge.source_id)
        self._refresh_goal_subgoal_children(edge.target_id)
        self._sync_root_goal()
        self._apply_goal_collapse_visibility()
        self._sync_graph_frames()
        self._notify_graph_changed()

    def _show_node_menu(self, node: _GraphNode, screen_pos):
        menu = QMenu(self)
        if node.node_id in self._auto_compacted_goal_ids:
            delete_action = menu.addAction("Delete")
            chosen = menu.exec(screen_pos)
            if chosen == delete_action:
                self._delete_nodes_from_menu(node)
            return
        open_file_action = None
        if self._can_open_scope(node):
            open_file_action = menu.addAction("Open Path")
        open_chat_action = None
        conversation_id = self._latest_run_conversation_id(node.node_id)
        if conversation_id:
            open_chat_action = menu.addAction("Open Chat")
            open_chat_action.setToolTip("Open the linked chat conversation for this canvas action.")
        if open_file_action is not None or open_chat_action is not None:
            menu.addSeparator()
        create_actions = {}
        for creation in self._all_creation_actions(node.token.kind):
            action = menu.addAction(f"Create: {creation.title}")
            action.setToolTip(creation.detail)
            create_actions[action] = creation
        breakdown_action = None
        collapse_action = None
        if node.token.kind == "goal":
            breakdown_action = menu.addAction("Split into goals")
            collapse_action = menu.addAction("Expand goal" if node.is_collapsed() else "Collapse goal")
        if create_actions or breakdown_action is not None:
            menu.addSeparator()
        pause_action = None
        resume_action = None
        stop_action = None
        if node.token.kind == "goal" and node.status == "running":
            pause_action = menu.addAction("Pause")
            stop_action = menu.addAction("Stop")
            menu.addSeparator()
        elif node.token.kind == "goal" and node.status == "paused":
            resume_action = menu.addAction("Run")
            stop_action = menu.addAction("Stop")
            menu.addSeparator()
        elif node.token.kind == "goal" and node.status == "blocked":
            resume_action = menu.addAction("Resume Run")
            menu.addSeparator()
        accept_action = None
        needs_changes_action = None
        rerun_action = None
        extend_action = None
        label = node.token.title or "selected node"
        attempt_count = len(self._node_run_history.get(node.node_id, []))
        latest_attempt = self._latest_run_attempt_status(node.node_id)
        attempt_context = (
            f"Attempt {attempt_count}" + (f" (last='{latest_attempt}')" if latest_attempt else "")
        )
        if node.token.kind in {"operation", "dod"} and node.status in {"review", "blocked"}:
            accept_action = menu.addAction("Approve DoD" if node.token.kind == "dod" else "Approve result")
            accept_action.setToolTip(
                f"{attempt_context}: Approve '{label}' and advance downstream run state."
                if node.token.kind != "dod"
                else f"{attempt_context}: Approve '{label}' as DoD, save acceptance evidence, and complete the branch acceptance gate."
            )
            needs_changes_action = menu.addAction("Needs changes")
            needs_changes_action.setToolTip(
                f"{attempt_context}: Pause '{label}', mark it blocked, and add the correction needed before continuing."
            )
        if node.token.kind in {"operation", "dod"} and node.status in {"review", "blocked", "done", "idle"}:
            if node.token.kind == "dod":
                label = "Re-evaluate DoD"
            else:
                label = "Retry step" if self._latest_run_attempt_status(node.node_id) == "error" else "Rerun step"
            rerun_action = menu.addAction(label)
            if self._latest_run_attempt_status(node.node_id) == "error":
                rerun_action.setToolTip(
                    f"{attempt_context}: Retry '{node.token.title or 'selected node'}' with compact context after a provider failure."
                )
            else:
                rerun_action.setToolTip(
                    f"{attempt_context}: Re-run '{node.token.title or 'selected node'}' and create a fresh attempt."
                )
        if node.token.kind == "dod" and node.status in {"review", "blocked"}:
            extend_action = menu.addAction("Extend from review")
            extend_action.setToolTip(
                f"{attempt_context}: Ask the graph agent for follow-up work from review of '{node.token.title or 'selected node'}'."
            )
        if any(action is not None for action in (accept_action, needs_changes_action, rerun_action, extend_action)):
            menu.addSeparator()
        undo_action = menu.addAction("Undo graph change")
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.setEnabled(self.can_undo_graph_change())
        redo_action = menu.addAction("Redo graph change")
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.setEnabled(self.can_redo_graph_change())
        menu.addSeparator()
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(screen_pos)
        if open_file_action is not None and chosen == open_file_action:
            self._activate_file_node(node)
        elif open_chat_action is not None and chosen == open_chat_action:
            self.open_conversation_requested.emit(conversation_id)
        elif chosen in create_actions:
            offset = QPointF(300, -80 + len(self._edges) % 4 * 80)
            self._create_connected_node(node, create_actions[chosen], node.pos() + offset)
        elif breakdown_action is not None and chosen == breakdown_action:
            node.setSelected(True)
            self._break_down_selected()
        elif collapse_action is not None and chosen == collapse_action:
            self._set_goal_collapsed(node)
        elif pause_action is not None and chosen == pause_action:
            self._pause_run(node)
        elif resume_action is not None and chosen == resume_action:
            self._run_node(node)
        elif stop_action is not None and chosen == stop_action:
            self._cancel_run(node)
        elif accept_action is not None and chosen == accept_action:
            self._select_node(node)
            self._accept_selected_run_node()
        elif needs_changes_action is not None and chosen == needs_changes_action:
            self._select_node(node)
            self._add_guidance_to_selected_run_node()
        elif rerun_action is not None and chosen == rerun_action:
            self._select_node(node)
            self._rerun_selected_run_node()
        elif extend_action is not None and chosen == extend_action:
            self._select_node(node)
            self._extend_graph_from_selected_review()
        elif chosen == undo_action:
            self.undo_graph_change()
        elif chosen == redo_action:
            self.redo_graph_change()
        elif chosen == delete_action:
            self._delete_nodes_from_menu(node)

    def _delete_nodes_from_menu(self, node: _GraphNode):
        selected_nodes = self._selected_nodes() if node.isSelected() else [node]
        if node not in selected_nodes:
            selected_nodes.append(node)
        if self._confirm_delete_nodes(selected_nodes):
            for selected_node in selected_nodes:
                if selected_node.node_id in self._nodes:
                    self._delete_node(selected_node)
            self._sync_counts()
            if len(selected_nodes) == 1:
                self._set_mode(f"deleted {node.token.title}")
            else:
                self._set_mode(f"deleted {len(selected_nodes)} nodes")
            self._notify_graph_changed()
        else:
            self._set_mode("delete cancelled")

    def _set_goal_collapsed(self, node: _GraphNode):
        if node.token.kind != "goal":
            return
        self._auto_compacted_goal_ids.discard(node.node_id)
        self._set_goal_subgoal_children(node)
        node.set_collapsed(not node.is_collapsed())
        self._apply_goal_collapse_visibility()
        self._sync_root_goal()
        self._sync_graph_frames()
        for edge in self._edges:
            if edge.source_id == node.node_id or edge.target_id == node.node_id:
                source = self._nodes.get(edge.source_id)
                target = self._nodes.get(edge.target_id)
                if source is not None and target is not None:
                    self._update_edge(edge.item, source, target, edge.kind, edge.source_port, edge.target_port)
        self._sync_counts()
        action = "expanded" if not node.is_collapsed() else "collapsed"
        self._set_mode(f"{action} {node.token.title}")
        self._notify_graph_changed()

    def _on_graph_zoom_changed(self, zoom: float):
        self._pending_zoom_compaction = float(zoom or 1.0)
        self._zoom_compaction_timer.start()

    def _flush_zoom_auto_compaction(self):
        zoom = self._pending_zoom_compaction
        self._pending_zoom_compaction = None
        if zoom is None:
            return
        self._sync_zoom_auto_compaction(zoom)

    def _sync_zoom_auto_compaction(self, zoom: float):
        compact_zoom = 0.34
        expand_zoom = 0.62
        readable_zoom = 0.62
        changed = False

        if zoom <= compact_zoom:
            for node in self._nodes.values():
                if node.token.kind != "goal" or node.is_collapsed():
                    continue
                self._set_goal_subgoal_children(node)
                node.set_collapsed(True)
                self._auto_compacted_goal_ids.add(node.node_id)
                changed = True
        elif zoom >= expand_zoom and self._auto_compacted_goal_ids:
            for node_id in list(self._auto_compacted_goal_ids):
                node = self._nodes.get(node_id)
                self._auto_compacted_goal_ids.discard(node_id)
                if node is not None and node.token.kind == "goal" and node.is_collapsed():
                    node.set_collapsed(False)
                    changed = True

        scale = max(1.0, readable_zoom / max(zoom, 0.01))
        for node in self._nodes.values():
            if node.token.kind == "goal":
                node.set_collapsed_readability_scale(scale if node.is_collapsed() else 1.0)

        if not changed:
            self._sync_graph_frames()
            self._refresh_edges()
            return

        self._apply_goal_collapse_visibility()
        self._sync_root_goal()
        self._sync_graph_frames()
        self._ensure_scene_for_items()
        self._refresh_edges()
        self._sync_counts()

    def _all_creation_actions(self, source_kind: str) -> list[CreationAction]:
        return [action for action in _CREATION_ACTIONS if action.source_kind == source_kind]

    def _show_edge_menu(self, edge_item: _GraphEdge, screen_pos):
        edge = self._edge_for_item(edge_item)
        if edge is None:
            return
        menu = QMenu(self)
        source = self._nodes.get(edge.source_id)
        target = self._nodes.get(edge.target_id)
        title = "Connection"
        if source is not None and target is not None:
            rule = connection_rule(source.token, target.token)
            title = rule.label if rule is not None else edge.kind
        menu.addAction(title).setEnabled(False)
        delete_action = menu.addAction("Delete Connection")
        chosen = menu.exec(screen_pos)
        if chosen == delete_action:
            self._remove_edge(edge)
            self._sync_counts()
            self._set_mode("deleted connection")
            self._notify_graph_changed()

    def _edge_for_item(self, item: _GraphEdge) -> CanvasEdge | None:
        for edge in self._edges:
            if edge.item is item:
                return edge
        return None

    def edit_node(self, node: _GraphNode):
        if not self._select_node(node):
            return
        self._edit_title.setFocus()
        self._edit_title.selectAll()
        self._set_mode(f"editing {node.token.title} in inspector")

    def edit_frame(self, frame: _GraphFrame):
        if not self._select_frame(frame):
            return
        self._edit_title.setFocus()
        self._edit_title.selectAll()
        self._set_mode(f"editing frame {frame.title}")

    
    def _populate_inspector(self, node: _GraphNode):
        self._populating_inspector = True
        try:
            self._selected.setText(f"Selected: {node.token.title}")
            spec = component_spec(node.token.kind)
            self._inspector_lines[0].setText(f"Type: {spec.title}")
            self._inspector_lines[1].setText(f"Status: {self._status_label(node)}")
            self._inspector_lines[3].setText(f"Role: {spec.role}")
            self._inspector_lines[4].setText(f"Purpose: {spec.detail}")
            self._edit_title.setEnabled(True)
            self._edit_title.setText(node.token.title)
            detail_contract = self._graph_node_detail_contract(node.token.kind)
            self._detail_label.setText(str(detail_contract.get("field_label") or "Description"))
            self._detail_label.setVisible(True)
            self._edit_detail.setVisible(True)
            self._edit_detail.setEnabled(True)
            self._edit_detail.setPlainText(node.token.detail)
            if node.token.kind == "scope":
                self._edit_detail.set_completion_mode("paths")
                self._edit_detail.set_candidates(repo_path_candidates(self._repo_root))
                self._edit_detail.setPlaceholderText("Type repo paths, one per line. Autocomplete is available while typing.")
            else:
                self._edit_detail.set_completion_mode("mentions")
                self._edit_detail.set_candidates(repo_path_candidates(self._repo_root))
                self._edit_detail.setPlaceholderText(self._description_placeholder(node.token.kind))
            self._frame_color_label.setVisible(False)
            self._frame_color_field.setVisible(False)
            self._frame_color_btn.setVisible(False)

            is_scope = node.token.kind == "scope"
            self._scope_path_label.setVisible(False)
            self._scope_path_field.setVisible(False)
            self._open_scope_btn.setVisible(is_scope)
            if is_scope:
                self._open_scope_btn.setToolTip("Open the first path listed in the Files editor.")
                self._scope_path_field.clear()

            is_goal = node.token.kind == "goal"
            existing_steps = self._outgoing_operation_nodes(node.node_id) if is_goal else []
            self._generate_steps_btn.setVisible(is_goal)
            self._generate_steps_btn.setText("Show Steps" if existing_steps else "Generate Steps")
            self._sync_graph_agent_controls()

            is_operation = node.token.kind == "operation"
            self._agent_label.setText("Crew" if is_operation else "Crew member")
            self._agent_label.setVisible(is_operation)
            self._agent_combo.setVisible(is_operation)
            if is_operation:
                agent_id = node.agent_id or self._crew_id_for_title(node.token.title)
                idx = self._agent_combo.findData(agent_id)
                self._agent_combo.setCurrentIndex(max(0, idx))
            self._inspector_snapshot = self._inspector_values(node)
        finally:
            self._populating_inspector = False

    def _populate_frame_inspector(self, frame: _GraphFrame):
        self._populating_inspector = True
        try:
            self._selected.setText(f"Selected: {frame.title}")
            self._inspector_lines[0].setText("Type: Graph Frame")
            self._inspector_lines[1].setText("Status: layout")
            self._inspector_lines[3].setText("Role: Names and visually groups this graph")
            self._inspector_lines[4].setText("Purpose: A non-executable boundary around related canvas nodes")
            self._edit_title.setEnabled(True)
            self._edit_title.setText(frame.title)
            self._detail_label.setVisible(False)
            self._edit_detail.setVisible(False)
            self._edit_detail.setEnabled(True)
            self._frame_color_label.setVisible(True)
            self._frame_color_field.setVisible(True)
            self._frame_color_btn.setVisible(True)
            self._set_frame_color_control(frame.color)
            self._scope_path_label.setVisible(False)
            self._scope_path_field.setVisible(False)
            self._open_scope_btn.setVisible(False)
            self._generate_steps_btn.setVisible(False)
            self._sync_graph_agent_controls()
            self._agent_label.setVisible(False)
            self._agent_combo.setVisible(False)
            self._inspector_snapshot = self._inspector_values(frame)
        finally:
            self._populating_inspector = False

    def _populate_empty_inspector(self):
        self._populating_inspector = True
        try:
            self._selected.setText("Selected: None")
            self._inspector_lines[0].setText("Type: Canvas")
            self._inspector_lines[1].setText("Status: empty")
            self._inspector_lines[2].setText("Activity: start with New Goal or right-click the canvas")
            self._inspector_lines[3].setText("Role: Workspace")
            self._inspector_lines[4].setText("Purpose: Create or select a graph component")
            self._edit_title.clear()
            self._edit_title.setEnabled(False)
            self._detail_label.setText("Description")
            self._detail_label.setVisible(True)
            self._edit_detail.clear()
            self._edit_detail.setPlaceholderText("Select a node to edit its description.")
            self._edit_detail.setEnabled(False)
            self._edit_detail.setVisible(True)
            self._frame_color_label.setVisible(False)
            self._frame_color_field.setVisible(False)
            self._frame_color_btn.setVisible(False)
            self._scope_path_label.setVisible(False)
            self._scope_path_field.setVisible(False)
            self._open_scope_btn.setVisible(False)
            self._generate_steps_btn.setVisible(False)
            self._agent_label.setVisible(False)
            self._agent_combo.setVisible(False)
            self._inspector_snapshot = None
        finally:
            self._populating_inspector = False

    def _apply_inspector_edits(self, *, refresh_inspector: bool = True):
        frame = self._selected_frame()
        if frame is not None:
            title = self._edit_title.text().strip()
            color = self._normalized_frame_color(self._frame_color_field.text().strip())
            if not title:
                self._set_mode("frame title is required")
                self._edit_title.setFocus()
                return False
            if color is None:
                self._set_mode("frame color must be a hex color")
                self._frame_color_field.setFocus()
                return False
            frame.set_title(title)
            frame.set_color(color)
            if refresh_inspector:
                self._populate_frame_inspector(frame)
            else:
                self._inspector_snapshot = self._inspector_values(frame)
            self._set_mode(f"updated frame {frame.title}")
            self._notify_graph_changed()
            return True
        node = self._selected_node()
        if node is None:
            self._set_mode("select a node or frame first")
            return False
        title = self._edit_title.text().strip()
        detail = self._edit_detail.toPlainText().strip()
        if node.token.kind == "scope":
            paths = self._scope_refs(detail)
            if not title:
                if len(paths) > 1:
                    title = f"{len(paths)} paths"
                elif paths:
                    title = self._scope_title(paths[0])
                else:
                    title = "Files"
            detail = "\n".join(paths)
        elif not title:
            self._set_mode("title is required")
            self._edit_title.setFocus()
            return False

        node.set_token(CanvasToken(node.token.kind, title, detail))
        if node.token.kind == "operation":
            agent_id = str(self._agent_combo.currentData() or "")
            agent_name = ""
            if agent_id:
                agent_name = self._agent_combo.currentText().split(" - ", 1)[0].strip()
            node.set_agent(agent_id, agent_name)
        if refresh_inspector:
            self._populate_inspector(node)
        else:
            self._inspector_snapshot = self._inspector_values(node)
        self._refresh_edges()
        self._set_mode(f"updated {node.token.title}")
        self._notify_graph_changed()
        return True

    def _schedule_inspector_auto_apply(self, *_args):
        if self._populating_inspector or self._restoring_graph or self._closing:
            return
        if self._inspector_snapshot is None:
            return
        target = self._last_inspector_target()
        if target is None or not self._inspector_is_dirty(target):
            return
        self._inspector_auto_apply_timer.start()

    def _flush_inspector_auto_apply(self):
        if self._populating_inspector or self._restoring_graph or self._closing:
            return
        target = self._last_inspector_target()
        if target is None or not self._inspector_is_dirty(target):
            return
        self._apply_inspector_edits(refresh_inspector=False)

    def _generate_steps_for_selected_goal(self):
        if self._is_graph_agent_running():
            self._set_mode("graph agent is already running")
            self._sync_graph_agent_controls()
            return
        goal = self._selected_node()
        if goal is None or goal.token.kind != "goal":
            self._set_mode("select a goal to generate steps")
            return
        if self._inspector_is_dirty(goal) and not self._apply_inspector_edits():
            return
        goal = self._selected_node()
        if goal is None or goal.token.kind != "goal":
            return
        existing = self._outgoing_operation_nodes(goal.node_id)
        if existing:
            self._select_node(existing[0])
            self._set_mode(f"steps already exist for {goal.token.title}")
            return

        goal_title = goal.token.title
        self._reset_graph_chat_output()
        self._set_mode(f"generating steps for {goal_title}")
        self._graph_agent_generation_goal_id = goal.node_id
        goal.set_status("thinking", "generating steps")
        self._active_node_id = goal.node_id
        self._refresh_edges()
        self._sync_counts()
        self._populate_inspector(goal)
        started = self._start_graph_agent(
            "Generate a runnable graph branch for the selected goal. This is workflow design only: "
            + self._generation_strategy_instructions()
            + " "
            "This generate run has a fresh goal-scoped context; ignore other canvas graphs. "
            "plan how future agents should use context, implement actions, produce accepted decisions, produce proof, and close the goal. "
            "Do not research the repo, inspect files, implement code, run tests, or claim findings now. "
            f"Selected goal id: {goal.node_id}. Title: {goal_title}. "
            "Call read_graph first. Use web_fetch only if external product/domain context would improve the graph plan. Then propose_graph_patch and apply_graph_patch. "
            "Do not stop after drafting a patch in text; the canvas changes only when apply_graph_patch succeeds. "
            "If ask_user returns an answer, continue immediately with propose_graph_patch and apply_graph_patch unless another focused design question is still required. "
            "Use this mode for mega-feature decomposition. If this goal is only one straightforward chat prompt, keep the graph minimal or ask what larger breakdown the user wants. "
            "Create the smallest useful graph-native branch for this goal, not just a list of action nodes. Prefer 3-5 useful new nodes total, and fewer is better when enough. "
            "Follow the selected generation strategy, but do not create fake structure. Use a straight flow for sequential work when it has real graph value; branch when the strategy and real workflow independence justify it. Do not create plain action lists with no graph signal. "
            "Break down by distinct responsibilities and real decision output contracts, not generic Analyze -> Implement -> Verify phases. "
            "Ask focused design questions if missing product intent, user-facing behavior, UX priority, acceptance criteria, constraints, risk tolerance, business tradeoff, or responsible crew would change the graph shape. Use one ask_user call per question; multiple calls are valid when each answer can change a different graph decision. "
            "When using answers as context, synthesize them into durable constraints and implications; do not just concatenate labels. "
            "Do not ask the user to choose engines, frameworks, libraries, file paths, or technical approaches; make those architecture/research work for the crew unless this goal explicitly asks for that choice. "
            "Use decision nodes only for output contracts or accepted choices produced by operations; use context for options, constraints, and tradeoff background. "
            "use scope/context/evidence/decision/DoD components when they clarify inputs, proof, accepted choices, or acceptance, and avoid cycles. "
            "If you create both a planning/design/research/spec action and an implementation action, include this connect op in the same patch: {\"op\":\"connect\",\"source\":\"<design_client_id>\",\"target\":\"<implement_client_id>\",\"source_port\":\"implement\"}. "
            "Do not leave them as two sibling actions from the goal. "
            "Do not connect implementation back into that upstream design/spec/context/evidence node; create separate downstream evidence/proof for implementation output. "
            "Use exact source_port values from read_graph.connection_rules; evidence.context -> operation is invalid. "
            "Do not create ownership-only nodes; use operation nodes as actions. Every operation node you add should include agent_id and agent_name from read_graph.available_crew; missing crew is autocorrected only when obvious. Use coder/Coder for implementation by default, scout/Scout for read-only research, architect/Architect for design/architecture/decomposition, and archivist/Archivist for durable memory or summary work. "
            "Do not ask the user to predict changed files. Files are path inputs only; if you create a Files node, its detail must contain actual repo paths, one per line, not affected-area descriptions. Touched files are discovered while running. "
            "Include or reuse a DoD node as the terminal acceptance contract; evidence should feed DoD, not end the graph. "
            "Reuse existing nodes where possible and avoid duplicate file/context nodes, evidence, decisions, or DoD. "
            "Do not create a generic fixed Plan -> Implement -> Verify chain unless it is actually the best graph."
            ,
            scope_goal_id=goal.node_id,
            generation_mode=True,
        )
        if not started:
            self._finish_goal_generation_status(error=True)
            self._graph_agent_generation_goal_id = None

    def _outgoing_operation_nodes(self, goal_id: int) -> list[_GraphNode]:
        nodes = []
        for edge in self._edges:
            if edge.source_id != goal_id or edge.source_port != "work":
                continue
            node = self._nodes.get(edge.target_id)
            if node is not None and node.token.kind == "operation":
                nodes.append(node)
        return sorted(nodes, key=lambda item: self._node_sort_key(item.node_id))

    def _cancel_inspector_edits(self):
        frame = self._selected_frame()
        if frame is not None:
            self._populate_frame_inspector(frame)
            self._set_mode(f"reverted editor for {frame.title}")
            return
        node = self._selected_node()
        if node is None:
            return
        self._populate_inspector(node)
        self._set_mode(f"reverted editor for {node.token.title}")

    def _add_scope_path_from_field(self):
        ref = self._scope_path_field.text().strip().lstrip("@").strip('"')
        if not ref:
            return
        paths = self._scope_refs(self._edit_detail.toPlainText())
        normalized = {self._normalize_scope_ref(path).casefold() for path in paths}
        if self._normalize_scope_ref(ref).casefold() not in normalized:
            paths.append(ref.replace("\\", "/"))
        self._edit_detail.setPlainText("\n".join(paths))
        self._scope_path_field.clear()
        self._edit_detail.setFocus()

    def _choose_frame_color(self):
        frame = self._selected_frame()
        if frame is None:
            return
        current = QColor(self._frame_color_field.text().strip() or frame.color)
        chosen = QColorDialog.getColor(current, self, "Frame background color")
        if not chosen.isValid():
            return
        self._set_frame_color_control(chosen.name())
        self._set_mode(f"picked frame color {chosen.name()}")

    def _set_frame_color_control(self, color: str):
        normalized = self._normalized_frame_color(color) or "#2f8f62"
        self._frame_color_field.setText(normalized)
        self._frame_color_btn.setStyleSheet(
            f"background-color: {normalized}; border: 1px solid #5b6575; border-radius: 6px;"
        )

    def _open_selected_scope(self):
        node = self._selected_node()
        if node is None or node.token.kind != "scope":
            return
        paths = self._scope_refs(self._edit_detail.toPlainText()) or self._scope_refs(node.token.detail)
        if not paths:
            self._set_mode("add a file path first")
            self._edit_detail.setFocus()
            return
        ref = paths[0]
        if ref == "Open files on the right":
            self._set_mode("add a real file path first")
            self._edit_detail.setFocus()
            return
        self.open_file_requested.emit(self._absolute_ref(ref))
        self._set_mode(f"opened {ref}")

    def _begin_output_drag(self, node: _GraphNode, scene_pos: QPointF, source_port: str = "out"):
        self._connect_anchor = node
        self._drag_source_port = source_port
        if self._drag_edge is None:
            self._drag_edge = QGraphicsPathItem()
            self._drag_edge.setZValue(1)
            self._scene.addItem(self._drag_edge)
        self._update_temporary_edge(node.output_port_scene_pos(source_port), scene_pos)
        self._set_mode(f"drag OUT from {node.token.title} to another node")

    def _move_output_drag(self, node: _GraphNode, scene_pos: QPointF, source_port: str = "out"):
        self._update_temporary_edge(node.output_port_scene_pos(source_port), scene_pos)

    def _begin_input_drag(self, node: _GraphNode, scene_pos: QPointF, target_port: str = "in"):
        self._connect_anchor = node
        self._drag_source_port = target_port
        if self._drag_edge is None:
            self._drag_edge = QGraphicsPathItem()
            self._drag_edge.setZValue(1)
            self._scene.addItem(self._drag_edge)
        self._update_temporary_edge(scene_pos, node.input_port_scene_pos(target_port))
        self._set_mode(f"drag IN from {node.token.title} to an upstream node")

    def _move_input_drag(self, node: _GraphNode, scene_pos: QPointF, target_port: str = "in"):
        self._update_temporary_edge(scene_pos, node.input_port_scene_pos(target_port))

    def _finish_output_drag(self, node: _GraphNode, scene_pos: QPointF, source_port: str = "out"):
        target = self._connection_target_at_scene_pos(node, scene_pos, source_port)
        self._clear_drag_edge()
        if target is not None:
            if self.connect_nodes(node.node_id, target.node_id, source_port):
                self._set_mode(f"connected {node.token.title} OUT -> {target.token.title} IN")
            else:
                if not self._show_create_menu_from_drag(node, source_port, scene_pos):
                    self._set_mode(f"cannot connect {node.token.kind} -> {target.token.kind}")
        else:
            if not self._show_create_menu_from_drag(node, source_port, scene_pos):
                self._set_mode("connection cancelled")
        self._connect_anchor = None
        self._drag_source_port = "out"

    def _finish_input_drag(self, node: _GraphNode, scene_pos: QPointF, target_port: str = "in"):
        source = self._connection_source_at_scene_pos(node, scene_pos, target_port)
        self._clear_drag_edge()
        if source is not None:
            rule = self._connection_rule_to_target(source, node, target_port)
            if rule is not None and self.connect_nodes(source.node_id, node.node_id, rule.source_port):
                self._set_mode(f"connected {source.token.title} OUT -> {node.token.title} IN")
            else:
                self._set_mode(f"cannot connect {source.token.kind} -> {node.token.kind}")
        else:
            if not self._show_create_menu_from_input_drag(node, target_port, scene_pos):
                self._set_mode("connection cancelled")
        self._connect_anchor = None
        self._drag_source_port = "out"

    def cancel_connection_drag(self):
        self._clear_drag_edge()
        self._connect_anchor = None
        self._drag_source_port = "out"
        self._set_mode("connection cancelled")

    def _clear_drag_edge(self):
        if self._drag_edge is not None:
            self._scene.removeItem(self._drag_edge)
            self._drag_edge = None

    def _show_create_menu_from_drag(self, source: _GraphNode, source_port: str, scene_pos: QPointF) -> bool:
        actions = self._creation_actions(source.token.kind, source_port)
        if not actions:
            return False
        menu = QMenu(self)
        action_map = {}
        for creation in actions:
            action = menu.addAction(creation.title)
            action.setToolTip(creation.detail)
            action_map[action] = creation
        chosen = menu.exec(self._scene_pos_to_global(scene_pos))
        creation = action_map.get(chosen)
        if creation is None:
            return False
        return self._create_connected_node(source, creation, scene_pos)

    def _show_create_menu_from_input_drag(self, target: _GraphNode, target_port: str, scene_pos: QPointF) -> bool:
        rules = tuple(
            rule
            for rule in connection_rules_for_target(target.token, target_port)
            if rule.source_kind in GRAPH_PATCH_NODE_KINDS
        )
        if not rules:
            return False
        menu = QMenu(self)
        action_map = {}
        for rule in rules:
            token = default_token_for_kind(rule.source_kind)
            action = menu.addAction(f"Create: {token.title}")
            action.setToolTip(rule.label)
            action_map[action] = rule
        chosen = menu.exec(self._scene_pos_to_global(scene_pos))
        rule = action_map.get(chosen)
        if rule is None:
            return False
        return self._create_upstream_node(target, rule, scene_pos)

    def _creation_actions(self, source_kind: str, source_port: str) -> list[CreationAction]:
        actions = [
            action
            for action in _CREATION_ACTIONS
            if action.source_kind == source_kind and action.source_port == source_port
        ]
        existing_targets = {action.target_kind for action in actions}
        for rule in _CONNECTION_RULES:
            if rule.source_kind != source_kind or rule.source_port != source_port:
                continue
            if rule.target_kind in existing_targets:
                continue
            token = default_token_for_kind(rule.target_kind)
            actions.append(
                CreationAction(
                    rule.source_kind,
                    rule.source_port,
                    rule.target_kind,
                    f"Create: {token.title}",
                    rule.label,
                    token.title,
                    token.detail,
                )
            )
            existing_targets.add(rule.target_kind)
        return actions

    def _create_connected_node(self, source: _GraphNode, creation: CreationAction, scene_pos: QPointF) -> bool:
        token = CanvasToken(creation.target_kind, creation.token_title, creation.token_detail)
        self._connect_anchor = None
        node = self._create_node(token, scene_pos)
        if not self.connect_nodes(source.node_id, node.node_id, creation.source_port):
            self._delete_node(node)
            self._sync_counts()
            return False
        self._select_node(node)
        self._set_mode(f"{creation.title}: {source.token.title} -> {node.token.title}")
        return True

    def _create_upstream_node(self, target: _GraphNode, rule, scene_pos: QPointF) -> bool:
        token = default_token_for_kind(rule.source_kind)
        self._connect_anchor = None
        node = self._create_node(token, scene_pos)
        if not self.connect_nodes(node.node_id, target.node_id, rule.source_port):
            self._delete_node(node)
            self._sync_counts()
            return False
        self._select_node(node)
        self._set_mode(f"{rule.label}: {node.token.title} -> {target.token.title}")
        return True

    def _scene_pos_to_global(self, scene_pos: QPointF):
        view_pos = self._graph.mapFromScene(scene_pos)
        return self._graph.viewport().mapToGlobal(view_pos)

    def _update_temporary_edge(self, start: QPointF, end: QPointF):
        if self._drag_edge is None:
            return
        p = palette()
        dx = max(80, abs(end.x() - start.x()) * 0.45)
        path = QPainterPath(start)
        path.cubicTo(start + QPointF(dx, 0), end - QPointF(dx, 0), end)
        self._drag_edge.setPath(path)
        self._drag_edge.setPen(QPen(QColor(p["TEXT_DIM"]), 1.5, Qt.PenStyle.DashLine))

    def _connection_target_at_scene_pos(
        self,
        source: _GraphNode,
        scene_pos: QPointF,
        source_port: str,
    ) -> _GraphNode | None:
        candidates = self._candidate_nodes_at_scene_pos(scene_pos, exclude=source)
        if not candidates:
            return None
        for target in candidates:
            if connection_rule(source.token, target.token, source_port) is not None:
                return target
        return None

    def _connection_source_at_scene_pos(
        self,
        target: _GraphNode,
        scene_pos: QPointF,
        target_port: str,
    ) -> _GraphNode | None:
        candidates = self._candidate_nodes_at_scene_pos(scene_pos, exclude=target)
        if not candidates:
            return None
        for source in candidates:
            if self._connection_rule_to_target(source, target, target_port) is not None:
                return source
        return candidates[0]

    def _connection_rule_to_target(self, source: _GraphNode, target: _GraphNode, target_port: str):
        for rule in connection_rules_for_target(target.token, target_port):
            if rule.source_kind == source.token.kind:
                return rule
        return None

    def _candidate_nodes_at_scene_pos(
        self,
        scene_pos: QPointF,
        *,
        exclude: _GraphNode | None = None,
    ) -> list[_GraphNode]:
        seen: set[int] = set()
        candidates: list[_GraphNode] = []
        search_rect = QRectF(scene_pos.x() - 34, scene_pos.y() - 34, 68, 68)
        for item in [*self._scene.items(scene_pos), *self._scene.items(search_rect)]:
            if isinstance(item, _GraphNode) and item is not exclude and item.node_id not in seen:
                seen.add(item.node_id)
                candidates.append(item)
        for node in self._nodes.values():
            if node is exclude or node.node_id in seen:
                continue
            if node.sceneBoundingRect().adjusted(-24, -24, 24, 24).contains(scene_pos):
                seen.add(node.node_id)
                candidates.append(node)
        candidates.sort(
            key=lambda candidate: (
                (candidate.center_scene_pos().x() - scene_pos.x()) ** 2
                + (candidate.center_scene_pos().y() - scene_pos.y()) ** 2
            )
        )
        return candidates

    def _select_node(self, node: _GraphNode) -> bool:
        if self._selection_guard or self._closing:
            return True
        if self._expand_auto_compacted_goal(node):
            return True
        previous = self._last_inspector_target()
        if previous is not None and previous is not node:
            if not self._confirm_discard_inspector_changes(previous, node):
                self._selection_guard = True
                try:
                    node.setSelected(False)
                    previous.setSelected(True)
                finally:
                    self._selection_guard = False
                return False
        selected_frames = [item for item in self._scene.selectedItems() if isinstance(item, _GraphFrame)]
        if selected_frames or not node.isSelected():
            self._scene.clearSelection()
            node.setSelected(True)
        self._last_selected_node_id = node.node_id
        self._last_selected_frame_id = None
        if self._connect_anchor is not None and self._connect_anchor is not node:
            if self.connect_nodes(self._connect_anchor.node_id, node.node_id):
                self._set_mode(f"connected {self._connect_anchor.token.title} OUT -> {node.token.title} IN")
            self._connect_anchor = None
        self._populate_inspector(node)
        self._render_graph_chat()
        return True

    def _select_frame(self, frame: _GraphFrame) -> bool:
        if self._selection_guard or self._closing:
            return True
        selected_nodes = [item for item in self._scene.selectedItems() if isinstance(item, _GraphNode)]
        if selected_nodes and frame.isSelected():
            node = selected_nodes[0]
            self._last_selected_node_id = node.node_id
            self._last_selected_frame_id = None
            self._populate_inspector(node)
            self._render_graph_chat()
            return True
        previous = self._last_inspector_target()
        if previous is not None and previous is not frame:
            if not self._confirm_discard_inspector_changes(previous, frame):
                self._selection_guard = True
                try:
                    frame.setSelected(False)
                    previous.setSelected(True)
                finally:
                    self._selection_guard = False
                return False
        if selected_nodes or not frame.isSelected():
            self._scene.clearSelection()
            frame.setSelected(True)
        self._last_selected_node_id = None
        self._last_selected_frame_id = frame.frame_id
        self._connect_anchor = None
        self._populate_frame_inspector(frame)
        self._render_graph_chat()
        return True

    def _confirm_discard_inspector_changes(self, current, next_item) -> bool:
        if not self._inspector_is_dirty(current):
            return True
        self._inspector_auto_apply_timer.stop()
        return bool(self._apply_inspector_edits())

    def _inspector_target_title(self, item) -> str:
        if isinstance(item, _GraphFrame):
            return item.title
        return item.token.title

    def _inspector_is_dirty(self, node) -> bool:
        if self._inspector_snapshot is None:
            return False
        return self._inspector_snapshot != self._inspector_values(node)

    def _inspector_values(self, node) -> dict:
        if isinstance(node, _GraphFrame):
            return {
                "frame_id": node.frame_id,
                "title": self._edit_title.text().strip(),
                "color": self._normalized_frame_color(self._frame_color_field.text().strip()) or "",
            }
        is_agentish = node.token.kind == "operation"
        is_scope = node.token.kind == "scope"
        return {
            "node_id": node.node_id,
            "title": self._edit_title.text().strip(),
            "detail": self._edit_detail.toPlainText().strip(),
            "agent_id": str(self._agent_combo.currentData() or "") if is_agentish else "",
            "scope_path": self._scope_path_field.text().strip() if is_scope else "",
        }

    def _set_active_node(self, node: _GraphNode, status: str = "running", note: str = ""):
        for other in self._nodes.values():
            if other is not node and other.status == "running":
                if self._run_session is not None and other.node_id in self._run_session.plan.goal_ids:
                    continue
                if self._run_session is not None and other.node_id in self._run_session.running_node_ids:
                    continue
                other.set_status("queued", "not currently active")
        self._active_node_id = node.node_id
        node.set_status(status, note)
        self._refresh_edges()
        self._sync_counts()
        if self._selected_node() is node:
            self._populate_inspector(node)
        self._notify_graph_changed()

    def _pause_run(self, node: _GraphNode):
        if node.status != "running":
            return
        if self._run_session is not None:
            self._run_session.paused = True
            running_ids = (
                set(self._run_session.running_node_ids)
                if node.token.kind == "goal"
                else {node.node_id}
            )
            for running_id in running_ids:
                running = self._nodes.get(running_id)
                if running is not None and running.status == "running":
                    running.set_status("paused", "run paused")
        if self._active_node_id == node.node_id:
            self._active_node_id = None
        elif node.token.kind == "goal":
            self._active_node_id = None
        node.set_status("paused", "run paused")
        self._refresh_edges()
        self._sync_counts()
        if self._selected_node() is node:
            self._populate_inspector(node)
        self._set_mode(f"paused {node.token.title}")
        self._notify_graph_changed()

    def _resume_run(self, node: _GraphNode):
        session = self._run_session
        if session is None:
            self._run_node(node)
            return
        session.paused = False
        if node.token.kind == "operation":
            session.running_node_id = node.node_id
            session.running_node_ids.add(node.node_id)
            owner = f"{node.agent_name} working" if node.agent_name else "Coder working"
            self._set_active_node(node, "running", owner)
        elif node.status == "paused":
            node.set_status("running", "orchestrating branch")
            running_ids = set(session.running_node_ids)
            if session.running_node_id is not None:
                running_ids.add(session.running_node_id)
            for running_id in running_ids:
                running = self._nodes.get(running_id)
                if running is None or running.status != "paused":
                    continue
                owner = f"{running.agent_name} working" if running.agent_name else "Coder working"
                self._set_active_node(running, "running", owner)
        self._set_mode(f"resumed {node.token.title}")
        self._start_next_run_operation()
        self._notify_graph_changed()

    def _cancel_run(self, node: _GraphNode):
        if node.status not in {"running", "paused"}:
            return
        session = self._run_session
        if session is not None:
            self._stop_run_thread()
            self._stop_running_node_attempts(
                session.plan.node_ids,
                reason="Run stopped before the provider returned a result.",
            )
            for node_id in session.plan.node_ids:
                planned = self._nodes.get(node_id)
                if planned is not None and planned.status in {"queued", "running", "paused"}:
                    planned.set_status("idle", "run stopped")
            self._run_session = None
            self._run_threads.clear()
            self._run_thread = None
        if self._active_node_id == node.node_id:
            self._active_node_id = None
        node.set_status("idle", "run stopped")
        self._refresh_edges()
        self._sync_counts()
        self._sync_run_controls()
        if self._selected_node() is node:
            self._populate_inspector(node)
        self._set_mode(f"stopped {node.token.title}")
        self._notify_graph_changed()

    def _status_label(self, node: _GraphNode) -> str:
        label = node.status
        if self._active_node_id == node.node_id:
            label = f"{label} - active"
        if node._status_note:
            label = f"{label}: {node._status_note}"
        return label

    @staticmethod
    def _description_placeholder(kind: str) -> str:
        return {
            "goal": "Define the desired outcome, constraints, and acceptance signal.",
            "operation": "Describe the work action: implement, decide, or prove; include expected output and proof.",
            "context": "Synthesize durable facts and implications. Example: Target web app; compact keyboard-first UX constrains layout and proof.",
            "evidence": "Describe the proof expected or produced: tests, screenshots, diffs, review notes, or acceptance signals.",
            "decision": "Record a decision contract or accepted choice: question, criteria, result, reason, and downstream guidance.",
            "dod": "List the acceptance criteria that must be satisfied before the goal is done.",
        }.get(kind, "Describe what this node means and what future agents should know.")

    @staticmethod
    def _normalized_frame_color(color: str) -> str | None:
        raw = str(color or "").strip()
        if not raw:
            return "#2f8f62"
        if not raw.startswith("#"):
            raw = f"#{raw}"
        parsed = QColor(raw)
        return parsed.name() if parsed.isValid() else None

    def _selected_inspector_target(self):
        frame = self._selected_frame()
        if frame is not None:
            return frame
        return self._selected_node()

    def _last_inspector_target(self):
        if self._last_selected_frame_id is not None:
            frame = self._frames.get(self._last_selected_frame_id)
            if frame is not None:
                return frame
        if self._last_selected_node_id is not None:
            return self._nodes.get(self._last_selected_node_id)
        return None

    def _selected_node(self) -> _GraphNode | None:
        if self._last_selected_node_id is not None:
            node = self._nodes.get(self._last_selected_node_id)
            if node is not None and node.isSelected():
                return node
        if self._last_selected_frame_id is not None:
            return None
        selected = self._selected_nodes()
        return selected[0] if selected else None

    def _selected_nodes(self) -> list[_GraphNode]:
        return [item for item in self._scene.selectedItems() if isinstance(item, _GraphNode)]

    def _selected_frame(self) -> _GraphFrame | None:
        if self._last_selected_frame_id is not None:
            frame = self._frames.get(self._last_selected_frame_id)
            if frame is not None and frame.isSelected():
                return frame
        if self._last_selected_node_id is not None:
            return None
        selected = [item for item in self._scene.selectedItems() if isinstance(item, _GraphFrame)]
        return selected[0] if selected else None

    def _activate_selected_node(self):
        frame = self._selected_frame()
        if frame is not None:
            self.edit_frame(frame)
            return
        node = self._selected_node()
        if node is None:
            self._set_mode("select a node first")
            return
        if self._expand_auto_compacted_goal(node):
            return
        if self._can_open_scope(node):
            self._activate_file_node(node)
            return
        self.edit_node(node)

    def _edit_selected_node(self):
        frame = self._selected_frame()
        if frame is not None:
            self.edit_frame(frame)
            return
        node = self._selected_node()
        if node is None:
            self._set_mode("select a node first")
            return
        self.edit_node(node)

    def _activate_node(self, node: _GraphNode):
        if self._expand_auto_compacted_goal(node):
            return
        self.edit_node(node)

    def _expand_auto_compacted_goal(self, node: _GraphNode) -> bool:
        if node.token.kind != "goal" or node.node_id not in self._auto_compacted_goal_ids:
            return False
        self._auto_compacted_goal_ids.discard(node.node_id)
        node.set_collapsed(False)
        node.set_collapsed_readability_scale(1.0)
        self._apply_goal_collapse_visibility()
        self._sync_root_goal()
        self._sync_graph_frames()
        self._ensure_scene_for_items()
        self._refresh_edges()
        self._sync_counts()
        self._set_mode(f"expanded {node.token.title}")
        return True

    def _activate_file_node(self, node: _GraphNode):
        if node.token.kind != "scope":
            return
        paths = self._scope_refs(node.token.detail)
        if len(paths) == 1 and paths[0] != "Open files on the right":
            self.open_file_requested.emit(self._absolute_ref(paths[0]))
            self._set_mode(f"opened {paths[0]}")

    def _can_open_scope(self, node: _GraphNode) -> bool:
        paths = self._scope_refs(node.token.detail) if node.token.kind == "scope" else []
        return len(paths) == 1 and paths[0] != "Open files on the right"

    def _add_file_nodes(self, refs: list[str], point: QPointF):
        for idx, ref in enumerate(refs):
            node = self.add_token_node(
                CanvasToken("scope", self._scope_title(ref), ref),
                point + QPointF(idx * 34, idx * 106),
            )
            self.open_file_requested.emit(self._absolute_ref(ref))
            self._select_node(node)

    def _find_scope_node(self, ref: str) -> _GraphNode | None:
        normalized = self._normalize_scope_ref(ref)
        for node in self._nodes.values():
            if node.token.kind != "scope":
                continue
            details = [self._normalize_scope_ref(path) for path in self._scope_refs(node.token.detail)]
            if any(detail.casefold() == normalized.casefold() for detail in details):
                return node
        return None

    def _active_operation_node(self) -> _GraphNode | None:
        active = self._nodes.get(self._active_node_id) if self._active_node_id is not None else None
        if active is not None and active.token.kind == "operation":
            return active
        selected = self._selected_node()
        if selected is not None and selected.token.kind == "operation":
            return selected
        for node in self._nodes.values():
            if node.token.kind == "operation":
                return node
        return None

    def _next_activity_point(self) -> QPointF:
        operation = self._active_operation_node()
        if operation is not None:
            offset = sum(1 for node in self._nodes.values() if node.token.kind == "scope") * 34
            return operation.pos() + QPointF(300, -80 + offset)
        return self._next_spawn_point()

    def _relative_ref(self, path: str) -> str:
        return relative_ref(path, self._repo_root)

    def _scope_title(self, ref: str) -> str:
        return scope_title(ref)

    @staticmethod
    def _scope_refs(detail: str) -> list[str]:
        return scope_refs(detail)

    @staticmethod
    def _normalize_scope_ref(ref: str) -> str:
        return normalize_scope_ref(ref)

    @staticmethod
    def _crew_id_for_title(title: str) -> str:
        return canvas_agent_id_for_title(title)

    def _restore_graph_state_strict(self, state: dict) -> bool:
        if not isinstance(state, dict):
            raise TypeError("Saved canvas is not an object.")
        if state.get("format") != "aichs-agent-canvas/v1":
            raise ValueError("Saved canvas uses an unsupported format.")
        nodes = state.get("nodes")
        edges = state.get("edges", [])
        if not isinstance(nodes, list):
            raise ValueError("Saved canvas nodes are invalid.")
        if not isinstance(edges, list):
            raise ValueError("Saved canvas connections are invalid.")

        self._clear_graph()
        allowed_kinds = set(GRAPH_NODE_KINDS)
        seen_ids: set[int] = set()
        collapsed_goal_ids: set[int] = set()
        for raw in nodes:
            if not isinstance(raw, dict):
                raise ValueError("Saved canvas contains an invalid node.")
            try:
                node_id = int(raw.get("id"))
                x = float(raw.get("x", 0))
                y = float(raw.get("y", 0))
            except (TypeError, ValueError):
                raise ValueError("Saved canvas contains an invalid node position.") from None
            kind = str(raw.get("kind") or "").strip()
            title = str(raw.get("title") or "").strip()
            detail = str(raw.get("detail") or "").strip()
            if node_id <= 0 or node_id in seen_ids:
                raise ValueError("Saved canvas contains duplicate node IDs.")
            if kind not in allowed_kinds or not title:
                raise ValueError("Saved canvas contains an unsupported node.")
            seen_ids.add(node_id)
            node = self._create_node(
                CanvasToken(kind, title, detail),
                QPointF(x, y),
                node_id=node_id,
                sync_visibility=False,
                sync_layout=False,
            )
            node.set_agent(str(raw.get("agent_id") or ""), str(raw.get("agent_name") or ""))
            node.set_status(str(raw.get("status") or "idle"), str(raw.get("status_note") or ""))
            if kind == "goal" and self._restored_bool(raw.get("collapsed")):
                collapsed_goal_ids.add(node_id)
            history = self._restored_run_history(raw.get("run_history"))
            if history:
                self._node_run_history[node_id] = history

        for raw in edges:
            if not isinstance(raw, dict):
                raise ValueError("Saved canvas contains an invalid connection.")
            try:
                source_id = int(raw.get("source_id"))
                target_id = int(raw.get("target_id"))
            except (TypeError, ValueError):
                raise ValueError("Saved canvas contains an invalid connection endpoint.") from None
            source = self._nodes.get(source_id)
            target = self._nodes.get(target_id)
            source_port = str(raw.get("source_port") or "out")
            target_port = str(raw.get("target_port") or "in")
            kind = str(raw.get("kind") or "")
            if source is None or target is None:
                raise ValueError("Saved canvas refers to a missing node.")
            rule = connection_rule(source.token, target.token, source_port)
            if rule is None or rule.kind != kind or rule.target_port != target_port:
                raise ValueError("Saved canvas contains a connection this version no longer supports.")
            self._connect_with_kind(
                source_id,
                target_id,
                rule.kind,
                rule.label,
                source_port=rule.source_port,
                target_port=rule.target_port,
                sync_visibility=False,
                sync_layout=False,
                skip_cycle_check=True,
            )
        if self._has_cycle():
            raise ValueError(f"Saved canvas contains a cycle: {self._cycle_summary() or 'invalid cycle'}.")

        self._restore_frames(state.get("frames"))
        self._sync_root_goal()
        for goal_id in collapsed_goal_ids:
            goal = self._nodes.get(goal_id)
            if goal is not None:
                goal.set_collapsed(True)
        if collapsed_goal_ids:
            for goal_id in collapsed_goal_ids:
                goal = self._nodes.get(goal_id)
                if goal is not None:
                    self._set_goal_subgoal_children(goal)
        else:
            self._refresh_goal_subgoal_children()
        if collapsed_goal_ids:
            self._apply_goal_collapse_visibility()
        self._sync_graph_frames()
        self._active_node_id = self._restored_node_id(state.get("active_node_id"))
        self._graph_chat_messages = self._restored_graph_chat(state.get("graph_chat"))
        self._graph_tool_events = self._restored_graph_tool_activity(state.get("graph_tool_activity"))
        self._expanded_graph_tool = None
        self._graph_check_failures = self._restored_graph_check_failures(state.get("graph_check_failures"))
        reconciled = self._reconcile_restored_run_state()
        self._render_graph_chat()
        self._restore_graph_chat_split(state.get("graph_chat_split"))
        selected_id = self._restored_node_id(state.get("selected_node_id"))
        selected_frame_id = self._restored_frame_id(state.get("selected_frame_id"))
        if selected_id is not None and selected_id in self._nodes and self._nodes[selected_id].isVisible():
            self._select_node(self._nodes[selected_id])
        elif selected_frame_id is not None and selected_frame_id in self._frames:
            self._select_frame(self._frames[selected_frame_id])
        elif self._nodes:
            visible_nodes = [node for node in self._nodes.values() if node.isVisible()]
            if visible_nodes:
                self._select_node(visible_nodes[0])
            else:
                self._populate_empty_inspector()
        else:
            self._populate_empty_inspector()
            self._sync_counts()
        self._ensure_scene_for_items()
        self._restore_view(state.get("view"))
        return reconciled

    def _reconcile_restored_run_state(self) -> bool:
        changed = self._stop_running_node_attempts(
            reason="Run was interrupted before the provider returned a result."
        )
        if not changed:
            return False
        cleared_active = False
        for node in self._nodes.values():
            if node.status in {"queued", "running", "paused"}:
                node.set_status("idle", "run interrupted")
                changed = True
                if self._active_node_id == node.node_id:
                    cleared_active = True
        if cleared_active:
            self._active_node_id = None
        return changed

    def _restore_frames(self, value: object):
        if not isinstance(value, list):
            return
        for raw in value:
            if not isinstance(raw, dict):
                continue
            try:
                frame_id = int(raw.get("id"))
                x = float(raw.get("x", 0))
                y = float(raw.get("y", 0))
                width = float(raw.get("w", 0))
                height = float(raw.get("h", 0))
            except (TypeError, ValueError):
                continue
            if frame_id <= 0 or width <= 0 or height <= 0 or frame_id in self._frames:
                continue
            title = str(raw.get("title") or "Graph").strip() or "Graph"
            color = str(raw.get("color") or "#2f8f62").strip()
            root_id = self._restored_node_id(raw.get("root_id"))
            node_ids: set[int] = set()
            raw_node_ids = raw.get("node_ids")
            if isinstance(raw_node_ids, list):
                for raw_node_id in raw_node_ids:
                    node_id = self._restored_node_id(raw_node_id)
                    if node_id is not None:
                        node_ids.add(node_id)
            self._create_frame(
                title,
                color,
                QRectF(x, y, width, height),
                root_id=root_id,
                node_ids=node_ids,
                frame_id=frame_id,
            )

    def _clear_graph(self):
        if hasattr(self, "_run_thread"):
            self._stop_run_thread()
            self._stop_running_node_attempts(reason="Run stopped because the canvas was cleared.")
        self._scene.clear()
        self._nodes.clear()
        self._edges.clear()
        self._outgoing_edge_counts.clear()
        self._incoming_edge_counts.clear()
        self._frames.clear()
        self._connect_anchor = None
        self._drag_edge = None
        self._drag_source_port = "out"
        self._active_node_id = None
        self._last_selected_node_id = None
        self._last_selected_frame_id = None
        self._selection_guard = False
        self._inspector_snapshot = None
        self._graph_chat_messages = []
        self._graph_tool_events = {}
        self._expanded_graph_tool = None
        self._graph_check_failures = []
        self._node_run_history.clear()
        self._expanded_run_tools.clear()
        self._run_session = None
        self._run_threads.clear()
        self._run_thread = None
        self._run_thread_attempt_id = ""
        self._run_last_edit_path = ""
        self._graph_agent_stop_requested = False
        self._render_graph_chat()
        if hasattr(self, "_cycle_warning"):
            self._cycle_warning.setVisible(False)
            self._cycle_warning.setText("")
        self._next_node_id = 1
        self._next_frame_id = 1
        self._collapsed_hidden_nodes.clear()
        self._auto_compacted_goal_ids.clear()

    def _index_edge_adjacency(self, source_id: int, target_id: int):
        out_counts = self._outgoing_edge_counts.setdefault(source_id, {})
        out_counts[target_id] = out_counts.get(target_id, 0) + 1
        in_counts = self._incoming_edge_counts.setdefault(target_id, {})
        in_counts[source_id] = in_counts.get(source_id, 0) + 1

    def _remove_edge_adjacency(self, source_id: int, target_id: int):
        out_counts = self._outgoing_edge_counts.get(source_id)
        if out_counts is not None:
            remaining = out_counts.get(target_id, 0) - 1
            if remaining > 0:
                out_counts[target_id] = remaining
            else:
                out_counts.pop(target_id, None)
                if not out_counts:
                    self._outgoing_edge_counts.pop(source_id, None)
        in_counts = self._incoming_edge_counts.get(target_id)
        if in_counts is not None:
            remaining = in_counts.get(source_id, 0) - 1
            if remaining > 0:
                in_counts[source_id] = remaining
            else:
                in_counts.pop(source_id, None)
                if not in_counts:
                    self._incoming_edge_counts.pop(target_id, None)

    @staticmethod
    def _restored_graph_chat(value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        messages: list[dict[str, str]] = []
        for raw in value[-100:]:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "Message").strip() or "Message"
            text = str(raw.get("text") or "").strip()
            if text:
                messages.append({"role": role[:40], "text": text})
        return messages

    @staticmethod
    def _restored_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return False

    @staticmethod
    def _restored_graph_tool_activity(value: object) -> dict[str, list[dict[str, str]]]:
        if not isinstance(value, list):
            return {}
        events: dict[str, list[dict[str, str]]] = {}
        for raw in value[-400:]:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("tool") or "").strip()
            summary = str(raw.get("summary") or "").strip()
            if not name or not summary:
                continue
            events.setdefault(name[:80], []).append(
                {
                    "status": str(raw.get("status") or "done").strip()[:20] or "done",
                    "summary": summary,
                    "detail": str(raw.get("detail") or "").strip(),
                }
            )
        return {name: values[-80:] for name, values in events.items()}

    @staticmethod
    def _restored_graph_check_failures(value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        failures: list[dict[str, str]] = []
        for raw in value[-80:]:
            if not isinstance(raw, dict):
                continue
            summary = str(raw.get("summary") or "").strip()
            if not summary:
                continue
            failures.append(
                {
                    "summary": summary[:120],
                    "detail": str(raw.get("detail") or "").strip(),
                    "error": str(raw.get("error") or "").strip(),
                }
            )
        return failures

    def _serializable_run_history(self, node_id: int) -> list[dict]:
        history = self._node_run_history.get(node_id, [])
        return self._restored_run_history(history)

    @staticmethod
    def _restored_run_history(value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        restored: list[dict] = []
        for raw in value[-20:]:
            if not isinstance(raw, dict):
                continue
            attempt_id = str(raw.get("id") or uuid4().hex).strip() or uuid4().hex
            tools = []
            raw_tools = raw.get("tools")
            if isinstance(raw_tools, list):
                for tool in raw_tools[-50:]:
                    if not isinstance(tool, dict):
                        continue
                    tools.append(
                        {
                            "name": str(tool.get("name") or "tool")[:80],
                            "tool_use_id": str(tool.get("tool_use_id") or "")[:160],
                            "status": str(tool.get("status") or "called")[:40],
                            "summary": str(tool.get("summary") or "")[:500],
                            "inputs": str(tool.get("inputs") or "")[:6000],
                            "output": str(tool.get("output") or "")[:6000],
                        }
                    )
            touched = raw.get("touched_files")
            touched_files = [str(path)[:500] for path in touched[-50:]] if isinstance(touched, list) else []
            guidance = []
            raw_guidance = raw.get("guidance")
            if isinstance(raw_guidance, list):
                for item in raw_guidance[-20:]:
                    if not isinstance(item, dict):
                        continue
                    content = str(item.get("content") or "").strip()
                    if content:
                        guidance.append(
                            {
                                "content": content[:8000],
                                "created_at": str(item.get("created_at") or "")[:80],
                            }
                        )
            restored.append(
                {
                    "id": attempt_id,
                    "kind": str(raw.get("kind") or "operation")[:40],
                    "role": str(raw.get("role") or "Agent")[:80],
                    "status": str(raw.get("status") or "done")[:40],
                    "started_at": str(raw.get("started_at") or "")[:40],
                    "prompt": str(raw.get("prompt") or "")[:8000],
                    "content": str(raw.get("content") or "")[:20000],
                    "artifact_ref": str(raw.get("artifact_ref") or "")[:500],
                    "artifact_title": str(raw.get("artifact_title") or "")[:500],
                    "conversation_id": str(raw.get("conversation_id") or "")[:120],
                    "conversation_created_at": str(raw.get("conversation_created_at") or "")[:80],
                    "conversation_model": str(raw.get("conversation_model") or "")[:200],
                    "conversation_error": str(raw.get("conversation_error") or "")[:500],
                    "compact_retry": bool(raw.get("compact_retry")),
                    "guidance": guidance,
                    "tools": tools,
                    "touched_files": touched_files,
                }
            )
        return restored

    def _restore_graph_chat_split(self, value: object):
        if not isinstance(value, list) or len(value) != 2:
            return
        try:
            sizes = [max(80, int(value[0])), max(120, int(value[1]))]
        except (TypeError, ValueError):
            return
        self._canvas_splitter.setSizes(sizes)

    def _restore_view(self, view: object):
        if not isinstance(view, dict):
            self._fit_graph()
            return
        try:
            zoom = max(self._graph.MIN_ZOOM, min(self._graph.MAX_ZOOM, float(view.get("zoom") or 1.0)))
            center = view.get("center")
            x = float(center.get("x")) if isinstance(center, dict) else 0.0
            y = float(center.get("y")) if isinstance(center, dict) else 0.0
        except (TypeError, ValueError):
            self._fit_graph()
            return
        self._graph.ensure_scene_rect_contains(QPointF(x, y))
        self._graph.zoom_reset()
        self._graph.zoom_by(zoom)
        self._graph.centerOn(QPointF(x, y))
        self._graph.expand_scene_around_viewport()

    def _on_node_moved(self, _node: _GraphNode):
        if self._layouting_graph:
            return
        if self._frames:
            self._sync_graph_frames()
        self._ensure_scene_for_items()
        self._refresh_edges()
        self._notify_graph_changed()

    def _notify_graph_changed(self):
        self._sync_attention_state()
        if not self._restoring_graph:
            self._record_undo_snapshot()
            self.graph_changed.emit()

    def has_attention(self) -> bool:
        return self._has_attention()

    def _has_attention(self) -> bool:
        return self._question_attention or self._run_attention or any(node.status == "review" for node in self._nodes.values())

    def _set_question_attention(self, active: bool):
        active = bool(active)
        if self._question_attention == active:
            return
        self._question_attention = active
        self._sync_attention_state()

    def _set_run_attention(self, active: bool):
        active = bool(active)
        if self._run_attention == active:
            return
        self._run_attention = active
        self._sync_attention_state()

    def _sync_attention_state(self, *, force: bool = False):
        active = self._has_attention()
        if not force and active == self._attention_active:
            return
        self._attention_active = active
        self.attention_changed.emit(active)

    def _restored_node_id(self, value: object) -> int | None:
        try:
            node_id = int(value)
        except (TypeError, ValueError):
            return None
        return node_id if node_id in self._nodes else None

    def _restored_frame_id(self, value: object) -> int | None:
        try:
            frame_id = int(value)
        except (TypeError, ValueError):
            return None
        return frame_id if frame_id in self._frames else None

    def _refresh_edges(self):
        for edge in self._edges:
            source = self._nodes.get(edge.source_id)
            target = self._nodes.get(edge.target_id)
            if source is not None and target is not None:
                self._update_edge(edge.item, source, target, edge.kind, edge.source_port, edge.target_port)

    def _update_edge(
        self,
        edge: QGraphicsPathItem,
        source: _GraphNode,
        target: _GraphNode,
        kind: str = "requires",
        source_port: str = "out",
        target_port: str = "in",
    ):
        p = palette()
        start = source.output_port_scene_pos(source_port)
        end = target.input_port_scene_pos(target_port)
        dx = max(80, abs(end.x() - start.x()) * 0.45)
        path = QPainterPath(start)
        path.cubicTo(start + QPointF(dx, 0), end - QPointF(dx, 0), end)
        edge.setPath(path)
        color = QColor(edge_color(kind, p))
        width = 2.4 if kind == "split" else 1.9
        if self._active_node_id in (source.node_id, target.node_id):
            width += 1.2
            color = QColor("#8ab4ff")
        edge.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))

    def _fit_graph(self, node_ids: set[int] | None = None):
        if not self._nodes:
            return
        if not isinstance(node_ids, set):
            node_ids = None
        if node_ids is None:
            rect = self._scene.itemsBoundingRect()
        else:
            node_ids = {node_id for node_id in node_ids if node_id in self._nodes}
            if not node_ids:
                return
            rect = self._nodes_bounding_rect(node_ids)
            for frame in self._frames.values():
                if frame.node_ids & node_ids:
                    frame_rect = frame.sceneBoundingRect()
                    rect = frame_rect if rect.isNull() else rect.united(frame_rect)
        rect = rect.adjusted(-120, -120, 120, 120)
        self._graph.ensure_scene_rect_contains(rect)
        self._graph.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._graph._zoom = max(
            self._graph.MIN_ZOOM,
            min(self._graph.MAX_ZOOM, self._graph.transform().m11()),
        )
        self._graph.expand_scene_around_viewport()
        self._sync_zoom_auto_compaction(self._graph._zoom)

    def _autoformat_graph(self, *, scope_goal_id: int | None = None):
        if not self._nodes:
            return
        scoped_node_ids = None
        is_scoped_autoformat = scope_goal_id is not None
        if scope_goal_id is not None:
            scoped_node_ids = self._scope_node_ids_for_goal(scope_goal_id)
            if not scoped_node_ids:
                return
        visible_node_ids = {node_id for node_id, node in self._nodes.items() if node.isVisible()}
        if scoped_node_ids is not None:
            scoped_node_ids &= visible_node_ids
        else:
            scoped_node_ids = visible_node_ids
        if not scoped_node_ids:
            return
        view_center = self._graph.mapToScene(self._graph.viewport().rect().center())
        view_transform = self._graph.transform()
        view_zoom = self._graph._zoom
        positions = self._autoformat_positions(scoped_node_ids)
        self._layouting_graph = True
        try:
            for node_id, point in positions.items():
                node = self._nodes.get(node_id)
                if node is not None:
                    node.setPos(point)
        finally:
            self._layouting_graph = False
        self._sync_graph_frames(scoped_node_ids if is_scoped_autoformat else None)
        self._ensure_scene_for_items()
        self._refresh_edges()
        self._graph.setTransform(view_transform)
        self._graph._zoom = view_zoom
        self._graph.centerOn(view_center)
        self._graph.expand_scene_around_viewport()
        self._sync_counts()
        label = "autoformatted active graph" if is_scoped_autoformat else "autoformatted graph"
        if self._has_cycle():
            self._set_mode(f"{label}; cycle remains ({self._cycle_summary()})")
        else:
            self._set_mode(label)
        self._notify_graph_changed()

    def _scope_node_ids_for_goal(self, scope_goal_id: int | None) -> set[int]:
        scope = self._graph_scope_for_goal(scope_goal_id)
        if scope is None:
            return set()
        return {node_id for node_id in scope.get("node_ids", []) if node_id in self._nodes}

    def _autoformat_positions(self, node_ids: set[int] | None = None) -> dict[int, QPointF]:
        if not self._nodes:
            return {}
        selected_node_ids = set(self._nodes) if node_ids is None else {node_id for node_id in node_ids if node_id in self._nodes}
        if not selected_node_ids:
            return {}
        layout_edges = self._layout_edges(selected_node_ids)
        outgoing: dict[int, set[int]] = {node_id: set() for node_id in selected_node_ids}
        incoming: dict[int, set[int]] = {node_id: set() for node_id in selected_node_ids}
        indegree: dict[int, int] = {node_id: 0 for node_id in selected_node_ids}
        root_goal_ids = {node.node_id for node in self._root_goal_nodes() if node.node_id in selected_node_ids}
        for edge in layout_edges:
            if edge.target_id in root_goal_ids and not self._edge_makes_goal_child(edge):
                continue
            if edge.target_id in outgoing[edge.source_id]:
                continue
            outgoing[edge.source_id].add(edge.target_id)
            incoming[edge.target_id].add(edge.source_id)
            indegree[edge.target_id] += 1

        root_goals = {
            node_id
            for node_id, node in self._nodes.items()
            if node_id in root_goal_ids and node.token.kind == "goal"
        }
        has_root_goal = bool(root_goals)
        levels = {
            node_id: 0 if node_id in root_goals or not has_root_goal else 1
            for node_id in selected_node_ids
        }
        ready = sorted([node_id for node_id, count in indegree.items() if count == 0], key=self._layout_root_sort_key)
        while ready:
            current = ready.pop(0)
            for target in sorted(outgoing[current], key=self._node_sort_key):
                levels[target] = max(levels[target], levels[current] + 1)
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
                    ready.sort(key=self._layout_root_sort_key)
        self._align_layout_input_levels(levels, layout_edges, selected_node_ids)
        branch_spans = self._layout_branch_spans(selected_node_ids, outgoing)

        columns: dict[int, list[int]] = {}
        for node_id, level in levels.items():
            columns.setdefault(level, []).append(node_id)

        bounds = self._nodes_bounding_rect(selected_node_ids)
        left = bounds.left() if not bounds.isNull() else -280.0
        max_width = 0.0
        max_height = 0.0
        for node_id in selected_node_ids:
            node = self._nodes[node_id]
            if node.token.kind == "goal" and node.is_collapsed():
                max_width = max(max_width, _GraphNode.COLLAPSED_WIDTH)
                max_height = max(max_height, _GraphNode.COLLAPSED_HEIGHT)
            else:
                max_width = max(max_width, _GraphNode.WIDTH)
                max_height = max(max_height, _GraphNode.HEIGHT)
        x_gap = max(max_width, _GraphNode.COLLAPSED_WIDTH) + 150
        y_gap = max(max_height, _GraphNode.COLLAPSED_HEIGHT) + 130
        positions: dict[int, QPointF] = {}
        slot_by_node: dict[int, float] = {}
        for level in sorted(columns):
            desired_slots: dict[int, float] = {}
            for node_id in columns[level]:
                parent_slots: list[float] = []
                for parent_id in incoming.get(node_id, ()):
                    if parent_id not in slot_by_node:
                        continue
                    siblings = sorted(
                        [child_id for child_id in outgoing.get(parent_id, ()) if levels.get(child_id) == level],
                        key=self._node_sort_key,
                    )
                    if node_id in siblings:
                        offset = self._layout_sibling_offsets(siblings, branch_spans).get(node_id, 0.0)
                    else:
                        offset = 0.0
                    parent_slots.append(slot_by_node[parent_id] + offset)
                if parent_slots:
                    desired_slots[node_id] = sum(parent_slots) / len(parent_slots)
                else:
                    y, _x, _fallback_id = self._node_sort_key(node_id)
                    desired_slots[node_id] = y / y_gap if y else 0.0

            self._anchor_layout_input_slots(level, columns[level], desired_slots, incoming, outgoing, levels)
            placed_slots: list[float] = []
            for node_id in sorted(columns[level], key=lambda candidate: (desired_slots[candidate], self._layout_slot_priority(candidate), *self._node_sort_key(candidate))):
                slot = desired_slots[node_id]
                while any(abs(slot - placed) < 0.95 for placed in placed_slots):
                    slot += 1.0
                placed_slots.append(slot)
                slot_by_node[node_id] = slot
                positions[node_id] = QPointF(left + level * x_gap, slot * y_gap)
        return positions

    def _anchor_layout_input_slots(
        self,
        level: int,
        column_node_ids: list[int],
        desired_slots: dict[int, float],
        incoming: dict[int, set[int]],
        outgoing: dict[int, set[int]],
        levels: dict[int, int],
    ):
        input_node_ids = [
            node_id
            for node_id in column_node_ids
            if self._is_layout_input_node(node_id) and not incoming.get(node_id)
        ]
        for node_id in sorted(input_node_ids, key=self._node_sort_key):
            peer_slots: list[float] = []
            for target_id in outgoing.get(node_id, ()):
                if levels.get(target_id) != level + 1:
                    continue
                if not self._is_layout_input_target(target_id):
                    continue
                for peer_id in incoming.get(target_id, ()):
                    if peer_id == node_id or levels.get(peer_id) != level:
                        continue
                    if peer_id in desired_slots and not self._is_layout_input_node(peer_id):
                        peer_slots.append(desired_slots[peer_id])
            if peer_slots:
                desired_slots[node_id] = sum(peer_slots) / len(peer_slots)

    def _is_layout_input_node(self, node_id: int) -> bool:
        node = self._nodes.get(node_id)
        return node is not None and node.token.kind in {"scope", "context", "evidence", "decision"}

    def _is_layout_input_target(self, node_id: int) -> bool:
        node = self._nodes.get(node_id)
        return node is not None and node.token.kind in {"operation", "dod", "context", "evidence", "decision"}

    def _layout_slot_priority(self, node_id: int) -> int:
        return 1 if self._is_layout_input_node(node_id) else 0

    def _layout_branch_spans(
        self,
        selected_node_ids: set[int],
        outgoing: dict[int, set[int]],
    ) -> dict[int, float]:
        spans: dict[int, float] = {}
        visiting: set[int] = set()

        def span_for(node_id: int) -> float:
            if node_id in spans:
                return spans[node_id]
            if node_id in visiting:
                return 1.0
            visiting.add(node_id)
            children = sorted(
                [child_id for child_id in outgoing.get(node_id, ()) if child_id in selected_node_ids],
                key=self._node_sort_key,
            )
            if not children:
                span = 1.0
            elif len(children) == 1:
                span = max(1.0, span_for(children[0]))
            else:
                span = max(1.0, sum(span_for(child_id) for child_id in children))
            visiting.remove(node_id)
            spans[node_id] = span
            return span

        for node_id in selected_node_ids:
            span_for(node_id)
        return spans

    @staticmethod
    def _layout_sibling_offsets(siblings: list[int], branch_spans: dict[int, float]) -> dict[int, float]:
        if not siblings:
            return {}
        padded_spans = [max(1.0, branch_spans.get(node_id, 1.0)) for node_id in siblings]
        total = sum(padded_spans)
        cursor = -total / 2.0
        offsets: dict[int, float] = {}
        for node_id, span in zip(siblings, padded_spans):
            offsets[node_id] = cursor + span / 2.0
            cursor += span
        return offsets

    def _align_layout_input_levels(
        self,
        levels: dict[int, int],
        layout_edges: list[CanvasEdge],
        selected_node_ids: set[int],
    ):
        for edge in layout_edges:
            if edge.source_id not in selected_node_ids or edge.target_id not in selected_node_ids:
                continue
            source = self._nodes.get(edge.source_id)
            target = self._nodes.get(edge.target_id)
            if source is None or target is None:
                continue
            if not self._is_layout_input_node(edge.source_id) or not self._is_layout_input_target(edge.target_id):
                continue
            target_level = levels.get(edge.target_id)
            if target_level is None or target_level <= 1:
                continue
            levels[edge.source_id] = max(levels.get(edge.source_id, 1), target_level - 1)

    def _sync_graph_frames(self, node_ids: set[int] | None = None):
        if not self._nodes:
            if node_ids is None:
                self._clear_frames()
            return
        scoped_node_ids = None if node_ids is None else {node_id for node_id in node_ids if node_id in self._nodes}
        if scoped_node_ids is not None and not scoped_node_ids:
            return
        groups = self._frame_groups()
        if scoped_node_ids is not None:
            groups = [(root_id, group_node_ids) for root_id, group_node_ids in groups if group_node_ids & scoped_node_ids]
        existing_by_key = {self._frame_key(frame.root_id, frame.node_ids): frame for frame in self._frames.values()}
        existing_color_by_root = {
            int(frame.root_id): frame.color
            for frame in self._frames.values()
            if frame.root_id is not None
        }
        for node in self._nodes.values():
            if node.token.kind != "goal" or not node.is_collapsed():
                continue
            color = existing_color_by_root.get(node.node_id)
            if color is None:
                color = self._frame_color_for_group(node.node_id, {node.node_id})
            node.set_collapsed_frame_color(color)
        used: set[int] = set()
        for root_id, node_ids in groups:
            rect = self._frame_rect_for_nodes(node_ids)
            if rect.isNull():
                continue
            key = self._frame_key(root_id, node_ids)
            frame = existing_by_key.get(key)
            if frame is None:
                title = self._frame_title_for_group(root_id, node_ids)
                color = existing_color_by_root.get(root_id) if root_id is not None else None
                color = color or self._frame_color_for_group(root_id, node_ids)
                frame = self._create_frame(title, color, rect, root_id=root_id, node_ids=node_ids)
            else:
                frame.root_id = root_id
                frame.node_ids = set(node_ids)
                frame.set_rect(rect)
            root = self._nodes.get(root_id) if root_id is not None else None
            if root is not None and root.token.kind == "goal":
                root.set_collapsed_frame_color(frame.color)
            used.add(frame.frame_id)
        for frame_id, frame in list(self._frames.items()):
            if frame_id in used:
                continue
            if scoped_node_ids is not None and not (frame.node_ids & scoped_node_ids):
                continue
            if frame_id not in used:
                self._scene.removeItem(frame)
                self._frames.pop(frame_id, None)
        self._sync_frame_z_order()

    def _sync_frame_z_order(self):
        frames = sorted(
            self._frames.values(),
            key=lambda frame: frame.scene_rect().width() * frame.scene_rect().height(),
            reverse=True,
        )
        for index, frame in enumerate(frames):
            frame.setZValue(-30 + index)

    def _frame_groups(self) -> list[tuple[int | None, set[int]]]:
        adjacency = self._weak_adjacency()
        pending = set(self._nodes)
        root_ids = {node.node_id for node in self._root_goal_nodes()}
        groups: list[tuple[int | None, set[int]]] = []
        while pending:
            start = min(pending)
            component: set[int] = set()
            stack = [start]
            while stack:
                node_id = stack.pop()
                if node_id in component:
                    continue
                component.add(node_id)
                for other_id in adjacency.get(node_id, ()):
                    if other_id in self._nodes and other_id not in component:
                        stack.append(other_id)
            pending -= component
            roots = sorted(node_id for node_id in component if node_id in root_ids)
            if not roots:
                roots = sorted(node_id for node_id in component if self._nodes[node_id].token.kind == "goal")
            frame_root_id = roots[0] if roots else None
            frame_root = self._nodes.get(frame_root_id) if frame_root_id is not None else None
            if frame_root is None or not frame_root.is_collapsed():
                groups.append((frame_root_id, component))
            for goal_id in sorted(
                (
                    node_id
                    for node_id in component
                    if node_id != frame_root_id
                    and self._nodes[node_id].token.kind == "goal"
                    and not self._nodes[node_id].is_collapsed()
                ),
                key=self._node_sort_key,
            ):
                subtree = self._goal_frame_subtree(goal_id, component)
                if subtree:
                    groups.append((goal_id, subtree))
        return sorted(groups, key=lambda item: self._frame_group_sort_key(item[0], item[1]))

    def _goal_frame_subtree(self, goal_id: int, component: set[int]) -> set[int]:
        subtree: set[int] = set()
        stack = [goal_id]
        while stack:
            node_id = stack.pop()
            if node_id in subtree or node_id not in component:
                continue
            subtree.add(node_id)
            for child_id in self._outgoing_edge_counts.get(node_id, {}):
                if child_id in component and child_id not in subtree:
                    stack.append(child_id)
        return subtree

    def _frame_group_sort_key(self, root_id: int | None, node_ids: set[int]) -> tuple[float, float, int]:
        anchor_id = root_id if root_id is not None else min(node_ids)
        node = self._nodes.get(anchor_id)
        if node is None:
            return (0.0, 0.0, anchor_id)
        return (float(node.pos().y()), float(node.pos().x()), anchor_id)

    @staticmethod
    def _frame_key(root_id: int | None, node_ids: set[int]) -> tuple[str, int]:
        if root_id is not None:
            return ("root", int(root_id))
        return ("loose", min(node_ids) if node_ids else 0)

    def _frame_title_for_group(self, root_id: int | None, node_ids: set[int]) -> str:
        if root_id is not None and root_id in self._nodes:
            return self._nodes[root_id].token.title
        if len(node_ids) == 1:
            return self._nodes[next(iter(node_ids))].token.title
        return "Graph"

    def _frame_color_for_group(self, root_id: int | None, node_ids: set[int]) -> str:
        if root_id is not None and root_id in {node.node_id for node in self._root_goal_nodes()}:
            return "#2f8f62"
        if root_id is not None:
            return "#3b82f6"
        return "#3b82f6"

    def _frame_rect_for_nodes(self, node_ids: set[int]) -> QRectF:
        rect = QRectF()
        for node_id in node_ids:
            node = self._nodes.get(node_id)
            if node is None or not node.isVisible():
                continue
            node_rect = node.sceneBoundingRect()
            rect = node_rect if rect.isNull() else rect.united(node_rect)
        if rect.isNull():
            return rect
        return rect.adjusted(-72, -54, 72, 66)

    def _clear_frames(self):
        for frame in list(self._frames.values()):
            self._scene.removeItem(frame)
        self._frames.clear()

    def _nodes_bounding_rect(self, node_ids: set[int] | None = None) -> QRectF:
        rect = QRectF()
        selected_node_ids = None if node_ids is None else {node_id for node_id in node_ids if node_id in self._nodes}
        for node_id, node in self._nodes.items():
            if selected_node_ids is not None and node_id not in selected_node_ids:
                continue
            node_rect = node.sceneBoundingRect()
            rect = node_rect if rect.isNull() else rect.united(node_rect)
        return rect

    def _layout_edges(self, node_ids: set[int] | None = None) -> list[CanvasEdge]:
        selected_node_ids = set(self._nodes) if node_ids is None else {node_id for node_id in node_ids if node_id in self._nodes}
        accepted: list[CanvasEdge] = []
        accepted_outgoing: dict[int, set[int]] = {node_id: set() for node_id in selected_node_ids}
        for edge in sorted(self._edges, key=self._layout_edge_sort_key):
            if edge.source_id not in selected_node_ids or edge.target_id not in selected_node_ids:
                continue
            if edge.source_id == edge.target_id:
                continue
            if self._layout_reaches(edge.target_id, edge.source_id, accepted_outgoing):
                continue
            accepted.append(edge)
            accepted_outgoing.setdefault(edge.source_id, set()).add(edge.target_id)
        return accepted

    def _layout_reaches(self, start_id: int, target_id: int, outgoing: dict[int, set[int]]) -> bool:
        pending = [start_id]
        seen: set[int] = set()
        while pending:
            node_id = pending.pop()
            if node_id == target_id:
                return True
            if node_id in seen:
                continue
            seen.add(node_id)
            pending.extend(outgoing.get(node_id, ()))
        return False

    def _layout_edge_sort_key(self, edge: CanvasEdge) -> tuple[int, tuple[float, float, int], tuple[float, float, int], int]:
        return (
            self._layout_edge_priority(edge),
            self._node_sort_key(edge.source_id),
            self._node_sort_key(edge.target_id),
            edge.target_id,
        )

    @staticmethod
    def _layout_edge_priority(edge: CanvasEdge) -> int:
        priorities = {
            "split": 0,
            "requires": 1,
            "assigns": 1,
            "owns": 1,
            "reads": 2,
            "context": 2,
            "informs": 2,
            "guides": 2,
            "then": 3,
            "produces": 3,
            "decides": 4,
            "source": 4,
            "defines_done": 4,
            "reviews": 5,
            "needs_review": 6,
            "supports": 7,
            "satisfies": 7,
            "accepts": 7,
            "feedback": 7,
            "resolves": 8,
        }
        return priorities.get(edge.kind, 5)

    def _layout_root_sort_key(self, node_id: int) -> tuple[int, float, float, int]:
        node = self._nodes.get(node_id)
        is_goal = 0 if node is not None and node.token.kind == "goal" else 1
        y, x, fallback_id = self._node_sort_key(node_id)
        return (is_goal, y, x, fallback_id)

    def _layout_column_sort_key(
        self,
        node_id: int,
        incoming: dict[int, set[int]],
        order_index: dict[int, int],
    ) -> tuple[float, float, float, int]:
        parents = [order_index[parent_id] for parent_id in incoming.get(node_id, ()) if parent_id in order_index]
        parent_order = sum(parents) / len(parents) if parents else 0.0
        y, x, fallback_id = self._node_sort_key(node_id)
        return (parent_order, y, x, fallback_id)

    def _has_cycle(self) -> bool:
        return bool(self._cyclic_components())

    def _cycle_path_for_new_edge(self, source_id: int, target_id: int) -> list[int]:
        path_back = self._path_between(target_id, source_id)
        if not path_back:
            return []
        return [source_id, *path_back]

    def _path_between(self, start_id: int, target_id: int) -> list[int]:
        pending: list[tuple[int, list[int]]] = [(start_id, [start_id])]
        seen: set[int] = set()
        while pending:
            node_id, path = pending.pop(0)
            if node_id == target_id:
                return path
            if node_id in seen:
                continue
            seen.add(node_id)
            neighbors = sorted(self._outgoing_edge_counts.get(node_id, {}).keys(), key=self._node_sort_key)
            for neighbor_id in neighbors:
                if neighbor_id not in seen:
                    pending.append((neighbor_id, path + [neighbor_id]))
        return []

    def _format_cycle_path(self, node_ids: list[int]) -> str:
        names = [self._nodes[node_id].token.title for node_id in node_ids if node_id in self._nodes]
        if len(names) > 4:
            names = names[:4] + ["..."]
        return " -> ".join(names)

    def _cycle_summary(self) -> str:
        components = self._cyclic_components()
        if not components:
            return ""
        names = [self._nodes[node_id].token.title for node_id in components[0] if node_id in self._nodes]
        if len(names) > 3:
            names = names[:3] + ["..."]
        return " -> ".join(names)

    def _cyclic_components(self) -> list[list[int]]:
        cyclic: list[list[int]] = []
        self_edges = {(edge.source_id, edge.target_id) for edge in self._edges if edge.source_id == edge.target_id}
        for component in self._strongly_connected_components():
            if len(component) > 1 or any((node_id, node_id) in self_edges for node_id in component):
                cyclic.append(component)
        return cyclic

    def _strongly_connected_components(self) -> list[list[int]]:
        index = 0
        stack: list[int] = []
        on_stack: set[int] = set()
        indices: dict[int, int] = {}
        lowlinks: dict[int, int] = {}
        components: list[list[int]] = []
        adjacency: dict[int, list[int]] = {node_id: [] for node_id in self._nodes}
        for edge in self._edges:
            if edge.source_id in self._nodes and edge.target_id in self._nodes:
                adjacency.setdefault(edge.source_id, []).append(edge.target_id)

        def visit(node_id: int):
            nonlocal index
            indices[node_id] = index
            lowlinks[node_id] = index
            index += 1
            stack.append(node_id)
            on_stack.add(node_id)
            for target_id in sorted(adjacency.get(node_id, []), key=self._node_sort_key):
                if target_id not in indices:
                    visit(target_id)
                    lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target_id])
                elif target_id in on_stack:
                    lowlinks[node_id] = min(lowlinks[node_id], indices[target_id])
            if lowlinks[node_id] == indices[node_id]:
                component: list[int] = []
                while stack:
                    member = stack.pop()
                    on_stack.remove(member)
                    component.append(member)
                    if member == node_id:
                        break
                components.append(sorted(component, key=self._node_sort_key))

        for node_id in sorted(self._nodes, key=self._node_sort_key):
            if node_id not in indices:
                visit(node_id)
        return components

    def _node_sort_key(self, node_id: int) -> tuple[float, float, int]:
        node = self._nodes.get(node_id)
        if node is None:
            return (0.0, 0.0, node_id)
        return (float(node.pos().y()), float(node.pos().x()), node_id)

    def _ensure_scene_for_items(self):
        if not self._nodes:
            self._graph.ensure_scene_rect_contains(QPointF(0, 0))
            return
        self._graph.ensure_scene_rect_contains(self._scene.itemsBoundingRect())

    def _sync_root_goal(self):
        roots = {node.node_id for node in self._root_goal_nodes()}
        goal_owned = self._goal_owned_node_ids()
        for node in self._nodes.values():
            node.set_root_goal(node.node_id in roots)
            node.set_unscoped(node.token.kind != "goal" and node.node_id not in goal_owned)

    def _apply_goal_collapse_visibility(self):
        hidden_nodes = self._collapsed_goal_hidden_nodes()
        if hidden_nodes == self._collapsed_hidden_nodes:
            return
        selected_node = self._selected_node()
        selected_node_id = selected_node.node_id if selected_node is not None else None
        selected_frame = self._selected_frame()
        reveal_nodes = self._collapsed_hidden_nodes - hidden_nodes
        hide_nodes = hidden_nodes - self._collapsed_hidden_nodes
        touched_nodes = hide_nodes | reveal_nodes
        for node_id in hide_nodes:
            node = self._nodes.get(node_id)
            if node is None:
                continue
            if node.isVisible():
                node.setVisible(False)
            if node.isSelected():
                node.setSelected(False)
                if self._last_selected_node_id == node_id:
                    self._last_selected_node_id = None
        for node_id in reveal_nodes:
            node = self._nodes.get(node_id)
            if node is not None and not node.isVisible():
                node.setVisible(True)
        for edge in self._edges:
            if edge.source_id not in touched_nodes and edge.target_id not in touched_nodes:
                continue
            should_hide = edge.source_id in hidden_nodes or edge.target_id in hidden_nodes
            if bool(edge.item.isVisible()) == should_hide:
                edge.item.setVisible(not should_hide)
        self._collapsed_hidden_nodes = hidden_nodes
        if (
            selected_node_id is not None
            and selected_node_id not in hidden_nodes
            and self._last_selected_node_id is None
            and selected_node_id in self._nodes
        ):
            self._select_node(self._nodes[selected_node_id])
        elif self._last_selected_node_id is None and selected_frame is None and self._last_selected_frame_id is None:
            self._populate_empty_inspector()

    def _collapsed_goal_hidden_nodes(self) -> set[int]:
        collapsed_goal_ids = {
            node.node_id
            for node in self._nodes.values()
            if node.token.kind == "goal" and node.is_collapsed()
        }
        if not collapsed_goal_ids:
            return set()

        hidden_nodes: set[int] = set()
        seen = set(collapsed_goal_ids)
        queue = list(collapsed_goal_ids)
        while queue:
            node_id = queue.pop()
            for child_id in self._outgoing_edge_counts.get(node_id, {}):
                if child_id in seen:
                    continue
                seen.add(child_id)
                hidden_nodes.add(child_id)
                queue.append(child_id)

        queue = list(seen)
        while queue:
            node_id = queue.pop()
            for parent_id in self._incoming_edge_counts.get(node_id, {}):
                if parent_id in seen:
                    continue
                parent_node = self._nodes.get(parent_id)
                if parent_node is None or parent_node.token.kind == "goal":
                    continue
                outgoing = self._outgoing_edge_counts.get(parent_id, {})
                if not outgoing:
                    continue
                if all(child_id in seen for child_id in outgoing):
                    seen.add(parent_id)
                    hidden_nodes.add(parent_id)
                    queue.append(parent_id)
        return hidden_nodes

    def _root_goal_node(self) -> _GraphNode | None:
        roots = self._root_goal_nodes()
        return roots[0] if roots else None

    def _root_goal_nodes(self) -> list[_GraphNode]:
        goals = [node for node in self._nodes.values() if node.token.kind == "goal"]
        if not goals:
            return []
        targeted = {
            edge.target_id
            for edge in self._edges
            if self._edge_makes_goal_child(edge)
        }
        roots = [node for node in goals if node.node_id not in targeted]
        candidates = roots or goals
        return sorted(candidates, key=lambda node: node.node_id)

    def _edge_makes_goal_child(self, edge: CanvasEdge) -> bool:
        target = self._nodes.get(edge.target_id)
        source = self._nodes.get(edge.source_id)
        if target is None or source is None:
            return False
        return target.token.kind == "goal" and source.token.kind == "goal" and edge.kind == "split"

    def _unscoped_node_ids(self) -> list[int]:
        goal_owned = self._goal_owned_node_ids()
        return sorted(
            node_id
            for node_id, node in self._nodes.items()
            if node.token.kind != "goal" and node_id not in goal_owned
        )

    def _goal_owned_node_ids(self) -> set[int]:
        goal_ids = {node_id for node_id, node in self._nodes.items() if node.token.kind == "goal"}
        if not goal_ids:
            return set()
        adjacency = self._weak_adjacency()
        owned: set[int] = set()
        pending = list(goal_ids)
        while pending:
            node_id = pending.pop()
            if node_id in owned:
                continue
            owned.add(node_id)
            for other_id in adjacency.get(node_id, ()):
                if other_id not in owned:
                    pending.append(other_id)
        return owned

    def _weak_adjacency(self, edges: list[dict] | None = None) -> dict[int, set[int]]:
        if edges is None:
            adjacency: dict[int, set[int]] = {node_id: set() for node_id in self._nodes}
            for source_id, targets in self._outgoing_edge_counts.items():
                adjacency.setdefault(source_id, set()).update(targets)
            for target_id, sources in self._incoming_edge_counts.items():
                adjacency.setdefault(target_id, set()).update(sources)
            return adjacency
        adjacency: dict[int, set[int]] = {node_id: set() for node_id in self._nodes}
        raw_edges = edges if edges is not None else [
            {"source_id": edge.source_id, "target_id": edge.target_id}
            for edge in self._edges
        ]
        for edge in raw_edges:
            try:
                source_id = int(edge["source_id"])
                target_id = int(edge["target_id"])
            except (KeyError, TypeError, ValueError):
                continue
            adjacency.setdefault(source_id, set()).add(target_id)
            adjacency.setdefault(target_id, set()).add(source_id)
        return adjacency

    def _graph_scope_for_goal(self, goal_id: int | None, state: dict | None = None) -> dict | None:
        if goal_id is None:
            return None
        try:
            goal_id = int(goal_id)
        except (TypeError, ValueError):
            return None
        state = state or self.graph_state()
        nodes = {int(node["id"]): node for node in state.get("nodes", []) if "id" in node}
        goal = nodes.get(goal_id)
        if goal is None or str(goal.get("kind") or "") != "goal":
            return None
        edges = [dict(edge) for edge in state.get("edges", []) if isinstance(edge, dict)]
        upstream = self._payload_reachable(edges, goal_id, reverse=True)
        downstream = self._payload_reachable(edges, goal_id, reverse=False)
        scoped = upstream | downstream | {goal_id}
        direct_neighbors = set()
        for edge in edges:
            try:
                source_id = int(edge["source_id"])
                target_id = int(edge["target_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if source_id in scoped or target_id in scoped:
                direct_neighbors.add(source_id)
                direct_neighbors.add(target_id)
        scoped |= direct_neighbors
        scoped &= set(nodes)
        return {
            "mode": "goal",
            "goal_id": goal_id,
            "node_ids": sorted(scoped),
            "outside_node_ids": sorted(set(nodes) - scoped),
        }

    @staticmethod
    def _payload_reachable(edges: list[dict], start_id: int, *, reverse: bool) -> set[int]:
        adjacency: dict[int, list[int]] = {}
        for edge in edges:
            try:
                source_id = int(edge["source_id"])
                target_id = int(edge["target_id"])
            except (KeyError, TypeError, ValueError):
                continue
            left, right = (target_id, source_id) if reverse else (source_id, target_id)
            adjacency.setdefault(left, []).append(right)
        seen: set[int] = set()
        pending = [start_id]
        while pending:
            node_id = pending.pop(0)
            if node_id in seen:
                continue
            seen.add(node_id)
            for next_id in adjacency.get(node_id, ()):
                if next_id not in seen:
                    pending.append(next_id)
        return seen

    def _next_spawn_point(self) -> QPointF:
        center = self._graph.mapToScene(self._graph.viewport().rect().center())
        offset = (len(self._nodes) % 7) * 28
        return center + QPointF(offset, offset)

    def _absolute_ref(self, ref: str) -> str:
        return absolute_ref(ref, self._repo_root)

    def _set_mode(self, text: str):
        self._inspector_lines[2].setText(f"Activity: {text}")

    def _sync_counts(self):
        if not hasattr(self, "_inspector_lines"):
            return
        self._update_cycle_warning()
        self._sync_run_controls()
        active = self._nodes.get(self._active_node_id) if self._active_node_id is not None else None
        if active is not None:
            self._goal.setText(f"Active: {active.token.title}")

    def _sync_run_controls(self):
        if not self._nodes:
            return
        if self._is_graph_agent_running():
            for node in self._nodes.values():
                node.set_runnable(False)
            return
        runnable = self._run_engine.runnable_node_ids(self.graph_state())
        for node in self._nodes.values():
            node.set_runnable(node.token.kind == "goal" and node.node_id in runnable)

    def _update_cycle_warning(self):
        if not hasattr(self, "_cycle_warning"):
            return
        summary = self._cycle_summary()
        if summary:
            self._cycle_warning.setText(f"Cycle: {summary}")
            self._cycle_warning.setToolTip("This graph has a directed loop. It can be discussed, but it cannot run as a linear work plan.")
            self._cycle_warning.setVisible(True)
        else:
            self._cycle_warning.setText("")
            self._cycle_warning.setToolTip("")
            self._cycle_warning.setVisible(False)

    def _advance_status_animation(self):
        for node in self._nodes.values():
            node.advance_status_animation()
