import os

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QApplication,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QByteArray, QSize, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QShortcut, QKeySequence

from storage.repository import ConversationStore
from storage.settings import SettingsStore
from services.git_snapshot import build_git_snapshot
from services.key_bindings import shortcut_sequences
from services.palette import PaletteContext, build_palette_items
from services.processes import get_process_manager
from services.tool_registry import disable_unreviewed_extensions
from ui.theme import apply_app_theme, current_theme, palette, toggle_tab_button_style
from ui.widgets.left_panel import LeftPanel
from ui.widgets.chat_panel import ChatPanel
from ui.widgets.file_viewer import FileViewerPanel
from ui.widgets.workbench_context import WorkbenchContextPanel
from ui.widgets.workspace_dashboard import WorkspaceDashboard
from ui.widgets.command_palette import CommandPalette
from ui.widgets.file_search_dialog import FileSearchDialog
from ui.widgets.text_search_dialog import TextSearchDialog


DEFAULT_ACTIVITY_WIDTH = 400
MIN_ACTIVITY_WIDTH = 280
MAX_ACTIVITY_WIDTH = 640
COLLAPSED_ACTIVITY_WIDTH = 64


def _right_rail_icon(kind: str, *, active: bool = False) -> QIcon:
    p = palette()
    color = QColor(p["TEXT"] if active else p["TEXT_DIM"])
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if kind == "language":
        font = QFont("Cascadia Code")
        font.setPixelSize(13)
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
        self.loaded.emit(self._repo, build_git_snapshot(self._repo))


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
        self._workspace_context_state: dict[str, int | bool] | None = None

        os.chdir(
            _startup_workspace(
                saved,
                startup_workspace,
                prefer_saved_workspace=prefer_saved_workspace,
            )
        )

        repo  = os.getcwd()
        store = ConversationStore(repo)

        self._left = LeftPanel(
            store,
            repo,
            settings=self._settings,
            current_model_getter=lambda: self._chat.current_model(),
            defer_refresh=True,
        )
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
        self._viewer.language_context_changed.connect(self._context.set_language_context)

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

        self._workbench = QSplitter(Qt.Orientation.Horizontal)
        self._workbench.addWidget(self._chat)
        self._workbench.addWidget(self._viewer)
        self._workbench.setStretchFactor(0, 3)
        self._workbench.setStretchFactor(1, 2)

        self._workspace_dashboard = WorkspaceDashboard(repo, defer_refresh=True)
        self._workspace_dashboard.switch_requested.connect(self._switch_workspace)
        self._workspace_dashboard.conversation_requested.connect(self._load_conversation)
        self._workspace_dashboard.open_file_requested.connect(self._open_file)
        self._workspace_dashboard.new_chat_requested.connect(self._new_conversation)
        self._workspace_dashboard.file_search_requested.connect(self._open_file_search)
        self._workspace_dashboard.text_search_requested.connect(self._open_text_search)

        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(self._workbench)
        self._center_stack.addWidget(self._workspace_dashboard)

        self._root_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._root_splitter.addWidget(self._left)
        self._root_splitter.addWidget(self._center_stack)
        self._root_splitter.addWidget(self._context_shell)
        self._root_splitter.setStretchFactor(0, 0)
        self._root_splitter.setStretchFactor(1, 1)
        self._root_splitter.setStretchFactor(2, 0)

        self._left.selected.connect(self._load_conversation)
        self._left.new_chat.connect(self._new_conversation)
        self._left.renamed.connect(self._chat.update_title)
        self._left.deleted.connect(self._chat.on_conversation_deleted)
        self._left.file_open.connect(self._open_file)
        self._left.git_file_open.connect(self._open_git_file)
        self._left.git_help_requested.connect(self._chat_draft_diagnostic_fix)
        self._left.file_attach.connect(self._chat.attach_file)
        self._left.file_search_requested.connect(self._open_file_search)
        self._left.text_search_requested.connect(self._open_text_search)
        self._left.extensions_requested.connect(self._chat.show_extensions)
        self._left.workspace_requested.connect(self._show_workspace_dashboard)
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
        self._left.settings_changed.connect(self._apply_appearance)

        self._setup_shortcuts()

        self.setCentralWidget(self._root_splitter)
        self._restore_layout(saved)
        self._apply_appearance()
        self._context.set_language_context(self._viewer.active_language_context())

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_default_activity_width:
            self._apply_default_activity_width()
        if self._first_paint_tasks_started:
            return
        self._first_paint_tasks_started = True
        QTimer.singleShot(0, self._run_after_first_paint)

    def _run_after_first_paint(self):
        self._review_new_extensions()
        if not self._pending_default_activity_width:
            self._start_initial_git_status_refresh()
            return
        self._pending_default_activity_width = False
        self._apply_default_activity_width()
        self._start_initial_git_status_refresh()

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
        self._center_stack.setCurrentWidget(self._workbench)
        self._sync_chat_width_mode()

    def _show_workspace_dashboard(self):
        self._workspace_dashboard.refresh(git_snapshot=self._initial_git_snapshot)
        self._center_stack.setCurrentWidget(self._workspace_dashboard)
        self._hide_context_for_workspace()

    def _on_activity_selected(self, key: str):
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
        self._chat.new_conversation()
        self._left.clear_conversation_selection()

    def _load_conversation(self, path: str):
        self._show_workbench()
        self._chat.load_conversation(path)

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
        total = max(1, self._workbench.width())
        self._workbench.setSizes([total * 55 // 100, total * 45 // 100])

    def _stop_streaming_if_active(self):
        if self._chat.is_streaming():
            self._chat.stop_streaming()

    def _restore_layout(self, saved: dict):
        geom = saved.get("window_geometry")
        if geom:
            self.restoreGeometry(QByteArray.fromHex(geom.encode()))

        self.resize(1360, 820)

        activity = saved.get("activity_sizes")
        has_saved_activity = bool(activity and len(activity) == 3)
        if has_saved_activity:
            self._root_splitter.setSizes(activity)
        else:
            self._root_splitter.setSizes([DEFAULT_ACTIVITY_WIDTH, 700, 260])
            self._pending_default_activity_width = True

        workbench = saved.get("workbench_sizes")
        if workbench and len(workbench) == 2:
            self._workbench.setSizes(workbench)
        else:
            self._workbench.setSizes([620, 500])

        active_activity = str(saved.get("active_activity") or "chats")
        self._left.set_active_activity(active_activity)
        if bool(saved.get("activity_collapsed", False)):
            self._left.collapse_activity_panel()
        if bool(saved.get("context_collapsed", True)):
            self._collapse_context()
        else:
            self._expand_context()
        if not has_saved_activity and not bool(saved.get("activity_collapsed", False)):
            sizes = self._root_splitter.sizes()
            if len(sizes) == 3:
                total = max(1, sum(sizes))
                right = 30 if self._is_context_collapsed() else min(300, max(240, total // 5))
                left = min(DEFAULT_ACTIVITY_WIDTH, max(MIN_ACTIVITY_WIDTH, total - right - 1))
                self._root_splitter.setSizes([left, max(1, total - left - right), right])

    def closeEvent(self, event):
        self._extension_review_generation += 1
        self._restore_context_after_workspace()
        context_collapsed = self._is_context_collapsed()
        activity_collapsed = self._left.is_activity_panel_collapsed()
        self._settings.update({
            "workspace_path": os.getcwd(),
            "window_geometry": self.saveGeometry().toHex().data().decode(),
            "activity_sizes": self._root_splitter.sizes(),
            "workbench_sizes": self._workbench.sizes(),
            "context_sizes": [self._context.width()],
            "context_collapsed": context_collapsed,
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
        self._chat.shutdown()
        for thread in list(self._extension_review_threads):
            thread.wait(3000)
        super().closeEvent(event)

    def _apply_appearance(self):
        app = QApplication.instance()
        if app:
            apply_app_theme(app, current_theme())
        self._viewer.reload_settings()
        self._left.apply_appearance()
        self._chat.refresh_models()
        self._chat.apply_appearance()
        self._viewer.apply_appearance()
        self._context.apply_appearance()
        self._workspace_dashboard.apply_appearance()
        tab_style = toggle_tab_button_style()
        self._context_tab.setStyleSheet(tab_style)
        self._language_context_tab.setStyleSheet(tab_style)
        self._sync_context_tab_icons()

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
        total = max(1, self._workbench.width())
        self._workbench.setSizes([total * 55 // 100, total * 45 // 100])

    def _open_git_file(self, path: str):
        self._open_file(path, activate_files=False)

    def _reveal_active_file(self, path: str):
        self._left.reveal_file(path, activate=False)

    def _open_content(self, content: str, title: str):
        self._show_workbench()
        self._viewer.open_content(content, title)
        self._viewer.show()
        self._sync_chat_width_mode()
        total = max(1, self._workbench.width())
        self._workbench.setSizes([total * 55 // 100, total * 45 // 100])

    def _refresh_open_file(self, path: str):
        self._viewer.refresh_file(path, repo_root=os.getcwd())

    def _close_file(self):
        self._viewer.hide()
        self._sync_chat_width_mode()

    def _chat_draft_diagnostic_fix(self, text: str, file_refs: list[str]):
        self._chat.draft_diagnostic_fix(text, file_refs)

    def _sync_chat_width_mode(self):
        if hasattr(self, "_chat") and hasattr(self, "_viewer"):
            self._chat.set_focused_width(self._viewer.isHidden())

    def _switch_workspace(self, path: str) -> bool:
        target = os.path.abspath(os.path.expanduser(str(path or "").strip()))
        if not target or not os.path.isdir(target):
            QMessageBox.warning(self, "Workspace unavailable", "Choose an existing folder.")
            return False

        if os.path.normcase(target) == os.path.normcase(os.getcwd()):
            self._workspace_dashboard.set_current_workspace(target)
            self._show_workspace_dashboard()
            return True

        if not self._confirm_workspace_switch():
            return False

        if self._chat.is_streaming():
            self._chat.stop_streaming()
        get_process_manager().stop_workspace(os.getcwd())

        store = ConversationStore(target)
        self._viewer.close_all_tabs()
        self._viewer.hide()
        self._sync_chat_width_mode()

        os.chdir(target)
        self._chat.set_workspace(store, cwd=target)
        self._left.set_workspace(target, store=store)
        self._viewer.set_repo_root(target)
        self._workspace_dashboard.set_current_workspace(target)
        self._initial_git_changes = None
        self._initial_git_snapshot = None
        self._settings.update({"workspace_path": target})
        self._left.clear_conversation_selection()
        self._context.set_current_conversation("")
        self._show_workspace_dashboard()
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
        self._restore_context_after_workspace()
        self._context.set_active_panel(panel)
        self._sync_context_tab_icons()
        self._expand_context()

    def _sync_context_tab_icons(self):
        active = getattr(self._context, "_active_panel", "run_log")
        run_active = active == "run_log"
        language_active = active == "language"
        self._context_tab.setChecked(run_active)
        self._language_context_tab.setChecked(language_active)
        self._context_tab.setIcon(_right_rail_icon("run_log", active=run_active))
        self._language_context_tab.setIcon(_right_rail_icon("language", active=language_active))

    def _hide_context_for_workspace(self):
        if self._workspace_context_state is not None:
            return
        sizes = self._root_splitter.sizes()
        context_width = sizes[2] if len(sizes) == 3 else self._context_shell.width()
        self._workspace_context_state = {
            "collapsed": self._is_context_collapsed(),
            "width": max(30, context_width),
            "hidden": self._context_shell.isHidden(),
        }
        self._collapse_context()
        self._context_shell.hide()
        self._context_shell.setMinimumWidth(0)
        self._context_shell.setMaximumWidth(0)
        sizes = self._root_splitter.sizes()
        if len(sizes) == 3:
            self._root_splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])

    def _restore_context_after_workspace(self):
        state = self._workspace_context_state
        if state is None:
            return
        self._workspace_context_state = None
        collapsed = bool(state["collapsed"])
        target_width = 30
        if collapsed:
            self._context_stack.setCurrentIndex(1)
            self._context_shell.setMinimumWidth(30)
            self._context_shell.setMaximumWidth(30)
        else:
            target_width = min(420, max(220, int(state["width"])))
            self._context_stack.setCurrentIndex(0)
            self._context_shell.setMinimumWidth(220)
            self._context_shell.setMaximumWidth(420)
        self._context_shell.setHidden(bool(state["hidden"]))
        sizes = self._root_splitter.sizes()
        if len(sizes) == 3 and not bool(state["hidden"]):
            total = max(1, sum(sizes))
            left_width = sizes[0]
            self._root_splitter.setSizes([
                left_width,
                max(1, total - left_width - target_width),
                target_width,
            ])

    def _collapse_context(self):
        self._context_stack.setCurrentIndex(1)
        self._sync_context_tab_icons()
        self._context_shell.setMinimumWidth(30)
        self._context_shell.setMaximumWidth(30)
        sizes = self._root_splitter.sizes()
        if len(sizes) == 3:
            self._root_splitter.setSizes([
                sizes[0],
                sizes[1] + max(0, sizes[2] - 30),
                30,
            ])

    def _expand_context(self):
        self._context_shell.setMinimumWidth(220)
        self._context_shell.setMaximumWidth(420)
        self._context_stack.setCurrentIndex(0)
        self._sync_context_tab_icons()
        sizes = self._root_splitter.sizes()
        if len(sizes) == 3:
            total = max(1, sum(sizes))
            context_width = min(300, max(240, total // 5))
            left_width = sizes[0]
            self._root_splitter.setSizes([
                left_width,
                max(1, total - left_width - context_width),
                context_width,
            ])

    def _is_context_collapsed(self) -> bool:
        return self._context_stack.currentIndex() == 1

    def _set_activity_panel_width(self, width: int):
        sizes = self._root_splitter.sizes()
        if len(sizes) != 3:
            return
        total = max(1, sum(sizes))
        right_width = sizes[2]
        available = max(1, total - right_width - 1)
        left_width = min(max(width, MIN_ACTIVITY_WIDTH), available)
        self._root_splitter.setSizes([
            left_width,
            max(1, total - left_width - right_width),
            right_width,
        ])

    def _prepare_splitters_for_close(self):
        self._context_stack.setCurrentIndex(0)
        self._context_shell.setMinimumWidth(0)
        self._context_shell.setMaximumWidth(16777215)

    def _on_activity_panel_collapsed(self, collapsed: bool):
        sizes = self._root_splitter.sizes()
        if len(sizes) != 3:
            return
        total = max(1, sum(sizes))
        right_width = sizes[2]
        if not collapsed:
            self._set_activity_panel_width(DEFAULT_ACTIVITY_WIDTH)
            return
        self._root_splitter.setSizes([
            COLLAPSED_ACTIVITY_WIDTH,
            max(1, total - COLLAPSED_ACTIVITY_WIDTH - right_width),
            right_width,
        ])
