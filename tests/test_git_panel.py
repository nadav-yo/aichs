import time
from contextlib import contextmanager

from PyQt6.QtWidgets import QMenu, QPushButton

from services.git_snapshot import GitSnapshot
from services.git_status import GitCommandResult
from storage.settings import GIT_FIX_PROMPT_TEMPLATE_KEY, SettingsStore
from ui.theme import ACCENT, palette
from ui.widgets.git_panel import (
    GitPanel,
    _CommitDiffDialog,
    _GitActionThread,
    _ROLE_HASH,
    _ROLE_REF_BADGES,
    _commit_ref_badges,
    _git_action_failure_prompt,
    _fit_git_action_button,
    _git_action_button_style,
    _git_action_button_text,
    _parse_commit_log_line,
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


def _snapshot(workspace, *, log_line=None, ahead=0, behind=0, is_repo=True):
    return GitSnapshot(
        repo_path=str(workspace),
        is_repo=is_repo,
        log_lines=tuple([log_line] if log_line else []),
        ahead=ahead,
        behind=behind,
    )


def _install_snapshot(monkeypatch, snapshot):
    monkeypatch.setattr("ui.widgets.git_panel.build_git_snapshot", lambda _path: snapshot)


def test_git_action_button_style_has_balanced_rule_boundaries(qapp):
    style = _git_action_button_style()
    assert "}}QPushButton" not in style
    assert style.count("{") == style.count("}")


def test_commit_diff_dialog_uses_contained_list_and_splitter_styles(qapp):
    dialog = _CommitDiffDialog(
        "abc1234",
        "Subject",
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
    )
    try:
        style = dialog.styleSheet()
        assert "QListWidget#commitFileList::item:selected:focus" in style
        assert "QSplitter#commitDiffSplitter::handle:hover" in style
    finally:
        dialog.close()


def test_git_action_button_styles_can_use_distinct_accents(qapp):
    pull_style = _git_action_button_style(ACCENT)
    push_style = _git_action_button_style(palette()["SUCCESS"])
    assert pull_style != push_style
    assert ACCENT in pull_style
    assert palette()["SUCCESS"] in push_style


def test_git_action_button_style_uses_each_theme_palette(qapp):
    for theme in ("dark", "modern", "light"):
        p = palette(theme)
        style = _git_action_button_style(p["SUCCESS"], theme=theme)
        assert p["BG2"] in style
        assert p["BG3"] in style
        assert p["BORDER"] in style
        assert p["TEXT"] in style
        assert p["TEXT_DIM"] in style
        assert p["BORDER_SUBTLE"] in style
        assert "min-width:16px" in style
        assert "padding:0 6px" in style
        assert p["SUCCESS"] in style


def test_parse_commit_log_line_accepts_decorations():
    parsed = _parse_commit_log_line(
        "abcdef123456\x1fabcdef1\x1fHEAD -> main, origin/main\x1finitial"
    )

    assert parsed == (
        "abcdef123456",
        "abcdef1",
        ["HEAD -> main", "origin/main"],
        "initial",
    )
    assert _commit_ref_badges(parsed[2]) == [("HEAD", "head"), ("origin/main", "origin")]


def test_parse_commit_log_line_keeps_legacy_mock_shape():
    assert _parse_commit_log_line("abcdef123456\x1fabcdef1\x1finitial") == (
        "abcdef123456",
        "abcdef1",
        [],
        "initial",
    )


def test_git_action_button_text_adds_count_only_when_present():
    assert _git_action_button_text("↑", 0) == "↑"
    assert _git_action_button_text("↑", 2) == "↑2"


def test_git_action_button_expands_only_when_count_needs_room(qapp):
    button = QPushButton("↓")
    _fit_git_action_button(button)
    resting_width = button.width()

    button.setText("↓12")
    _fit_git_action_button(button)

    assert resting_width == 28
    assert button.width() > resting_width
    assert button.height() == 24


def test_git_action_buttons_start_at_empty_state_size(qapp, workspace):
    panel = GitPanel(str(workspace), defer_refresh=True)

    assert panel._pull_btn.text() == "↓"
    assert panel._push_btn.text() == "↑"
    assert panel._pull_btn.width() == 28
    assert panel._push_btn.width() == 28
    assert panel._pull_btn.height() == 24
    assert panel._push_btn.height() == 24


def test_git_action_buttons_use_directional_labels(qapp, workspace, monkeypatch):
    _install_snapshot(
        monkeypatch,
        _snapshot(workspace, log_line="abcdef123456\x1fabcdef1\x1finitial"),
    )

    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel.log.count() == 1)

    assert panel._pull_btn.text() == "↓"
    assert panel._pull_btn.accessibleName() == "Pull"
    assert panel._push_btn.text() == "↑"
    assert panel._push_btn.accessibleName() == "Push"


