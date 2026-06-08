import os

from PyQt6.QtWidgets import QMainWindow, QSplitter, QApplication
from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QShortcut, QKeySequence

from storage.repository import ConversationStore
from storage.settings import SettingsStore
from services.palette import PaletteContext, build_palette_items
from ui.theme import apply_app_theme, current_theme
from ui.widgets.left_panel import LeftPanel
from ui.widgets.chat_panel import ChatPanel
from ui.widgets.file_viewer import FileViewerPanel
from ui.widgets.command_palette import CommandPalette


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

        self._outer = QSplitter(Qt.Orientation.Horizontal)

        self._left = LeftPanel(
            store,
            repo,
            settings=self._settings,
            current_model_getter=lambda: self._chat.current_model(),
        )
        self._left.setMinimumWidth(150)
        self._left.setMaximumWidth(400)

        self._inner = QSplitter(Qt.Orientation.Vertical)

        self._viewer = FileViewerPanel(repo, settings=self._settings)
        self._viewer.hide()
        self._viewer.all_closed.connect(self._close_file)

        self._chat = ChatPanel(store, cwd=repo, settings=self._settings)

        self._inner.addWidget(self._viewer)
        self._inner.addWidget(self._chat)
        self._inner.setStretchFactor(0, 2)
        self._inner.setStretchFactor(1, 1)

        self._left.selected.connect(self._chat.load_conversation)
        self._left.new_chat.connect(self._new_conversation)
        self._left.renamed.connect(self._chat.update_title)
        self._left.deleted.connect(self._chat.on_conversation_deleted)
        self._left.file_open.connect(self._open_file)
        self._left.file_attach.connect(self._chat.attach_file)
        self._chat.saved.connect(self._left.refresh)
        self._chat.conversation_created.connect(self._left.select_conversation)
        self._chat.open_code.connect(self._open_content)
        self._chat.open_file.connect(self._open_file)
        self._chat.file_written.connect(self._left.mark_file_touched)
        self._left.settings_changed.connect(self._apply_appearance)

        self._setup_shortcuts()

        self._outer.addWidget(self._left)
        self._outer.addWidget(self._inner)
        self._outer.setStretchFactor(0, 0)
        self._outer.setStretchFactor(1, 1)

        self.setCentralWidget(self._outer)
        self._restore_layout(saved)
        self._apply_appearance()

    def _setup_shortcuts(self):
        ctx = Qt.ShortcutContext.WindowShortcut

        new_chat = QShortcut(QKeySequence.StandardKey.New, self)
        new_chat.setContext(ctx)
        new_chat.activated.connect(self._new_conversation)

        close_tab = QShortcut(QKeySequence.StandardKey.Close, self)
        close_tab.setContext(ctx)
        close_tab.activated.connect(self._close_viewer_tab)

        settings = QShortcut(QKeySequence.StandardKey.Preferences, self)
        settings.setContext(ctx)
        settings.activated.connect(self._left.open_settings)

        stop = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        stop.setContext(ctx)
        stop.activated.connect(self._stop_streaming_if_active)

        for seq in ("Ctrl+K", "Meta+K"):
            palette_sc = QShortcut(QKeySequence(seq), self)
            palette_sc.setContext(ctx)
            palette_sc.activated.connect(self._open_command_palette)

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

        outer = saved.get("outer_sizes")
        if outer:
            if len(outer) == 3:
                self._outer.setSizes([outer[0], outer[1] + outer[2]])
            elif len(outer) == 2:
                self._outer.setSizes(outer)
        else:
            self.resize(1280, 800)
            self._outer.setSizes([360, 920])

        inner = saved.get("inner_sizes")
        if inner and len(inner) == 2:
            self._inner.setSizes(inner)

    def closeEvent(self, event):
        self._settings.update({
            "workspace_path": os.getcwd(),
            "window_geometry": self.saveGeometry().toHex().data().decode(),
            "outer_sizes": self._outer.sizes(),
            "inner_sizes": self._inner.sizes(),
        })
        self._chat.stop_managed_processes()
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

    def _open_file(self, path: str, diff_text: str | None = None):
        self._viewer.open_file(path, repo_root=os.getcwd(), diff_text=diff_text)
        self._viewer.show()
        total = self._inner.height()
        self._inner.setSizes([total * 2 // 3, total // 3])

    def _open_content(self, content: str, title: str):
        self._viewer.open_content(content, title)
        self._viewer.show()
        total = self._inner.height()
        self._inner.setSizes([total * 2 // 3, total // 3])

    def _close_file(self):
        self._viewer.hide()
