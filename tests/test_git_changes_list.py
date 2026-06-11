import json
import time

from PyQt6.QtCore import QMimeData, Qt
from PyQt6.QtWidgets import QAbstractItemView, QMessageBox, QPushButton

from services.chat_drag import AICHS_FILE_DROP_MIME, parse_file_drop
from services.git_snapshot import GitSnapshot
from services.git_status import GitCommandResult, GitFileChange, stage_files
from storage.settings import SettingsStore
from ui.theme import ACCENT, palette
from ui.widgets.git_changes_list import (
    GitChangesList,
    _GIT_CHANGE_MIME,
    _GitChangeList,
    _default_stash_message,
    _commit_message_busy_icon,
    _git_change_button_style,
    _git_change_field_style,
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


def test_git_changes_list_populates_staged_and_unstaged(qapp, git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('staged')\n", encoding="utf-8")
    from services.git_status import stage_files

    assert stage_files(str(git_repo), ["src/main.py"]).ok
    main.write_text("print('unstaged too')\n", encoding="utf-8")
    (git_repo / "note.txt").write_text("new\n", encoding="utf-8")

    widget = GitChangesList(str(git_repo))
    _wait_until(qapp, lambda: widget.staged_list.count() == 1 and widget.unstaged_list.count() == 2)

    staged = [widget.staged_list.item(i).text() for i in range(widget.staged_list.count())]
    unstaged = [widget.unstaged_list.item(i).text() for i in range(widget.unstaged_list.count())]

    assert staged == ["src/main.py"]
    assert "src/main.py" in unstaged
    assert "note.txt" in unstaged
    assert not widget.staged_list.item(0).icon().isNull()
    assert widget.staged_list.item(0).toolTip() == "Modified — src/main.py"
    note = next(
        widget.unstaged_list.item(i)
        for i in range(widget.unstaged_list.count())
        if widget.unstaged_list.item(i).text() == "note.txt"
    )
    assert not note.icon().isNull()
    assert note.toolTip() == "Untracked — note.txt"
    assert widget._staged_label.text() == "Staged (1)"
    assert widget._unstaged_label.text() == "Unstaged (2)"


def test_git_changes_list_commit_controls_are_first_and_only_button(qapp, workspace):
    widget = GitChangesList(str(workspace))
    margins = widget.layout().contentsMargins()

    assert widget.layout().itemAt(0).widget() is widget.summary
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (6, 0, 6, 0)
    assert widget.summary.placeholderText() == "Commit Message"
    assert widget.layout().itemAt(3).spacerItem() is not None
    assert widget.layout().itemAt(4).widget() is widget._staged_label
    assert [button.text() for button in widget.findChildren(QPushButton)] == ["Commit"]


def test_git_changes_list_commit_requires_staged_files_and_summary(qapp, workspace):
    widget = GitChangesList(str(workspace))
    widget._staged_count = 1
    widget._update_action_state()

    assert not widget._commit_btn.isEnabled()
    widget.summary.setText("commit from ui")
    assert widget._commit_btn.isEnabled()


def test_git_changes_list_generate_action_tracks_staged_files(qapp, workspace, monkeypatch):
    changes = []
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.build_git_snapshot",
        lambda repo_path: GitSnapshot(repo_path=repo_path, is_repo=True, changes=tuple(changes)),
    )
    widget = GitChangesList(str(workspace), current_model_getter=lambda: "model-a")
    _wait_until(qapp, lambda: not widget._refresh_threads)

    assert widget._generate_action in widget.summary.actions()
    assert widget._generate_action.toolTip() == "Generate commit message from staged files"
    assert not widget._generate_action.isEnabled()

    changes.append(
        GitFileChange(
            code="M ",
            label="M",
            rel_path="src/main.py",
            abs_path=str(workspace / "src" / "main.py"),
            staged=True,
            unstaged=False,
            staged_label="M",
        )
    )
    widget.refresh()
    _wait_until(qapp, lambda: widget._generate_action.isEnabled())

    assert widget._generate_action.isEnabled()


def test_git_changes_list_generate_action_disables_while_running(qapp, workspace):
    widget = GitChangesList(str(workspace), current_model_getter=lambda: "model-a")

    class Running:
        def isRunning(self):
            return True

    widget._staged_count = 1
    widget._message_thread = Running()
    widget._update_action_state()

    assert not widget._generate_action.isEnabled()
    widget._on_commit_message_finished()


def test_git_changes_list_shutdown_clears_refresh_threads(qapp, workspace):
    widget = GitChangesList(str(workspace), defer_refresh=True)

    widget.refresh()
    widget.shutdown()

    assert widget._refresh_threads == []
    assert widget._refresh_generation == 2


def test_git_changes_list_generated_message_replaces_summary_and_body(qapp, workspace):
    widget = GitChangesList(str(workspace))
    widget.summary.setText("old summary")
    widget.body.setPlainText("old body")

    widget._on_commit_message_generated("new summary", "new body")

    assert widget.summary.text() == "new summary"
    assert widget.body.toPlainText() == "new body"


def test_git_changes_list_generate_error_uses_dialog(qapp, workspace, monkeypatch):
    widget = GitChangesList(str(workspace))
    warnings = []
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.QMessageBox.warning",
        lambda parent, title, detail: warnings.append((parent, title, detail)),
    )

    widget._on_commit_message_error("model unavailable")

    assert warnings == [(widget, "Generate commit message failed", "model unavailable")]


