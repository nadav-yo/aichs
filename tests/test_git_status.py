import shutil
import subprocess

import pytest

from services.git_status import (
    GitFileChange,
    count_commits_to_pull,
    count_commits_to_push,
    is_git_repo,
    list_file_changes,
    parse_status_line,
    run_git,
    run_git_command,
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
