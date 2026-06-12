from services.git_snapshot import build_git_snapshot, clear_git_snapshot_cache
from services.git_status import GitFileChange, GitStatusSnapshot


def test_build_git_snapshot_collects_status_log_and_counts(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    change = GitFileChange(
        code=" M",
        label="M",
        rel_path="src/main.py",
        abs_path=str(workspace / "src" / "main.py"),
    )
    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: True)
    status_calls = []

    def fake_status(repo, *, check_repo=True):
        status_calls.append((repo, check_repo))
        return GitStatusSnapshot(
            changes=(change,),
            branch="main",
            ahead=2,
            behind=3,
        )

    monkeypatch.setattr(
        git_snapshot,
        "read_git_status_snapshot",
        fake_status,
    )

    git_calls = []

    def fake_run_git(cmd, _repo):
        git_calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return "abcdef123456"
        return "abcdef123456\x1fabcdef1\x1fHEAD -> main\x1finitial\n"

    monkeypatch.setattr(git_snapshot, "run_git", fake_run_git)

    snapshot = build_git_snapshot(str(workspace))

    assert snapshot.repo_path == str(workspace)
    assert snapshot.is_repo is True
    assert snapshot.changes == (change,)
    assert snapshot.log_lines == ("abcdef123456\x1fabcdef1\x1fHEAD -> main\x1finitial",)
    assert snapshot.branch == "main"
    assert snapshot.ahead == 2
    assert snapshot.behind == 3
    assert status_calls == [(str(workspace.resolve()), False)]
    assert git_calls == [
        ["git", "rev-parse", "HEAD"],
        ["git", "log", "--decorate=short", "--format=%H%x1f%h%x1f%D%x1f%s", "-40"],
    ]


def test_build_git_snapshot_outside_repo_skips_git(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: False)
    monkeypatch.setattr(
        git_snapshot,
        "read_git_status_snapshot",
        lambda _repo, *, check_repo=True: (_ for _ in ()).throw(
            AssertionError("should not inspect status"),
        ),
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

    def fake_status(repo, *, check_repo=True):
        calls.append((repo, check_repo))
        return GitStatusSnapshot()

    monkeypatch.setattr(
        git_snapshot,
        "read_git_status_snapshot",
        fake_status,
    )
    monkeypatch.setattr(git_snapshot, "run_git", lambda _cmd, _repo: "")

    first = build_git_snapshot(str(workspace))
    second = build_git_snapshot(str(workspace))

    assert second is first
    assert calls == [(str(workspace.resolve()), False)]


def test_clear_git_snapshot_cache_invalidates_repo(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    clear_git_snapshot_cache()
    calls = []
    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: True)

    def fake_status(repo, *, check_repo=True):
        calls.append((repo, check_repo))
        return GitStatusSnapshot()

    monkeypatch.setattr(
        git_snapshot,
        "read_git_status_snapshot",
        fake_status,
    )
    monkeypatch.setattr(git_snapshot, "run_git", lambda _cmd, _repo: "")

    first = build_git_snapshot(str(workspace))
    clear_git_snapshot_cache(workspace)
    second = build_git_snapshot(str(workspace))

    assert second is not first
    assert calls == [
        (str(workspace.resolve()), False),
        (str(workspace.resolve()), False),
    ]


def test_build_git_snapshot_rescans_after_cache_ttl(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    clear_git_snapshot_cache()
    calls = []
    clock = iter([10.0, 10.0 + git_snapshot._GIT_SNAPSHOT_CACHE_TTL_S + 0.1])
    monkeypatch.setattr(git_snapshot.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: True)

    def fake_status(repo, *, check_repo=True):
        calls.append((repo, check_repo))
        return GitStatusSnapshot()

    monkeypatch.setattr(
        git_snapshot,
        "read_git_status_snapshot",
        fake_status,
    )
    monkeypatch.setattr(git_snapshot, "run_git", lambda _cmd, _repo: "")

    build_git_snapshot(str(workspace))
    build_git_snapshot(str(workspace))

    assert calls == [
        (str(workspace.resolve()), False),
        (str(workspace.resolve()), False),
    ]


def test_build_git_snapshot_reuses_commit_log_for_same_head_after_snapshot_ttl(workspace, monkeypatch):
    import services.git_snapshot as git_snapshot

    clear_git_snapshot_cache()
    clock = iter([
        20.0,
        20.0 + git_snapshot._GIT_SNAPSHOT_CACHE_TTL_S + 0.1,
        20.0 + (git_snapshot._GIT_SNAPSHOT_CACHE_TTL_S * 2) + 0.2,
    ])
    git_calls = []
    status_calls = []
    monkeypatch.setattr(git_snapshot.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(git_snapshot, "is_git_repo", lambda _repo: True)

    def fake_status(repo, *, check_repo=True):
        status_calls.append((repo, check_repo))
        return GitStatusSnapshot(branch="main")

    def fake_run_git(cmd, _repo):
        git_calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return "same-head"
        return "same-head\x1fsamehea\x1fHEAD -> main\x1finitial\n"

    monkeypatch.setattr(git_snapshot, "read_git_status_snapshot", fake_status)
    monkeypatch.setattr(git_snapshot, "run_git", fake_run_git)

    first = build_git_snapshot(str(workspace))
    second = build_git_snapshot(str(workspace))
    clear_git_snapshot_cache(workspace)
    third = build_git_snapshot(str(workspace))

    assert first is not second
    assert second.log_lines == first.log_lines
    assert third.log_lines == first.log_lines
    assert status_calls == [
        (str(workspace.resolve()), False),
        (str(workspace.resolve()), False),
        (str(workspace.resolve()), False),
    ]
    assert git_calls == [
        ["git", "rev-parse", "HEAD"],
        ["git", "log", "--decorate=short", "--format=%H%x1f%h%x1f%D%x1f%s", "-40"],
        ["git", "rev-parse", "HEAD"],
        ["git", "rev-parse", "HEAD"],
        ["git", "log", "--decorate=short", "--format=%H%x1f%h%x1f%D%x1f%s", "-40"],
    ]
