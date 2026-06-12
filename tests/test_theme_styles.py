from pathlib import Path

from tests.qss_helpers import (
    assert_all_app_stylesheets_parse,
    assert_stylesheets_parse,
    collect_theme_branching_stylesheet_cases,
    collect_theme_stylesheet_cases,
    collect_widget_module_stylesheet_cases,
    find_new_ad_hoc_qss_offenders,
    iter_double_close_brace_literals,
    iter_inline_dim_label_stylesheet_offenders,
    run_offscreen_window_probe,
)
from tests import qss_helpers
from ui import theme


def test_theme_stylesheets_parse_without_qt_warnings(qapp):
    assert_stylesheets_parse(qapp, collect_theme_stylesheet_cases())
    assert_stylesheets_parse(qapp, collect_theme_branching_stylesheet_cases())
    assert_all_app_stylesheets_parse(qapp)


def test_widget_module_stylesheets_parse_without_qt_warnings(qapp):
    assert_stylesheets_parse(qapp, collect_widget_module_stylesheet_cases())


def test_ui_modules_have_no_literal_double_close_brace_fragments():
    ui_dir = Path(theme.__file__).resolve().parent
    paths = [ui_dir / "theme.py", *sorted((ui_dir / "widgets").glob("*.py"))]
    offenders = list(iter_double_close_brace_literals(*paths))
    assert offenders == []


def test_widget_modules_avoid_inline_text_dim_stylesheets():
    offenders = iter_inline_dim_label_stylesheet_offenders()
    assert offenders == []


def test_widget_modules_avoid_new_ad_hoc_qss_stylesheets():
    new_offenders = find_new_ad_hoc_qss_offenders()
    assert new_offenders == [], (
        "New inline QSS in ui/widgets setStyleSheet calls. "
        "Use ui.theme helpers or update tests/qss_ad_hoc_baseline.txt:\n"
        + "\n".join(new_offenders)
    )


def test_ad_hoc_qss_baseline_ignores_line_number_drift(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("ui\\widgets\\demo.py:12:f\"QLabel {{ color:{p['TEXT']}; }}\"\n", encoding="utf-8")
    monkeypatch.setattr(
        qss_helpers,
        "iter_ad_hoc_qss_stylesheet_offenders",
        lambda: [("ui\\widgets\\demo.py", 99, "f\"QLabel {{ color:{p['TEXT']}; }}\"")],
    )

    assert qss_helpers.find_new_ad_hoc_qss_offenders(baseline) == []


def test_window_stylesheets_parse_without_qt_warnings(workspace, qapp):
    result = run_offscreen_window_probe(str(workspace))
    assert result.returncode == 0, result.stderr or result.stdout


def test_app_and_mono_font(qapp):
    font = theme.app_font("medium")
    assert font.pointSize() > 0
    mono = theme.mono_font(12)
    assert mono.family()


def test_primary_button_style_has_hover_and_pressed(qapp):
    css = theme.primary_button_style(selector="QPushButton#primary")
    assert "QPushButton#primary:hover" in css
    assert theme.ACCENT_HOVER in css
    assert "QPushButton#primary:pressed" in css
