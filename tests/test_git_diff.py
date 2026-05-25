from services.git_diff import can_diff_against_head, diff_against_head, is_git_repo


class TestGitDiff:
    def test_not_git_repo_returns_none(self, workspace):
        path = workspace / "src" / "main.py"
        assert diff_against_head(str(workspace), str(path)) is None

    def test_can_diff_when_modified(self, git_repo_with_change):
        repo, main = git_repo_with_change
        assert is_git_repo(str(repo))
        assert can_diff_against_head(str(repo), str(main))

    def test_diff_contains_change(self, git_repo_with_change):
        repo, main = git_repo_with_change
        diff = diff_against_head(str(repo), str(main))
        assert diff is not None
        assert "changed" in diff or "main.py" in diff

    def test_clean_committed_file_not_diffable(self, git_repo):
        main = git_repo / "src" / "main.py"
        assert not can_diff_against_head(str(git_repo), str(main))
