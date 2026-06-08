import services.file_search as file_search
from services.file_search import FileSearchIndex, FileSearchMatch, match_file_name, search_file_names
from ui.widgets.file_search_dialog import _highlight_html, _match_path_html


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
