import subprocess

from services.git_status import GitCommandResult
from ui.theme import ACCENT, palette
from ui.widgets.git_panel import (
    GitPanel,
    _ROLE_REF_BADGES,
    _commit_ref_badges,
    _git_action_button_style,
    _git_action_button_text,
    _parse_commit_log_line,
)


def test_git_action_button_style_has_balanced_rule_boundaries(qapp):
    style = _git_action_button_style()
    assert "}}QPushButton" not in style
    assert style.count("{") == style.count("}")


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
    assert _git_action_button_text("↑", 2) == "↑ (2)"


def test_git_action_buttons_use_directional_labels(qapp, workspace, monkeypatch):
    import ui.widgets.git_panel as git_panel

    monkeypatch.setattr(git_panel, "is_git_repo", lambda _path: True)
    monkeypatch.setattr(git_panel, "count_commits_to_pull", lambda _path: 0)
    monkeypatch.setattr(git_panel, "count_commits_to_push", lambda _path: 0)
    monkeypatch.setattr(
        git_panel,
        "run_git",
        lambda _cmd, _path: "abcdef123456\x1fabcdef1\x1finitial",
    )

    panel = GitPanel(str(workspace))

    assert panel._pull_btn.text() == "↓"
    assert panel._pull_btn.accessibleName() == "Pull"
    assert panel._push_btn.text() == "↑"
    assert panel._push_btn.accessibleName() == "Push"


def test_git_panel_passes_current_model_getter_to_changes(qapp, workspace):
    panel = GitPanel(str(workspace), current_model_getter=lambda: "model-a")

    assert panel._changes._current_model_getter() == "model-a"


def test_git_log_marks_origin_ref(qapp, git_repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=git_repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=git_repo, check=True)

    panel = GitPanel(str(git_repo))
    item = panel.log.item(0)

    assert ("HEAD", "head") in item.data(_ROLE_REF_BADGES)
    assert (f"origin/{branch}", "origin") in item.data(_ROLE_REF_BADGES)
    assert f"origin/{branch}" in item.toolTip()


def test_git_log_push_button_follows_ahead_state(qapp, workspace, monkeypatch):
    import ui.widgets.git_panel as git_panel

    ahead = {"count": 0}
    monkeypatch.setattr(git_panel, "is_git_repo", lambda _path: True)
    monkeypatch.setattr(git_panel, "count_commits_to_pull", lambda _path: 0)
    monkeypatch.setattr(git_panel, "count_commits_to_push", lambda _path: ahead["count"])
    monkeypatch.setattr(
        git_panel,
        "run_git",
        lambda _cmd, _path: "abcdef123456\x1fabcdef1\x1finitial",
    )

    panel = GitPanel(str(workspace))

    assert panel._pull_btn.isEnabled()
    assert not panel._push_btn.isEnabled()

    ahead["count"] = 2
    panel.refresh()

    assert panel._push_btn.isEnabled()
    assert panel._push_btn.text() == "↑ (2)"
    assert "2 local commits" in panel._push_btn.toolTip()


def test_git_log_pull_button_shows_behind_count(qapp, workspace, monkeypatch):
    import ui.widgets.git_panel as git_panel

    monkeypatch.setattr(git_panel, "is_git_repo", lambda _path: True)
    monkeypatch.setattr(git_panel, "count_commits_to_pull", lambda _path: 3)
    monkeypatch.setattr(git_panel, "count_commits_to_push", lambda _path: 0)
    monkeypatch.setattr(
        git_panel,
        "run_git",
        lambda _cmd, _path: "abcdef123456\x1fabcdef1\x1finitial",
    )

    panel = GitPanel(str(workspace))

    assert panel._pull_btn.text() == "↓ (3)"
    assert panel._pull_btn.isEnabled()
    assert "3 upstream commits" in panel._pull_btn.toolTip()


def test_git_log_pull_and_push_buttons_run_commands(qapp, workspace, monkeypatch):
    import ui.widgets.git_panel as git_panel

    calls = []
    monkeypatch.setattr(git_panel, "is_git_repo", lambda _path: True)
    monkeypatch.setattr(git_panel, "count_commits_to_pull", lambda _path: 0)
    monkeypatch.setattr(git_panel, "count_commits_to_push", lambda _path: 1)
    monkeypatch.setattr(
        git_panel,
        "run_git",
        lambda _cmd, _path: "abcdef123456\x1fabcdef1\x1finitial",
    )

    def fake_run_git_command(cmd, _path, timeout=60):
        calls.append((cmd, timeout))
        return GitCommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(git_panel, "run_git_command", fake_run_git_command)

    panel = GitPanel(str(workspace))
    panel._pull_btn.click()
    panel._push_btn.click()

    assert calls == [
        (["git", "pull", "--ff-only"], 120),
        (["git", "push"], 120),
    ]
    assert panel._git_action_status.text() == "Push complete"
