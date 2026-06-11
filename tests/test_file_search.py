from contextlib import contextmanager

import services.file_search as file_search
from services.file_search import (
    FileSearchEntry,
    FileSearchIndex,
    FileSearchMatch,
    clear_workspace_file_cache,
    list_workspace_files,
    match_file_name,
    search_file_names,
)
from ui.widgets.file_search_dialog import (
    FileSearchDialog,
    _FileSearchIndexWorker,
    _highlight_html,
    _match_path_html,
)


def test_match_file_name_handles_camel_case_initials():
    score, indices = match_file_name("CCF", "CamelCaseFiltering.py")

    assert score > 0
    assert indices == (0, 5, 9)


def test_match_file_name_handles_camel_case_chunks():
    score, indices = match_file_name("CamCaFil", "CamelCaseFiltering.py")

    assert score > 0
    assert indices == (0, 1, 2, 5, 6, 9, 10, 11)


def test_search_file_names_ranks_file_name_matches(workspace):
    (workspace / "src" / "CamelCaseFiltering.py").write_text("x = 1\n", encoding="utf-8")

    matches = search_file_names(workspace, "CCF")

    assert matches[0].name == "CamelCaseFiltering.py"
    assert matches[0].indices == (0, 5, 9)


def test_search_file_names_ignores_hidden_and_configured_noise(workspace):
    hidden = workspace / ".hidden.py"
    hidden.write_text("hidden\n", encoding="utf-8")
    ignored_dir = workspace / "node_modules"
    ignored_dir.mkdir()
    (ignored_dir / "VisibleName.py").write_text("noise\n", encoding="utf-8")

    matches = search_file_names(workspace, "Visible")

    assert matches == []


def test_file_search_index_reuses_file_scan(workspace, monkeypatch):
    (workspace / "src" / "CamelCaseFiltering.py").write_text("x = 1\n", encoding="utf-8")
    calls = []
    real_list = file_search.list_workspace_files

    def tracked_list(*args, **kwargs):
        calls.append((args, kwargs))
        return real_list(*args, **kwargs)

    monkeypatch.setattr(file_search, "list_workspace_files", tracked_list)
    index = FileSearchIndex.from_root(workspace)

    assert index.search("CCF")
    assert index.search("CamCaFil")
    assert len(calls) == 1


def test_file_search_index_reuses_recent_index(workspace, monkeypatch):
    clear_workspace_file_cache()
    (workspace / "src" / "CamelCaseFiltering.py").write_text("x = 1\n", encoding="utf-8")
    calls = []
    real_list = file_search.list_workspace_files

    def tracked_list(*args, **kwargs):
        calls.append((args, kwargs))
        return real_list(*args, **kwargs)

    monkeypatch.setattr(file_search, "list_workspace_files", tracked_list)

    first = FileSearchIndex.from_root(workspace)
    second = FileSearchIndex.from_root(workspace)

    assert second is first
    assert len(calls) == 1


def test_clear_workspace_file_cache_invalidates_file_search_index(workspace, monkeypatch):
    clear_workspace_file_cache()
    calls = []
    real_list = file_search.list_workspace_files

    def tracked_list(*args, **kwargs):
        calls.append((args, kwargs))
        return real_list(*args, **kwargs)

    monkeypatch.setattr(file_search, "list_workspace_files", tracked_list)

    first = FileSearchIndex.from_root(workspace)
    clear_workspace_file_cache(workspace)
    second = FileSearchIndex.from_root(workspace)

    assert second is not first
    assert len(calls) == 2


def test_list_workspace_files_reuses_recent_scan(workspace, monkeypatch):
    clear_workspace_file_cache()
    calls = []
    real_walk = file_search._walk_files

    def tracked_walk(dir_path, found, limit):
        calls.append(dir_path)
        real_walk(dir_path, found, limit)

    monkeypatch.setattr(file_search, "_walk_files", tracked_walk)

    first = list_workspace_files(workspace)
    second = list_workspace_files(workspace)

    assert second == first
    assert calls.count(workspace.resolve()) == 1


def test_clear_workspace_file_cache_invalidates_root(workspace, monkeypatch):
    clear_workspace_file_cache()
    calls = []
    real_walk = file_search._walk_files

    def tracked_walk(dir_path, found, limit):
        calls.append(dir_path)
        real_walk(dir_path, found, limit)

    monkeypatch.setattr(file_search, "_walk_files", tracked_walk)

    list_workspace_files(workspace)
    clear_workspace_file_cache(workspace)
    list_workspace_files(workspace)

    assert calls.count(workspace.resolve()) == 2


