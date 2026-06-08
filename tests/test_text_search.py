from services.text_search import TextSearchMatch, search_file_contents
from ui.widgets.text_search_dialog import TextSearchDialog, _highlight_line_html


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
