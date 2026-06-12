import subprocess

import pytest

import services.git_status as gs
from services.git_status import (
    GitFileChange,
    GitCommandResult,
    commit_staged,
    count_commits_ahead_behind,
    count_commits_to_pull,
    count_commits_to_push,
    discard_files,
    is_git_repo,
    list_file_changes,
    parse_status_branch_line,
    parse_status_line,
    parse_status_snapshot,
    read_git_status_snapshot,
    repo_relative_paths,
    run_git,
    run_git_command,
    stage_files,
    stash_files,
    status_stage_flags,
    unstage_files,
)


class TestParseStatusLine:
    def test_parse_short_line(self):
        assert parse_status_line("x") == ("", "", "x")

    @pytest.mark.parametrize(
        "line,code,label,path",
        [
            (" M file.py", " M", "M", "file.py"),
            ("?? new.txt", "??", "?", "new.txt"),
            (' M "spaced.py"', " M", "M", "spaced.py"),
            ("R  old -> new.py", "R ", "R", "new.py"),
            ("A assets/foo.py", "A ", "A", "assets/foo.py"),
        ],
    )
    def test_parse(self, line, code, label, path):
        assert parse_status_line(line) == (code, label, path)

    @pytest.mark.parametrize(
        "line,branch,ahead,behind",
        [
            ("## main", "main", 0, 0),
            ("## main...origin/main [ahead 2]", "main", 2, 0),
            ("## main...origin/main [behind 3]", "main", 0, 3),
            ("## main...origin/main [ahead 2, behind 3]", "main", 2, 3),
            ("## No commits yet on main", "main", 0, 0),
            ("## HEAD (no branch)", "", 0, 0),
        ],
    )
    def test_parse_status_branch_line(self, line, branch, ahead, behind):
        assert parse_status_branch_line(line) == (branch, ahead, behind)

    @pytest.mark.parametrize(
        "code,staged,unstaged,staged_label,unstaged_label",
        [
            ("M ", True, False, "M", ""),
            (" M", False, True, "", "M"),
            ("MM", True, True, "M", "M"),
            ("A ", True, False, "A", ""),
            (" D", False, True, "", "D"),
            ("R ", True, False, "R", ""),
            ("??", False, True, "", "?"),
        ],
    )
    def test_status_stage_flags(self, code, staged, unstaged, staged_label, unstaged_label):
        assert status_stage_flags(code) == (staged, unstaged, staged_label, unstaged_label)


