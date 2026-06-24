import pytest
import time
from contextlib import contextmanager

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QKeySequence
from services.chat_drag import (
    AICHS_CHAT_DROP_MIME,
    AICHS_COMMIT_DROP_MIME,
    AICHS_FILE_DROP_MIME,
    chat_drop_payload,
    chat_drop_text,
    commit_drop_payload,
    commit_drop_text,
    file_drop_payload,
    file_drop_text,
    parse_chat_drop,
    parse_commit_drop,
    parse_file_drop,
)
from services.file_search import clear_workspace_file_cache, list_workspace_files
from PyQt6.QtWidgets import QLabel, QListWidget, QLineEdit, QMenu, QTextBrowser
from PyQt6.QtWidgets import QMessageBox

from services.file_tree_snapshot import FileTreeEntry, FileTreeSnapshot
from services.git_snapshot import GitSnapshot
from services.git_status import GitCommandResult
from storage.settings import SettingsStore
from ui.theme import palette
from ui.widgets.git_panel import GitPanel, _CommitDiffDialog
from ui.widgets.left_panel import FileTree, _FileTreeActionThread, _FileTreeRefreshThread, _FilesHeader, _FILE_TREE_MOVE_MIME, _IS_DIR_ROLE, _LOAD_GENERATION_ROLE, _path_key
from ui.widgets.conversation_panel import ConversationPanel


def _has_shortcut(widget, sequence: str) -> bool:
    wanted = QKeySequence(sequence)
    return any(
        shortcut.key().matches(wanted) == QKeySequence.SequenceMatch.ExactMatch
        for shortcut in widget._shortcut_handles
    )


def _wait_until(qapp, predicate, timeout_s: float = 2.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate()


def _wait_for_loaded_children(qapp, item):
    _wait_until(
        qapp,
        lambda: item.childCount() > 0 and bool(item.child(0).data(0, Qt.ItemDataRole.UserRole)),
    )


class _FakeDropEvent:
    def __init__(self, mime, pos=None):
        self._mime = mime
        self._pos = pos
        self.accepted = False
        self.ignored = False
        self.drop_action = None

    def mimeData(self):
        return self._mime

    def pos(self):
        return self._pos

    def setDropAction(self, action):
        self.drop_action = action

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


def test_files_tree_drags_file_mentions(qapp, workspace):
    tree = FileTree(str(workspace))
    src = tree.topLevelItem(0)
    tree._on_item_expanded(src)
    _wait_for_loaded_children(qapp, src)
    item = src.child(0)

    mime = tree.mimeData([item])

    assert mime.hasFormat(AICHS_FILE_DROP_MIME)
    assert mime.hasFormat(_FILE_TREE_MOVE_MIME)
    assert parse_file_drop(mime.data(AICHS_FILE_DROP_MIME)) == ["src/main.py"]
    assert tree._move_paths_from_mime(mime) == [str(workspace / "src" / "main.py")]
    assert mime.text() == "@src/main.py"


def test_files_tree_context_menu_empty_space_offers_new_folder(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace))
    action_texts = []
    monkeypatch.setattr(tree, "itemAt", lambda _pos: None)

    def capture_menu(menu, _pos):
        action_texts.extend(action.text() for action in menu.actions() if not action.isSeparator())

    monkeypatch.setattr(QMenu, "exec", capture_menu)

    tree._context_menu(QPoint(1, 1))

    assert action_texts == ["New Folder...", "Refresh"]


def test_files_tree_context_menu_file_offers_new_folder_in_parent(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace))
    assert tree.reveal_file(str(workspace / "src" / "main.py"))
    item = tree._find_item_for_path(str(workspace / "src" / "main.py"))
    assert item is not None
    action_texts = []
    created_in = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QInputDialog.getText",
        lambda *args, **kwargs: ("pkg", True),
    )
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, path, **kwargs: created_in.append((action, path, kwargs)),
    )

    def choose_new_folder(menu, _pos):
        action_texts.extend(action.text() for action in menu.actions() if not action.isSeparator())
        for action in menu.actions():
            if action.text() == "New Folder...":
                action.trigger()
                return

    monkeypatch.setattr(QMenu, "exec", choose_new_folder)

    tree._context_menu(tree.visualItemRect(item).center())

    assert "New Folder..." in action_texts
    assert created_in == [("create_folder", str(workspace / "src"), {"name": "pkg"})]


def test_files_tree_reveals_nested_file(qapp, workspace):
    nested_dir = workspace / "src" / "pkg"
    nested_dir.mkdir()
    target = nested_dir / "api.py"
    target.write_text("API = True\n", encoding="utf-8")
    tree = FileTree(str(workspace))

    assert tree.reveal_file(str(target)) is True

    item = tree.currentItem()
    assert item is not None
    assert item.data(0, Qt.ItemDataRole.UserRole) == str(target)
    assert item.text(0).endswith("api.py")


def test_files_tree_reveal_nested_file_does_not_scan_directories(qapp, workspace, monkeypatch):
    nested_dir = workspace / "src" / "pkg"
    nested_dir.mkdir()
    target = nested_dir / "api.py"
    target.write_text("API = True\n", encoding="utf-8")
    tree = FileTree(str(workspace), defer_git_status=True)
    _wait_until(qapp, lambda: not tree._children_threads)
    child_generation = tree._children_generation

    assert tree.reveal_file(str(target)) is True

    assert tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == str(target)
    assert tree._children_generation == child_generation


