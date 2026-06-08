from services.diff_html import (
    _changed_new_line_numbers,
    diff_to_html,
    inline_new_file_diff_to_html,
)

SAMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index 111..222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 line one
-removed
+added
 context
"""


def test_diff_to_html_escapes_and_styles():
    html = diff_to_html(SAMPLE_DIFF, theme="dark")
    assert "<pre " in html
    assert "added" in html
    assert "&lt;" not in html  # plain text lines, not raw tags in content
    assert "+added" in html or ">+added<" in html.replace(" ", "")


def test_diff_to_html_empty():
    html = diff_to_html("", theme="dark")
    assert "(no differences)" in html


def test_diff_to_html_uses_requested_theme_for_delete_background():
    light = diff_to_html("-removed\n", theme="light")
    dark = diff_to_html("-removed\n", theme="dark")
    modern = diff_to_html("-removed\n", theme="modern")

    assert "background:#fef2f2" in light
    assert "background:#2a1518" in dark
    assert "background:#2a1518" in modern


def test_changed_new_line_numbers():
    assert _changed_new_line_numbers(SAMPLE_DIFF) == {2}


def test_inline_new_file_diff_highlights_changed_lines():
    content = "line one\nremoved\nadded\ncontext\n"
    html = inline_new_file_diff_to_html(SAMPLE_DIFF, content, theme="dark")
    assert "added" in html
    assert "line one" in html


def test_inline_new_file_diff_empty_content():
    html = inline_new_file_diff_to_html("", "", theme="light")
    assert "(empty file)" in html


def test_changed_new_line_numbers_invalid_hunk_header():
    diff = "@@ ?? @@\n+line\n"
    assert _changed_new_line_numbers(diff) == set()


def test_changed_new_line_numbers_ignores_no_newline_marker():
    diff = "@@ -1 +1 @@\n+added\n\\ No newline at end of file\n"
    assert 1 in _changed_new_line_numbers(diff)
