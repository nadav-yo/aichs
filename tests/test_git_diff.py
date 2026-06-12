import services.git_diff as gd
from services.git_snapshot import GitSnapshot
from services.git_status import GitFileChange
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

    def test_commit_diff_contains_committed_change(self, workspace, monkeypatch):
        calls = []

        def fake_run_git(cmd, repo_path):
            calls.append((cmd, repo_path))
            if cmd == ["git", "show", "--no-patch", "--format=%H", "abc123"]:
                return 0, "abc123\n"
            if cmd == ["git", "show", "--format=", "--no-color", "--patch", "abc123"]:
                return 0, "diff --git a/src/main.py b/src/main.py\n+second\n"
            raise AssertionError(f"unexpected git command: {cmd!r}")

        monkeypatch.setattr("services.git_diff.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_diff._run_git", fake_run_git)

        diff = commit_diff(str(workspace), "abc123")

        assert diff is not None
        assert "src/main.py" in diff
        assert "second" in diff
        assert calls == [
            (["git", "show", "--no-patch", "--format=%H", "abc123"], str(workspace)),
            (["git", "show", "--format=", "--no-color", "--patch", "abc123"], str(workspace)),
        ]

    def test_commit_diff_invalid_commit_returns_none(self, workspace, monkeypatch):
        monkeypatch.setattr("services.git_diff.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_diff._run_git", lambda cmd, repo_path: (1, ""))

        assert commit_diff(str(workspace), "missing") is None

    def test_commit_diff_returns_none_when_patch_fetch_fails(self, workspace, monkeypatch):
        monkeypatch.setattr("services.git_diff.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr(
            "services.git_diff._run_git",
            lambda cmd, repo_path: (0, "abc123") if "--no-patch" in cmd else (1, ""),
        )

        assert commit_diff(str(workspace), "abc123") is None

    def test_commit_diff_rejects_blank_hash(self, workspace, monkeypatch):
        calls = []
        monkeypatch.setattr("services.git_diff.is_git_repo", lambda repo_path: calls.append(repo_path) or True)

        assert commit_diff(str(workspace), "  ") is None
        assert calls == []

    def test_diff_against_head_builds_added_file_diff(self, workspace, monkeypatch):
        path = workspace / "new.py"
        path.write_text("print('new')\n", encoding="utf-8")
        change = GitFileChange("A ", "A", "new.py", str(path), staged=True, unstaged=False)
        monkeypatch.setattr("services.git_diff.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_diff.change_for_file", lambda repo_path, abs_path: change)

        diff = diff_against_head(str(workspace), str(path))

        assert "--- a/new.py" in diff
        assert "+print('new')" in diff

    def test_diff_against_head_reuses_supplied_change_without_status_scan(self, workspace, monkeypatch):
        path = workspace / "new.py"
        path.write_text("print('new')\n", encoding="utf-8")
        change = GitFileChange("A ", "A", "new.py", str(path), staged=True, unstaged=False)
        monkeypatch.setattr("services.git_diff.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr(
            "services.git_diff.change_for_file",
            lambda _repo_path, _abs_path: (_ for _ in ()).throw(AssertionError("status scan")),
        )

        diff = diff_against_head(str(workspace), str(path), change=change)

        assert "--- a/new.py" in diff
        assert "+print('new')" in diff

    def test_diff_against_head_reuses_supplied_snapshot_without_status_scan(self, workspace, monkeypatch):
        path = workspace / "new.py"
        path.write_text("print('new')\n", encoding="utf-8")
        change = GitFileChange("A ", "A", "new.py", str(path), staged=True, unstaged=False)
        monkeypatch.setattr(
            "services.git_diff.list_file_changes",
            lambda _repo_path: (_ for _ in ()).throw(AssertionError("status scan")),
        )

        diff = diff_against_head(
            str(workspace),
            str(path),
            git_snapshot=GitSnapshot(
                repo_path=str(workspace.resolve()),
                is_repo=True,
                changes=(change,),
            ),
        )

        assert "--- a/new.py" in diff
        assert "+print('new')" in diff

    def test_can_diff_against_head_uses_snapshot_repo_state(self, workspace, monkeypatch):
        path = workspace / "src" / "main.py"
        monkeypatch.setattr(
            "services.git_diff.list_file_changes",
            lambda _repo_path: (_ for _ in ()).throw(AssertionError("status scan")),
        )

        assert can_diff_against_head(
            str(workspace),
            str(path),
            git_snapshot=GitSnapshot(repo_path=str(workspace.resolve()), is_repo=False),
        ) is False

    def test_diff_against_head_deleted_file_returns_none_without_head_text(self, workspace, monkeypatch):
        path = workspace / "gone.py"
        change = GitFileChange(" D", "D", "gone.py", str(path), staged=False, unstaged=True)
        monkeypatch.setattr("services.git_diff.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_diff.change_for_file", lambda repo_path, abs_path: change)
        monkeypatch.setattr("services.git_diff._head_text", lambda repo_path, rel_path: None)

        assert diff_against_head(str(workspace), str(path)) is None

    def test_diff_against_head_falls_back_to_synthetic_diff(self, workspace, monkeypatch):
        path = workspace / "src" / "main.py"
        path.write_text("print('new')\n", encoding="utf-8")
        change = GitFileChange(" M", "M", "src/main.py", str(path), staged=False, unstaged=True)
        monkeypatch.setattr("services.git_diff.is_git_repo", lambda repo_path: True)
        monkeypatch.setattr("services.git_diff.change_for_file", lambda repo_path, abs_path: change)
        monkeypatch.setattr("services.git_diff.run_git", lambda cmd, repo_path: "")
        monkeypatch.setattr("services.git_diff._head_text", lambda repo_path, rel_path: "print('old')\n")

        diff = diff_against_head(str(workspace), str(path))

        assert "-print('old')" in diff
        assert "+print('new')" in diff

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

    def test_split_diff_by_file_handles_dev_null_and_unknown_paths(self):
        added = split_diff_by_file("\n".join([
            "diff --git a/new.py b/new.py",
            "--- /dev/null",
            "+++ b/new.py",
            "+new",
        ]))
        malformed = split_diff_by_file('"broken diff')

        assert added[0].path == "new.py"
        assert malformed[0].path == "(unknown file)"


def test_run_git_returns_failure_tuple_on_exception(monkeypatch):
    monkeypatch.setattr("services.git_diff.run_no_window", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))

    assert gd._run_git(["git", "status"], "repo") == (1, "")


def test_head_text_reads_bounded_prefix_and_stops_large_blob(workspace, monkeypatch):
    read_sizes = []
    processes = []

    class FakeStdout:
        def read(self, size):
            read_sizes.append(size)
            return b"abcdef"

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, **kwargs):
        assert cmd == ["git", "show", "HEAD:large.txt"]
        assert kwargs["cwd"] == str(workspace)
        proc = FakeProcess()
        processes.append(proc)
        return proc

    monkeypatch.setattr(gd, "MAX_FILE_PREVIEW_BYTES", 5)
    monkeypatch.setattr(gd, "popen_no_window", fake_popen)

    text = gd._head_text(str(workspace), "large.txt")

    assert text == "abcde\n\n[Diff truncated at 5 bytes]"
    assert read_sizes == [6]
    assert processes[0].killed is True