def test_files_tree_refresh_preserves_expanded_folders_and_selection(qapp, workspace, monkeypatch):
    nested_dir = workspace / "src" / "pkg"
    nested_dir.mkdir()
    target = nested_dir / "api.py"
    target.write_text("API = True\n", encoding="utf-8")
    monkeypatch.setattr("services.file_tree_snapshot.list_file_changes", lambda _root: [])
    tree = FileTree(str(workspace), defer_git_status=True)
    src = tree.topLevelItem(0)
    tree._on_item_expanded(src)
    _wait_for_loaded_children(qapp, src)
    src.setExpanded(True)
    pkg = tree._find_child_for_path(src, str(nested_dir))
    assert pkg is not None
    tree._on_item_expanded(pkg)
    _wait_for_loaded_children(qapp, pkg)
    pkg.setExpanded(True)
    selected = tree._find_child_for_path(pkg, str(target))
    assert selected is not None
    tree.setCurrentItem(selected)

    tree.refresh()
    _wait_until(qapp, lambda: not tree._refresh_threads and not tree._children_threads)

    src_after = tree._find_child_for_path(tree.invisibleRootItem(), str(workspace / "src"))
    assert src_after is not None
    pkg_after = tree._find_child_for_path(src_after, str(nested_dir))
    assert pkg_after is not None
    assert src_after.isExpanded()
    assert pkg_after.isExpanded()
    assert tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == str(target)


def test_files_tree_watcher_refresh_is_debounced(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace), defer_git_status=True)
    calls = []
    monkeypatch.setattr(tree, "_request_refresh", lambda: calls.append(True))

    tree._schedule_refresh(delay_ms=1)
    tree._schedule_refresh(delay_ms=1)

    assert calls == []
    _wait_until(qapp, lambda: calls == [True])


def test_files_tree_refresh_thread_drops_cancelled_result(qapp, workspace, monkeypatch):
    calls = []

    def fake_build(root_path, *, filter_text="", cancelled=None, **_kwargs):
        calls.append((root_path, filter_text, cancelled()))
        return FileTreeSnapshot(root_path=root_path, filter_text=filter_text)

    monkeypatch.setattr("ui.widgets.left_panel.build_file_tree_snapshot", fake_build)
    thread = _FileTreeRefreshThread(1, str(workspace), "main")
    emitted = []
    thread.done.connect(lambda *args: emitted.append(args))

    thread.cancel()
    thread.run()

    assert calls == [(str(workspace), "main", True)]
    assert emitted == []


def test_files_tree_apply_snapshots_are_timed(qapp, workspace, monkeypatch):
    import ui.widgets.left_panel as left_panel

    operations = []

    @contextmanager
    def fake_time_operation(operation, *, detail="", slow_ms=100.0):
        operations.append((operation, detail, slow_ms))
        yield

    monkeypatch.setattr(left_panel, "time_operation", fake_time_operation)
    tree = FileTree(str(workspace), defer_git_status=True)
    operations.clear()
    folder = workspace / "src"
    child = folder / "main.py"

    tree._apply_snapshot(
        FileTreeSnapshot(
            root_path=str(workspace),
            filter_text="",
            entries=(FileTreeEntry("src", str(folder), True),),
        )
    )
    item = tree.topLevelItem(0)
    item.setData(0, _LOAD_GENERATION_ROLE, 3)
    tree._apply_children_snapshot(
        3,
        str(folder),
        FileTreeSnapshot(
            root_path=str(folder),
            filter_text="",
            entries=(FileTreeEntry("main.py", str(child), False),),
        ),
    )

    assert operations == [
        ("file_tree.apply", "entries=1 filtered=False git=0", 50),
        ("file_tree.children.apply", f"entries=1 path={folder}", 50),
    ]


def test_files_tree_snapshot_apply_sets_icons_once_per_decorated_item(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace), defer_git_status=True)
    src = workspace / "src"
    docs = workspace / "docs"
    docs.mkdir()
    icon_paths = []

    def fake_apply_icon(_item, path, **_kwargs):
        icon_paths.append(path)

    monkeypatch.setattr(tree, "_apply_item_icon", fake_apply_icon)

    tree._apply_snapshot(
        FileTreeSnapshot(
            root_path=str(workspace),
            filter_text="",
            entries=(
                FileTreeEntry("src", str(src), True),
                FileTreeEntry("docs", str(docs), True),
            ),
        )
    )

    assert icon_paths == [str(src), str(docs)]


def test_files_tree_uses_uniform_row_heights(qapp, workspace):
    tree = FileTree(str(workspace), defer_git_status=True)

    assert tree.uniformRowHeights() is True


def test_files_tree_snapshot_apply_batches_updates(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace), defer_git_status=True)
    src = workspace / "src"
    calls = []

    monkeypatch.setattr(tree, "_begin_tree_update_batch", lambda: calls.append("begin") or False)
    monkeypatch.setattr(tree, "_end_tree_update_batch", lambda enabled: calls.append(("end", enabled)))

    tree._apply_snapshot(
        FileTreeSnapshot(
            root_path=str(workspace),
            filter_text="",
            entries=(FileTreeEntry("src", str(src), True),),
        )
    )

    assert calls == ["begin", ("end", False)]


