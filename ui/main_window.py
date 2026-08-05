import os

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QSplitterHandle,
    QSizePolicy,
    QApplication,
    QPushButton,
    QFrame,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QByteArray, QSize, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QShortcut, QKeySequence

from storage.repository import ConversationStore
from storage.agent_canvas import CanvasSaveRefused, load_agent_canvas, save_agent_canvas
from storage.settings import (
    CANVAS_ENABLED_KEY,
    CHECK_FOR_UPDATES_KEY,
    DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
    FILE_EDITOR_AUTO_SAVE_KEY,
    FILE_EDITOR_TAB_SPACES_KEY,
    FILE_REVIEW_PROMPT_TEMPLATE_KEY,
    SettingsStore,
    canvas_enabled,
    check_for_updates,
    resume_session,
    update_dismissed_version,
    update_last_checked,
)
from storage.workspace_session import (
    load_workspace_session,
    save_workspace_session,
    session_has_restorable_state,
)
from services.git_snapshot import build_git_snapshot
from services.key_bindings import shortcut_sequences
from services.mcp_tools import start_mcp_capability_warmup
from services.palette import PaletteContext, build_palette_items
from services.processes import get_process_manager
from services.tool_registry import disable_unreviewed_extensions
from services import updates as app_updates
from ui.theme import (
    apply_app_theme,
    current_theme,
    mono_font,
    palette,
    toggle_tab_button_style,
    workbench_mode_bar_style,
    workbench_mode_button_style,
    workbench_mode_group_style,
)
from ui.widgets.left_panel import LeftPanel
from ui.widgets.chat_panel import ChatPanel
from ui.widgets.file_viewer import FileViewerPanel
from ui.widgets.agent_canvas import AgentCanvasPanel
from ui.widgets.workbench_context import WorkbenchContextPanel
from ui.widgets.workspace_dashboard import WorkspaceDashboard
from ui.widgets.context_ring import ContextRing
from ui.widgets.update_banner import UpdateBanner
from ui.widgets.window_chrome import set_chromed_central_widget
import config
from ui.widgets.command_palette import CommandPalette
from ui.widgets.file_search_dialog import FileSearchDialog
from ui.widgets.text_search_dialog import TextSearchDialog


DEFAULT_ACTIVITY_WIDTH = config.DEFAULT_ACTIVITY_WIDTH
MIN_ACTIVITY_WIDTH = config.MIN_ACTIVITY_WIDTH
MAX_ACTIVITY_WIDTH = config.MAX_ACTIVITY_WIDTH
COLLAPSED_ACTIVITY_WIDTH = 0
WORKBENCH_COLLAPSED_WIDTH = 64
WORKBENCH_DEFAULT_CHAT_PERCENT = 40
WORKBENCH_MIN_CHAT_WIDTH = 280
WORKBENCH_MIN_VIEWER_WIDTH = 300


class _WorkbenchSplitterHandle(QSplitterHandle):
    def mouseDoubleClickEvent(self, event):
        splitter = self.splitter()
        if isinstance(splitter, _WorkbenchSplitter):
            splitter.reset_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _WorkbenchSplitter(QSplitter):
    reset_requested = pyqtSignal()
    resized = pyqtSignal()

    def createHandle(self):
        return _WorkbenchSplitterHandle(self.orientation(), self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


def _right_rail_icon(kind: str, *, active: bool = False) -> QIcon:
    p = palette()
    color = QColor(p["TEXT"] if active else p["TEXT_DIM"])
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if kind == "language":
        font = mono_font(13)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "{}")
    else:
        painter.drawRoundedRect(3, 3, 12, 12, 2, 2)
        painter.drawLine(6, 7, 8, 9)
        painter.drawLine(8, 9, 6, 11)
        painter.drawLine(10, 11, 13, 11)
    painter.end()
    return QIcon(pix)


class _InitialGitStatusThread(QThread):
    loaded = pyqtSignal(str, object)

    def __init__(self, repo: str, parent=None):
        super().__init__(parent)
        self._repo = repo

    def run(self):
        self.loaded.emit(self._repo, build_git_snapshot(self._repo, untracked_mode="normal"))


class _ExtensionReviewThread(QThread):
    done = pyqtSignal(int, str, object, str)

    def __init__(self, generation: int, repo: str, parent=None):
        super().__init__(parent)
        self._generation = generation
        self._repo = repo

    def run(self):
        try:
            summaries = disable_unreviewed_extensions(self._repo)
        except Exception as exc:
            self.done.emit(self._generation, self._repo, [], str(exc))
            return
        self.done.emit(self._generation, self._repo, summaries, "")


class _UpdateCheckThread(QThread):
    done = pyqtSignal(object)

    def run(self):
        try:
            result = app_updates.check_for_update()
        except Exception:
            result = None
        self.done.emit(result)


def _startup_workspace(
    saved: dict,
    startup_workspace: str | None = None,
    *,
    prefer_saved_workspace: bool = False,
    launch_cwd: str | None = None,
) -> str:
    if startup_workspace:
        return os.path.abspath(startup_workspace)
    if prefer_saved_workspace:
        workspace = saved.get("workspace_path", "")
        if workspace and os.path.isdir(workspace):
            return os.path.abspath(workspace)
    return os.path.abspath(launch_cwd or os.getcwd())