def test_git_panel_passes_current_model_getter_to_changes(qapp, workspace):
    panel = GitPanel(str(workspace), current_model_getter=lambda: "model-a")

    assert panel._changes._current_model_getter() == "model-a"


def test_git_panel_shutdown_clears_refresh_threads(qapp, workspace):
    panel = GitPanel(str(workspace), defer_refresh=True)

    panel.refresh()
    panel.shutdown()

    assert panel._refresh_threads == []
    assert panel._refresh_generation == 2
    assert panel._changes._refresh_threads == []


def test_git_panel_history_area_has_horizontal_inset(qapp, workspace):
    panel = GitPanel(str(workspace), defer_refresh=True)
    history_layout = panel._history_page.layout()
    margins = history_layout.contentsMargins()

    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (10, 4, 10, 8)


def test_git_panel_apply_snapshot_is_timed(qapp, workspace, monkeypatch):
    import ui.widgets.git_panel as git_panel

    operations = []

    @contextmanager
    def fake_time_operation(operation, *, detail="", slow_ms=100.0):
        operations.append((operation, detail, slow_ms))
        yield

    monkeypatch.setattr(git_panel, "time_operation", fake_time_operation)
    panel = GitPanel(str(workspace), defer_refresh=True)
    snapshot = _snapshot(
        workspace,
        log_line="abcdef123456\x1fabcdef1\x1fHEAD -> main\x1finitial",
    )

    panel.apply_snapshot(snapshot)

    assert operations == [("git.apply", "changes=0 commits=1", 50)]


def test_git_panel_deferred_auto_refresh_starts_only_when_ensured(qapp, workspace):
    panel = GitPanel(str(workspace), defer_refresh=True, auto_refresh=False)
    snapshot = _snapshot(
        workspace,
        log_line="abcdef123456\x1fabcdef1\x1fHEAD -> main\x1finitial",
    )

    panel.apply_snapshot(snapshot)

    assert not panel._refresh_timer.isActive()
    assert panel._auto_refresh_started is False

    panel.ensure_loaded()

    assert panel._refresh_timer.isActive()
    assert panel._auto_refresh_started is True
    panel.shutdown()


def test_git_panel_refresh_coalesces_while_snapshot_worker_runs(qapp, workspace, monkeypatch):
    import ui.widgets.git_panel as git_panel

    snapshot = _snapshot(
        workspace,
        log_line="abcdef123456\x1fabcdef1\x1fHEAD -> main\x1finitial",
    )
    started = []

    class FakeSignal:
        def __init__(self):
            self._callbacks = []

        def connect(self, callback):
            self._callbacks.append(callback)

        def disconnect(self):
            self._callbacks.clear()

        def emit(self, *args):
            for callback in list(self._callbacks):
                callback(*args)

    class FakeRefreshThread:
        def __init__(self, generation, repo_path, parent=None):
            self._generation = generation
            self._repo_path = repo_path
            self.done = FakeSignal()
            self.finished = FakeSignal()
            self._running = False

        def start(self):
            self._running = True
            started.append(self)

        def isRunning(self):
            return self._running

        def deleteLater(self):
            pass

        def finish(self):
            self.done.emit(self._generation, snapshot)
            self._running = False
            self.finished.emit()

    monkeypatch.setattr(git_panel, "_GitRefreshThread", FakeRefreshThread)
    panel = GitPanel(str(workspace), defer_refresh=True)

    panel.refresh()
    panel.refresh()

    assert len(started) == 1
    assert panel._refresh_pending is True

    started[0].finish()

    assert len(started) == 2
    assert panel._refresh_pending is False
    assert panel._refresh_generation == 2

    started[1].finish()

    assert len(started) == 2
    assert panel.log.count() == 1


def test_git_log_skips_git_command_outside_repo(qapp, workspace, monkeypatch):
    _install_snapshot(monkeypatch, _snapshot(workspace, is_repo=False))

    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel._loaded and panel._last_snapshot.repo_path == str(workspace))

    assert panel.log.count() == 0


def test_git_log_marks_origin_ref(qapp, workspace, monkeypatch):
    _install_snapshot(
        monkeypatch,
        _snapshot(
            workspace,
            log_line="abcdef123456\x1fabcdef1\x1fHEAD -> main, origin/main\x1finitial",
        ),
    )

    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel.log.count() == 1)
    item = panel.log.item(0)

    assert ("HEAD", "head") in item.data(_ROLE_REF_BADGES)
    assert ("origin/main", "origin") in item.data(_ROLE_REF_BADGES)
    assert "origin/main" in item.toolTip()