def test_files_tree_snapshot_apply_restores_updates_after_failure(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace), defer_git_status=True)
    src = workspace / "src"
    calls = []

    monkeypatch.setattr(tree, "_begin_tree_update_batch", lambda: calls.append("begin") or True)
    monkeypatch.setattr(tree, "_end_tree_update_batch", lambda enabled: calls.append(("end", enabled)))

    def fail_fill(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tree, "_fill_from_snapshot", fail_fill)

    with pytest.raises(RuntimeError, match="boom"):
        tree._apply_snapshot(
            FileTreeSnapshot(
                root_path=str(workspace),
                filter_text="",
                entries=(FileTreeEntry("src", str(src), True),),
            )
        )

    assert calls == ["begin", ("end", True)]


def test_files_tree_children_apply_batches_updates(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace), defer_git_status=True)
    src = workspace / "src"
    child = src / "main.py"
    tree._apply_snapshot(
        FileTreeSnapshot(
            root_path=str(workspace),
            filter_text="",
            entries=(FileTreeEntry("src", str(src), True),),
        )
    )
    src_item = tree.topLevelItem(0)
    src_item.setData(0, _LOAD_GENERATION_ROLE, 9)
    calls = []

    monkeypatch.setattr(tree, "_begin_tree_update_batch", lambda: calls.append("begin") or True)
    monkeypatch.setattr(tree, "_end_tree_update_batch", lambda enabled: calls.append(("end", enabled)))

    tree._apply_children_snapshot(
        9,
        str(src),
        FileTreeSnapshot(
            root_path=str(src),
            filter_text="",
            entries=(FileTreeEntry("main.py", str(child), False),),
        ),
    )

    assert calls == ["begin", ("end", True)]


def test_files_tree_git_timer_refreshes_decorations_without_rebuilding_tree(qapp, workspace, monkeypatch):
    import ui.widgets.left_panel as left_panel

    tree = FileTree(str(workspace), defer_git_status=True)
    refresh_calls = []
    loaded_changes = []
    decorated = []

    class FakeSignal:
        def __init__(self):
            self._callbacks = []

        def connect(self, callback):
            self._callbacks.append(callback)

        def emit(self, *args):
            for callback in list(self._callbacks):
                callback(*args)

    class FakeThread:
        def __init__(self, generation, root_path, parent=None):
            self.done = FakeSignal()
            self.finished = FakeSignal()
            self.generation = generation
            self.root_path = root_path
            self.parent = parent

        def start(self):
            self.done.emit(self.generation, ["change"])
            self.finished.emit()

        def deleteLater(self):
            pass

    monkeypatch.setattr(left_panel, "_FileTreeGitStatusThread", FakeThread)
    monkeypatch.setattr(tree, "_request_refresh", lambda **_kwargs: refresh_calls.append(True))
    monkeypatch.setattr(tree, "_load_git_status", lambda changes=None: loaded_changes.append(changes))
    monkeypatch.setattr(tree, "_update_git_labels", lambda: decorated.append("labels"))
    monkeypatch.setattr(tree, "_apply_decorations", lambda *args: decorated.append("decorations"))

    tree._refresh_git_status()

    assert refresh_calls == []
    assert loaded_changes == [["change"]]
    assert decorated == ["labels", "decorations"]
    assert tree._git_status_threads == []


def test_files_tree_ignores_stale_git_status_results(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace), defer_git_status=True)
    loaded_changes = []
    monkeypatch.setattr(tree, "_refresh_git_status", lambda changes=None: loaded_changes.append(changes))
    tree._git_status_generation = 2

    tree._apply_git_status_result(1, ["old"])
    tree._apply_git_status_result(2, ["current"])

    assert loaded_changes == [["current"]]


def test_files_tree_children_apply_decorates_loaded_subtree_only(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace), defer_git_status=True)
    src = workspace / "src"
    docs = workspace / "docs"
    docs.mkdir()
    child = src / "main.py"
    tree._apply_snapshot(
        FileTreeSnapshot(
            root_path=str(workspace),
            filter_text="",
            entries=(
                FileTreeEntry("src", str(src), True),
                FileTreeEntry("docs", str(docs), True),
            ),
        )
    )
    src_item = tree._find_child_for_path(tree.invisibleRootItem(), str(src))
    docs_item = tree._find_child_for_path(tree.invisibleRootItem(), str(docs))
    assert src_item is not None
    assert docs_item is not None
    src_item.setData(0, _LOAD_GENERATION_ROLE, 4)
    decorated = []

    def fake_apply_icon(_item, path, **_kwargs):
        decorated.append(path)

    monkeypatch.setattr(tree, "_apply_item_icon", fake_apply_icon)

    tree._apply_children_snapshot(
        4,
        str(src),
        FileTreeSnapshot(
            root_path=str(src),
            filter_text="",
            entries=(FileTreeEntry("main.py", str(child), False),),
        ),
    )

    assert str(src) in decorated
    assert str(child) in decorated
    assert str(docs) not in decorated


