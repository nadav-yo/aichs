import os

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QApplication,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QShortcut, QKeySequence

from storage.repository import ConversationStore
from storage.settings import SettingsStore
from services.key_bindings import shortcut_sequences
from services.palette import PaletteContext, build_palette_items
from ui.theme import apply_app_theme, current_theme
from ui.widgets.left_panel import LeftPanel
from ui.widgets.chat_panel import ChatPanel
from ui.widgets.file_viewer import FileViewerPanel
from ui.widgets.workbench_context import WorkbenchContextPanel
from ui.widgets.command_palette import CommandPalette
from ui.widgets.file_search_dialog import FileSearchDialog
from ui.widgets.text_search_dialog import TextSearchDialog


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
        )
        self._left.setMinimumWidth(240)
        self._left.setMaximumWidth(480)

        self._viewer = FileViewerPanel(repo, settings=self._settings)
        self._viewer.hide()
        self._viewer.all_closed.connect(self._close_file)
        self._viewer.diagnostic_fix_requested.connect(self._chat_draft_diagnostic_fix)
        self._viewer.active_file_changed.connect(self._reveal_active_file)

        self._chat = ChatPanel(store, cwd=repo, settings=self._settings)

        self._context = WorkbenchContextPanel()
        self._context.setMinimumWidth(220)
        self._context.setMaximumWidth(380)
        self._context.collapse_requested.connect(self._collapse_context)

        self._context_tab = QPushButton("A\nc\nt\ni\nv\ni\nt\ny")
        self._context_tab.setToolTip("Show activity")
        self._context_tab.setFixedWidth(30)
        self._context_tab.setMinimumHeight(112)
        self._context_tab.clicked.connect(self._expand_context)

        context_handle = QWidget()
        context_handle.setObjectName("contextHandle")
        context_handle_layout = QVBoxLayout(context_handle)
        context_handle_layout.setContentsMargins(0, 0, 0, 0)
        context_handle_layout.setSpacing(0)
        context_handle_layout.addStretch()
        context_handle_layout.addWidget(
            self._context_tab,
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

        self._root_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._root_splitter.addWidget(self._left)
        self._root_splitter.addWidget(self._workbench)
        self._root_splitter.addWidget(self._context_shell)
        self._root_splitter.setStretchFactor(0, 0)
        self._root_splitter.setStretchFactor(1, 1)
        self._root_splitter.setStretchFactor(2, 0)

        self._left.selected.connect(self._chat.load_conversation)
        self._left.new_chat.connect(self._new_conversation)
        self._left.renamed.connect(self._chat.update_title)
        self._left.deleted.connect(self._chat.on_conversation_deleted)
        self._left.file_open.connect(self._open_file)
        self._left.git_file_open.connect(self._open_git_file)
        self._left.file_attach.connect(self._chat.attach_file)
        self._left.file_search_requested.connect(self._open_file_search)
        self._left.text_search_requested.connect(self._open_text_search)
        self._left.extensions_requested.connect(self._chat.show_extensions)
        self._left.activity_panel_collapsed_changed.connect(self._on_activity_panel_collapsed)
        self._chat.saved.connect(self._left.refresh)
        self._chat.conversation_created.connect(self._left.select_conversation)
        self._chat.open_code.connect(self._open_content)
        self._chat.open_file.connect(self._open_file)
        self._chat.file_written.connect(self._left.mark_file_touched)
        self._chat.file_write_completed.connect(self._refresh_open_file)
        self._chat.tool_activity.connect(self._context.add_tool_activity)
        self._left.settings_changed.connect(self._apply_appearance)

        self._setup_shortcuts()

        self.setCentralWidget(self._root_splitter)
        self._restore_layout(saved)
        self._apply_appearance()

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
        self._bind_shortcut_action("file_search", self._open_file_search, saved)
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
            on_open_conversation=self._chat.load_conversation,
            on_open_file=self._open_file,
            on_new_chat=self._new_conversation,
            on_export=self._chat.export_conversation,
            on_compact=lambda: self._chat.compact_conversation(force=True),
            on_settings=self._left.open_settings,
            on_stop=self._chat.stop_streaming,
            on_set_model=self._chat.set_model,
        )
        CommandPalette(build_palette_items(ctx), parent=self).exec()

    def _open_file_search(self):
        FileSearchDialog(os.getcwd(), self._open_file, parent=self).exec()

    def _open_text_search(self):
        TextSearchDialog(
            os.getcwd(),
            lambda path, line_no: self._open_file(path, line_no=line_no),
            parent=self,
        ).exec()

    def _new_conversation(self):
        self._chat.new_conversation()
        self._left.clear_conversation_selection()

    def _close_viewer_tab(self):
        if self._viewer.isVisible():
            self._viewer.close_current_tab()

    def _stop_streaming_if_active(self):
        if self._chat.is_streaming():
            self._chat.stop_streaming()

    def _restore_layout(self, saved: dict):
        geom = saved.get("window_geometry")
        if geom:
            self.restoreGeometry(QByteArray.fromHex(geom.encode()))

        self.resize(1360, 820)

        activity = saved.get("activity_sizes")
        if activity and len(activity) == 3:
            self._root_splitter.setSizes(activity)
        else:
            self._root_splitter.setSizes([320, 780, 260])

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

    def closeEvent(self, event):
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
        self._left.shutdown()
        self._viewer.shutdown()
        self._chat.shutdown()
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
        self._context_tab.setStyleSheet(
            "QPushButton { padding:6px 0px; border-radius:0px; text-align:center; }"
        )

    def _open_file(
        self,
        path: str,
        diff_text: str | None = None,
        *,
        line_no: int | None = None,
        activate_files: bool = True,
    ):
        self._viewer.open_file(
            path,
            repo_root=os.getcwd(),
            diff_text=diff_text,
            line_no=line_no,
        )
        self._left.reveal_file(path, activate=activate_files)
        self._viewer.show()
        total = max(1, self._workbench.width())
        self._workbench.setSizes([total * 55 // 100, total * 45 // 100])

    def _open_git_file(self, path: str):
        self._open_file(path, activate_files=False)

    def _reveal_active_file(self, path: str):
        self._left.reveal_file(path, activate=False)

    def _open_content(self, content: str, title: str):
        self._viewer.open_content(content, title)
        self._viewer.show()
        total = max(1, self._workbench.width())
        self._workbench.setSizes([total * 55 // 100, total * 45 // 100])

    def _refresh_open_file(self, path: str):
        self._viewer.refresh_file(path, repo_root=os.getcwd())

    def _close_file(self):
        self._viewer.hide()

    def _chat_draft_diagnostic_fix(self, text: str, file_refs: list[str]):
        self._chat.draft_diagnostic_fix(text, file_refs)

    def _collapse_context(self):
        self._context_stack.setCurrentIndex(1)
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
        left_width = 64 if collapsed else 320
        self._root_splitter.setSizes([
            left_width,
            max(1, total - left_width - right_width),
            right_width,
        ])
