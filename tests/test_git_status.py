import shutil

import pytest

from services.git_status import (
    GitFileChange,
    is_git_repo,
    list_file_changes,
    parse_status_line,
    run_git,
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
