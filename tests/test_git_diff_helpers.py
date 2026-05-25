from unittest.mock import patch

from services.git_diff import _unified, can_diff_against_head, change_for_file


def test_unified_diff_format():
    text = _unified("old\n", "new\n", "file.py")
    assert "file.py" in text
    assert "-old" in text or "---" in text


def test_change_for_file_missing(git_repo):
    assert change_for_file(str(git_repo), str(git_repo / "nope.py")) is None
    assert not can_diff_against_head(str(git_repo), str(git_repo / "nope.py"))


def test_diff_against_head_not_git(workspace):
    from services.git_diff import diff_against_head

    assert diff_against_head(str(workspace), str(workspace / "src" / "main.py")) is None


def test_head_text_via_git_show(git_repo):
    from services.git_diff import _head_text

    text = _head_text(str(git_repo), "src/main.py")
    assert text is None or "print" in text


def test_read_text_missing():
    from services.git_diff import _read_text

    text, ok = _read_text(__file__ + ".missing")
    assert text == ""
    assert ok is False
