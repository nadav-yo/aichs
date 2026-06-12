import subprocess
from unittest.mock import patch

import pytest

from services.git_diff import diff_against_head


@pytest.fixture
def git_repo_staged_new(git_repo):
    new = git_repo / "added.txt"
    new.write_text("new file\n", encoding="utf-8")
    subprocess.run(["git", "add", "added.txt"], cwd=git_repo, check=True, capture_output=True)
    return git_repo, new


def test_staged_new_file_diff(git_repo_staged_new):
    repo, path = git_repo_staged_new
    diff = diff_against_head(str(repo), str(path))
    assert diff is not None
    assert "new file" in diff


def test_git_diff_command_path(git_repo_with_change):
    repo, main = git_repo_with_change
    with patch("services.git_diff.run_git", return_value="@@ diff\n-old\n+new\n"):
        diff = diff_against_head(str(repo), str(main))
    assert diff is not None
