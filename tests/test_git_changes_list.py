import json

from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QPushButton

from services.git_status import GitCommandResult, stage_files
from storage.settings import SettingsStore
from ui.theme import ACCENT, palette
from ui.widgets.git_changes_list import (
    GitChangesList,
    _GIT_CHANGE_MIME,
    _default_stash_message,
    _commit_message_busy_icon,
    _git_change_button_style,
    _git_change_field_style,
)


def test_git_changes_list_populates_staged_and_unstaged(qapp, git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('staged')\n", encoding="utf-8")
    from services.git_status import stage_files

    assert stage_files(str(git_repo), ["src/main.py"]).ok
    main.write_text("print('unstaged too')\n", encoding="utf-8")
    (git_repo / "note.txt").write_text("new\n", encoding="utf-8")

    widget = GitChangesList(str(git_repo))

    staged = [widget.staged_list.item(i).text() for i in range(widget.staged_list.count())]
    unstaged = [widget.unstaged_list.item(i).text() for i in range(widget.unstaged_list.count())]

    assert staged == ["M src/main.py"]
    assert "M src/main.py" in unstaged
    assert "? note.txt" in unstaged
    assert widget._staged_label.text() == "Staged (1)"
    assert widget._unstaged_label.text() == "Unstaged (2)"


def test_git_changes_list_commit_controls_are_first_and_only_button(qapp, git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")
    from services.git_status import stage_files

    assert stage_files(str(git_repo), ["src/main.py"]).ok
    (git_repo / "note.txt").write_text("new\n", encoding="utf-8")

    widget = GitChangesList(str(git_repo))

    assert widget.layout().itemAt(0).widget() is widget.summary
    assert widget.summary.placeholderText() == "Commit Message"
    assert widget.layout().itemAt(3).widget() is widget._staged_label
    assert [button.text() for button in widget.findChildren(QPushButton)] == ["Commit"]


def test_git_changes_list_commit_requires_staged_files_and_summary(qapp, git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")

    assert stage_files(str(git_repo), ["src/main.py"]).ok

    widget = GitChangesList(str(git_repo))

    assert not widget._commit_btn.isEnabled()
    widget.summary.setText("commit from ui")
    assert widget._commit_btn.isEnabled()


def test_git_changes_list_generate_action_tracks_staged_files(qapp, git_repo):
    widget = GitChangesList(str(git_repo), current_model_getter=lambda: "model-a")

    assert widget._generate_action in widget.summary.actions()
    assert widget._generate_action.toolTip() == "Generate commit message from staged files"
    assert not widget._generate_action.isEnabled()

    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")
    assert stage_files(str(git_repo), ["src/main.py"]).ok
    widget.refresh()

    assert widget._generate_action.isEnabled()


def test_git_changes_list_generate_action_disables_while_running(qapp, git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")
    assert stage_files(str(git_repo), ["src/main.py"]).ok
    widget = GitChangesList(str(git_repo), current_model_getter=lambda: "model-a")

    class Running:
        def isRunning(self):
            return True

    widget._message_thread = Running()
    widget._update_action_state()

    assert not widget._generate_action.isEnabled()
    widget._on_commit_message_finished()


def test_git_changes_list_generated_message_replaces_summary_and_body(qapp, git_repo):
    widget = GitChangesList(str(git_repo))
    widget.summary.setText("old summary")
    widget.body.setPlainText("old body")

    widget._on_commit_message_generated("new summary", "new body")

    assert widget.summary.text() == "new summary"
    assert widget.body.toPlainText() == "new body"


def test_git_changes_list_generate_error_uses_dialog(qapp, git_repo, monkeypatch):
    widget = GitChangesList(str(git_repo))
    warnings = []
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.QMessageBox.warning",
        lambda parent, title, detail: warnings.append((parent, title, detail)),
    )

    widget._on_commit_message_error("model unavailable")

    assert warnings == [(widget, "Generate commit message failed", "model unavailable")]


def test_git_changes_list_generate_uses_current_model_and_guidance(qapp, git_repo, monkeypatch):
    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")
    assert stage_files(str(git_repo), ["src/main.py"]).ok
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
        str(git_repo),
        settings=store,
        current_model_getter=lambda: "model-a",
    )

    widget._generate_commit_message()

    assert created == [("model-a", str(git_repo), "Use Jira keys")]
    assert not widget._generate_action.icon().isNull()
    assert widget._generate_action.toolTip() == "Generating commit message..."
    widget._on_commit_message_finished()
    assert widget._generate_action.text() == ""
    assert widget._generate_action.toolTip() == "Generate commit message from staged files"


def test_git_changes_list_generate_animation_advances_frames(qapp, git_repo):
    widget = GitChangesList(str(git_repo))

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


def test_git_changes_lists_drag_normally_between_sections(qapp, git_repo):
    widget = GitChangesList(str(git_repo))

    for changes_list in (widget.staged_list, widget.unstaged_list):
        assert changes_list.dragDropMode() == QAbstractItemView.DragDropMode.DragOnly
        assert changes_list.dragEnabled()
        assert not changes_list.acceptDrops()
        assert changes_list.viewport().acceptDrops()
        assert changes_list.defaultDropAction() == Qt.DropAction.MoveAction


def test_git_changes_drop_releases_with_move_action(qapp, git_repo):
    widget = GitChangesList(str(git_repo))
    calls = []
    widget.staged_list.files_dropped.connect(lambda *args: calls.append(args))

    mime = QMimeData()
    mime.setData(
        _GIT_CHANGE_MIME,
        json.dumps({"staged": False, "paths": ["src/main.py"]}).encode("utf-8"),
    )
    enter = QDragEnterEvent(
        QPoint(2, 2),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget.staged_list.viewport(), enter)
    drop = QDropEvent(
        QPointF(2, 2),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget.staged_list.viewport(), drop)

    assert enter.isAccepted()
    assert drop.isAccepted()
    assert drop.dropAction() == Qt.DropAction.MoveAction
    assert calls == [(False, True, ["src/main.py"])]


def test_git_changes_list_drag_move_stages_selected_file(qapp, git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")

    widget = GitChangesList(str(git_repo))
    history_refreshes = []
    widget.git_changed.connect(lambda: history_refreshes.append(True))
    widget._move_paths(False, True, ["src/main.py"])

    assert widget.staged_list.count() == 1
    assert widget.unstaged_list.count() == 0
    assert widget.staged_list.item(0).text() == "M src/main.py"
    assert history_refreshes == []


def test_git_changes_list_drag_move_unstages_selected_file(qapp, git_repo):
    main = git_repo / "src" / "main.py"
    main.write_text("print('changed')\n", encoding="utf-8")
    from services.git_status import stage_files

    assert stage_files(str(git_repo), ["src/main.py"]).ok

    widget = GitChangesList(str(git_repo))
    widget._move_paths(True, False, ["src/main.py"])

    assert widget.staged_list.count() == 0
    assert widget.unstaged_list.count() == 1
    assert widget.unstaged_list.item(0).text() == "M src/main.py"


def test_git_changes_list_failure_uses_dialog_without_status_row(qapp, git_repo, monkeypatch):
    widget = GitChangesList(str(git_repo))
    warnings = []
    monkeypatch.setattr(
        "ui.widgets.git_changes_list.QMessageBox.warning",
        lambda parent, title, detail: warnings.append((parent, title, detail)),
    )

    widget._run_change_action("Back", GitCommandResult(1, "", "cannot unstage"))

    assert not hasattr(widget, "_status")
    assert warnings == [(widget, "Back failed", "cannot unstage")]


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
