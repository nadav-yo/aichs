import json

import pytest

from ui.theme import (
    build_stylesheet,
    bubble_label_style,
    compaction_threshold_pct,
    current_theme,
    git_status_color,
    markdown_css,
    palette,
)


def test_palette_dark_and_light():
    assert "BG" in palette("dark")
    assert palette("light")["BG"] != palette("dark")["BG"]


def test_current_theme_from_settings(isolate_aicc_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    assert current_theme() == "light"


def test_current_theme_invalid_falls_back(isolate_aicc_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(json.dumps({"theme": "neon"}), encoding="utf-8")
    assert current_theme() in ("dark", "light")


def test_compaction_threshold_clamped(isolate_aicc_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(json.dumps({"compaction_threshold_pct": 10}), encoding="utf-8")
    assert compaction_threshold_pct() == 60
    SETTINGS_PATH.write_text(json.dumps({"compaction_threshold_pct": 99}), encoding="utf-8")
    assert compaction_threshold_pct() == 95


@pytest.mark.parametrize("code", ["??", " M", "D ", "UU"])
def test_git_status_color(code):
    assert git_status_color(code).startswith("#")


def test_markdown_css_and_stylesheet(qapp):
    css = markdown_css(14, "dark")
    assert "body {" in css
    sheet = build_stylesheet("dark")
    assert "QMainWindow" in sheet
    assert "background" in bubble_label_style(is_user=True)
