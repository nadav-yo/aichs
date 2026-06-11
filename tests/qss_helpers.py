"""Shared Qt stylesheet parser regression helpers."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from PyQt6.QtCore import qInstallMessageHandler
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QWidget,
)

StyleCase = tuple[str, Callable[[], QWidget], str]
THEMES = ("dark", "modern", "light")
DEFAULT_THEME = "modern"

_QSS_MARKERS = (
    "QPushButton",
    "QWidget",
    "QFrame",
    "QLabel",
    "QListWidget",
    "QTreeWidget",
    "QLineEdit",
    "QTextEdit",
    "QComboBox",
    "QDialog",
    "QTabWidget",
    "QScrollArea",
    "QMenu",
    "QSplitter",
    "QCheckBox",
    "QToolButton",
    "TrashHeader",
    "composerShell",
)


def _looks_like_qss(text: str) -> bool:
    if any(marker in text for marker in ("</style>", "<p>", "<div", "<html", "<body")):
        return False
    return any(marker in text for marker in _QSS_MARKERS)


@contextmanager
def capture_qt_stylesheet_warnings():
    messages: list[str] = []

    def _handler(_mode, _context, message):
        messages.append(message)

    previous = qInstallMessageHandler(_handler)
    try:
        yield messages
    finally:
        qInstallMessageHandler(previous)


def parse_failures(messages: Iterable[str]) -> list[str]:
    return [message for message in messages if "Could not parse" in message]


def assert_stylesheets_parse(
    qapp: QApplication,
    cases: Iterable[StyleCase],
    *,
    prefix: str = "",
) -> None:
    failures: list[str] = []
    with capture_qt_stylesheet_warnings() as messages:
        for name, widget_factory, style in cases:
            messages.clear()
            widget = widget_factory()
            try:
                widget.setStyleSheet(style)
                qapp.processEvents()
                failures.extend(
                    f"{prefix}{name}: {message}"
                    for message in parse_failures(messages)
                )
            finally:
                widget.deleteLater()
    assert failures == []


def assert_app_stylesheets_parse(
    qapp: QApplication,
    sheets: Iterable[tuple[str, str]],
) -> None:
    probe = QWidget()
    original = qapp.styleSheet()
    failures: list[str] = []
    try:
        with capture_qt_stylesheet_warnings() as messages:
            for name, sheet in sheets:
                messages.clear()
                probe.setStyleSheet(sheet)
                qapp.processEvents()
                failures.extend(
                    f"{name}: {message}" for message in parse_failures(messages)
                )
    finally:
        probe.deleteLater()
        qapp.setStyleSheet(original)
    assert failures == []


def assert_all_app_stylesheets_parse(qapp: QApplication) -> None:
    import ui.theme as theme

    assert_app_stylesheets_parse(
        qapp,
        [(f"app {theme_name}", theme.build_stylesheet(theme_name)) for theme_name in THEMES],
    )


def _style_probe(widget_type: type[QWidget]) -> QWidget:
    try:
        return widget_type()
    except TypeError:
        return QWidget()


def reparse_widget_stylesheets(
    qapp: QApplication,
    root: QWidget,
    *,
    prefix: str = "",
) -> list[str]:
    """Apply collected widget styles to fresh instances to avoid Qt crashes on live trees."""
    seen: set[str] = set()
    failures: list[str] = []
    with capture_qt_stylesheet_warnings() as messages:
        for widget in [root, *root.findChildren(QWidget)]:
            style = widget.styleSheet()
            if not style or style in seen:
                continue
            seen.add(style)
            name = widget.objectName() or type(widget).__name__
            messages.clear()
            probe = _style_probe(type(widget))
            try:
                probe.setStyleSheet(style)
                qapp.processEvents()
                failures.extend(
                    f"{prefix}{name}: {message}"
                    for message in parse_failures(messages)
                )
            finally:
                probe.deleteLater()
    return failures


def iter_inline_dim_label_stylesheet_offenders(
    widgets_dir: Path | None = None,
) -> list[tuple[str, int, str]]:
    """Widget modules should use hint_label_style() instead of inline TEXT_DIM QSS."""
    root = Path(__file__).resolve().parent.parent
    directory = widgets_dir or root / "ui" / "widgets"
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(directory.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "setStyleSheet" in line and "TEXT_DIM" in line:
                offenders.append((str(path.relative_to(root)), lineno, line.strip()))
    return offenders


_AD_HOC_QSS_FSTRING = re.compile(
    r"color:\{|background:\{|font-size:\{|border:\{|border-radius:\{"
)


def _is_ad_hoc_qss_stylesheet_arg(source: str, arg: ast.AST) -> bool:
    if isinstance(arg, (ast.Call, ast.Name, ast.Attribute, ast.Subscript, ast.BinOp)):
        return False
    segment = (ast.get_source_segment(source, arg) or "").strip()
    if isinstance(arg, ast.JoinedStr):
        return bool(
            _looks_like_qss(segment)
            or _AD_HOC_QSS_FSTRING.search(segment)
            or "p['" in segment
            or 'p["' in segment
        )
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return _looks_like_qss(arg.value)
    return False


def iter_ad_hoc_qss_stylesheet_offenders(
    widgets_dir: Path | None = None,
) -> list[tuple[str, int, str]]:
    """Inline f-string / literal QSS in widget setStyleSheet calls (not theme helpers)."""
    root = Path(__file__).resolve().parent.parent
    directory = widgets_dir or root / "ui" / "widgets"
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(directory.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rel = str(path.relative_to(root))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "setStyleSheet"
                and node.args
            ):
                continue
            arg = node.args[0]
            if not _is_ad_hoc_qss_stylesheet_arg(source, arg):
                continue
            segment = (ast.get_source_segment(source, arg) or "").strip()
            snippet = " ".join(segment.split())
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            offenders.append((rel, arg.lineno, snippet))
    return offenders


def format_ad_hoc_qss_offender(entry: tuple[str, int, str]) -> str:
    path, lineno, snippet = entry
    return f"{path}:{lineno}:{snippet}"


def load_ad_hoc_qss_baseline(
    baseline_path: Path | None = None,
) -> set[str]:
    path = baseline_path or Path(__file__).resolve().parent / "qss_ad_hoc_baseline.txt"
    if not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def find_new_ad_hoc_qss_offenders(
    baseline_path: Path | None = None,
) -> list[str]:
    current = {
        format_ad_hoc_qss_offender(entry)
        for entry in iter_ad_hoc_qss_stylesheet_offenders()
    }
    baseline = load_ad_hoc_qss_baseline(baseline_path)
    return sorted(current - baseline)


def iter_double_close_brace_literals(*paths: Path) -> Iterator[tuple[str, int, str]]:
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if "}}" not in source:
            continue
        tree = ast.parse(source)
        theme_module = path.name == "theme.py"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if "}}" not in node.value:
                continue
            if not theme_module and not _looks_like_qss(node.value):
                continue
            yield str(path), node.lineno, node.value


def run_offscreen_window_probe(*probe_args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parent.parent
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COVERAGE")
    }
    env["PYTHONPATH"] = str(repo_root)
    env["QT_QPA_PLATFORM"] = "offscreen"
    script = Path(__file__).resolve().parent / "qss_window_probe.py"
    return subprocess.run(
        [sys.executable, str(script), *probe_args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _tab_widget(object_name: str) -> QTabWidget:
    widget = QTabWidget()
    widget.setObjectName(object_name)
    return widget


def _named_frame(object_name: str) -> QFrame:
    widget = QFrame()
    widget.setObjectName(object_name)
    return widget


def collect_theme_stylesheet_cases(theme_name: str = DEFAULT_THEME) -> list[StyleCase]:
    from storage.settings import SettingsStore
    import ui.theme as theme

    SettingsStore().update({"theme": theme_name})
    separator = theme.separator_color()
    nav_border = f"0px solid {separator}; border-right:1px solid {separator}"
    font_pt = 13

    return [
        ("checkbox", QCheckBox, theme.checkbox_style()),
        ("combo popup", QComboBox, theme.combo_box_popup_style()),
        (
            "custom combo popup",
            QComboBox,
            theme.combo_box_popup_style(
                theme_name,
                bg="#101820",
                border_radius=3,
                font_pt=11,
                item_padding="2px 3px",
            ),
        ),
        ("compact combo", QComboBox, theme.compact_combo_box_style()),
        ("form field", QLineEdit, theme.form_field_style()),
        ("compact field", QLineEdit, theme.compact_field_style()),
        (
            "code text edit",
            QTextEdit,
            theme.code_text_edit_style(selector="QTextEdit"),
        ),
        ("editor text area", QPlainTextEdit, theme.editor_text_area_style()),
        (
            "dialog shell",
            QDialog,
            theme.dialog_shell_style(include_labels=True),
        ),
        ("panel stack", QStackedWidget, theme.panel_stack_style()),
        ("avatar preview", QLabel, theme.avatar_preview_style()),
        ("dialog buttons", QDialogButtonBox, theme.dialog_button_box_style()),
        ("transparent scroll", QScrollArea, theme.transparent_scroll_area_style()),
        ("menu", QMenu, theme.menu_style()),
        ("surface frame", QFrame, theme.surface_frame_style()),
        ("card frame", QFrame, theme.card_frame_style()),
        ("separator frame", QFrame, theme.separator_frame_style()),
        ("overlay separator", QFrame, theme.overlay_separator_style()),
        ("button primary", QPushButton, theme.primary_button_style()),
        ("button secondary", QPushButton, theme.secondary_button_style()),
        ("icon button", QPushButton, theme.icon_button_style()),
        ("bordered icon", QToolButton, theme.bordered_icon_button_style()),
        ("send button", QPushButton, theme.send_button_style()),
        ("stop button", QPushButton, theme.stop_button_style()),
        ("floating button", QPushButton, theme.floating_button_style()),
        ("new chat button", QPushButton, theme.new_chat_button_style()),
        (
            "sidebar settings button",
            QPushButton,
            theme.sidebar_settings_button_style(),
        ),
        ("contained list", QListWidget, theme.contained_list_style()),
        (
            "navigation list",
            QListWidget,
            theme.navigation_list_style(border=nav_border),
        ),
        ("conversation list", QListWidget, theme.conversation_list_style()),
        ("git changes list", QListWidget, theme.git_changes_list_style()),
        ("overlay results", QListWidget, theme.overlay_results_list_style()),
        ("popover list", QListWidget, theme.popover_list_style()),
        ("contained tree", QTreeWidget, theme.contained_tree_style()),
        (
            "file tree",
            QTreeWidget,
            theme.file_tree_sidebar_style(),
        ),
        (
            "data table",
            QTableWidget,
            theme.data_table_style(border_radius=8),
        ),
        ("splitter", QSplitter, theme.splitter_style()),
        (
            "flat tabs",
            lambda: _tab_widget("demoTabs"),
            theme.flat_tab_style("demoTabs"),
        ),
        (
            "file tabs",
            lambda: _tab_widget("fileViewerTabs"),
            theme.file_tab_style(),
        ),
        (
            "sidebar tabs",
            lambda: _tab_widget("sidebarTabs"),
            theme.sidebar_tab_style(),
        ),
        ("overlay dialog", QDialog, theme.overlay_dialog_style()),
        ("overlay search", QLineEdit, theme.overlay_search_input_style()),
        ("files header", QWidget, theme.files_header_style()),
        ("search field", QLineEdit, theme.search_field_style()),
        ("title label", QLabel, theme.title_label_style()),
        ("section label", QLabel, theme.section_label_style()),
        ("hint label", QLabel, theme.hint_label_style()),
        ("field label", QLabel, theme.field_label_style()),
        ("sidebar section label", QLabel, theme.sidebar_section_label_style()),
        ("status pill", QLabel, theme.status_pill_style()),
        ("tool notice", QLabel, theme.tool_notice_style()),
        ("center notice", QLabel, theme.center_notice_style()),
        ("timestamp", QLabel, theme.timestamp_style()),
        ("crew name", QLabel, theme.crew_name_style("scout")),
        ("search match", QLabel, theme.search_match_style(theme_name)),
        ("user reference", QLabel, theme.user_reference_style(theme_name)),
        ("composer text", QTextEdit, theme.composer_style(font_pt)),
        ("composer shell", QFrame, theme.composer_shell_style()),
        ("edit bubble", QTextEdit, theme.edit_bubble_style(font_pt)),
        ("input bar", QFrame, theme.input_bar_style()),
        ("popover frame", QFrame, theme.popover_frame_style()),
        (
            "bubble user",
            QLabel,
            theme.bubble_label_style(True, font_pt),
        ),
        (
            "bubble crew",
            QLabel,
            theme.bubble_label_style(False, font_pt, crew_id="scout"),
        ),
        (
            "bubble ai",
            QLabel,
            theme.bubble_label_style(False, font_pt),
        ),
        ("rail button active", QPushButton, theme.rail_button_style(font_size=font_pt, active=True)),
        ("rail button idle", QPushButton, theme.rail_button_style(font_size=font_pt, active=False)),
        ("git action button", QPushButton, theme.git_action_button_style()),
        ("git change button", QPushButton, theme.git_change_button_style()),
        ("context title button", QPushButton, theme.context_panel_title_button_style()),
        ("toggle tab button", QPushButton, theme.toggle_tab_button_style()),
        ("skill chip", QPushButton, theme.skill_chip_style()),
        ("attachment thumbnail", QLabel, theme.attachment_thumbnail_style()),
        ("attachment remove", QPushButton, theme.attachment_remove_button_style()),
        ("conversation row title", QLabel, theme.conversation_row_title_style()),
        ("conversation row edit", QLineEdit, theme.conversation_row_inline_edit_style()),
        (
            "conversation row icon",
            QLabel,
            theme.conversation_row_icon_label_style(hover_color="#ff5555"),
        ),
        ("conversation row restore", QPushButton, theme.conversation_row_restore_button_style()),
        ("conversation trash header", QWidget, theme.conversation_trash_header_style()),
        ("sidebar footer button", QPushButton, theme.sidebar_footer_button_style()),
        ("tone badge button", QPushButton, theme.tone_badge_button_style("success")),
        ("extension list row", lambda: _named_frame("extensionListRow"), theme.extension_list_row_style(selected=False)),
        ("extension list name", QLabel, theme.extension_list_name_style()),
        ("extension detail value", QLabel, theme.extension_detail_value_style()),
    ]


def collect_theme_branching_stylesheet_cases() -> list[StyleCase]:
    """Styles whose QSS shape can vary by theme (not just palette colors)."""
    from storage.settings import SettingsStore
    import ui.theme as theme

    cases: list[StyleCase] = []
    font_pt = 13
    for theme_name in THEMES:
        SettingsStore().update({"theme": theme_name})
        prefix = f"{theme_name}/"
        cases.extend([
            (f"{prefix}composer shell", QFrame, theme.composer_shell_style()),
            (f"{prefix}card frame", QFrame, theme.card_frame_style()),
            (f"{prefix}new chat button", QPushButton, theme.new_chat_button_style(theme_name)),
            (
                f"{prefix}bubble user",
                QLabel,
                theme.bubble_label_style(True, font_pt),
            ),
        ])
    return cases


def collect_widget_module_stylesheet_cases(theme_name: str = DEFAULT_THEME) -> list[StyleCase]:
    from storage.settings import SettingsStore
    import ui.theme as theme
    from ui.widgets.extension_contributions import _badge_style
    from ui.widgets.extension_panel_dialog import _action_button_style, _heading_style
    from ui.widgets.extensions_dialog import (
        _detail_name_style,
        _detail_scroll_style,
        _detail_table_style,
        _detail_value_style,
        _enabled_checkbox_style,
        _header_style,
        _heading_style as _extension_heading_style,
        _install_scope_combo_style,
        _list_meta_style,
        _list_name_style,
        _list_path_style,
        _list_row_style,
        _list_scroll_style,
        _status_label_style,
    )
    from ui.widgets.git_changes_list import _git_change_field_style
    from ui.widgets.tool_approval_dialog import _muted_label_style

    SettingsStore().update({"theme": theme_name})

    cases: list[StyleCase] = [
        ("extension heading", QLabel, _extension_heading_style()),
        ("extension heading danger", QLabel, _extension_heading_style("danger")),
        ("extension list scroll", QScrollArea, _list_scroll_style()),
        ("extension detail scroll", QScrollArea, _detail_scroll_style()),
        ("extension list name", QLabel, _list_name_style()),
        ("extension list path", QLabel, _list_path_style()),
        ("extension header", lambda: _named_frame("extensionHeader"), _header_style()),
        (
            "extension detail table",
            lambda: _named_frame("extensionDetailTable"),
            _detail_table_style(),
        ),
        ("extension detail name", QLabel, _detail_name_style()),
        ("extension detail value", QLabel, _detail_value_style()),
        ("extension enabled checkbox", QCheckBox, _enabled_checkbox_style()),
        ("extension install scope combo", QComboBox, _install_scope_combo_style()),
        ("extension panel heading", QLabel, _heading_style()),
        ("extension panel action button", QPushButton, _action_button_style()),
        (
            "git change field",
            QLineEdit,
            _git_change_field_style(theme=theme_name),
        ),
        ("tool approval muted label", QLabel, _muted_label_style()),
    ]
    for tone in ("", "success", "danger", "warning", "accent"):
        cases.append((f"extension badge {tone or 'default'}", QPushButton, _badge_style(tone)))
    for tone in ("", "danger", "disabled", "success"):
        tone_label = tone or "default"
        cases.append((f"extension list meta {tone_label}", QLabel, _list_meta_style(tone)))
        cases.append(
            (f"extension status {tone_label}", QLabel, _status_label_style(tone or "success"))
        )
    for selected in (False, True):
        for tone in ("", "danger", "disabled", "success"):
            tone_label = tone or "default"
            cases.append(
                (
                    f"extension list row selected={selected} tone={tone_label}",
                    lambda: _named_frame("extensionListRow"),
                    _list_row_style(selected, tone),
                )
            )
    return cases