def test_files_tree_children_apply_uses_snapshot_entry_types(qapp, workspace, monkeypatch):
    import ui.widgets.left_panel as left_panel

    tree = FileTree(str(workspace), defer_git_status=True)
    src = workspace / "src"
    child = src / "main.py"
    tree._apply_snapshot(
        FileTreeSnapshot(
            root_path=str(workspace),
            filter_text="",
            entries=(FileTreeEntry("src", str(src), True),),
        )
    )
    src_item = tree.topLevelItem(0)
    assert src_item.data(0, _IS_DIR_ROLE) is True
    src_item.setData(0, _LOAD_GENERATION_ROLE, 5)

    def fail_isdir(path):
        if str(path) in {str(src), str(child)}:
            raise AssertionError(f"unexpected isdir stat: {path}")
        return False

    def fail_isfile(path):
        if str(path) in {str(src), str(child)}:
            raise AssertionError(f"unexpected isfile stat: {path}")
        return False

    monkeypatch.setattr(left_panel.os.path, "isdir", fail_isdir)
    monkeypatch.setattr(left_panel.os.path, "isfile", fail_isfile)

    tree._apply_children_snapshot(
        5,
        str(src),
        FileTreeSnapshot(
            root_path=str(src),
            filter_text="",
            entries=(FileTreeEntry("main.py", str(child), False),),
        ),
    )

    child_item = src_item.child(0)
    assert child_item.data(0, _IS_DIR_ROLE) is False
    assert child_item.text(0) == "main.py"


def test_files_tree_registers_keyboard_shortcuts(qapp, workspace):
    tree = FileTree(str(workspace))

    for sequence in [
        "F2",
        "Delete",
        "F5",
        "Ctrl+Alt+N",
        "Ctrl+Shift+N",
        "Ctrl+C",
        "Ctrl+Shift+C",
        "Shift+F10",
    ]:
        assert _has_shortcut(tree, sequence)


def test_files_tree_open_selected_emits_file(qapp, workspace):
    tree = FileTree(str(workspace))
    opened = []
    tree.file_opened.connect(opened.append)
    assert tree.reveal_file(str(workspace / "src" / "main.py"))

    tree._open_selected()

    assert opened == [str(workspace / "src" / "main.py")]


def test_files_tree_copies_selected_paths(qapp, workspace):
    tree = FileTree(str(workspace))
    path = workspace / "src" / "main.py"
    assert tree.reveal_file(str(path))

    tree._copy_selected_relative_path()
    assert qapp.clipboard().text() == "src/main.py"

    tree._copy_selected_absolute_path()
    assert qapp.clipboard().text() == str(path.resolve())


def test_files_header_filter_replaces_path_label(qapp, workspace):
    header = _FilesHeader(str(workspace), filter_enabled=True)
    values = []
    header.filter_changed.connect(values.append)
    edit = header.findChild(QLineEdit, "filesFilter")

    assert edit is not None
    assert edit.placeholderText() == "Filter files"
    assert edit.toolTip() == str(workspace)

    edit.setText("main")

    assert values == ["main"]


def test_files_tree_uses_icons_for_folders_and_known_files(qapp, workspace):
    tree = FileTree(str(workspace))
    src = tree.topLevelItem(0)
    tree._on_item_expanded(src)
    _wait_for_loaded_children(qapp, src)
    item = src.child(0)

    assert not src.icon(0).isNull()
    assert not item.icon(0).isNull()


def test_files_tree_filters_by_relative_path(qapp, workspace):
    (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
    tree = FileTree(str(workspace))

    tree.set_filter_text("main")
    _wait_until(qapp, lambda: tree.topLevelItemCount() == 1)

    assert tree.topLevelItemCount() == 1
    item = tree.topLevelItem(0)
    assert item.text(0) == "src/main.py"
    assert item.data(0, Qt.ItemDataRole.UserRole) == str(workspace / "src" / "main.py")
    assert tree.mimeData([item]).text() == "@src/main.py"

    tree.set_filter_text("")
    _wait_until(qapp, lambda: tree.topLevelItemCount() > 0 and tree.topLevelItem(0).text(0) == "src")

    assert tree.topLevelItem(0).text(0) == "src"


def test_files_tree_shows_project_dot_folders(qapp, workspace):
    skills = workspace / ".agents" / "skills"
    skills.mkdir(parents=True)
    (skills / "demo.md").write_text("Use this skill.\n", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    tree = FileTree(str(workspace))

    names = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]

    assert ".agents" in names
    assert ".git" not in names
    assert ".env" in names

    tree.set_filter_text("demo")
    _wait_until(qapp, lambda: tree.topLevelItemCount() == 1)

    assert tree.topLevelItemCount() == 1
    assert tree.topLevelItem(0).text(0) == ".agents/skills/demo.md"


def test_files_tree_marks_dirty_files_and_parent_folders(qapp, workspace):
    path = workspace / "src" / "main.py"
    tree = FileTree(str(workspace))
    src = tree.topLevelItem(0)
    tree._on_item_expanded(src)
    _wait_for_loaded_children(qapp, src)

    tree.set_file_dirty(str(path), True)

    item = src.child(0)
    assert item.text(0) == "* main.py"
    assert "Unsaved editor changes" in item.toolTip(0)
    assert "Contains unsaved editor changes" in src.toolTip(0)

    tree.set_file_dirty(str(path), False)

    assert item.text(0) == "main.py"
    assert item.toolTip(0) == ""


def test_files_tree_reveal_rejects_outside_workspace(qapp, workspace, tmp_path):
    tree = FileTree(str(workspace))
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    assert tree.reveal_file(str(outside)) is False


def test_files_tree_creates_file_in_folder(qapp, workspace):
    tree = FileTree(str(workspace))

    created = tree.create_file(str(workspace / "src"), "notes.txt")

    assert created == workspace / "src" / "notes.txt"
    assert created.read_text(encoding="utf-8") == ""


def test_files_tree_create_file_invalidates_workspace_file_cache(qapp, workspace):
    clear_workspace_file_cache()
    tree = FileTree(str(workspace))
    cached = list_workspace_files(workspace)

    created = tree.create_file(str(workspace / "src"), "notes.txt")
    refreshed = list_workspace_files(workspace)

    assert str(created) not in cached
    assert str(created) in refreshed


def test_files_tree_refresh_invalidates_workspace_file_cache(qapp, workspace, monkeypatch):
    clear_workspace_file_cache()
    tree = FileTree(str(workspace), defer_git_status=True)
    monkeypatch.setattr(tree, "_request_refresh", lambda: None)
    cached = list_workspace_files(workspace)
    created = workspace / "src" / "external.txt"
    created.write_text("outside tree action\n", encoding="utf-8")

    tree.refresh()
    refreshed = list_workspace_files(workspace)

    assert str(created) not in cached
    assert str(created) in refreshed


def test_files_tree_new_file_shortcut_uses_selected_file_parent(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace))
    assert tree.reveal_file(str(workspace / "src" / "main.py"))
    actions = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QInputDialog.getText",
        lambda *args, **kwargs: ("notes.txt", True),
    )
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, path, **kwargs: actions.append((action, path, kwargs)),
    )

    tree._new_file_selected()

    assert actions == [("create_file", str(workspace / "src"), {"name": "notes.txt"})]
    assert not (workspace / "src" / "notes.txt").exists()


