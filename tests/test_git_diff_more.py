import subprocess

import pytest

from services.git_diff import diff_against_head


@pytest.fixture
def git_repo_untracked(git_repo):
    new = git_repo / "new.txt"
    new.write_text("fresh\n", encoding="utf-8")
    return git_repo, new


@pytest.fixture
def git_repo_deleted(git_repo):
    path = git_repo / "src" / "main.py"
    subprocess.run(["git", "rm", "src/main.py"], cwd=git_repo, check=True, capture_output=True)
    return git_repo, path


def test_untracked_file_diff(git_repo_untracked):
    repo, path = git_repo_untracked
    diff = diff_against_head(str(repo), str(path))
    assert diff is not None
    assert "fresh" in diff


def test_deleted_file_diff(git_repo_deleted):
    repo, path = git_repo_deleted
    diff = diff_against_head(str(repo), str(path))
    assert diff is not None
