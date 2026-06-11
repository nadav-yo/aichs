from services.crew import all_crew
from ui.widgets.file_mention_picker import FileMentionPicker


def test_file_mention_picker_includes_crew(qapp):
    picker = FileMentionPicker([], crew=list(all_crew()))
    picker.filter("@sc")
    assert picker.count() >= 1
    assert "QListWidget::item:selected:focus" in picker._list.styleSheet()

    selected = []
    picker.crew_selected.connect(selected.append)
    picker.confirm()
    assert selected == ["Scout"]


def test_file_mention_picker_still_selects_files(qapp):
    picker = FileMentionPicker([("src/main.py", "/tmp/src/main.py")], crew=[])
    picker.filter("@main")
    selected = []
    picker.file_selected.connect(lambda rel, abs_path: selected.append((rel, abs_path)))
    picker.confirm()
    assert selected == [("src/main.py", "/tmp/src/main.py")]