def test_files_tree_creates_folder_in_folder(qapp, workspace):
    tree = FileTree(str(workspace))

    created = tree.create_folder(str(workspace / "src"), "package")

    assert created == workspace / "src" / "package"
    assert created.is_dir()


def test_files_tree_moves_file_to_folder(qapp, workspace):
    destination = workspace / "docs"
    destination.mkdir()
    tree = FileTree(str(workspace))
    source = workspace / "src" / "main.py"

    moved_to = tree.move_paths([str(source)], str(destination))

    assert moved_to == destination
    assert (destination / "main.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert not source.exists()


def test_files_tree_drop_move_requires_confirmation(qapp, workspace, monkeypatch):
    destination = workspace / "docs"
    destination.mkdir()
    tree = FileTree(str(workspace))
    assert tree.reveal_file(str(workspace / "src" / "main.py"))
    source_item = tree._find_item_for_path(str(workspace / "src" / "main.py"))
    destination_item = tree._find_item_for_path(str(destination))
    assert source_item is not None
    assert destination_item is not None
    mime = tree.mimeData([source_item])
    pos = tree.visualItemRect(destination_item).center()
    actions = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )

    enter = _FakeDropEvent(mime, pos)
    drop = _FakeDropEvent(mime, pos)
    tree.dragEnterEvent(enter)
    tree.dropEvent(drop)

    assert enter.accepted is True
    assert enter.drop_action == Qt.DropAction.MoveAction
    assert drop.accepted is True
    assert drop.drop_action == Qt.DropAction.MoveAction
    assert actions == [
        (
            "move",
            [str(workspace / "src" / "main.py")],
            {"destination": str(destination)},
        )
    ]
    assert (workspace / "src" / "main.py").exists()


def test_files_tree_drop_move_highlights_destination_folder(qapp, workspace):
    destination = workspace / "docs"
    destination.mkdir()
    tree = FileTree(str(workspace))
    assert tree.reveal_file(str(workspace / "src" / "main.py"))
    source_item = tree._find_item_for_path(str(workspace / "src" / "main.py"))
    destination_item = tree._find_item_for_path(str(destination))
    assert source_item is not None
    assert destination_item is not None
    mime = tree.mimeData([source_item])
    pos = tree.visualItemRect(destination_item).center()

    move = _FakeDropEvent(mime, pos)
    tree.dragMoveEvent(move)

    assert move.accepted is True
    assert tree._move_drop_highlight_item is destination_item

    tree._clear_move_drop_highlight()

    assert tree._move_drop_highlight_item is None


def test_files_tree_drop_move_highlights_file_parent(qapp, workspace):
    destination_dir = workspace / "docs"
    destination_dir.mkdir()
    destination_file = destination_dir / "note.txt"
    destination_file.write_text("note\n", encoding="utf-8")
    tree = FileTree(str(workspace))
    assert tree.reveal_file(str(workspace / "src" / "main.py"))
    assert tree.reveal_file(str(destination_file))
    source_item = tree._find_item_for_path(str(workspace / "src" / "main.py"))
    destination_item = tree._find_item_for_path(str(destination_file))
    destination_parent = tree._find_item_for_path(str(destination_dir))
    assert source_item is not None
    assert destination_item is not None
    assert destination_parent is not None
    mime = tree.mimeData([source_item])
    pos = tree.visualItemRect(destination_item).center()

    move = _FakeDropEvent(mime, pos)
    tree.dragMoveEvent(move)

    assert move.accepted is True
    assert tree._move_drop_highlight_item is destination_parent


