import re

from ui.markdown_html import code_from_copy_url, copy_code_url, markdown_body
from ui.widgets.bubble import _to_html
from ui.widgets.markdown_browser import copy_code_url_to_clipboard


def test_markdown_body_renders_fenced_code_as_qt_friendly_block():
    html = markdown_body(
        "Start it from a repository:\n\n```bash\ncd /path/to/your/repo\naichs\n```",
        extensions=["fenced_code"],
    )

    assert 'class="aichs-code-block"' in html
    assert "title=\"Copy code\"" in html
    assert "&#x29c9;" in html
    assert "<code" not in html
    assert "cd /path/to/your/repo" in html
    assert "aichs" in html
    href = re.search(r'href="([^"]+)"', html).group(1)
    assert code_from_copy_url(href) == "cd /path/to/your/repo\naichs\n"


def test_markdown_copy_code_url_round_trips_special_characters():
    code = "print('<tag>')\npath = r'C:\\tmp'\n"
    assert code_from_copy_url(copy_code_url(code)) == code


def test_copy_code_url_to_clipboard(qapp):
    code = "cd /repo\naichs\n"
    assert copy_code_url_to_clipboard(copy_code_url(code)) is True
    assert qapp.clipboard().text() == code


def test_markdown_body_keeps_inline_code_as_code_chip():
    html = markdown_body("Run `aichs` from the repository.", extensions=["fenced_code"])

    assert "<code>" in html
    assert 'class="aichs-code-block"' not in html


def test_bubble_linkify_skips_paths_inside_code_blocks():
    html = _to_html(
        "```text\nservices/chat.py\n```\n\nSee services/git_diff.py for the parser."
    )

    assert 'href="aichs-file:services/chat.py"' not in html
    assert 'href="aichs-file:services/git_diff.py"' in html