def test_git_log_context_menu_offers_copy_actions(qapp, workspace, monkeypatch):
    _install_snapshot(
        monkeypatch,
        _snapshot(workspace, log_line="abcdef123456\x1fabcdef1\x1finitial"),
    )
    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel.log.count() == 1)
    item = panel.log.item(0)
    panel.log.setCurrentItem(item)
    monkeypatch.setattr(panel.log, "itemAt", lambda _pos: item)
    action_texts = []

    def capture_menu(menu, _pos):
        action_texts.extend(action.text() for action in menu.actions())
        return None

    monkeypatch.setattr(QMenu, "exec", capture_menu)

    panel.log._context_menu(panel.log.visualItemRect(item).center())

    assert action_texts == [
        "Copy commit message",
        "Copy commit hash",
        "Ask about this commit in chat",
    ]


def test_git_log_context_menu_copy_survives_list_refresh(qapp, workspace, monkeypatch):
    _install_snapshot(
        monkeypatch,
        _snapshot(workspace, log_line="abcdef123456\x1fabcdef1\x1finitial"),
    )
    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel.log.count() == 1)
    item = panel.log.item(0)
    panel.log.setCurrentItem(item)
    monkeypatch.setattr(panel.log, "itemAt", lambda _pos: item)

    def exec_copy_message(menu, _pos):
        panel.log.clear()
        for action in menu.actions():
            if str(action.data() or "") == "message":
                return action
        return None

    monkeypatch.setattr(QMenu, "exec", exec_copy_message)
    qapp.clipboard().clear()
    panel.log._context_menu(panel.log.visualItemRect(item).center())

    assert qapp.clipboard().text() == "initial"


def test_git_log_copy_helpers_copy_commit_message_and_hash(qapp, workspace, monkeypatch):
    _install_snapshot(
        monkeypatch,
        _snapshot(workspace, log_line="abcdef123456\x1fabcdef1\x1finitial"),
    )
    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel.log.count() == 1)
    item = panel.log.item(0)
    qapp.clipboard().clear()

    panel.log._copy_commit_message("initial")

    assert qapp.clipboard().text() == "initial"

    panel.log._copy_commit_hash("abcdef123456")

    assert qapp.clipboard().text() == item.data(_ROLE_HASH)


def test_git_log_push_button_follows_ahead_state(qapp, workspace, monkeypatch):
    ahead = {"count": 0}
    monkeypatch.setattr(
        "ui.widgets.git_panel.build_git_snapshot",
        lambda _path: _snapshot(
            workspace,
            log_line="abcdef123456\x1fabcdef1\x1finitial",
            ahead=ahead["count"],
        ),
    )

    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel.log.count() == 1)

    assert panel._pull_btn.isEnabled()
    assert not panel._push_btn.isEnabled()

    ahead["count"] = 2
    panel.refresh()
    _wait_until(qapp, lambda: panel._push_btn.text() == "↑2")

    assert panel._push_btn.isEnabled()
    assert panel._push_btn.text() == "↑2"
    assert "2 local commits" in panel._push_btn.toolTip()


def test_git_log_pull_button_shows_behind_count(qapp, workspace, monkeypatch):
    _install_snapshot(
        monkeypatch,
        _snapshot(workspace, log_line="abcdef123456\x1fabcdef1\x1finitial", behind=3),
    )

    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel._pull_btn.text() == "↓3")

    assert panel._pull_btn.text() == "↓3"
    assert panel._pull_btn.isEnabled()
    assert "3 upstream commits" in panel._pull_btn.toolTip()


def test_git_log_pull_and_push_buttons_run_commands(qapp, workspace, monkeypatch):
    import ui.widgets.git_panel as git_panel

    calls = []
    _install_snapshot(
        monkeypatch,
        _snapshot(workspace, log_line="abcdef123456\x1fabcdef1\x1finitial", ahead=1),
    )

    def fake_run_git_command(cmd, _path, timeout=60):
        calls.append((cmd, timeout))
        return GitCommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(git_panel, "run_git_command", fake_run_git_command)

    panel = GitPanel(str(workspace))
    _wait_until(qapp, lambda: panel._push_btn.isEnabled())
    panel._pull_btn.click()
    deadline = time.monotonic() + 2.0
    while panel._git_action_thread is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert panel._git_action_thread is None

    assert calls == [(["git", "pull", "--ff-only"], 120)]
    assert panel._git_action_status.text() == "Pull complete"