class MainWindow(QMainWindow):
    def __init__(
        self,
        startup_workspace: str | None = None,
        *,
        prefer_saved_workspace: bool = False,
    ):
        super().__init__()
        self.setWindowTitle("AICHS")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._settings = SettingsStore()
        saved = self._settings.load()
        self._pending_default_activity_width = False
        self._extension_review_prompt_shown = False
        self._first_paint_tasks_started = False
        self._initial_git_status_thread: _InitialGitStatusThread | None = None
        self._initial_git_changes = None
        self._initial_git_snapshot = None
        self._extension_review_generation = 0
        self._extension_review_threads: list[_ExtensionReviewThread] = []
        self._update_check_thread: _UpdateCheckThread | None = None
        self._workspace_context_state: dict[str, int | bool] | None = None
        self._pending_workspace_session: dict | None = None
        self._workbench_preview_host_tab = None
        self._workbench_preview_pinned_chat = False
        self._workbench_user_split = False
        self._last_open_workbench_sizes: list[int] | None = None
        self._session_restore_started = False
        self._session_save_timer = QTimer(self)
        self._session_save_timer.setSingleShot(True)
        self._session_save_timer.setInterval(500)
        self._session_save_timer.timeout.connect(self._save_workspace_session)
        self._canvas_save_timer = QTimer(self)
        self._canvas_save_timer.setSingleShot(True)
        self._canvas_save_timer.setInterval(500)
        self._canvas_save_timer.timeout.connect(self._save_agent_canvas)
        self._canvas_conversation_refresh_timer = QTimer(self)
        self._canvas_conversation_refresh_timer.setSingleShot(True)
        self._canvas_conversation_refresh_timer.setInterval(600)
        self._canvas_conversation_refresh_timer.timeout.connect(self._flush_canvas_conversation_refresh)
        self._pending_canvas_conversation_refreshes: set[str] = set()

        os.chdir(
            _startup_workspace(
                saved,
                startup_workspace,
                prefer_saved_workspace=prefer_saved_workspace,
            )
        )

        repo  = os.getcwd()
        self._canvas_workspace = repo
        self._agent_canvas_restore_pending_workspace: str | None = repo
        store = ConversationStore(repo)

        self._left = LeftPanel(
            store,
            repo,
            settings=self._settings,
            current_model_getter=lambda: self._chat.current_model(),
            defer_refresh=True,
        )
        self._left.set_canvas_enabled(canvas_enabled(self._settings.load()))
        self._left.setMinimumWidth(MIN_ACTIVITY_WIDTH)
        self._left.setMaximumWidth(MAX_ACTIVITY_WIDTH)

        self._viewer = FileViewerPanel(repo, settings=self._settings)
        self._viewer.hide()
        self._viewer.all_closed.connect(self._close_file)
        self._viewer.diagnostic_fix_requested.connect(self._chat_draft_diagnostic_fix)
        self._viewer.active_file_changed.connect(self._reveal_active_file)
        self._viewer.dirty_file_changed.connect(self._left.set_file_dirty)

        self._chat = ChatPanel(store, cwd=repo, settings=self._settings)
        self._sync_chat_width_mode()

        self._context = WorkbenchContextPanel()
        self._context.setMinimumWidth(220)
        self._context.setMaximumWidth(380)
        self._context.collapse_requested.connect(self._collapse_context)
        self._context.language_refresh_requested.connect(self._viewer.refresh_active_language)
        self._context.language_format_requested.connect(self._viewer.format_active_language)
        self._context.language_fix_safe_requested.connect(self._viewer.fix_safe_active_language)
        self._context.language_chat_file_requested.connect(
            self._viewer.draft_active_language_file_question
        )
        self._context.language_quick_fix_requested.connect(self._viewer.show_active_language_actions)
        self._context.language_chat_fix_requested.connect(self._viewer.draft_active_language_fix)
        self._context.language_chat_fix_all_requested.connect(self._viewer.draft_active_language_fix_all)
        self._viewer.language_context_changed.connect(self._context.set_language_context)
        self._viewer.markdown_preview_pane_changed.connect(
            self._sync_workbench_markdown_preview_pane
        )

        self._context_tab = QPushButton()
        self._context_tab.setToolTip("Show run log")
        self._context_tab.setAccessibleName("Run Log")
        self._context_tab.setCheckable(True)
        self._context_tab.setIcon(_right_rail_icon("run_log"))
        self._context_tab.setIconSize(QSize(18, 18))
        self._context_tab.setFixedWidth(30)
        self._context_tab.setFixedHeight(34)
        self._context_tab.clicked.connect(lambda _checked=False: self._show_context_panel("run_log"))

        self._language_context_tab = QPushButton()
        self._language_context_tab.setToolTip("Show language")
        self._language_context_tab.setAccessibleName("Language")
        self._language_context_tab.setCheckable(True)
        self._language_context_tab.setIcon(_right_rail_icon("language"))
        self._language_context_tab.setIconSize(QSize(18, 18))
        self._language_context_tab.setFixedWidth(30)
        self._language_context_tab.setFixedHeight(34)
        self._language_context_tab.clicked.connect(
            lambda _checked=False: self._show_context_panel("language")
        )

        context_handle = QWidget()
        context_handle.setObjectName("contextHandle")
        context_handle_layout = QVBoxLayout(context_handle)
        context_handle_layout.setContentsMargins(0, 0, 0, 0)
        context_handle_layout.setSpacing(6)
        context_handle_layout.addStretch()
        context_handle_layout.addWidget(
            self._context_tab,
            0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        context_handle_layout.addWidget(
            self._language_context_tab,
            0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        context_handle_layout.addStretch()

        self._context_shell = QWidget()
        self._context_shell.setMinimumWidth(30)
        self._context_shell.setMaximumWidth(420)
        context_shell_layout = QHBoxLayout(self._context_shell)
        context_shell_layout.setContentsMargins(0, 0, 0, 0)
        context_shell_layout.setSpacing(0)

        self._context_stack = QStackedWidget()
        self._context_stack.addWidget(self._context)
        self._context_stack.addWidget(context_handle)
        context_shell_layout.addWidget(self._context_stack, 1)

        self._workbench_left = QStackedWidget()
        self._workbench_left.addWidget(self._chat)
        self._workbench_preview_host = QWidget()
        self._workbench_preview_layout = QVBoxLayout(self._workbench_preview_host)
        self._workbench_preview_layout.setContentsMargins(0, 0, 0, 0)
        self._workbench_preview_layout.setSpacing(0)
        self._workbench_left.addWidget(self._workbench_preview_host)
        self._agent_canvas = AgentCanvasPanel(repo, settings=self._settings)
        self._agent_canvas.set_lazy_restore_callback(self._ensure_agent_canvas_restored)
        self._agent_canvas.open_file_requested.connect(self._open_file_from_canvas)
        self._agent_canvas.open_conversation_requested.connect(self._open_canvas_conversation)
        self._agent_canvas.conversation_created.connect(lambda _conv_id: self._left.refresh())
        self._agent_canvas.conversation_updated.connect(self._on_canvas_conversation_updated)
        self._agent_canvas.conversation_chunk.connect(self._on_canvas_conversation_chunk)
        self._agent_canvas.conversation_tool_called.connect(self._on_canvas_conversation_tool_called)
        self._agent_canvas.conversation_tool_result.connect(self._on_canvas_conversation_tool_result)
        self._agent_canvas.conversation_run_finished.connect(self._on_canvas_conversation_run_finished)
        self._agent_canvas.graph_changed.connect(self._schedule_canvas_save)
        self._agent_canvas.attention_changed.connect(self._set_canvas_attention)
        self._workbench_left.addWidget(self._agent_canvas)
        self._workbench_chat_rail = QWidget()
        self._workbench_chat_rail.setObjectName("workbenchChatRail")
        chat_rail_layout = QVBoxLayout(self._workbench_chat_rail)
        chat_rail_layout.setContentsMargins(0, 8, 0, 8)
        chat_rail_layout.setSpacing(0)
        chat_rail_layout.addStretch(1)
        self._workbench_chat_rail_ring = ContextRing(self._workbench_chat_rail)
        self._workbench_chat_rail_ring.setToolTip("Show chat")
        self._workbench_chat_rail_ring.clicked.connect(
            lambda: self._restore_workbench_side("chat")
        )
        chat_rail_layout.addWidget(
            self._workbench_chat_rail_ring,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        chat_rail_layout.addStretch(1)
        self._workbench_left.addWidget(self._workbench_chat_rail)
        self._workbench_left.setMinimumWidth(0)
        self._viewer.setMinimumWidth(0)
        self._chat.setMinimumWidth(0)
        self._agent_canvas.setMinimumWidth(0)
        self._workbench_left.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._viewer.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        self._workbench = _WorkbenchSplitter(Qt.Orientation.Horizontal)
        self._workbench.addWidget(self._workbench_left)
        self._workbench.addWidget(self._viewer)
        self._workbench.setStretchFactor(0, 2)
        self._workbench.setStretchFactor(1, 3)
        self._workbench.setChildrenCollapsible(True)
        self._workbench.setCollapsible(0, True)
        self._workbench.setCollapsible(1, True)

        self._workbench_mode = "chat"
        self._workbench_mode_buttons: dict[str, QPushButton] = {}
        self._workbench_host = QWidget()
        workbench_host_layout = QVBoxLayout(self._workbench_host)
        workbench_host_layout.setContentsMargins(0, 0, 0, 0)
        workbench_host_layout.setSpacing(0)
        self._workbench_mode_bar = QWidget()
        self._workbench_mode_bar.setObjectName("workbenchModeBar")
        mode_layout = QHBoxLayout(self._workbench_mode_bar)
        mode_layout.setContentsMargins(8, 0, 4, 0)
        mode_layout.setSpacing(0)
        self._workbench_mode_group = QFrame()
        self._workbench_mode_group.setObjectName("workbenchModeGroup")
        mode_group_layout = QHBoxLayout(self._workbench_mode_group)
        mode_group_layout.setContentsMargins(3, 3, 3, 3)
        mode_group_layout.setSpacing(2)
        for key, label in (("chat", "Chat Focus"), ("review", "Review"), ("editor", "Editor Focus")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName(f"workbenchMode{key.title()}")
            button.clicked.connect(lambda _checked=False, mode=key: self._set_workbench_mode(mode))
            self._workbench_mode_buttons[key] = button
            mode_group_layout.addWidget(button)
        mode_layout.addWidget(self._workbench_mode_group)
        workbench_host_layout.addWidget(self._workbench, 1)

        self._workbench_restore_chat_btn = QPushButton("Chat", self._workbench_left)
        self._workbench_restore_chat_btn.setObjectName("workbenchRestoreChat")
        self._workbench_restore_chat_btn.setToolTip("Show chat")
        self._workbench_restore_chat_btn.setAccessibleName("Show chat")
        self._workbench_restore_chat_btn.setFixedSize(48, 64)
        self._workbench_restore_chat_btn.hide()
        self._workbench_restore_chat_btn.clicked.connect(
            lambda _checked=False: self._restore_workbench_side("chat")
        )

        self._workbench_restore_files_btn = QPushButton("Files", self._viewer)
        self._workbench_restore_files_btn.setObjectName("workbenchRestoreFiles")
        self._workbench_restore_files_btn.setToolTip("Show file editor")
        self._workbench_restore_files_btn.setAccessibleName("Show file editor")
        self._workbench_restore_files_btn.setFixedSize(48, 64)
        self._workbench_restore_files_btn.hide()
        self._workbench_restore_files_btn.clicked.connect(
            lambda _checked=False: self._restore_workbench_side("files")
        )

        self._workspace_dashboard = WorkspaceDashboard(repo, defer_refresh=True)
        self._workspace_dashboard.switch_requested.connect(self._switch_workspace)
        self._workspace_dashboard.conversation_requested.connect(self._load_conversation)
        self._workspace_dashboard.open_file_requested.connect(self._open_file)
        self._workspace_dashboard.new_chat_requested.connect(self._new_conversation)
        self._workspace_dashboard.file_search_requested.connect(self._open_file_search)
        self._workspace_dashboard.text_search_requested.connect(self._open_text_search)
        self._left.set_inspect_panel(self._context)

        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(self._workbench_host)
        self._center_stack.addWidget(self._workspace_dashboard)

        self._root_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._root_splitter.setObjectName("activityRootSplitter")
        self._root_splitter.addWidget(self._left)
        self._root_splitter.addWidget(self._center_stack)
        self._root_splitter.setStretchFactor(0, 0)
        self._root_splitter.setStretchFactor(1, 1)
        self._root_splitter.setCollapsible(0, False)

        self._left.selected.connect(self._load_conversation)
        self._left.new_chat.connect(self._new_conversation)
        self._left.renamed.connect(self._chat.update_title)
        self._left.deleted.connect(self._chat.on_conversation_deleted)
        self._left.file_open.connect(self._open_file)
        self._left.git_file_open.connect(self._open_git_file)
        self._left.git_help_requested.connect(self._chat_draft_diagnostic_fix)
        self._left.commit_summarized.connect(
            lambda text: self._chat_draft_diagnostic_fix(text, [])
        )
        self._left.file_attach.connect(self._chat.attach_file)
        self._left.file_search_requested.connect(self._open_file_search)
        self._left.text_search_requested.connect(self._open_text_search)
        self._left.extensions_requested.connect(self._chat.show_extensions)
        self._left.mcp_requested.connect(self._chat.show_mcp)
        self._left.workspace_switch_requested.connect(self._switch_workspace)
        self._left.canvas_requested.connect(self._show_agent_canvas)
        self._left.activity_selected.connect(self._on_activity_selected)
        self._left.activity_panel_collapsed_changed.connect(self._on_activity_panel_collapsed)
        self._chat.saved.connect(self._left.refresh)
        self._chat.conversation_created.connect(self._left.select_conversation)
        self._chat.open_code.connect(self._open_content)
        self._chat.open_file.connect(self._open_file)
        self._chat.file_written.connect(self._left.mark_file_touched)
        self._chat.file_write_completed.connect(self._refresh_open_file)
        self._chat.run_log_activity.connect(self._context.add_tool_activity)
        self._chat.conversation_changed.connect(self._context.set_current_conversation)
        self._chat.conversation_changed.connect(lambda _conv_id: self._schedule_session_save())
        self._viewer.active_file_changed.connect(lambda _path: self._schedule_session_save())
        self._workbench.splitterMoved.connect(self._on_workbench_splitter_moved)
        self._workbench.reset_requested.connect(self._reset_workbench_split)
        self._workbench.resized.connect(self._sync_workbench_restore_tabs)
        self._left.settings_changed.connect(self._apply_appearance)

        self._setup_shortcuts()

        set_chromed_central_widget(self, self._root_splitter)
        self._update_banner = UpdateBanner(self)
        self._update_banner.dismissed.connect(self._on_update_dismissed)
        self._update_banner.remind_later.connect(self._update_banner.hide)
        shell_layout = getattr(self, "_window_shell_layout", None)
        if shell_layout is not None:
            shell_layout.insertWidget(1, self._update_banner)
        self._window_chrome.set_toolbar_content(
            leading=self._left.detach_activity_rail(),
            trailing=self._workbench_mode_bar,
        )

        self._restore_layout(saved)
        self._apply_appearance()
        self._context.set_language_context(self._viewer.active_language_context())


    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_default_activity_width:
            self._apply_default_activity_width()
        if self._first_paint_tasks_started:
            self._sync_workbench_markdown_preview_pane()
            return
        self._first_paint_tasks_started = True
        QTimer.singleShot(0, self._run_after_first_paint)

    def _run_after_first_paint(self):
        self._review_new_extensions()
        self._maybe_check_for_updates()
        start_mcp_capability_warmup(os.getcwd())
        if not self._pending_default_activity_width:
            self._start_initial_git_status_refresh()
            self._maybe_restore_workspace_session()
            return
        self._pending_default_activity_width = False
        self._apply_default_activity_width()
        self._start_initial_git_status_refresh()
        self._maybe_restore_workspace_session()

    def _start_initial_git_status_refresh(self):
        if self._initial_git_status_thread is not None:
            return
        self._initial_git_status_thread = _InitialGitStatusThread(os.getcwd(), self)
        self._initial_git_status_thread.loaded.connect(self._apply_initial_git_status)
        self._initial_git_status_thread.finished.connect(self._clear_initial_git_status_thread)
        self._initial_git_status_thread.start()

    def _apply_initial_git_status(self, repo: str, snapshot):
        if os.path.normcase(os.path.abspath(repo)) != os.path.normcase(os.getcwd()):
            return
        self._initial_git_snapshot = snapshot
        self._initial_git_changes = list(snapshot.changes)
        self._left.apply_initial_git_snapshot(snapshot)

    def _clear_initial_git_status_thread(self):
        self._initial_git_status_thread = None

    def _apply_default_activity_width(self):
        if not self._left.is_activity_panel_collapsed():
            self._set_activity_panel_width(DEFAULT_ACTIVITY_WIDTH)

    def _setup_shortcuts(self):
        ctx = Qt.ShortcutContext.WindowShortcut
        saved = self._settings.load()
        self._shortcut_handles = []

        new_chat = QShortcut(QKeySequence.StandardKey.New, self)
        new_chat.setContext(ctx)
        new_chat.activated.connect(self._new_conversation)
        self._shortcut_handles.append(new_chat)

        close_tab = QShortcut(QKeySequence.StandardKey.Close, self)
        close_tab.setContext(ctx)
        close_tab.activated.connect(self._close_viewer_tab)
        self._shortcut_handles.append(close_tab)

        settings = QShortcut(QKeySequence.StandardKey.Preferences, self)
        settings.setContext(ctx)
        settings.activated.connect(self._left.open_settings)
        self._shortcut_handles.append(settings)

        stop = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        stop.setContext(ctx)
        stop.activated.connect(self._stop_streaming_if_active)
        self._shortcut_handles.append(stop)

        self._bind_shortcut_action("command_palette", self._open_command_palette, saved)
        self._bind_shortcut_action("file_browser", self._focus_file_browser, saved)
        self._bind_shortcut_action("file_search", self._open_file_search, saved)
        self._bind_shortcut_action("reopen_closed_file", self._reopen_closed_file, saved)
        self._bind_shortcut_action("text_search", self._open_text_search, saved)

    def _bind_shortcut_action(self, action: str, callback, saved: dict):
        ctx = Qt.ShortcutContext.WindowShortcut
        for seq in shortcut_sequences(action, saved):
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(ctx)
            shortcut.activated.connect(callback)
            self._shortcut_handles.append(shortcut)

    def _open_command_palette(self):
        ctx = PaletteContext(
            store=self._chat.store,
            cwd=os.getcwd(),
            is_streaming=self._chat.is_streaming,
            on_open_conversation=self._load_conversation,
            on_open_file=self._open_file,
            on_new_chat=self._new_conversation,
            on_export=self._chat.export_conversation,
            on_compact=lambda: self._chat.compact_conversation(force=True),
            on_settings=self._left.open_settings,
            on_stop=self._chat.stop_streaming,
            on_set_model=self._chat.set_model,
        )
        CommandPalette(build_palette_items(ctx), parent=self).exec()

    def _show_workbench(self):
        self._restore_context_after_workspace()
        self._center_stack.setCurrentWidget(self._workbench_host)
        if self._left.active_activity() == "canvas" and self._canvas_is_enabled():
            self._ensure_agent_canvas_restored()
            self._workbench_left.setCurrentWidget(self._agent_canvas)
            self._hide_context_for_workspace()
            self._sync_chat_width_mode()
            self._apply_pending_workspace_session()
            return
        self._sync_workbench_markdown_preview_pane()
        self._sync_chat_width_mode()
        self._apply_pending_workspace_session()

    def _show_agent_canvas(self):
        if not self._canvas_is_enabled():
            return
        self._ensure_agent_canvas_restored()
        self._center_stack.setCurrentWidget(self._workbench_host)
        self._workbench_left.setCurrentWidget(self._agent_canvas)
        self._hide_context_for_workspace()
        self._sync_chat_width_mode()

    def _set_canvas_attention(self, active: bool):
        self._left.set_activity_attention("canvas", bool(active))

    def _sync_selected_activity_view(self):
        if self._left.active_activity() == "canvas" and self._canvas_is_enabled():
            self._show_agent_canvas()

    def _canvas_is_enabled(self) -> bool:
        return canvas_enabled(self._settings.load())

    def _sync_canvas_enabled(self):
        enabled = self._canvas_is_enabled()
        self._left.set_canvas_enabled(enabled)
        if not enabled and self._left.active_activity() == "canvas":
            self._left.set_active_activity("chats")

    def _on_activity_selected(self, key: str):
        if key == "canvas":
            self._show_agent_canvas()
            return
        if key == "chats":
            self._sync_workbench_markdown_preview_pane(force_chat=True)
        if key != "workspace":
            self._show_workbench()

    def _focus_file_browser(self):
        self._show_workbench()
        self._left.focus_file_browser()

    def _open_file_search(self):
        FileSearchDialog(os.getcwd(), self._open_file, parent=self).exec()

    def _open_text_search(self):
        TextSearchDialog(
            os.getcwd(),
            lambda path, line_no: self._open_file(path, line_no=line_no),
            parent=self,
        ).exec()

    def _new_conversation(self):
        self._show_workbench()
        self._left.set_active_activity("chats")
        self._set_workbench_mode("chat", schedule_save=False)
        self._chat.new_conversation()
        self._left.clear_conversation_selection()

    def _load_conversation(self, path: str):
        self._show_workbench()
        self._set_workbench_mode("chat", schedule_save=False)
        self._chat.load_conversation(path)

    def _open_canvas_conversation(self, conv_id: str):
        conv_id = str(conv_id or "").strip()
        if not conv_id:
            return
        try:
            path = str(self._chat.store.path_for_id(conv_id))
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "Canvas chat missing",
                "The linked canvas chat is no longer available.",
            )
            return
        self._show_workbench()
        self._left.set_active_activity("chats")
        self._left.refresh()
        self._left.select_conversation(conv_id)
        self._chat.load_conversation(path)

    def _on_canvas_conversation_updated(self, conv_id: str):
        conv_id = str(conv_id or "").strip()
        if not conv_id:
            return
        self._pending_canvas_conversation_refreshes.add(conv_id)
        if not self._canvas_conversation_refresh_timer.isActive():
            self._canvas_conversation_refresh_timer.start()

    def _on_canvas_conversation_chunk(self, conv_id: str, text: str):
        conv_id = str(conv_id or "").strip()
        if not conv_id:
            return
        self._chat.append_external_conversation_chunk(conv_id, str(text or ""))

    def _on_canvas_conversation_tool_called(self, conv_id: str, tool_id: str, name: str, inputs: dict):
        conv_id = str(conv_id or "").strip()
        if not conv_id:
            return
        self._chat.show_external_conversation_tool_called(conv_id, str(tool_id or ""), str(name or "tool"), dict(inputs or {}))

    def _on_canvas_conversation_tool_result(self, conv_id: str, tool_id: str, name: str, output: str):
        conv_id = str(conv_id or "").strip()
        if not conv_id:
            return
        self._chat.show_external_conversation_tool_result(conv_id, str(tool_id or ""), str(name or "tool"), str(output or ""))

    def _on_canvas_conversation_run_finished(self, conv_id: str):
        conv_id = str(conv_id or "").strip()
        if not conv_id:
            return
        self._chat.finish_external_conversation_stream(conv_id)
        self._pending_canvas_conversation_refreshes.add(conv_id)
        self._canvas_conversation_refresh_timer.start(0)

    def _flush_canvas_conversation_refresh(self):
        pending = set(self._pending_canvas_conversation_refreshes)
        self._pending_canvas_conversation_refreshes.clear()
        if not pending:
            return
        self._left.refresh()
        active_conv_id = self._chat.current_conversation_id()
        if active_conv_id not in pending:
            return
        if self._chat.is_external_conversation_streaming(active_conv_id):
            return
        try:
            path = str(self._chat.store.path_for_id(active_conv_id))
        except FileNotFoundError:
            return
        self._chat.load_conversation(path, force=True)

    def _close_viewer_tab(self):
        if self._viewer.isVisible():
            self._viewer.close_current_tab()

    def _reopen_closed_file(self):
        path = self._viewer.reopen_recent_closed_file(repo_root=os.getcwd())
        if not path:
            return
        self._show_workbench()
        self._left.reveal_file(path)
        self._viewer.show()
        self._ensure_file_viewer_space()

    def _stop_streaming_if_active(self):
        if self._chat.is_streaming():
            self._chat.stop_streaming()

    def _restore_layout(self, saved: dict):
        geom = saved.get("window_geometry")
        if geom:
            self.restoreGeometry(QByteArray.fromHex(geom.encode()))

        self.resize(1360, 820)

        activity = saved.get("activity_sizes")
        if isinstance(activity, list) and len(activity) == 3:
            activity = [activity[0], activity[1] + activity[2]]
        has_saved_activity = bool(activity and len(activity) == 2)
        activity_collapsed = bool(saved.get("activity_collapsed", False))
        if has_saved_activity and activity[0] <= COLLAPSED_ACTIVITY_WIDTH:
            activity_collapsed = True
        if has_saved_activity:
            self._root_splitter.setSizes(activity)
        else:
            self._root_splitter.setSizes([DEFAULT_ACTIVITY_WIDTH, 960])
            self._pending_default_activity_width = True

        workbench = saved.get("workbench_sizes")
        if workbench and len(workbench) == 2:
            self._workbench.setSizes(self._normalized_workbench_sizes(list(workbench)))
            self._remember_workbench_split()
            self._workbench_user_split = True
        else:
            self._ensure_file_viewer_space()
        self._set_workbench_mode(str(saved.get("workbench_mode") or "chat"), schedule_save=False)

        active_activity = str(saved.get("active_activity") or "chats")
        if active_activity == "workspace":
            active_activity = "chats"
        if active_activity == "canvas" and self._canvas_is_enabled():
            self._left.show_canvas_activity()
        else:
            self._left.set_active_activity(active_activity)
        if activity_collapsed:
            self._left.collapse_activity_panel()
        if not has_saved_activity and not activity_collapsed:
            sizes = self._root_splitter.sizes()
            if len(sizes) == 2:
                total = max(1, sum(sizes))
                left = min(DEFAULT_ACTIVITY_WIDTH, max(MIN_ACTIVITY_WIDTH, total - 1))
                self._root_splitter.setSizes([left, max(1, total - left)])
        self._sync_selected_activity_view()

    def closeEvent(self, event):
        self._extension_review_generation += 1
        self._restore_context_after_workspace()
        self._save_workspace_session()
        self._save_agent_canvas()
        activity_collapsed = self._left.is_activity_panel_collapsed()
        self._settings.update({
            "workspace_path": os.getcwd(),
            "window_geometry": self.saveGeometry().toHex().data().decode(),
            "activity_sizes": self._root_splitter.sizes(),
            "workbench_sizes": self._workbench.sizes(),
            "workbench_mode": self._workbench_mode,
            "active_activity": self._left.active_activity(),
            "activity_collapsed": activity_collapsed,
        })
        self._prepare_splitters_for_close()
        if self._initial_git_status_thread is not None and self._initial_git_status_thread.isRunning():
            self._initial_git_status_thread.wait(3000)
        self._initial_git_status_thread = None
        self._left.shutdown()
        self._viewer.shutdown()
        self._context.shutdown()
        self._workspace_dashboard.shutdown()
        self._agent_canvas.close()
        self._chat.shutdown()
        for thread in list(self._extension_review_threads):
            thread.wait(3000)
        super().closeEvent(event)

    def _apply_appearance(self, changed_keys: object = None):
        changed = set(changed_keys or []) if changed_keys is not None else None
        if changed is not None and not changed:
            return
        model_keys = {
            "anthropic_api_key",
            "openai_api_key",
            "provider_api_keys",
            "provider_order",
            "default_models",
            "crew",
            "crew_models",
            "user_providers",
        }
        editor_keys = {
            FILE_EDITOR_AUTO_SAVE_KEY,
            FILE_EDITOR_TAB_SPACES_KEY,
            FILE_REVIEW_PROMPT_TEMPLATE_KEY,
            DIAGNOSTIC_FIX_PROMPT_TEMPLATE_KEY,
        }
        refresh_models = changed is None or bool(changed & model_keys)
        reload_editor_settings = changed is None or bool(changed & editor_keys)
        sync_canvas_enabled = changed is None or CANVAS_ENABLED_KEY in changed

        app = QApplication.instance()
        previous_updates = self.updatesEnabled()
        self.setUpdatesEnabled(False)
        try:
            if app:
                apply_app_theme(app, current_theme())
            if reload_editor_settings:
                self._viewer.reload_settings()
            self._left.apply_appearance()
            if sync_canvas_enabled:
                self._sync_canvas_enabled()
            if refresh_models:
                self._chat.refresh_models()
            self._chat.apply_appearance()
            self._viewer.apply_appearance()
            self._context.apply_appearance()
            self._workspace_dashboard.apply_appearance()
            self._agent_canvas.apply_appearance(refresh_models=refresh_models)
            if self._window_chrome is not None:
                self._window_chrome.apply_appearance()
            if hasattr(self, "_update_banner"):
                self._update_banner.apply_appearance()
                if changed is not None and CHECK_FOR_UPDATES_KEY in changed:
                    if not check_for_updates(self._settings.load()):
                        self._update_banner.clear()
            self._sync_workbench_mode_buttons()
            self._apply_workbench_restore_tab_style()
            self._sync_context_tab_icons()
            self._sync_workbench_restore_tabs()
        finally:
            self.setUpdatesEnabled(previous_updates)
            self.update()

    def _open_file(
        self,
        path: str,
        diff_text: str | None = None,
        *,
        line_no: int | None = None,
        activate_files: bool = True,
    ):
        self._show_workbench()
        self._viewer.open_file(
            path,
            repo_root=os.getcwd(),
            diff_text=diff_text,
            line_no=line_no,
        )
        self._left.reveal_file(path, activate=activate_files)
        self._viewer.show()
        self._sync_chat_width_mode()
        if activate_files:
            self._set_workbench_mode("editor", schedule_save=False)
        else:
            self._ensure_file_viewer_space()
        self._schedule_session_save()

    def _open_file_from_canvas(self, path: str):
        self._left.show_canvas_activity()
        self._open_file(path, activate_files=False)
        self._workbench_left.setCurrentWidget(self._agent_canvas)

    def _workbench_total_width(self) -> int:
        sizes = self._workbench.sizes()
        total = sum(sizes)
        if total > 0:
            return total
        return max(1, self._workbench.width())

    def _set_workbench_mode(self, mode: str, *, schedule_save: bool = True):
        mode = mode if mode in {"chat", "review", "editor"} else "chat"
        if mode != "chat" and not self._viewer.has_open_tabs():
            mode = "chat"
        current = getattr(self, "_workbench_mode", "chat")
        if current == "review":
            self._remember_workbench_split()
        self._workbench_mode = mode
        total = max(1, self._workbench_total_width())
        if mode == "review":
            self._viewer.show()
            sizes = list(self._last_open_workbench_sizes or self._default_workbench_sizes())
            if len(sizes) != 2 or sum(sizes) <= 0:
                sizes = self._default_workbench_sizes()
            ratio = sizes[0] / max(1, sum(sizes))
            left = max(1, int(total * ratio))
            self._workbench.setSizes([left, max(1, total - left)])
            self._workbench_user_split = True
            self._sync_workbench_markdown_preview_pane(force_chat=True)
        elif mode == "chat":
            self._restore_preview_to_host_tab()
            self._workbench_left.setCurrentWidget(self._chat)
            self._workbench.setSizes([total, 0])
        else:
            self._restore_preview_to_host_tab()
            self._viewer.show()
            self._workbench.setSizes([0, total])
        self._sync_workbench_mode_buttons()
        self._sync_workbench_restore_tabs()
        if schedule_save:
            self._schedule_session_save()

    def _sync_workbench_mode_buttons(self):
        has_file = self._viewer.has_open_tabs()
        for key, button in self._workbench_mode_buttons.items():
            active = key == self._workbench_mode
            available = key == "chat" or has_file
            button.setEnabled(available)
            button.setToolTip("" if available else "Open a file to use this view")
            button.setChecked(active)
            button.setStyleSheet(workbench_mode_button_style(active=active))
        self._workbench_mode_bar.setStyleSheet(workbench_mode_bar_style())
        self._workbench_mode_group.setStyleSheet(workbench_mode_group_style())

    def _default_workbench_sizes(self) -> list[int]:
        total = max(1, self._workbench_total_width())
        chat = total * WORKBENCH_DEFAULT_CHAT_PERCENT // 100
        return [max(1, chat), max(1, total - chat)]

    def _apply_default_workbench_split(self):
        self._workbench.setSizes(self._default_workbench_sizes())
        self._remember_workbench_split()
        self._sync_workbench_markdown_preview_pane()
        self._sync_workbench_restore_tabs()

    def _ensure_file_viewer_space(self):
        sizes = self._workbench.sizes()
        if (
            self._workbench_user_split
            and len(sizes) == 2
            and sizes[0] > WORKBENCH_COLLAPSED_WIDTH
            and sizes[1] >= WORKBENCH_MIN_VIEWER_WIDTH
        ):
            self._remember_workbench_split()
            self._sync_workbench_markdown_preview_pane()
            self._sync_workbench_restore_tabs()
            return
        self._apply_default_workbench_split()

    def _normalized_workbench_sizes(self, sizes: list[int]) -> list[int]:
        if len(sizes) != 2:
            return sizes
        left, right = max(0, int(sizes[0])), max(0, int(sizes[1]))
        total = max(1, left + right)
        if left <= WORKBENCH_COLLAPSED_WIDTH < right:
            return [WORKBENCH_COLLAPSED_WIDTH, max(1, total - WORKBENCH_COLLAPSED_WIDTH)]
        if right <= WORKBENCH_COLLAPSED_WIDTH < left:
            return [max(1, total - WORKBENCH_COLLAPSED_WIDTH), WORKBENCH_COLLAPSED_WIDTH]
        if left <= WORKBENCH_COLLAPSED_WIDTH and right <= WORKBENCH_COLLAPSED_WIDTH:
            return self._default_workbench_sizes()
        return [max(1, left), max(1, right)]

    def _remember_workbench_split(self):
        sizes = self._workbench.sizes()
        if (
            len(sizes) == 2
            and sizes[0] > WORKBENCH_COLLAPSED_WIDTH
            and sizes[1] > WORKBENCH_COLLAPSED_WIDTH
        ):
            self._last_open_workbench_sizes = [int(sizes[0]), int(sizes[1])]

    def _on_workbench_splitter_moved(self, *_args):
        if self._workbench_mode != "review":
            self._workbench_mode = "review"
            self._sync_workbench_mode_buttons()
        self._workbench_user_split = True
        self._snap_workbench_collapsed_side()
        self._remember_workbench_split()
        self._sync_workbench_restore_tabs()
        self._schedule_session_save()

    def _snap_workbench_collapsed_side(self):
        sizes = self._workbench.sizes()
        if len(sizes) != 2:
            return
        left, right = sizes
        total = max(1, left + right)
        if left <= WORKBENCH_COLLAPSED_WIDTH < right:
            self._workbench.setSizes([
                WORKBENCH_COLLAPSED_WIDTH,
                max(1, total - WORKBENCH_COLLAPSED_WIDTH),
            ])
        elif right <= WORKBENCH_COLLAPSED_WIDTH < left:
            self._workbench.setSizes([
                max(1, total - WORKBENCH_COLLAPSED_WIDTH),
                WORKBENCH_COLLAPSED_WIDTH,
            ])

    def _reset_workbench_split(self):
        self._workbench_user_split = False
        self._apply_default_workbench_split()
        self._schedule_session_save()

    def _restore_workbench_side(self, side: str):
        self._workbench_mode = "review"
        self._sync_workbench_mode_buttons()
        total = max(1, self._workbench_total_width())
        base = list(self._last_open_workbench_sizes or self._default_workbench_sizes())
        if len(base) != 2 or sum(base) <= 0:
            base = self._default_workbench_sizes()
        ratio = base[0] / max(1, sum(base))
        left = int(total * ratio)
        right = total - left
        if side == "chat":
            left = max(WORKBENCH_MIN_CHAT_WIDTH, left)
            right = max(1, total - left)
        elif side == "files":
            right = max(WORKBENCH_MIN_VIEWER_WIDTH, right)
            left = max(1, total - right)
        self._workbench.setSizes([left, right])
        self._workbench_user_split = True
        self._remember_workbench_split()
        self._sync_workbench_restore_tabs()
        self._schedule_session_save()

    def _sync_workbench_restore_tabs(self):
        if not hasattr(self, "_workbench_restore_chat_btn"):
            return
        if self._workbench_mode != "review":
            self._workbench_restore_chat_btn.hide()
            self._workbench_restore_files_btn.hide()
            return
        sizes = self._workbench.sizes()
        left_collapsed = len(sizes) == 2 and sizes[0] <= WORKBENCH_COLLAPSED_WIDTH and sizes[1] > WORKBENCH_COLLAPSED_WIDTH
        right_collapsed = (
            len(sizes) == 2
            and sizes[1] <= WORKBENCH_COLLAPSED_WIDTH
            and sizes[0] > WORKBENCH_COLLAPSED_WIDTH
            and self._viewer.isVisible()
            and self._viewer.has_open_tabs()
        )
        height = max(1, self._workbench.height())
        chat_btn = self._workbench_restore_chat_btn
        files_btn = self._workbench_restore_files_btn
        y_chat = max(6, (height - chat_btn.height()) // 2)
        y_files = max(6, (height - files_btn.height()) // 2)
        chat_btn.move(max(2, (self._workbench_left.width() - chat_btn.width()) // 2), y_chat)
        files_btn.move(max(2, (self._viewer.width() - files_btn.width()) // 2), y_files)
        if left_collapsed:
            self._sync_workbench_chat_rail_context()
            if self._workbench_left.currentWidget() is not self._workbench_chat_rail:
                self._workbench_left.setCurrentWidget(self._workbench_chat_rail)
        elif self._workbench_left.currentWidget() is self._workbench_chat_rail:
            self._sync_workbench_markdown_preview_pane(force_chat=True)
        chat_btn.setVisible(False)
        files_btn.setVisible(right_collapsed)
        chat_btn.setEnabled(False)
        files_btn.setEnabled(right_collapsed)
        if right_collapsed:
            files_btn.raise_()

    def _sync_workbench_chat_rail_context(self):
        if not hasattr(self, "_workbench_chat_rail_ring"):
            return
        self._workbench_chat_rail_ring.set_budget(
            getattr(self._chat.context_ring, "_budget", None)
        )
        self._workbench_chat_rail_ring.setToolTip("Show chat")

    def _apply_workbench_restore_tab_style(self):
        p = palette()
        rail_style = (
            f"QWidget#workbenchChatRail {{ background:{p['BG']};"
            f"border-right:1px solid {p['BORDER_SUBTLE']}; }}"
        )
        self._workbench_chat_rail.setStyleSheet(rail_style)
        style = (
            "QPushButton {"
            f"background:{p['BG2']}; color:{p['TEXT']};"
            f"border:1px solid {p['BORDER']}; border-radius:6px;"
            "font-weight:600; padding:0px;"
            "}"
            "QPushButton:hover {"
            f"background:{p['BG3']}; border-color:{p['TEXT_DIM']};"
            "}"
        )
        self._workbench_restore_chat_btn.setStyleSheet(style)
        self._workbench_restore_files_btn.setStyleSheet(style)

    def _restore_preview_to_host_tab(self):
        tab = self._workbench_preview_host_tab
        if tab is None:
            return
        tab.restore_preview_to_tab()
        self._workbench_preview_host_tab = None

    def _sync_workbench_markdown_preview_pane(self, *, force_chat: bool = False):
        if force_chat:
            self._workbench_preview_pinned_chat = True

        show_preview = (
            self._viewer.isVisible()
            and self._viewer.has_open_tabs()
            and self._viewer.active_markdown_preview_pane_active()
            and not self._workbench_preview_pinned_chat
        )
        if not self._viewer.active_markdown_preview_pane_active():
            self._workbench_preview_pinned_chat = False

        tab = self._viewer.active_text_tab()
        if self._workbench_preview_host_tab is not None and self._workbench_preview_host_tab is not tab:
            self._restore_preview_to_host_tab()

        if show_preview and tab is not None:
            if self._workbench_preview_host_tab is not tab:
                self._restore_preview_to_host_tab()
                tab.take_preview_for_pane(self._workbench_preview_host)
                self._workbench_preview_host_tab = tab
            self._workbench_left.setCurrentWidget(self._workbench_preview_host)
            return

        self._restore_preview_to_host_tab()
        self._workbench_left.setCurrentWidget(self._chat)

    def _open_git_file(self, path: str):
        self._open_file(path, activate_files=False)

    def _reveal_active_file(self, path: str):
        self._left.reveal_file(path, activate=False)

    def _open_content(self, content: str, title: str):
        self._show_workbench()
        self._viewer.open_content(content, title)
        self._viewer.show()
        self._sync_chat_width_mode()
        self._set_workbench_mode("editor", schedule_save=False)

    def _refresh_open_file(self, path: str):
        self._viewer.refresh_file(path, repo_root=os.getcwd())

    def _close_file(self):
        self._viewer.hide()
        self._workbench_preview_pinned_chat = False
        if self._left.active_activity() == "canvas":
            self._restore_preview_to_host_tab()
            self._workbench_left.setCurrentWidget(self._agent_canvas)
            self._hide_context_for_workspace()
        else:
            self._set_workbench_mode("chat", schedule_save=False)
            self._sync_workbench_markdown_preview_pane(force_chat=True)
        self._sync_chat_width_mode()
        self._schedule_session_save()

    def _chat_draft_diagnostic_fix(self, text: str, file_refs: list[str]):
        self._sync_workbench_markdown_preview_pane(force_chat=True)
        self._chat.draft_diagnostic_fix(text, file_refs)

    def _sync_chat_width_mode(self):
        if hasattr(self, "_chat") and hasattr(self, "_viewer"):
            self._chat.set_focused_width(self._viewer.isHidden())

    def _schedule_session_save(self):
        self._session_save_timer.start()

    def _schedule_canvas_save(self):
        if self._agent_canvas_restore_pending_workspace is not None:
            self._agent_canvas_restore_pending_workspace = None
        self._canvas_save_timer.start()

    def _restore_agent_canvas(self, workspace: str):
        state, warning = load_agent_canvas(workspace)
        if warning:
            self._agent_canvas.reset_graph()
            QMessageBox.warning(
                self,
                "Canvas reset",
                f"{warning}\n\nThe saved graph was reset for this workspace.",
            )
            self._schedule_canvas_save()
            return
        if state is None:
            self._agent_canvas.reset_graph()
            return
        warning = self._agent_canvas.restore_graph_state(state)
        if warning:
            QMessageBox.warning(
                self,
                "Canvas reset",
                f"{warning}\n\nThe saved graph no longer matches this version, so it was reset.",
            )
            self._schedule_canvas_save()

    def _ensure_agent_canvas_restored(self):
        workspace = self._agent_canvas_restore_pending_workspace
        if workspace is None:
            return
        self._agent_canvas_restore_pending_workspace = None
        self._restore_agent_canvas(workspace)

    def _save_agent_canvas(self):
        self._canvas_save_timer.stop()
        if self._agent_canvas_restore_pending_workspace is not None:
            return
        flush_edits = getattr(self._agent_canvas, "_flush_inspector_auto_apply", None)
        if callable(flush_edits):
            flush_edits()
        if self._agent_canvas_busy_for_save():
            self._canvas_save_timer.start(2000)
            return
        try:
            save_agent_canvas(self._canvas_workspace, self._agent_canvas.graph_state())
        except CanvasSaveRefused as exc:
            setter = getattr(self._agent_canvas, "_set_mode", None)
            if callable(setter):
                setter(str(exc))
        except OSError:
            pass

    def _agent_canvas_busy_for_save(self) -> bool:
        canvas = getattr(self, "_agent_canvas", None)
        if canvas is None:
            return False
        for name in ("_is_graph_agent_running", "_is_run_agent_running"):
            checker = getattr(canvas, name, None)
            if callable(checker) and checker():
                return True
        return False

    def _collect_workspace_session(self) -> dict:
        workbench_sizes = self._workbench.sizes()
        sizes = None
        if len(workbench_sizes) == 2:
            sizes = list(workbench_sizes)
        return {
            "conversation_id": self._chat.current_conversation_id(),
            "open_files": self._viewer.open_file_states(),
            "viewer_visible": self._viewer.isVisible() and self._viewer.has_open_tabs(),
            "workbench_sizes": sizes,
            "workbench_mode": self._workbench_mode,
            "context_panel": self._context.active_panel(),
            "context_collapsed": self._is_context_collapsed(),
        }

    def _save_workspace_session(self):
        self._session_save_timer.stop()
        try:
            save_workspace_session(os.getcwd(), self._collect_workspace_session())
        except OSError:
            pass

    def _queue_workspace_session_restore(self, workspace: str):
        mode = resume_session(self._settings.load())
        if mode == "never":
            self._pending_workspace_session = None
            return
        session = load_workspace_session(workspace)
        if not session_has_restorable_state(session):
            self._pending_workspace_session = None
            return
        self._pending_workspace_session = session

    def _maybe_restore_workspace_session(self):
        if self._session_restore_started:
            return
        self._session_restore_started = True
        mode = resume_session(self._settings.load())
        if mode == "never":
            return
        session = load_workspace_session(os.getcwd())
        if not session_has_restorable_state(session):
            return
        if mode == "ask":
            choice = QMessageBox.question(
                self,
                "Resume session",
                "Restore your last open chat and files for this workspace?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        self._show_workbench()
        self._apply_workspace_session(session)

    def _apply_pending_workspace_session(self):
        session = self._pending_workspace_session
        if session is None:
            return
        self._pending_workspace_session = None
        mode = resume_session(self._settings.load())
        if mode == "never":
            return
        if mode == "ask":
            choice = QMessageBox.question(
                self,
                "Resume session",
                "Restore your last open chat and files for this workspace?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        self._apply_workspace_session(session)

    def _apply_workspace_session(self, session: dict):
        conv_id = str(session.get("conversation_id") or "").strip()
        if conv_id:
            try:
                path = str(self._chat.store.path_for_id(conv_id))
            except FileNotFoundError:
                path = ""
            if path:
                self._left.select_conversation(conv_id)
                self._load_conversation(path)
            else:
                QMessageBox.information(
                    self,
                    "Session restore",
                    "The previous chat is no longer available.",
                )

        open_files = session.get("open_files") or []
        skipped: list[str] = []
        if open_files:
            skipped = self._viewer.restore_open_files(open_files, repo_root=os.getcwd())
            if session.get("viewer_visible") and self._viewer.has_open_tabs():
                self._viewer.show()
                self._sync_chat_width_mode()
                for path in self._viewer.open_paths():
                    self._left.reveal_file(path, activate=False)
            if skipped:
                QMessageBox.information(
                    self,
                    "Session restore",
                    "Some files from the previous session are no longer available.",
                )

        sizes = session.get("workbench_sizes")
        if isinstance(sizes, list) and len(sizes) == 2 and self._viewer.isVisible():
            try:
                normalized = self._normalized_workbench_sizes([
                    max(1, int(sizes[0])),
                    max(1, int(sizes[1])),
                ])
                self._workbench.setSizes(normalized)
            except (TypeError, ValueError):
                pass
        self._set_workbench_mode(str(session.get("workbench_mode") or "chat"), schedule_save=False)
        self._sync_workbench_markdown_preview_pane()

        panel = str(session.get("context_panel") or "run_log")
        self._context.set_active_panel(panel)
        self._sync_context_tab_icons()
        if bool(session.get("context_collapsed", True)):
            self._collapse_context()
        else:
            self._expand_context()
        self._sync_selected_activity_view()

    def _switch_workspace(self, path: str) -> bool:
        target = os.path.abspath(os.path.expanduser(str(path or "").strip()))
        if not target or not os.path.isdir(target):
            QMessageBox.warning(self, "Workspace unavailable", "Choose an existing folder.")
            return False

        if os.path.normcase(target) == os.path.normcase(os.getcwd()):
            self._workspace_dashboard.set_current_workspace(target)
            self._show_workbench()
            return True

        if not self._confirm_workspace_switch():
            return False

        if self._chat.is_streaming():
            self._chat.stop_streaming()
        get_process_manager().stop_workspace(os.getcwd())
        self._save_workspace_session()
        self._save_agent_canvas()

        store = ConversationStore(target)
        self._viewer.close_all_tabs()
        self._viewer.hide()
        self._sync_chat_width_mode()

        os.chdir(target)
        self._chat.set_workspace(store, cwd=target)
        self._left.set_workspace(target, store=store)
        self._viewer.set_repo_root(target)
        self._agent_canvas.set_repo_root(target)
        self._canvas_workspace = target
        self._agent_canvas_restore_pending_workspace = target
        self._workspace_dashboard.set_current_workspace(target)
        self._initial_git_changes = None
        self._initial_git_snapshot = None
        self._settings.update({"workspace_path": target})
        self._left.clear_conversation_selection()
        self._context.set_current_conversation("")
        start_mcp_capability_warmup(target)
        self._queue_workspace_session_restore(target)
        self._show_workbench()
        self._extension_review_prompt_shown = False
        self._review_new_extensions()
        return True

    def _review_new_extensions(self):
        if self._extension_review_prompt_shown:
            return
        self._extension_review_prompt_shown = True
        self._extension_review_generation += 1
        generation = self._extension_review_generation
        repo = os.getcwd()
        thread = _ExtensionReviewThread(generation, repo, self)
        self._extension_review_threads.append(thread)
        thread.done.connect(self._on_extension_review_done)
        thread.finished.connect(lambda t=thread: self._release_extension_review_thread(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _maybe_check_for_updates(self):
        if self._update_check_thread is not None:
            return
        saved = self._settings.load()
        if not app_updates.should_run_network_check(
            enabled=check_for_updates(saved),
            last_checked=update_last_checked(saved),
        ):
            return
        thread = _UpdateCheckThread(self)
        self._update_check_thread = thread
        thread.done.connect(self._on_update_check_done)
        thread.finished.connect(self._clear_update_check_thread)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_update_check_done(self, availability: object):
        saved = self._settings.load()
        updated = app_updates.mark_checked(saved)
        if updated != saved:
            self._settings.save(updated)
        if not check_for_updates(updated):
            self._update_banner.clear()
            return
        if app_updates.should_prompt(availability, update_dismissed_version(updated)):
            self._update_banner.show_update(availability)
        else:
            self._update_banner.clear()

    def _on_update_dismissed(self, version: str):
        saved = self._settings.load()
        updated = app_updates.dismiss_version(saved, version)
        if updated != saved:
            self._settings.save(updated)
        self._update_banner.clear()

    def _clear_update_check_thread(self):
        self._update_check_thread = None

    def _on_extension_review_done(self, generation: int, repo: str, summaries: object, error: str):
        if generation != self._extension_review_generation:
            return
        if os.path.normcase(os.path.abspath(repo)) != os.path.normcase(os.getcwd()):
            return
        if error:
            QMessageBox.warning(self, "Extension review failed", error)
            return
        summaries = list(summaries or [])
        if not summaries:
            return
        count = len(summaries)
        noun = "extension" if count == 1 else "extensions"
        result = QMessageBox.question(
            self,
            "Review extensions?",
            (
                f"{count} new or changed {noun} found. They were disabled until "
                "you review them. Enabled extensions run local Python code; "
                "manifest permissions are AICHS-level controls, not an OS sandbox.\n\n"
                "Open Extensions now?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if result == QMessageBox.StandardButton.Yes:
            self._chat.show_extensions()

    def _release_extension_review_thread(self, thread: _ExtensionReviewThread):
        if thread in self._extension_review_threads:
            self._extension_review_threads.remove(thread)

    def _confirm_workspace_switch(self) -> bool:
        has_stream = self._chat.is_streaming()
        has_processes = bool(get_process_manager().status(workspace=os.getcwd()))
        if not has_stream and not has_processes:
            return True

        result = QMessageBox.question(
            self,
            "Switch workspace?",
            "Switching workspaces will stop active work in this workspace. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    def _show_context_panel(self, panel: str):
        self._context.set_active_panel(panel)
        self._left.set_active_activity("inspect")

    def _sync_context_tab_icons(self):
        return

    def _hide_context_for_workspace(self):
        return

    def _restore_context_after_workspace(self):
        return

    def _collapse_context(self):
        if self._left.active_activity() == "inspect":
            self._left.set_active_activity("chats")

    def _expand_context(self, *, width: int | None = None):
        self._left.set_active_activity("inspect")

    def _is_context_collapsed(self) -> bool:
        return self._left.active_activity() != "inspect"

    def _set_activity_panel_width(self, width: int):
        sizes = self._root_splitter.sizes()
        if len(sizes) != 2:
            return
        total = max(1, sum(sizes))
        available = max(1, total - 1)
        left_width = min(max(width, MIN_ACTIVITY_WIDTH), available)
        self._root_splitter.setSizes([
            left_width,
            max(1, total - left_width),
        ])

    def _prepare_splitters_for_close(self):
        return

    def _on_activity_panel_collapsed(self, collapsed: bool):
        sizes = self._root_splitter.sizes()
        if len(sizes) != 2:
            return
        total = max(1, sum(sizes))
        if not collapsed:
            self._set_activity_panel_width(DEFAULT_ACTIVITY_WIDTH)
            return
        self._root_splitter.setSizes([
            COLLAPSED_ACTIVITY_WIDTH,
            max(1, total - COLLAPSED_ACTIVITY_WIDTH),
        ])
