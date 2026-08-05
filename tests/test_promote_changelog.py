from __future__ import annotations

import pytest

from tools.promote_changelog import promote_changelog, section_body


SAMPLE = """# Changelog

Intro.

## Next

- Fix the widget
- Add a setting

## 0.5.1

- Older change
"""


def test_promote_changelog_renames_next_and_reopens_empty_next():
    updated = promote_changelog(SAMPLE, "0.5.2")
    assert updated.startswith("# Changelog\n\nIntro.\n\n## Next\n\n## 0.5.2\n")
    assert "- Fix the widget" in section_body(updated, "0.5.2")
    assert "- Add a setting" in section_body(updated, "0.5.2")
    assert section_body(updated, "Next") == ""
    assert "## 0.5.1" in updated
    assert "- Older change" in section_body(updated, "0.5.1")


def test_promote_changelog_rejects_empty_next():
    text = "# Changelog\n\n## Next\n\n## 0.5.1\n\n- Older\n"
    with pytest.raises(ValueError, match="empty"):
        promote_changelog(text, "0.5.2")


def test_promote_changelog_rejects_duplicate_version():
    with pytest.raises(ValueError, match="already has"):
        promote_changelog(SAMPLE, "0.5.1")


def test_promote_changelog_requires_next_heading():
    with pytest.raises(ValueError, match="missing a ## Next"):
        promote_changelog("# Changelog\n\n## 0.5.1\n\n- Older\n", "0.5.2")


def test_promote_changelog_cli_writes_file(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(SAMPLE, encoding="utf-8")
    from tools.promote_changelog import main

    assert main(["0.5.2", "--path", str(path)]) == 0
    text = path.read_text(encoding="utf-8")
    assert section_body(text, "Next") == ""
    assert "- Fix the widget" in section_body(text, "0.5.2")
