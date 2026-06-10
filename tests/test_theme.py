import json

import pytest

import ui.theme as theme_module
from ui.theme import (
    apply_app_theme,
    build_stylesheet,
    bubble_label_style,
    compaction_threshold_pct,
    crew_name_style,
    crew_tone,
    current_theme,
    git_status_color,
    markdown_css,
    palette,
)


def test_palette_dark_and_light():
    assert "BG" in palette("dark")
    assert palette("light")["BG"] != palette("dark")["BG"]


def test_current_theme_from_settings(isolate_aichs_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    assert current_theme() == "light"


def test_current_theme_invalid_falls_back(isolate_aichs_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(json.dumps({"theme": "neon"}), encoding="utf-8")
    assert current_theme() in ("dark", "light")


def test_compaction_threshold_clamped(isolate_aichs_home):
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


def test_apply_app_theme_skips_reapplying_same_theme(monkeypatch):
    from storage.settings import SettingsStore

    SettingsStore().save({"font_size": "medium"})
    builds = []
    app = _FakeApp()
    monkeypatch.setattr(
        theme_module,
        "build_stylesheet",
        lambda name: builds.append(name) or f"QWidget {{ /* {name} */ }}",
    )
    monkeypatch.setattr("ui.win_caption.install_caption_sync", lambda _app: None)
    monkeypatch.setattr("ui.win_caption.sync_all_windows_captions", lambda *_args: None)

    apply_app_theme(app, "modern")
    apply_app_theme(app, "modern")
    app.setStyleSheet("")
    apply_app_theme(app, "modern")
    SettingsStore().update({"font_size": "large"})
    apply_app_theme(app, "modern")

    assert builds == ["modern", "modern", "modern"]


def test_crew_styles_are_distinct():
    scout = bubble_label_style(False, crew_id="scout")
    archivist = bubble_label_style(False, crew_id="archivist")
    assert scout != archivist
    assert "#123456" in bubble_label_style(False, crew_id="scout", crew_color="#123456")
    assert "#123456" in crew_name_style("scout", "#123456")
    assert crew_tone("archivist")["accent"].startswith("#")


class _FakeApp:
    def __init__(self):
        self._font = None
        self._style = ""
        self._properties = {}

    def font(self):
        return self._font or theme_module.app_font()

    def setFont(self, font):
        self._font = font

    def styleSheet(self):
        return self._style

    def setStyleSheet(self, style):
        self._style = style

    def property(self, name):
        return self._properties.get(name)

    def setProperty(self, name, value):
        self._properties[name] = value
