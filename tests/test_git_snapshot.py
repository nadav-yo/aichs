from services.git_snapshot import build_git_snapshot, clear_git_snapshot_cache
from services.git_status import GitFileChange


def test_build_git_snapshot_collects_status_log_and_counts(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    change = GitFileChange(
        code=" M",
        label="M",
        rel_path="src/main.py",
        abs_path=str(workspace / "src" / "main.py"),
    )
    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: True)
    monkeypatch.setattr(git_snapshot, "list_file_changes", lambda _repo: [change])
    def fake_run_git(cmd, _repo):
        if cmd[:3] == ["git", "branch", "--show-current"]:
            return "main\n"
        return "abcdef123456\x1fabcdef1\x1fHEAD -> main\x1finitial\n"

    monkeypatch.setattr(git_snapshot, "run_git", fake_run_git)
    monkeypatch.setattr(git_snapshot, "count_commits_to_push", lambda _repo: 2)
    monkeypatch.setattr(git_snapshot, "count_commits_to_pull", lambda _repo: 3)

    snapshot = build_git_snapshot(str(workspace))

    assert snapshot.repo_path == str(workspace)
    assert snapshot.is_repo is True
    assert snapshot.changes == (change,)
    assert snapshot.log_lines == ("abcdef123456\x1fabcdef1\x1fHEAD -> main\x1finitial",)
    assert snapshot.branch == "main"
    assert snapshot.ahead == 2
    assert snapshot.behind == 3


def test_build_git_snapshot_outside_repo_skips_git(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: False)
    monkeypatch.setattr(
        git_snapshot,
        "list_file_changes",
        lambda _repo: (_ for _ in ()).throw(AssertionError("should not inspect status")),
    )

    snapshot = build_git_snapshot(str(workspace))

    assert snapshot.repo_path == str(workspace)
    assert snapshot.is_repo is False
    assert snapshot.changes == ()
    assert snapshot.log_lines == ()
    assert snapshot.branch == ""
    assert snapshot.ahead == 0
    assert snapshot.behind == 0


def test_build_git_snapshot_reuses_recent_snapshot(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    clear_git_snapshot_cache()
    calls = []
    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: True)
    monkeypatch.setattr(
        git_snapshot,
        "list_file_changes",
        lambda repo: calls.append(repo) or [],
    )
    monkeypatch.setattr(git_snapshot, "run_git", lambda _cmd, _repo: "")
    monkeypatch.setattr(git_snapshot, "count_commits_to_push", lambda _repo: 0)
    monkeypatch.setattr(git_snapshot, "count_commits_to_pull", lambda _repo: 0)

    first = build_git_snapshot(str(workspace))
    second = build_git_snapshot(str(workspace))

    assert second is first
    assert calls == [str(workspace.resolve())]


def test_clear_git_snapshot_cache_invalidates_repo(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    clear_git_snapshot_cache()
    calls = []
    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: True)
    monkeypatch.setattr(
        git_snapshot,
        "list_file_changes",
        lambda repo: calls.append(repo) or [],
    )
    monkeypatch.setattr(git_snapshot, "run_git", lambda _cmd, _repo: "")
    monkeypatch.setattr(git_snapshot, "count_commits_to_push", lambda _repo: 0)
    monkeypatch.setattr(git_snapshot, "count_commits_to_pull", lambda _repo: 0)

    first = build_git_snapshot(str(workspace))
    clear_git_snapshot_cache(workspace)
    second = build_git_snapshot(str(workspace))

    assert second is not first
    assert calls == [str(workspace.resolve()), str(workspace.resolve())]


def test_build_git_snapshot_rescans_after_cache_ttl(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    clear_git_snapshot_cache()
    calls = []
    clock = iter([10.0, 10.0 + git_snapshot._GIT_SNAPSHOT_CACHE_TTL_S + 0.1])
    monkeypatch.setattr(git_snapshot.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: True)
    monkeypatch.setattr(
        git_snapshot,
        "list_file_changes",
        lambda repo: calls.append(repo) or [],
    )
    monkeypatch.setattr(git_snapshot, "run_git", lambda _cmd, _repo: "")
    monkeypatch.setattr(git_snapshot, "count_commits_to_push", lambda _repo: 0)
    monkeypatch.setattr(git_snapshot, "count_commits_to_pull", lambda _repo: 0)

    build_git_snapshot(str(workspace))
    build_git_snapshot(str(workspace))

    assert calls == [str(workspace.resolve()), str(workspace.resolve())]
