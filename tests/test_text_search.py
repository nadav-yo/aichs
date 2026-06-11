from contextlib import contextmanager

import services.text_search as text_search
from services.text_search import (
    TextSearchMatch,
    search_file_contents,
    search_file_contents_with_candidates,
)
from ui.widgets.text_search_dialog import TextSearchDialog, _TextSearchWorker, _highlight_line_html


def test_search_file_contents_finds_plain_text(workspace):
    path = workspace / "src" / "main.py"
    path.write_text("print('needle')\nprint('other')\n", encoding="utf-8")

    matches = search_file_contents(workspace, "needle")

    assert len(matches) == 1
    assert matches[0].path == str(path)
    assert matches[0].line_no == 1
    assert matches[0].line_text == "print('needle')"


def test_search_file_contents_is_case_insensitive(workspace):
    path = workspace / "src" / "main.py"
    path.write_text("CamelCaseFiltering = True\n", encoding="utf-8")

    matches = search_file_contents(workspace, "casefilter")

    assert matches[0].path == str(path)


def test_search_file_contents_ignores_empty_query(workspace):
    assert search_file_contents(workspace, "   ") == []


def test_search_file_contents_ignores_hidden_and_configured_noise(workspace):
    (workspace / ".hidden.py").write_text("needle\n", encoding="utf-8")
    ignored_dir = workspace / "node_modules"
    ignored_dir.mkdir()
    (ignored_dir / "dep.py").write_text("needle\n", encoding="utf-8")

    matches = search_file_contents(workspace, "needle")

    assert matches == []


def test_search_file_contents_skips_binary_and_honors_limit(workspace):
    (workspace / "binary.bin").write_bytes(b"needle\x00hidden")
    for idx in range(3):
        (workspace / f"match{idx}.txt").write_text("needle\n", encoding="utf-8")

    matches = search_file_contents(workspace, "needle", limit=2)

    assert len(matches) == 2
    assert all(not match.path.endswith("binary.bin") for match in matches)


def test_search_file_contents_keeps_uncapped_refinement_candidates(workspace):
    for name, text in (
        ("one.txt", "alpha needle deep\n"),
        ("two.txt", "alpha needle deeper\n"),
        ("three.txt", "alpha other\n"),
    ):
        (workspace / name).write_text(text, encoding="utf-8")

    visible, candidates = search_file_contents_with_candidates(workspace, "alpha", limit=1)
    refined, refined_candidates = search_file_contents_with_candidates(
        workspace,
        "alpha needle",
        limit=10,
        candidates=candidates,
    )

    assert len(visible) == 1
    assert len(candidates) == 3
    assert [match.rel_path for match in refined] == ["one.txt", "two.txt"]
    assert len(refined_candidates) == 2


def test_search_file_contents_records_scan_and_refine_operations(workspace, monkeypatch):
    (workspace / "one.txt").write_text("alpha needle deep\n", encoding="utf-8")
    operations = []

    @contextmanager
    def fake_time_operation(operation, *, detail="", slow_ms=100.0):
        operations.append((operation, detail))
        yield

    monkeypatch.setattr(text_search, "time_operation", fake_time_operation)

    _visible, candidates = search_file_contents_with_candidates(workspace, "alpha")
    search_file_contents_with_candidates(
        workspace,
        "alpha needle",
        candidates=candidates,
    )

    assert [operation for operation, _detail in operations] == [
        "text_search.scan",
        "text_search.refine",
    ]


def test_search_file_contents_can_cancel_scan(workspace):
    for idx in range(3):
        (workspace / f"match{idx}.txt").write_text("needle\n", encoding="utf-8")
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 1

    matches, candidates = search_file_contents_with_candidates(
        workspace,
        "needle",
        cancelled=cancelled,
    )

    assert len(matches) <= 1
    assert len(candidates) <= 1


def test_highlight_line_html_marks_match():
    match = TextSearchMatch(
        path="C:/repo/src/main.py",
        rel_path="src\\main.py",
        line_no=1,
        line_text="print('needle')",
        start=7,
        end=13,
    )

    html = _highlight_line_html(match)

    assert html.count("<span") == 1
    assert ">needle</span>" in html


def test_text_search_dialog_opens_match_line(qapp, workspace):
    opened = []
    dialog = TextSearchDialog(str(workspace), lambda path, line_no: opened.append((path, line_no)))
    match = TextSearchMatch(
        path=str(workspace / "src" / "main.py"),
        rel_path="src\\main.py",
        line_no=4,
        line_text="needle",
        start=0,
        end=6,
    )
    dialog._filtered = [match]
    dialog._run_search = lambda: None
    dialog._list.clear()
    dialog._run_search()
    from PyQt6.QtWidgets import QListWidgetItem
    from PyQt6.QtCore import Qt

    row = QListWidgetItem()
    row.setData(Qt.ItemDataRole.UserRole, match)
    dialog._list.addItem(row)

    dialog._on_activated(row)

    assert opened == [(match.path, 4)]
    dialog.close()


def test_text_search_dialog_refines_extended_query_from_active_candidates(qapp, workspace, monkeypatch):
    dialog = TextSearchDialog(str(workspace), lambda _path, _line_no: None)
    first_candidates = (
        TextSearchMatch("a.txt", "a.txt", 1, "alpha needle", 0, 5),
        TextSearchMatch("b.txt", "b.txt", 1, "beta needle", 0, 4),
    )
    second_candidates = (
        TextSearchMatch("a.txt", "a.txt", 1, "alpha needle", 0, 12),
    )
    calls = []

    def fake_search(root, query, *, candidates=None, cancelled=None):
        assert cancelled is not None
        calls.append((root, query, candidates))
        if query == "alpha":
            return list(first_candidates), first_candidates
        if query == "alpha needle":
            return list(second_candidates), second_candidates
        return [], ()

    monkeypatch.setattr(
        "ui.widgets.text_search_dialog.search_file_contents_with_candidates",
        fake_search,
    )
    monkeypatch.setattr(
        "ui.widgets.text_search_dialog.QThreadPool.start",
        lambda _pool, worker: worker.run(),
    )

    dialog._query.setText("alpha")
    dialog._timer.stop()
    dialog._run_search()
    dialog._query.setText("alpha needle")
    dialog._timer.stop()
    dialog._run_search()
    dialog._query.setText("beta")
    dialog._timer.stop()
    dialog._run_search()

    assert calls[0][1:] == ("alpha", None)
    assert calls[1][1:] == ("alpha needle", first_candidates)
    assert calls[2][1:] == ("beta", None)
    dialog.close()


def test_text_search_dialog_starts_worker_without_scanning_on_ui_thread(qapp, workspace, monkeypatch):
    dialog = TextSearchDialog(str(workspace), lambda _path, _line_no: None)
    started = []
    monkeypatch.setattr(
        "ui.widgets.text_search_dialog.search_file_contents_with_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.text_search_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog._query.setText("needle")
    dialog._timer.stop()
    dialog._run_search()

    assert isinstance(started[0], _TextSearchWorker)
    assert dialog._search_cancel is not None
    dialog.close()


def test_text_search_dialog_ignores_stale_worker_result(qapp, workspace, monkeypatch):
    dialog = TextSearchDialog(str(workspace), lambda _path, _line_no: None)
    dialog._search_generation = 2
    match = TextSearchMatch("new.py", "new.py", 1, "needle", 0, 6)

    dialog._on_search_ready(1, "needle", [match], (match,), "")

    assert dialog._filtered == []
    assert dialog._candidate_matches == ()
    assert dialog._list.count() == 0
    dialog.close()