def test_list_workspace_files_rescans_after_cache_ttl(workspace, monkeypatch):
    clear_workspace_file_cache()
    calls = []
    clock = iter([10.0, 10.0 + file_search._WORKSPACE_FILE_CACHE_TTL_S + 0.1])
    real_walk = file_search._walk_files

    def tracked_walk(dir_path, found, limit):
        calls.append(dir_path)
        real_walk(dir_path, found, limit)

    monkeypatch.setattr(file_search.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(file_search, "_walk_files", tracked_walk)

    list_workspace_files(workspace)
    list_workspace_files(workspace)

    assert calls.count(workspace.resolve()) == 2


def test_file_search_records_index_and_query_operations(workspace, monkeypatch):
    operations = []

    @contextmanager
    def fake_time_operation(operation, *, detail="", slow_ms=100.0):
        operations.append((operation, detail))
        yield

    monkeypatch.setattr(file_search, "time_operation", fake_time_operation)
    (workspace / "src" / "CamelCaseFiltering.py").write_text("x = 1\n", encoding="utf-8")

    index = FileSearchIndex.from_root(workspace)
    index.search("CCF")

    assert [operation for operation, _detail in operations] == [
        "file_search.index",
        "workspace.files",
        "file_search.query",
    ]


def test_file_search_index_returns_uncapped_refinement_candidates():
    entries = tuple(
        FileSearchEntry(
            path=f"/repo/{name}",
            rel_path=name,
            name=name,
        )
        for name in ("alpha.py", "alphabet.py", "application.py")
    )
    index = FileSearchIndex(entries)

    visible, candidates = index.search_with_candidates("a", limit=1)
    refined = index.search("alph", entries=candidates)

    assert len(visible) == 1
    assert [entry.name for entry in candidates] == ["alpha.py", "alphabet.py", "application.py"]
    assert [match.name for match in refined] == ["alpha.py", "alphabet.py"]


def test_file_search_dialog_refines_extended_query_from_active_candidates(qapp, workspace, monkeypatch):
    monkeypatch.setattr(
        "ui.widgets.file_search_dialog.QThreadPool.start",
        lambda _pool, _worker: None,
    )
    dialog = FileSearchDialog(str(workspace), lambda _path: None)
    entries = tuple(
        FileSearchEntry(
            path=f"/repo/{name}",
            rel_path=name,
            name=name,
        )
        for name in ("alpha.py", "alphabet.py", "beta.py")
    )
    real_index = FileSearchIndex(entries)
    seen_sources = []

    class _Index:
        def __init__(self, index_entries):
            self.entries = index_entries

        def search_with_candidates(self, query, *, limit=80, entries=None):
            source = tuple(self.entries if entries is None else entries)
            seen_sources.append((query, source))
            return real_index.search_with_candidates(query, limit=limit, entries=source)

    dialog._index = _Index(entries)
    dialog._candidate_query = ""
    dialog._candidate_entries = dialog._index.entries

    dialog._refilter("a")
    first_candidates = dialog._candidate_entries
    dialog._refilter("al")
    dialog._refilter("b")

    assert seen_sources[0] == ("a", entries)
    assert seen_sources[1] == ("al", first_candidates)
    assert seen_sources[2] == ("b", entries)
    dialog.close()


def test_file_search_dialog_builds_index_on_worker(qapp, workspace, monkeypatch):
    started = []
    monkeypatch.setattr(
        "ui.widgets.file_search_dialog.FileSearchIndex.from_root",
        lambda _root: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.file_search_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog = FileSearchDialog(str(workspace), lambda _path: None)

    assert dialog._index is None
    assert dialog._list.item(0).text() == "Loading files..."
    assert isinstance(started[0], _FileSearchIndexWorker)
    dialog.close()


def test_file_search_dialog_applies_current_query_when_index_ready(qapp, workspace, monkeypatch):
    monkeypatch.setattr(
        "ui.widgets.file_search_dialog.QThreadPool.start",
        lambda _pool, _worker: None,
    )
    entries = tuple(
        FileSearchEntry(
            path=f"/repo/{name}",
            rel_path=name,
            name=name,
        )
        for name in ("alpha.py", "beta.py")
    )
    dialog = FileSearchDialog(str(workspace), lambda _path: None)
    dialog._query.setText("alp")

    dialog._on_index_ready(1, FileSearchIndex(entries), "")

    assert [match.name for match in dialog._filtered] == ["alpha.py"]
    assert dialog._list.count() == 1
    dialog.close()


def test_file_search_dialog_ignores_stale_index_result(qapp, workspace, monkeypatch):
    monkeypatch.setattr(
        "ui.widgets.file_search_dialog.QThreadPool.start",
        lambda _pool, _worker: None,
    )
    dialog = FileSearchDialog(str(workspace), lambda _path: None)
    dialog._index_generation = 2

    dialog._on_index_ready(1, FileSearchIndex(()), "")

    assert dialog._index is None
    assert dialog._list.item(0).text() == "Loading files..."
    dialog.close()


def test_highlight_html_marks_matched_characters():
    html = _highlight_html("CamelCaseFiltering.py", (0, 5, 9))

    assert html.count("<span") == 3
    assert ">C</span>" in html
    assert ">F</span>" in html


def test_match_path_html_highlights_single_relative_path():
    match = FileSearchMatch(
        path="C:/repo/src/CamelCaseFiltering.py",
        rel_path="src\\CamelCaseFiltering.py",
        name="CamelCaseFiltering.py",
        score=1,
        indices=(0, 5, 9),
    )

    html = _match_path_html(match)

    assert "src\\" in html
    assert html.count("<span") == 3
    assert "amel" in html
    assert "ase" in html
