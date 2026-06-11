from types import SimpleNamespace

from services.language_snapshot import (
    LanguageStatusSnapshot,
    build_language_status_snapshot,
    clear_language_status_cache,
)
from services.tool_registry import clear_extension_cache
from tests.conftest import write_extension


def test_language_status_snapshot_filters_matching_file(workspace):
    write_extension(
        workspace,
        "languages.py",
        """
        def register(registry):
            registry.language(
                name="python",
                file_patterns=["src/*.py"],
                diagnostics=lambda ctx: [],
                format_document=lambda ctx: {},
            )
            registry.language(
                name="markdown",
                file_patterns=["*.md"],
                diagnostics=lambda ctx: [],
            )
        """,
    )

    snapshot = build_language_status_snapshot({
        "path": str(workspace / "src" / "main.py"),
        "repo_root": str(workspace),
        "is_text": True,
    })

    assert isinstance(snapshot, LanguageStatusSnapshot)
    assert snapshot.path == str(workspace / "src" / "main.py")
    assert snapshot.repo_root == str(workspace)
    assert snapshot.errors == ()
    assert [status.language for status in snapshot.statuses] == ["python"]
    assert snapshot.statuses[0].features == ("diagnostics", "format_document")


def test_language_status_snapshot_skips_non_text_context(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "services.language_snapshot.language_status",
        lambda repo_root: calls.append(repo_root) or ([], []),
    )

    snapshot = build_language_status_snapshot({
        "path": str(workspace / "src" / "main.py"),
        "repo_root": str(workspace),
        "is_text": False,
    })

    assert snapshot == LanguageStatusSnapshot(
        path=str(workspace / "src" / "main.py"),
        repo_root=str(workspace),
        is_text=False,
    )
    assert calls == []


def test_language_status_snapshot_uses_basename_for_external_paths(workspace, tmp_path, monkeypatch):
    outside = tmp_path / "external.py"
    status = SimpleNamespace(file_patterns=("*.py",))
    monkeypatch.setattr(
        "services.language_snapshot.language_status",
        lambda _repo_root: ([status], ["broken"]),
    )

    snapshot = build_language_status_snapshot({
        "path": str(outside),
        "repo_root": str(workspace),
        "is_text": True,
    })

    assert snapshot.statuses == (status,)
    assert snapshot.errors == ("broken",)


def test_language_status_snapshot_reuses_workspace_status_cache(workspace, monkeypatch):
    clear_language_status_cache()
    calls = []
    py_status = SimpleNamespace(language="python", file_patterns=("*.py",))
    md_status = SimpleNamespace(language="markdown", file_patterns=("*.md",))

    monkeypatch.setattr(
        "services.language_snapshot.extension_cache_signature",
        lambda _repo_root: ("extensions", 1),
    )
    monkeypatch.setattr(
        "services.language_snapshot.language_status",
        lambda repo_root: calls.append(repo_root) or ([py_status, md_status], []),
    )

    py_snapshot = build_language_status_snapshot({
        "path": str(workspace / "src" / "main.py"),
        "repo_root": str(workspace),
        "is_text": True,
    })
    md_snapshot = build_language_status_snapshot({
        "path": str(workspace / "README.md"),
        "repo_root": str(workspace),
        "is_text": True,
    })

    assert py_snapshot.statuses == (py_status,)
    assert md_snapshot.statuses == (md_status,)
    assert calls == [str(workspace.resolve())]


def test_language_status_snapshot_invalidates_when_extension_signature_changes(workspace, monkeypatch):
    clear_language_status_cache()
    calls = []
    signatures = iter([("extensions", 1), ("extensions", 2)])
    status = SimpleNamespace(language="python", file_patterns=("*.py",))

    monkeypatch.setattr(
        "services.language_snapshot.extension_cache_signature",
        lambda _repo_root: next(signatures),
    )
    monkeypatch.setattr(
        "services.language_snapshot.language_status",
        lambda repo_root: calls.append(repo_root) or ([status], []),
    )

    context = {
        "path": str(workspace / "src" / "main.py"),
        "repo_root": str(workspace),
        "is_text": True,
    }
    build_language_status_snapshot(context)
    build_language_status_snapshot(context)

    assert calls == [str(workspace.resolve()), str(workspace.resolve())]


def test_clear_language_status_cache_invalidates_workspace(workspace, monkeypatch):
    clear_language_status_cache()
    calls = []
    status = SimpleNamespace(language="python", file_patterns=("*.py",))

    monkeypatch.setattr(
        "services.language_snapshot.extension_cache_signature",
        lambda _repo_root: ("extensions", 1),
    )
    monkeypatch.setattr(
        "services.language_snapshot.language_status",
        lambda repo_root: calls.append(repo_root) or ([status], []),
    )

    context = {
        "path": str(workspace / "src" / "main.py"),
        "repo_root": str(workspace),
        "is_text": True,
    }
    build_language_status_snapshot(context)
    clear_language_status_cache(workspace)
    build_language_status_snapshot(context)

    assert calls == [str(workspace.resolve()), str(workspace.resolve())]


def test_extension_cache_clear_invalidates_language_status_cache(workspace, monkeypatch):
    clear_language_status_cache()
    calls = []
    status = SimpleNamespace(language="python", file_patterns=("*.py",))
    monkeypatch.setattr(
        "services.language_snapshot.language_status",
        lambda repo_root: calls.append(repo_root) or ([status], []),
    )

    context = {
        "path": str(workspace / "src" / "main.py"),
        "repo_root": str(workspace),
        "is_text": True,
    }
    build_language_status_snapshot(context)
    clear_extension_cache(str(workspace))
    build_language_status_snapshot(context)

    assert calls == [str(workspace.resolve()), str(workspace.resolve())]