def test_files_tree_drop_move_to_own_folder_is_ignored(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace))
    assert tree.reveal_file(str(workspace / "src" / "main.py"))
    source_item = tree._find_item_for_path(str(workspace / "src" / "main.py"))
    assert source_item is not None
    mime = tree.mimeData([source_item])
    pos = tree.visualItemRect(source_item).center()
    actions = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not confirm")),
    )
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )

    move = _FakeDropEvent(mime, pos)
    drop = _FakeDropEvent(mime, pos)
    tree.dragMoveEvent(move)
    tree.dropEvent(drop)

    assert move.accepted is True
    assert move.drop_action == Qt.DropAction.MoveAction
    assert drop.ignored is True
    assert tree._move_drop_highlight_item is None
    assert actions == []


def test_files_tree_drop_move_can_cancel_confirmation(qapp, workspace, monkeypatch):
    destination = workspace / "docs"
    destination.mkdir()
    tree = FileTree(str(workspace))
    assert tree.reveal_file(str(workspace / "src" / "main.py"))
    source_item = tree._find_item_for_path(str(workspace / "src" / "main.py"))
    destination_item = tree._find_item_for_path(str(destination))
    assert source_item is not None
    assert destination_item is not None
    mime = tree.mimeData([source_item])
    pos = tree.visualItemRect(destination_item).center()
    actions = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )

    drop = _FakeDropEvent(mime, pos)
    tree.dropEvent(drop)

    assert drop.ignored is True
    assert actions == []
    assert (workspace / "src" / "main.py").exists()


def test_files_tree_renames_file(qapp, workspace):
    tree = FileTree(str(workspace))
    old = workspace / "src" / "main.py"

    new = tree.rename_file(str(old), "app.py")

    assert new == workspace / "src" / "app.py"
    assert new.read_text(encoding="utf-8") == "print('hi')\n"
    assert not old.exists()


def test_files_tree_renames_folder(qapp, workspace):
    tree = FileTree(str(workspace))

    new = tree.rename_path(str(workspace / "src"), "app")

    assert new == workspace / "app"
    assert (workspace / "app" / "main.py").exists()


@pytest.mark.parametrize("name", ["", "  ", ".", "..", "../escape.py", "nested/file.py"])
def test_files_tree_rejects_path_like_file_names(qapp, workspace, name):
    tree = FileTree(str(workspace))

    with pytest.raises(ValueError):
        tree.create_file(str(workspace / "src"), name)


def test_files_tree_rename_rejects_existing_file(qapp, workspace):
    tree = FileTree(str(workspace))
    (workspace / "src" / "app.py").write_text("already here\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        tree.rename_file(str(workspace / "src" / "main.py"), "app.py")


def test_files_tree_create_folder_rejects_existing_path(qapp, workspace):
    tree = FileTree(str(workspace))

    with pytest.raises(FileExistsError):
        tree.create_folder(str(workspace), "src")


def test_files_tree_deletes_file(qapp, workspace):
    tree = FileTree(str(workspace))
    path = workspace / "src" / "main.py"

    tree.delete_path(str(path))

    assert not path.exists()


def test_files_tree_deletes_folder_recursively(qapp, workspace):
    tree = FileTree(str(workspace))
    folder = workspace / "src" / "package"
    folder.mkdir()
    (folder / "module.py").write_text("x = 1\n", encoding="utf-8")

    tree.delete_path(str(folder))

    assert not folder.exists()


def test_files_tree_action_thread_creates_file_and_emits_path(qapp, workspace):
    done = []
    thread = _FileTreeActionThread(
        str(workspace),
        "create_file",
        str(workspace / "src"),
        name="queued.txt",
    )
    thread.done.connect(lambda *args: done.append(args))

    thread.run()

    assert done == [("create_file", str(workspace / "src" / "queued.txt"), "")]
    assert (workspace / "src" / "queued.txt").exists()


def test_files_tree_action_thread_discards_with_error_data(qapp, workspace, monkeypatch):
    done = []
    path = workspace / "src" / "main.py"
    monkeypatch.setattr(
        "ui.widgets.left_panel.discard_files",
        lambda *_args, **_kwargs: GitCommandResult(1, "", "nope"),
    )
    thread = _FileTreeActionThread(
        str(workspace),
        "discard",
        str(path),
        rel_path="src/main.py",
    )
    thread.done.connect(lambda *args: done.append(args))

    thread.run()

    assert done == [("discard", "", "nope")]


def test_files_tree_action_thread_discards_multiple_files(qapp, workspace, monkeypatch):
    done = []
    main = workspace / "src" / "main.py"
    note = workspace / "note.txt"
    note.write_text("new\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.discard_files",
        lambda repo_path, paths, **_kwargs: calls.append((repo_path, paths)) or GitCommandResult(0, "", ""),
    )
    thread = _FileTreeActionThread(
        str(workspace),
        "discard",
        [str(main), str(note)],
        rel_path=["src/main.py", "note.txt"],
    )
    thread.done.connect(lambda *args: done.append(args))

    thread.run()

    assert calls == [(str(workspace), ["src/main.py", "note.txt"])]
    assert done == [("discard", str(main), "")]


def test_files_tree_delete_rejects_workspace_root(qapp, workspace):
    tree = FileTree(str(workspace))

    with pytest.raises(ValueError):
        tree.delete_path(str(workspace))


def test_files_tree_delete_dialog_requires_confirmation(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace))
    path = workspace / "src" / "main.py"
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    tree._delete_path_dialog(str(path))

    assert path.exists()