class TestGitRepo:
    def test_is_git_repo_false_without_dot_git(self, workspace):
        assert not is_git_repo(str(workspace))

    def test_is_git_repo_true_after_init(self, git_repo):
        assert is_git_repo(str(git_repo))

    def test_list_file_changes_skips_non_git_workspace(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "services.git_status.run_git",
            lambda *args, **kwargs: calls.append((args, kwargs)) or "",
        )

        assert list_file_changes(str(workspace)) == []
        assert calls == []

    def test_list_file_changes_after_edit(self, git_repo):
        main = git_repo / "src" / "main.py"
        main.write_text("print('changed')\n", encoding="utf-8")
        changes = list_file_changes(str(git_repo))
        assert len(changes) >= 1
        paths = {c.rel_path.replace("\\", "/") for c in changes}
        assert "src/main.py" in paths
        ch = next(c for c in changes if c.rel_path.replace("\\", "/") == "src/main.py")
        assert isinstance(ch, GitFileChange)
        assert ch.label in ("M", "·", " M")
        assert ch.unstaged is True
        assert ch.staged is False

    def test_read_git_status_snapshot_collects_branch_counts_and_changes(self, workspace, monkeypatch):
        calls = []

        def fake_run_git(cmd, repo_path):
            calls.append((cmd, repo_path))
            return "## main...origin/main [ahead 2, behind 3]\n M src/main.py\n"

        monkeypatch.setattr("services.git_status.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_status.run_git", fake_run_git)

        snapshot = read_git_status_snapshot(str(workspace))

        assert snapshot.branch == "main"
        assert snapshot.ahead == 2
        assert snapshot.behind == 3
        assert [change.rel_path for change in snapshot.changes] == ["src/main.py"]
        assert calls == [
            (["git", "status", "--short", "--branch", "-uall"], str(workspace))
        ]

    def test_parse_status_snapshot_skips_branch_header(self, workspace):
        snapshot = parse_status_snapshot(
            str(workspace),
            "## main\n?? new.py\n M src/main.py\n",
        )

        assert snapshot.branch == "main"
        assert [change.rel_path for change in snapshot.changes] == ["new.py", "src/main.py"]

    def test_list_file_changes_marks_staged_and_unstaged(self, git_repo):
        main = git_repo / "src" / "main.py"
        main.write_text("print('staged')\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/main.py"], cwd=git_repo, check=True, capture_output=True)
        main.write_text("print('both')\n", encoding="utf-8")

        ch = next(c for c in list_file_changes(str(git_repo)) if c.rel_path.replace("\\", "/") == "src/main.py")

        assert ch.code == "MM"
        assert ch.staged is True
        assert ch.unstaged is True
        assert ch.staged_label == "M"
        assert ch.unstaged_label == "M"

    def test_run_git_returns_empty_on_failure(self, workspace):
        assert run_git(["git", "not-a-command"], str(workspace)) == ""

    def test_run_git_command_returns_failure_detail(self, workspace):
        result = run_git_command(["git", "not-a-command"], str(workspace))
        assert not result.ok
        assert result.returncode != 0

    def test_count_commits_to_push_without_upstream(self, git_repo):
        assert count_commits_to_push(str(git_repo)) == 0
        assert count_commits_to_pull(str(git_repo)) == 0

    def test_count_commits_to_push_with_ahead_commit(self, workspace, monkeypatch):
        state = {"ahead": 0, "behind": 0}
        calls = []

        def fake_run_git(cmd, repo_path):
            calls.append(cmd)
            assert repo_path == str(workspace)
            if cmd == ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"]:
                return f"{state['behind']}\t{state['ahead']}"
            raise AssertionError(f"unexpected git command: {cmd!r}")

        monkeypatch.setattr("services.git_status.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_status.run_git", fake_run_git)

        assert count_commits_to_push(str(workspace)) == 0
        state["ahead"] = 1
        assert count_commits_to_push(str(workspace)) == 1
        assert ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"] in calls

    def test_count_commits_to_pull_uses_fetched_tracking_info(self, workspace, monkeypatch):
        state = {"ahead": 0, "behind": 0}
        calls = []

        def fake_run_git(cmd, repo_path):
            calls.append(cmd)
            assert repo_path == str(workspace)
            if cmd == ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"]:
                return f"{state['behind']} {state['ahead']}"
            raise AssertionError(f"unexpected git command: {cmd!r}")

        monkeypatch.setattr("services.git_status.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_status.run_git", fake_run_git)

        assert count_commits_to_pull(str(workspace)) == 0
        state["behind"] = 1
        assert count_commits_to_pull(str(workspace)) == 1
        assert ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"] in calls

    def test_count_commits_ahead_behind_uses_one_git_command(self, workspace, monkeypatch):
        calls = []

        def fake_run_git(cmd, repo_path):
            calls.append((cmd, repo_path))
            return "3\t2"

        monkeypatch.setattr("services.git_status.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_status.run_git", fake_run_git)

        assert count_commits_ahead_behind(str(workspace)) == (2, 3)
        assert calls == [
            (["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"], str(workspace))
        ]

    def test_count_commits_returns_zero_for_bad_counts(self, workspace, monkeypatch):
        def fake_run_git(cmd, repo_path):
            return "not-a-number"

        monkeypatch.setattr("services.git_status.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_status.run_git", fake_run_git)

        assert count_commits_to_push(str(workspace)) == 0
        assert count_commits_to_pull(str(workspace)) == 0
        assert count_commits_ahead_behind(str(workspace)) == (0, 0)

    def test_repo_relative_paths_filters_outside_workspace(self, git_repo, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("nope\n", encoding="utf-8")
        assert repo_relative_paths(str(git_repo), ["src/main.py", str(outside), "../x"]) == [
            "src/main.py"
        ]

    def test_stage_and_unstage_files(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "services.git_status.run_git_command",
            lambda cmd, repo_path, **kwargs: calls.append((cmd, repo_path, kwargs))
            or GitCommandResult(0, "ok", ""),
        )

        result = stage_files(str(workspace), ["src/main.py"])
        assert result.ok

        result = unstage_files(str(workspace), ["src/main.py"])
        assert result.ok
        assert calls == [
            (["git", "add", "--", "src/main.py"], str(workspace), {}),
            (["git", "restore", "--staged", "--", "src/main.py"], str(workspace), {}),
        ]

    def test_file_commands_reject_empty_or_escaped_selection(self, workspace):
        for result in (
            stage_files(str(workspace), []),
            unstage_files(str(workspace), ["../escape.py"]),
            discard_files(str(workspace), [""]),
            stash_files(str(workspace), ["../escape.py"], ""),
        ):
            assert not result.ok
            assert "No files selected" in result.stderr

    def test_discard_unstaged_files_restores_tracked_and_removes_untracked(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr("services.git_status._tracked_paths", lambda repo_path, paths: {"src/main.py"})
        monkeypatch.setattr(
            "services.git_status.run_git_command",
            lambda cmd, repo_path, **kwargs: calls.append((cmd, repo_path, kwargs))
            or GitCommandResult(0, "ok", ""),
        )

        result = discard_files(str(workspace), ["src/main.py", "note.txt"])

        assert result.ok
        assert calls == [
            (["git", "restore", "--worktree", "--", "src/main.py"], str(workspace), {}),
            (["git", "clean", "-f", "--", "src/main.py", "note.txt"], str(workspace), {}),
        ]

    def test_discard_staged_files_restores_tracked_file(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr("services.git_status._paths_in_head", lambda repo_path, paths: {"src/main.py"})
        monkeypatch.setattr(
            "services.git_status.run_git_command",
            lambda cmd, repo_path, **kwargs: calls.append((cmd, repo_path, kwargs))
            or GitCommandResult(0, "ok", ""),
        )

        result = discard_files(str(workspace), ["src/main.py"], staged=True)

        assert result.ok
        assert calls == [
            (
                ["git", "restore", "--staged", "--worktree", "--", "src/main.py"],
                str(workspace),
                {},
            )
        ]

    def test_discard_staged_added_file_removes_it(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr("services.git_status._paths_in_head", lambda repo_path, paths: set())
        monkeypatch.setattr(
            "services.git_status.run_git_command",
            lambda cmd, repo_path, **kwargs: calls.append((cmd, repo_path, kwargs))
            or GitCommandResult(0, "ok", ""),
        )

        result = discard_files(str(workspace), ["note.txt"], staged=True)

        assert result.ok
        assert calls == [
            (["git", "rm", "-f", "--cached", "--", "note.txt"], str(workspace), {}),
            (["git", "clean", "-f", "--", "note.txt"], str(workspace), {}),
        ]

    def test_discard_staged_added_file_skips_clean_when_cached_rm_fails(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr("services.git_status._paths_in_head", lambda repo_path, paths: set())
        monkeypatch.setattr(
            "services.git_status.run_git_command",
            lambda cmd, repo_path, **kwargs: calls.append(cmd)
            or GitCommandResult(1, "", "rm failed"),
        )

        result = discard_files(str(workspace), ["note.txt"], staged=True)

        assert not result.ok
        assert calls == [["git", "rm", "-f", "--cached", "--", "note.txt"]]

    def test_stash_files_includes_untracked_selected_files(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "services.git_status.run_git_command",
            lambda cmd, repo_path, **kwargs: calls.append((cmd, repo_path, kwargs))
            or GitCommandResult(0, "saved", ""),
        )

        result = stash_files(str(workspace), ["src/main.py", "note.txt"], "test stash")

        assert result.ok
        assert calls == [
            (
                [
                    "git",
                    "stash",
                    "push",
                    "-u",
                    "-m",
                    "test stash",
                    "--",
                    "src/main.py",
                    "note.txt",
                ],
                str(workspace),
                {},
            )
        ]

    def test_commit_staged_commits_only_staged_files(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "services.git_status.run_git_command",
            lambda cmd, repo_path, **kwargs: calls.append((cmd, repo_path, kwargs))
            or GitCommandResult(0, "committed", ""),
        )

        result = commit_staged(str(workspace), "commit staged", "body text")

        assert result.ok
        assert calls == [
            (
                ["git", "commit", "-m", "commit staged", "-m", "body text"],
                str(workspace),
                {"timeout": 120},
            )
        ]

    def test_commit_staged_requires_summary(self, git_repo):
        result = commit_staged(str(git_repo), "  ")
        assert not result.ok
        assert "summary" in result.stderr

    def test_private_git_helpers_handle_empty_and_merge_failures(self, workspace):
        assert gs._tracked_paths(str(workspace), []) == set()
        assert gs._paths_in_head(str(workspace), []) == set()

        result = gs._combined_git_result([
            GitCommandResult(0, "first", ""),
            GitCommandResult(2, "second", "failed"),
        ])

        assert result.returncode == 2
        assert result.stdout == "first\nsecond"
        assert result.stderr == "failed"

    def test_change_from_status_line_skips_directories(self, workspace):
        folder = workspace / "generated"
        folder.mkdir()

        assert gs._change_from_status_line(str(workspace), "?? generated/") is None
        assert gs._change_from_status_line(str(workspace), "?? generated") is None