def test_git_changes_list_generate_uses_current_model_and_guidance(qapp, workspace, monkeypatch):
    store = SettingsStore()
    store.save({"commit_message_prompt_addition": "Use Jira keys"})
    created = []

    class FakeSignal:
        def connect(self, callback):
            pass

    class FakeThread:
        done = FakeSignal()
        error = FakeSignal()
        finished = FakeSignal()

        def __init__(self, model, repo_path, guidance):
            created.append((model, repo_path, guidance))

        def isRunning(self):
            return False

        def start(self):
            pass

    monkeypatch.setattr("ui.widgets.git_changes_list.CommitMessageThread", FakeThread)
    widget = GitChangesList(
        str(workspace),
        settings=store,
        current_model_getter=lambda: "model-a",
    )
    widget._staged_count = 1

    widget._generate_commit_message()

    assert created == [("model-a", str(workspace), "Use Jira keys")]
    assert not widget._generate_action.icon().isNull()
    assert widget._generate_action.toolTip() == "Generating commit message..."
    widget._on_commit_message_finished()
    assert widget._generate_action.text() == ""
    assert widget._generate_action.toolTip() == "Generate commit message from staged files"


def test_git_changes_list_generate_animation_advances_frames(qapp, workspace):
    widget = GitChangesList(str(workspace))

    widget._start_generate_animation()
    first = widget._generate_frame
    widget._advance_generate_animation()
    second = widget._generate_frame
    widget._stop_generate_animation()

    assert first != second
    assert not widget._generate_action.icon().isNull()
    assert widget._generate_action.text() == ""


def test_git_changes_list_busy_icon_is_visible(qapp):
    icon = _commit_message_busy_icon(0)

    assert not icon.isNull()


def test_git_changes_lists_drag_normally_between_sections(qapp, workspace):
    widget = GitChangesList(str(workspace))

    for changes_list in (widget.staged_list, widget.unstaged_list):
        assert changes_list.dragDropMode() == QAbstractItemView.DragDropMode.DragOnly
        assert changes_list.dragEnabled()
        assert not changes_list.acceptDrops()
        assert changes_list.viewport().acceptDrops()
        assert changes_list.defaultDropAction() == Qt.DropAction.MoveAction


def test_git_changes_lists_drag_file_refs_to_chat(qapp, git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('staged')\n", encoding="utf-8")
    assert stage_files(str(git_repo), ["src/main.py"]).ok
    (git_repo / "note.txt").write_text("new\n", encoding="utf-8")
    widget = GitChangesList(str(git_repo))
    _wait_until(qapp, lambda: widget.staged_list.count() == 1 and widget.unstaged_list.count() == 1)

    staged_mime = widget.staged_list.mimeData([widget.staged_list.item(0)])
    unstaged_mime = widget.unstaged_list.mimeData([widget.unstaged_list.item(0)])

    assert staged_mime.hasFormat(_GIT_CHANGE_MIME)
    assert staged_mime.hasFormat(AICHS_FILE_DROP_MIME)
    assert parse_file_drop(staged_mime.data(AICHS_FILE_DROP_MIME)) == ["src/main.py"]
    assert staged_mime.text() == "@src/main.py"
    assert unstaged_mime.hasFormat(_GIT_CHANGE_MIME)
    assert unstaged_mime.hasFormat(AICHS_FILE_DROP_MIME)
    assert parse_file_drop(unstaged_mime.data(AICHS_FILE_DROP_MIME)) == ["note.txt"]
    assert unstaged_mime.text() == "@note.txt"


def test_git_changes_drop_releases_with_move_action(qapp):
    changes = _GitChangeList(staged=True)
    calls = []
    changes.files_dropped.connect(lambda *args: calls.append(args))

    mime = QMimeData()
    mime.setData(
        _GIT_CHANGE_MIME,
        json.dumps({"staged": False, "paths": ["src/main.py"]}).encode("utf-8"),
    )
    enter = _FakeDropEvent(mime)
    drop = _FakeDropEvent(mime)
    changes.dragEnterEvent(enter)
    changes.dropEvent(drop)

    assert enter.accepted is True
    assert drop.accepted is True
    assert drop.drop_action == Qt.DropAction.MoveAction
    assert calls == [(False, True, ["src/main.py"])]
    changes.close()


def test_git_changes_list_drag_move_stages_selected_file(qapp, workspace, monkeypatch):
    calls = []
    refreshes = []
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.stage_files",
        lambda repo_path, paths: calls.append(("stage", repo_path, paths))
        or GitCommandResult(0, "staged", ""),
    )

    widget = GitChangesList(str(workspace))
    monkeypatch.setattr(widget, "refresh", lambda: refreshes.append(True))
    history_refreshes = []
    widget.git_changed.connect(lambda: history_refreshes.append(True))
    widget._move_paths(False, True, ["src/main.py"])

    assert calls == [("stage", str(workspace), ["src/main.py"])]
    assert refreshes == [True]
    assert history_refreshes == []