def test_files_tree_delete_dialog_removes_confirmed_path(qapp, workspace, monkeypatch):
    tree = FileTree(str(workspace))
    path = workspace / "src" / "main.py"
    actions = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )

    tree._delete_path_dialog(str(path))

    assert actions == [("delete", [str(path)], {})]
    assert path.exists()


def test_files_tree_delete_dialog_removes_multiple_confirmed_files(qapp, workspace, monkeypatch):
    extra = workspace / "note.txt"
    extra.write_text("note\n", encoding="utf-8")
    tree = FileTree(str(workspace))
    path = workspace / "src" / "main.py"
    actions = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )

    tree._delete_path_dialog([str(path), str(extra)])

    assert actions == [("delete", [str(path), str(extra)], {})]
    assert path.exists()
    assert extra.exists()


def test_files_tree_discard_option_only_for_modified_files(qapp, workspace):
    modified = workspace / "src" / "main.py"
    added = workspace / "note.txt"
    modified.write_text("print('modified')\n", encoding="utf-8")
    added.write_text("new\n", encoding="utf-8")

    tree = FileTree(str(workspace))
    tree._git_by_path = {
        _path_key(str(modified)): (" M", "M"),
        _path_key(str(added)): ("??", "?"),
    }

    assert tree._is_discardable_modified_file(str(modified))
    assert not tree._is_discardable_modified_file(str(added))


def test_files_tree_discard_dialog_restores_modified_file(qapp, workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    path.write_text("print('discard from files tab')\n", encoding="utf-8")
    tree = FileTree(str(workspace))
    tree._git_by_path = {_path_key(str(path)): (" M", "M")}
    actions = []
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )
    questions = []
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda parent, title, detail, buttons, default: questions.append(
            (parent, title, detail, buttons, default)
        )
        or QMessageBox.StandardButton.Discard,
    )

    tree._discard_file_dialog(str(path))

    assert actions == [
        ("discard", [str(path)], {"rel_path": ["src/main.py"], "staged": False})
    ]
    assert questions
    assert questions[0][0] is tree
    assert questions[0][1] == "Discard changes?"
    assert "src/main.py" in questions[0][2]


def test_files_tree_discard_dialog_restores_staged_modified_file(qapp, workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    path.write_text("print('discard staged from files tab')\n", encoding="utf-8")
    tree = FileTree(str(workspace))
    tree._git_by_path = {_path_key(str(path)): ("M ", "M")}
    actions = []
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )

    tree._discard_file_dialog(str(path))

    assert actions == [
        ("discard", [str(path)], {"rel_path": ["src/main.py"], "staged": True})
    ]


def test_files_tree_discard_dialog_restores_multiple_modified_files(qapp, workspace, monkeypatch):
    first = workspace / "src" / "main.py"
    second = workspace / "src" / "other.py"
    first.write_text("print('first')\n", encoding="utf-8")
    second.write_text("print('second')\n", encoding="utf-8")
    tree = FileTree(str(workspace))
    tree._git_by_path = {
        _path_key(str(first)): (" M", "M"),
        _path_key(str(second)): (" M", "M"),
    }
    actions = []
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )

    tree._discard_file_dialog([str(first), str(second)])

    assert actions == [
        (
            "discard",
            [str(first), str(second)],
            {"rel_path": ["src/main.py", "src/other.py"], "staged": False},
        )
    ]


def test_files_tree_discard_cancel_keeps_modified_file(qapp, workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    path.write_text("print('keep files tab change')\n", encoding="utf-8")
    tree = FileTree(str(workspace))
    tree._git_by_path = {_path_key(str(path)): (" M", "M")}
    actions = []
    monkeypatch.setattr(
        tree,
        "_start_action",
        lambda action, queued_path, **kwargs: actions.append((action, queued_path, kwargs)),
    )
    monkeypatch.setattr(
        "ui.widgets.left_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )

    tree._discard_file_dialog(str(path))

    assert actions == []
    assert path.read_text(encoding="utf-8") == "print('keep files tab change')\n"


def test_files_tree_combines_git_and_dirty_markers(qapp, workspace):
    path = workspace / "src" / "main.py"
    path.write_text("print('modified')\n", encoding="utf-8")
    tree = FileTree(str(workspace))
    tree._git_by_path = {_path_key(str(path)): (" M", "M")}
    src = tree.topLevelItem(0)
    tree._on_item_expanded(src)
    _wait_for_loaded_children(qapp, src)

    tree.set_file_dirty(str(path), True)

    item = src.child(0)
    assert item.text(0) == "* main.py"
    assert not item.icon(0).isNull()
    assert "Git: Modified" in item.toolTip(0)


def test_git_log_drags_commit_reference(qapp, workspace, monkeypatch):
    monkeypatch.setattr(
        "ui.widgets.git_panel.build_git_snapshot",
        lambda repo_path: GitSnapshot(
            repo_path=repo_path,
            is_repo=True,
            log_lines=("abcdef123456\x1fabcdef1\x1finitial",),
        ),
    )
    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel.log.count() == 1)
    item = panel.log.item(0)

    mime = panel.log.mimeData([item])
    commits = parse_commit_drop(mime.data(AICHS_COMMIT_DROP_MIME))

    assert mime.hasFormat(AICHS_COMMIT_DROP_MIME)
    assert len(commits) == 1
    assert commits[0]["subject"] == "initial"
    assert mime.text().startswith("commit ")
    assert "initial" in mime.text()


def test_git_log_double_click_opens_commit_diff(qapp, workspace, monkeypatch):
    monkeypatch.setattr(
        "ui.widgets.git_panel.build_git_snapshot",
        lambda repo_path: GitSnapshot(
            repo_path=repo_path,
            is_repo=True,
            log_lines=("abcdef123456\x1fabcdef1\x1finitial",),
        ),
    )
    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel.log.count() == 1)
    item = panel.log.item(0)
    calls = []

    def fake_commit_diff(repo_path, commit_hash):
        calls.append(("diff", repo_path, commit_hash))
        return "@@ diff\n-old\n+new\n"

    def fake_show(short_hash, subject, diff_text):
        calls.append(("show", short_hash, subject, diff_text))

    monkeypatch.setattr("ui.widgets.git_panel.commit_diff", fake_commit_diff)
    monkeypatch.setattr(panel, "_show_commit_diff_dialog", fake_show)

    panel.log.itemDoubleClicked.emit(item)

    assert calls[0][0] == "diff"
    assert calls[0][1] == str(workspace)
    assert len(calls[0][2]) >= 7
    assert calls[1] == ("show", item.text().split(" ", 1)[0], "initial", "@@ diff\n-old\n+new\n")