def test_git_action_failure_status_keeps_detail_and_prompt(qapp, workspace):
    panel = GitPanel(str(workspace), defer_refresh=True)
    panel.refresh = lambda: None
    panel._git_action_thread = type(
        "FakeGitActionThread",
        (),
        {"_cmd": ["git", "pull", "--ff-only"]},
    )()
    result = GitCommandResult(128, "", "fatal: refusing to merge unrelated histories\nhint")

    panel._on_git_action_done("Pull", result)

    assert not panel._git_action_status.isHidden()
    assert panel._git_action_status.text() == (
        "Pull failed: fatal: refusing to merge unrelated histories"
    )
    assert panel._git_action_status.toolTip() == (
        "fatal: refusing to merge unrelated histories\nhint"
    )
    prompt = _git_action_failure_prompt(
        "Pull",
        ["git", "pull", "--ff-only"],
        str(workspace),
        result,
    )
    assert "Diagnose this git pull failure and suggest a fix." in prompt
    assert "Command: git pull --ff-only" in prompt
    assert "Exit code: 128" in prompt
    assert "fatal: refusing to merge unrelated histories" in prompt


def test_git_action_done_clears_snapshot_cache_before_refresh(qapp, workspace, monkeypatch):
    panel = GitPanel(str(workspace), defer_refresh=True)
    refreshes = []
    cleared = []
    panel.refresh = lambda: refreshes.append(True)
    monkeypatch.setattr(
        "ui.widgets.git_panel.clear_git_snapshot_cache",
        lambda repo_path: cleared.append(repo_path),
    )

    panel._on_git_action_done("Pull", GitCommandResult(0, "ok", ""))

    assert cleared == [str(workspace)]
    assert refreshes == [True]


def test_git_action_failure_prompt_uses_custom_template(workspace):
    result = GitCommandResult(1, "remote rejected", "")

    prompt = _git_action_failure_prompt(
        "Push",
        ["git", "push"],
        str(workspace),
        result,
        "Investigate git {action} in {repo}: {command} exited {exit_code}.",
    )

    assert prompt.startswith(
        f"Investigate git push in {workspace}: git push exited 1."
    )
    assert "Output:\nremote rejected" in prompt

    fallback = _git_action_failure_prompt(
        "Push",
        ["git", "push"],
        str(workspace),
        result,
        "Bad {missing}",
    )

    assert fallback.startswith("Diagnose this git push failure and suggest a fix.")


def test_git_action_failure_context_menu_can_ask_agent(qapp, workspace, monkeypatch):
    store = SettingsStore()
    store.save({GIT_FIX_PROMPT_TEMPLATE_KEY: "Investigate git {action}: {command}."})
    panel = GitPanel(str(workspace), settings=store, defer_refresh=True)
    panel.refresh = lambda: None
    panel._git_action_thread = type(
        "FakeGitActionThread",
        (),
        {"_cmd": ["git", "push"]},
    )()
    panel._on_git_action_done(
        "Push",
        GitCommandResult(1, "", "fatal: no upstream configured"),
    )
    drafted = []
    panel.git_help_requested.connect(lambda text, refs: drafted.append((text, refs)))
    action_texts = []

    def choose_ask(menu, _pos):
        action_texts.extend(action.text() for action in menu.actions())
        for action in menu.actions():
            if action.data() == "ask":
                return action
        return None

    monkeypatch.setattr(QMenu, "exec", choose_ask)

    panel._show_git_action_status_menu(panel._git_action_status.rect().center())

    assert action_texts == ["Ask agent about failure", "Copy details"]
    assert len(drafted) == 1
    assert drafted[0][0].startswith("Investigate git push: git push.")
    assert "git push" in drafted[0][0]
    assert "fatal: no upstream configured" in drafted[0][0]
    assert drafted[0][1] == []


def test_git_action_thread_runs_command(qapp, workspace, monkeypatch):
    import ui.widgets.git_panel as git_panel

    calls = []
    result = GitCommandResult(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(
        git_panel,
        "run_git_command",
        lambda cmd, repo_path, timeout: calls.append((cmd, repo_path, timeout)) or result,
    )
    emitted = []
    thread = _GitActionThread("Pull", ["git", "pull"], str(workspace))
    thread.done.connect(lambda label, value: emitted.append((label, value)))

    thread.run()

    assert calls == [(["git", "pull"], str(workspace), 120)]
    assert emitted == [("Pull", result)]
