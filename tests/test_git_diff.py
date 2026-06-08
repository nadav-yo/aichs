import subprocess

from services.git_diff import (
    can_diff_against_head,
    commit_diff,
    diff_against_head,
    is_git_repo,
    split_diff_by_file,
)


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

    def test_commit_diff_contains_committed_change(self, git_repo):
        main = git_repo / "src" / "main.py"
        main.write_text("print('second')\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/main.py"], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "second"], cwd=git_repo, check=True, capture_output=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        diff = commit_diff(str(git_repo), sha)

        assert diff is not None
        assert "src/main.py" in diff
        assert "second" in diff

    def test_commit_diff_invalid_commit_returns_none(self, git_repo):
        assert commit_diff(str(git_repo), "missing") is None

    def test_split_diff_by_file_groups_chunks(self):
        diff = "\n".join([
            "diff --git a/src/main.py b/src/main.py",
            "index 111..222 100644",
            "--- a/src/main.py",
            "+++ b/src/main.py",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "diff --git a/README.md b/README.md",
            "index 333..444 100644",
            "--- a/README.md",
            "+++ b/README.md",
            "@@ -1,0 +1,2 @@",
            "+one",
            "+two",
        ])

        files = split_diff_by_file(diff)

        assert [file.path for file in files] == ["src/main.py", "README.md"]
        assert [(file.added, file.removed) for file in files] == [(1, 1), (2, 0)]
        assert files[0].diff.startswith("diff --git a/src/main.py")