def test_commit_diff_dialog_uses_file_list_and_single_diff_viewer(qapp):
    diff = "\n".join([
        "diff --git a/src/main.py b/src/main.py",
        "index 111..222 100644",
        "--- a/src/main.py",
        "+++ b/src/main.py",
        "@@ -1 +1 @@",
        "-old",
        "+new",
        "diff --git a/README.md b/README.md",
        "index 333..444 100644",
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -0,0 +1 @@",
        "+hello",
    ])
    dlg = _CommitDiffDialog("abc1234", "files", diff)

    file_list = dlg.findChild(QListWidget, "commitFileList")
    viewers = dlg.findChildren(QTextBrowser)
    summary = dlg.findChild(QLabel, "commitSummary")

    assert summary.text() == "2 files changed  +2 -1"
    assert file_list is not None
    assert [file_list.item(row).text() for row in range(file_list.count())] == [
        "src/main.py (+1 -1)",
        "README.md (+1)",
    ]
    assert len(viewers) == 1
    assert "old" in viewers[0].toPlainText()
    file_list.setCurrentRow(1)
    assert "hello" in viewers[0].toPlainText()


@pytest.mark.parametrize("theme_name", ["dark", "light", "modern"])
def test_commit_diff_dialog_uses_active_theme(qapp, theme_name):
    SettingsStore().update({"theme": theme_name})
    dlg = _CommitDiffDialog(
        "abc1234",
        "theme check",
        "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n",
    )
    p = palette(theme_name)

    css = dlg.styleSheet()
    viewer = dlg.findChild(QTextBrowser, "commitDiffViewer")

    assert p["BG2"] in css
    assert p["BG3"] in css
    assert p["BORDER"] in css
    assert viewer is not None
    assert p["BG3"] in viewer.toHtml()


def test_conversation_list_drags_chat_reference(qapp, store):
    store.save(
        "drag_chat",
        {
            "id": "drag_chat",
            "title": "Viewport picking",
            "messages": [],
            "updated_at": "2026-02-01T12:00:00",
        },
    )
    panel = ConversationPanel(store)
    item = panel.list.item(0)

    mime = panel.list.mimeData([item])

    assert mime.hasFormat(AICHS_CHAT_DROP_MIME)
    assert parse_chat_drop(mime.data(AICHS_CHAT_DROP_MIME)) == [
        {"id": "drag_chat", "title": "Viewport picking"}
    ]
    assert mime.text() == '@Archivist using chat "Viewport picking", '


def test_drag_payload_helpers_clean_bad_values():
    assert parse_file_drop(b"not json") == []
    assert parse_commit_drop(file_drop_payload(["README.md"])) == []
    assert parse_chat_drop(b"not json") == []
    assert parse_chat_drop(file_drop_payload(["README.md"])) == []
    assert parse_chat_drop(b'{"kind":"aichs-chat-drop","chats":[null,{"id":""},{"id":"c3"}]}') == [
        {"id": "c3", "title": "Untitled"}
    ]
    assert chat_drop_text([]) == ""
    assert chat_drop_text([
        {"id": "c1", "title": "One"},
        {"id": "c2", "title": "Two"},
    ]) == '@Archivist using chats "One", "Two", '
    assert parse_chat_drop(chat_drop_payload([None, {"id": ""}, {"id": "c1", "title": "  A   B  "}])) == [
        {"id": "c1", "title": "A B"}
    ]
    assert parse_commit_drop(
        b'{"kind":"aichs-commit-drop","commits":[null,{"hash":""},{"hash":"def","subject":"  fix   it  "}]}'
    ) == [{"hash": "def", "subject": "fix it"}]
    assert parse_commit_drop(commit_drop_payload([None, {"hash": ""}, {"hash": "abc"}])) == [
        {"hash": "abc", "subject": ""}
    ]
    assert chat_drop_text([{"id": "c1", "title": 'Say "hi"'}]) == '@Archivist using chat "Say \'hi\'", '
    assert commit_drop_text([{"hash": "abc", "subject": ""}]) == "commit abc"
    assert file_drop_text(["docs/read me.md", "docs/read me.md"]) == '@"docs/read me.md"'