def test_git_changes_list_successful_action_clears_snapshot_cache(qapp, workspace, monkeypatch):
    widget = GitChangesList(str(workspace))
    refreshes = []
    cleared = []
    monkeypatch.setattr(widget, "refresh", lambda: refreshes.append(True))
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.clear_git_snapshot_cache",
        lambda repo_path: cleared.append(repo_path),
    )

    widget._run_change_action("Stage", GitCommandResult(0, "ok", ""))

    assert cleared == [str(workspace)]
    assert refreshes == [True]


def test_git_changes_list_drag_move_unstages_selected_file(qapp, workspace, monkeypatch):
    calls = []
    refreshes = []
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.unstage_files",
        lambda repo_path, paths: calls.append(("unstage", repo_path, paths))
        or GitCommandResult(0, "unstaged", ""),
    )

    widget = GitChangesList(str(workspace))
    monkeypatch.setattr(widget, "refresh", lambda: refreshes.append(True))
    widget._move_paths(True, False, ["src/main.py"])

    assert calls == [("unstage", str(workspace), ["src/main.py"])]
    assert refreshes == [True]


def test_git_changes_list_failure_uses_dialog_without_status_row(qapp, workspace, monkeypatch):
    widget = GitChangesList(str(workspace))
    warnings = []
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.QMessageBox.warning",
        lambda parent, title, detail: warnings.append((parent, title, detail)),
    )

    widget._run_change_action("Back", GitCommandResult(1, "", "cannot unstage"))

    assert not hasattr(widget, "_status")
    assert warnings == [(widget, "Back failed", "cannot unstage")]


def test_git_changes_list_discard_selected_confirms_and_discards(qapp, workspace, monkeypatch):
    calls = []
    refreshes = []
    widget = GitChangesList(str(workspace))
    widget.unstaged_list.clear()
    widget._add_change(
        widget.unstaged_list,
        GitFileChange(" M", "M", "src/main.py", str(workspace / "src" / "main.py")),
        "M",
    )
    widget.unstaged_list.item(0).setSelected(True)
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.discard_files",
        lambda repo_path, paths, staged=False: calls.append((repo_path, paths, staged))
        or GitCommandResult(0, "discarded", ""),
    )
    monkeypatch.setattr(widget, "refresh", lambda: refreshes.append(True))
    questions = []
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.QMessageBox.question",
        lambda parent, title, detail, buttons, default: questions.append(
            (parent, title, detail, buttons, default)
        )
        or QMessageBox.StandardButton.Discard,
    )

    widget._discard_selected(widget.unstaged_list)

    assert calls == [(str(workspace), ["src/main.py"], False)]
    assert refreshes == [True]
    assert questions
    assert questions[0][0] is widget
    assert questions[0][1] == "Discard changes?"
    assert "permanently removes" in questions[0][2]


def test_git_changes_list_discard_cancel_keeps_changes(qapp, git_repo, monkeypatch):
    main = git_repo / "src" / "main.py"
    main.write_text("print('keep me')\n", encoding="utf-8")
    widget = GitChangesList(str(git_repo))
    _wait_until(qapp, lambda: widget.unstaged_list.count() == 1)
    widget.unstaged_list.item(0).setSelected(True)
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.QMessageBox.question",
        lambda *args: QMessageBox.StandardButton.Cancel,
    )

    widget._discard_selected(widget.unstaged_list)

    assert main.read_text(encoding="utf-8") == "print('keep me')\n"
    assert widget.unstaged_list.count() == 1


def test_default_stash_message_summarizes_selected_paths():
    assert _default_stash_message(["a.py"]) == "AICHS stash: a.py"
    assert _default_stash_message(["a.py", "b.py", "c.py", "d.py"]) == (
        "AICHS stash: a.py, b.py, c.py, +1 more"
    )


def test_git_changes_list_styles_use_each_theme_palette(qapp):
    for theme in ("dark", "modern", "light"):
        p = palette(theme)
        button_style = _git_change_button_style(theme)
        field_style = _git_change_field_style(theme)
        assert button_style.count("{") == button_style.count("}")
        assert field_style.count("{") == field_style.count("}")
        for color in (p["BG2"], p["BG3"], p["TEXT"], p["TEXT_DIM"], p["BORDER_SUBTLE"]):
            assert color in button_style + field_style
        assert ACCENT in field_style


class _FakeDropEvent:
    def __init__(self, mime):
        self._mime = mime
        self.accepted = False
        self.drop_action = None

    def mimeData(self):
        return self._mime

    def setDropAction(self, action):
        self.drop_action = action

    def accept(self):
        self.accepted = True
