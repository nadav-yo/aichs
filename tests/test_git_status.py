import shutil
import subprocess

import pytest

from services.git_status import (
    GitFileChange,
    commit_staged,
    count_commits_to_pull,
    count_commits_to_push,
    is_git_repo,
    list_file_changes,
    parse_status_line,
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

    def test_count_commits_to_push_with_ahead_commit(self, git_repo, tmp_path):
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

        def git(*args: str) -> None:
            subprocess.run(
                ["git", *args],
                cwd=git_repo,
                check=True,
                capture_output=True,
            )

        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        git("remote", "add", "origin", str(remote))
        git("push", "-u", "origin", branch)
        assert count_commits_to_push(str(git_repo)) == 0

        main = git_repo / "src" / "main.py"
        main.write_text("print('ahead')\n", encoding="utf-8")
        git("add", "src/main.py")
        git("commit", "-m", "ahead")

        assert count_commits_to_push(str(git_repo)) == 1

    def test_count_commits_to_pull_uses_fetched_tracking_info(self, git_repo, tmp_path):
        remote = tmp_path / "remote.git"
        clone = tmp_path / "clone"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

        def git(cwd, *args: str) -> None:
            subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=True,
                capture_output=True,
            )

        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        git(git_repo, "remote", "add", "origin", str(remote))
        git(git_repo, "push", "-u", "origin", branch)
        subprocess.run(["git", "clone", str(remote), str(clone)], check=True, capture_output=True)
        git(clone, "config", "user.email", "test@example.com")
        git(clone, "config", "user.name", "Test User")
        (clone / "src" / "main.py").write_text("print('remote')\n", encoding="utf-8")
        git(clone, "add", "src/main.py")
        git(clone, "commit", "-m", "remote")
        git(clone, "push")

        assert count_commits_to_pull(str(git_repo)) == 0
        git(git_repo, "fetch", "origin")
        assert count_commits_to_pull(str(git_repo)) == 1

    def test_repo_relative_paths_filters_outside_workspace(self, git_repo, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("nope\n", encoding="utf-8")
        assert repo_relative_paths(str(git_repo), ["src/main.py", str(outside), "../x"]) == [
            "src/main.py"
        ]

    def test_stage_and_unstage_files(self, git_repo):
        main = git_repo / "src" / "main.py"
        main.write_text("print('stage')\n", encoding="utf-8")

        result = stage_files(str(git_repo), ["src/main.py"])
        assert result.ok
        ch = next(c for c in list_file_changes(str(git_repo)) if c.rel_path.replace("\\", "/") == "src/main.py")
        assert ch.staged
        assert not ch.unstaged

        result = unstage_files(str(git_repo), ["src/main.py"])
        assert result.ok
        ch = next(c for c in list_file_changes(str(git_repo)) if c.rel_path.replace("\\", "/") == "src/main.py")
        assert not ch.staged
        assert ch.unstaged

    def test_stash_files_includes_untracked_selected_files(self, git_repo):
        main = git_repo / "src" / "main.py"
        note = git_repo / "note.txt"
        main.write_text("print('stash')\n", encoding="utf-8")
        note.write_text("new\n", encoding="utf-8")

        result = stash_files(str(git_repo), ["src/main.py", "note.txt"], "test stash")

        assert result.ok
        assert not note.exists()
        assert list_file_changes(str(git_repo)) == []
        assert "test stash" in run_git(["git", "stash", "list"], str(git_repo))

    def test_commit_staged_commits_only_staged_files(self, git_repo):
        main = git_repo / "src" / "main.py"
        other = git_repo / "other.txt"
        main.write_text("print('commit')\n", encoding="utf-8")
        other.write_text("unstaged\n", encoding="utf-8")
        assert stage_files(str(git_repo), ["src/main.py"]).ok

        result = commit_staged(str(git_repo), "commit staged", "body text")

        assert result.ok
        assert "commit staged" in run_git(["git", "log", "-1", "--format=%s"], str(git_repo))
        changes = list_file_changes(str(git_repo))
        assert [ch.rel_path.replace("\\", "/") for ch in changes] == ["other.txt"]

    def test_commit_staged_requires_summary(self, git_repo):
        result = commit_staged(str(git_repo), "  ")
        assert not result.ok
        assert "summary" in result.stderr
