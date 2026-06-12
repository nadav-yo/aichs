from services.file_tree_snapshot import build_directory_snapshot, build_file_tree_snapshot
from services.git_snapshot import GitSnapshot
from services.git_status import GitFileChange


def test_file_tree_snapshot_resolves_filter_root_once(workspace, monkeypatch):
    for idx in range(3):
        (workspace / "src" / f"match{idx}.py").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr("services.file_tree_snapshot.list_file_changes", lambda _root: [])
    import pathlib

    original_resolve = pathlib.Path.resolve
    root_resolves = 0

    def counted_resolve(self, *args, **kwargs):
        nonlocal root_resolves
        if self == workspace:
            root_resolves += 1
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", counted_resolve)

    snapshot = build_file_tree_snapshot(str(workspace), filter_text="match")

    assert [entry.name for entry in snapshot.entries] == ["match0.py", "match1.py", "match2.py"]
    assert root_resolves == 1


def test_directory_snapshot_reads_entry_type_once(monkeypatch):
    class FakeEntry:
        def __init__(self, name, path, is_dir):
            self.name = name
            self.path = path
            self._is_dir = is_dir
            self.calls = 0

        def is_dir(self):
            self.calls += 1
            return self._is_dir

    class FakeScandir:
        def __init__(self, entries):
            self._entries = entries

        def __enter__(self):
            return iter(self._entries)

        def __exit__(self, *_args):
            return False

    entries = [
        FakeEntry("b.txt", "/repo/b.txt", False),
        FakeEntry("a", "/repo/a", True),
    ]
    monkeypatch.setattr(
        "services.file_tree_snapshot.os.scandir",
        lambda _path: FakeScandir(entries),
    )

    snapshot = build_directory_snapshot("/repo")

    assert [(entry.name, entry.is_dir) for entry in snapshot.entries] == [("a", True), ("b.txt", False)]
    assert [entry.calls for entry in entries] == [1, 1]


def test_file_tree_snapshot_lists_visible_root_entries(workspace, monkeypatch):
    (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
    (workspace / ".env").write_text("SECRET=1\n", encoding="utf-8")
    monkeypatch.setattr("services.file_tree_snapshot.list_file_changes", lambda _root: [])

    snapshot = build_file_tree_snapshot(str(workspace))

    names = [entry.name for entry in snapshot.entries]
    assert "src" in names
    assert "README.md" in names
    assert ".env" not in names
    assert not snapshot.filter_text


def test_file_tree_snapshot_filters_by_relative_path(workspace, monkeypatch):
    nested = workspace / "src" / "pkg"
    nested.mkdir()
    (nested / "api.py").write_text("API = True\n", encoding="utf-8")
    monkeypatch.setattr("services.file_tree_snapshot.list_file_changes", lambda _root: [])

    snapshot = build_file_tree_snapshot(str(workspace), filter_text="pkg api")

    assert [entry.display_name for entry in snapshot.entries] == ["src/pkg/api.py"]


def test_file_tree_snapshot_cancelled_before_walk_skips_git(workspace, monkeypatch):
    monkeypatch.setattr(
        "services.file_tree_snapshot.list_file_changes",
        lambda _root: (_ for _ in ()).throw(AssertionError("git status should be skipped")),
    )

    snapshot = build_file_tree_snapshot(str(workspace), filter_text="main", cancelled=lambda: True)

    assert snapshot.entries == ()
    assert snapshot.git_status == ()


def test_file_tree_snapshot_cancelled_during_filter_walk_stops(workspace, monkeypatch):
    for idx in range(3):
        (workspace / "src" / f"match{idx}.py").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(
        "services.file_tree_snapshot.list_file_changes",
        lambda _root: (_ for _ in ()).throw(AssertionError("git status should be skipped")),
    )
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 1

    snapshot = build_file_tree_snapshot(str(workspace), filter_text="match", cancelled=cancelled)

    assert len(snapshot.entries) <= 1
    assert snapshot.git_status == ()


def test_file_tree_snapshot_includes_git_status(workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    change = GitFileChange(" M", "M", "src/main.py", str(path), unstaged=True)
    monkeypatch.setattr("services.file_tree_snapshot.list_file_changes", lambda _root: [change])

    snapshot = build_file_tree_snapshot(str(workspace), load_git_status=True)

    assert [(status.abs_path, status.code, status.label) for status in snapshot.git_status] == [
        (str(path), " M", "M")
    ]


def test_file_tree_snapshot_reuses_supplied_git_snapshot(workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    change = GitFileChange(" M", "M", "src/main.py", str(path), unstaged=True)
    monkeypatch.setattr(
        "services.file_tree_snapshot.list_file_changes",
        lambda _root: (_ for _ in ()).throw(AssertionError("status scan")),
    )

    snapshot = build_file_tree_snapshot(
        str(workspace),
        git_snapshot=GitSnapshot(
            repo_path=str(workspace.resolve()),
            is_repo=True,
            changes=(change,),
        ),
    )

    assert [(status.abs_path, status.code, status.label) for status in snapshot.git_status] == [
        (str(path), " M", "M")
    ]


def test_directory_snapshot_lists_children(workspace):
    snapshot = build_directory_snapshot(str(workspace / "src"))

    assert [entry.name for entry in snapshot.entries] == ["main.py"]
