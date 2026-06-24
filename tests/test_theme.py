import json
import re

import pytest

import ui.theme as theme_module
from ui.theme import (
    _markdown_tokens,
    apply_app_theme,
    build_stylesheet,
    bubble_label_style,
    checkbox_style,
    combo_box_popup_style,
    compaction_threshold_pct,
    crew_name_style,
    crew_tone,
    current_theme,
    git_status_color,
    markdown_css,
    markdown_file_link_style,
    palette,
)


def _relative_luminance(hex_color: str) -> float:
    raw = hex_color.strip("#")[:6]
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(fg: str, bg: str) -> float:
    first = _relative_luminance(fg)
    second = _relative_luminance(bg)
    lighter = max(first, second)
    darker = min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


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
    SETTINGS_PATH.write_text(json.dumps({"compaction_threshold_pct": "soon"}), encoding="utf-8")
    assert compaction_threshold_pct() == 90


@pytest.mark.parametrize("code", ["??", " M", "D ", "UU"])
def test_git_status_color(code):
    assert git_status_color(code).startswith("#")


def test_markdown_css_and_stylesheet(qapp):
    css = markdown_css(14, "dark")
    assert "body {" in css
    sheet = build_stylesheet("dark")
    assert "QMainWindow" in sheet
    assert "QMenu::separator" in sheet
    assert "QComboBoxPrivateContainer" in sheet
    assert "QComboBox QAbstractItemView::item" in sheet
    assert "background" in bubble_label_style(is_user=True)


def test_combo_box_popup_style_paints_container_viewport_and_items():
    p = palette("modern")
    style = combo_box_popup_style("modern", bg=p["BG3"], border_radius=6, font_pt=12)

    assert "QComboBoxPrivateContainer" in style
    assert "padding:6px" in style
    assert "padding:5px 10px" in style
    assert "QComboBox::indicator" not in style
    assert "QAbstractItemView::indicator" in style
    assert "QComboBoxPrivateContainer QWidget" in style
    assert "QComboBox QAbstractItemView::item" in style
    assert p["BG3"] in style
    assert p["SELECTION"] in style
    assert p["SELECTION_TEXT"] in style


def test_conversation_list_style_overrides_global_item_padding():
    style = theme_module.conversation_list_style()
    assert "padding:0px 0px" in style


def test_workbench_header_styles_share_frame_and_title_contract():
    frame = theme_module.workbench_header_frame_style(object_name="chatHeader")
    chat = theme_module.chat_header_style()
    files = theme_module.files_header_style()

    assert "border-bottom:1px solid" in frame
    assert "chatHeaderTitle" in chat
    assert "chatHeaderSubtitle" in chat
    assert "filesPath" in files
    assert "font-weight:600" in chat
    assert "font-weight:600" in files
    assert theme_module.WORKBENCH_HEADER_MARGINS == (16, 8, 12, 8)


def test_combo_box_popup_container_style_includes_item_padding():
    style = theme_module.combo_box_popup_container_style("modern", border_radius=6, font_pt=12)

    assert "padding:6px" in style
    assert "padding:5px 10px" in style
    assert "QListView::item" in style
    assert "border-radius:6px" in style


def test_combo_popup_hover_uses_lifted_surface():
    modern = palette("modern")
    modern_style = theme_module.combo_box_popup_container_style("modern")
    assert f"background:{modern['BORDER']}" in modern_style

    light = palette("light")
    light_style = theme_module.combo_box_popup_container_style("light")
    assert f"background:{light['BG2']}" in light_style


def test_combo_popup_visible_row_count():
    count = theme_module.combo_popup_visible_row_count
    assert count(0) == 0
    assert count(2) == 2
    assert count(4) == 4
    assert count(8) == 8
    assert count(15) == theme_module.COMBO_POPUP_MAX_VISIBLE_ROWS


def test_combo_popup_container_resizes_for_multiple_rows(qapp):
    from PyQt6.QtWidgets import QComboBox, QListView

    apply_app_theme(qapp, "modern")
    combo = QComboBox()
    for i in range(8):
        combo.addItem(f"provider-{i}")
    combo.show()
    combo.showPopup()
    for _ in range(3):
        qapp.processEvents()

    container = next(
        w for w in qapp.allWidgets()
        if w.metaObject().className() == "QComboBoxPrivateContainer" and w.isVisible()
    )
    view = container.findChild(QListView)
    assert view is not None
    row_h = max(view.sizeHintForRow(0), theme_module.COMBO_POPUP_MIN_ROW_HEIGHT)
    visible = theme_module.combo_popup_visible_row_count(8)
    assert visible == 8
    assert view.minimumHeight() >= visible * (row_h - 2)
    assert container.height() >= 4 * (row_h - 2)

    combo.hidePopup()
    combo.deleteLater()
    qapp.processEvents()


def test_combo_popup_container_fits_short_lists(qapp):
    from PyQt6.QtWidgets import QComboBox, QListView

    apply_app_theme(qapp, "modern")
    combo = QComboBox()
    combo.addItems(["one", "two"])
    combo.show()
    combo.showPopup()
    for _ in range(3):
        qapp.processEvents()

    container = next(
        w for w in qapp.allWidgets()
        if w.metaObject().className() == "QComboBoxPrivateContainer" and w.isVisible()
    )
    view = container.findChild(QListView)
    row_h = max(view.sizeHintForRow(0), theme_module.COMBO_POPUP_MIN_ROW_HEIGHT)
    pad = theme_module.COMBO_POPUP_VIEW_PADDING * 2
    assert container.height() <= 2 * row_h + pad + 4
    assert container.height() >= 2 * row_h + pad - 4
    assert view.minimumHeight() >= 2 * theme_module.COMBO_POPUP_MIN_ROW_HEIGHT

    combo.hidePopup()
    combo.deleteLater()
    qapp.processEvents()


def test_overlay_search_styles_share_modal_search_contract():
    dialog = theme_module.overlay_dialog_style()
    query = theme_module.overlay_search_input_style()
    separator = theme_module.overlay_separator_style()
    results = theme_module.overlay_results_list_style()

    assert "QDialog" in dialog
    assert f"border-radius:{theme_module.MODAL_BORDER_RADIUS}px" in dialog
    assert "QLineEdit:focus" in query
    assert f"border:1px solid {theme_module.ACCENT};" in query
    assert f"border-radius:{theme_module.OVERLAY_SEARCH_BORDER_RADIUS}px" in query
    assert "padding:10px 14px" in query
    assert "max-height:1px" in separator
    assert "QListWidget::item:selected" in results
    assert f"border-left:3px solid {theme_module.ACCENT};" in results


def test_hint_and_field_label_styles_use_meta_scale():
    hint = theme_module.hint_label_style()
    field = theme_module.field_label_style()

    assert f"font-size:{theme_module.meta_font_pt()}px" in hint
    assert "font-weight:normal" in hint
    assert f"font-size:{theme_module.meta_font_pt()}px" in field
    assert "font-weight:500" in field
    assert theme_module.palette()["TEXT_DIM"] in hint


def test_design_tokens_align_form_and_global_fields():
    sheet = build_stylesheet("modern")
    form = theme_module.form_field_style(theme="modern")

    assert f"border-radius:{theme_module.FIELD_BORDER_RADIUS}px" in sheet
    assert f"border-radius:{theme_module.FIELD_BORDER_RADIUS}px" in form
    assert theme_module.ACCENT_DIM not in theme_module.search_field_style()
    assert f"border:1px solid {theme_module.ACCENT};" in theme_module.search_field_style()


def test_specialized_button_styles_use_shared_tokens():
    p = palette()
    rail = theme_module.rail_button_style(font_size=13, active=True)
    git = theme_module.git_action_button_style()
    tab = theme_module.toggle_tab_button_style()
    chip = theme_module.skill_chip_style()

    assert p["SELECTION"] in rail
    assert f"border-radius:{theme_module.FIELD_BORDER_RADIUS}px" in git
    assert ":checked" in tab
    assert f"border:1px solid {p['BORDER']}" in chip


def test_conversation_row_styles_use_theme_palette():
    title = theme_module.conversation_row_title_style()
    edit = theme_module.conversation_row_inline_edit_style()
    icon = theme_module.conversation_row_icon_label_style(hover_color="#ff5555")
    trash = theme_module.conversation_trash_header_style()

    assert palette()["TEXT"] in title
    assert palette()["BG3"] in edit
    assert "#ff5555" in icon
    assert "TrashHeader" in trash


def test_flat_list_variants_share_shell_and_differ_on_selection():
    git = theme_module.git_changes_list_style()
    overlay = theme_module.overlay_results_list_style()
    conversation = theme_module.conversation_list_style()

    assert "QListWidget { background:" in git
    assert "padding:1px 6px" in git
    assert f"border-left:3px solid {theme_module.ACCENT}" in overlay
    assert "border-radius:5px" in conversation


def test_new_chat_button_style_uses_theme_soft_accent(qapp):
    for theme_name in ("dark", "modern", "light"):
        style = theme_module.new_chat_button_style(theme_name)
        assert theme_module.ACCENT in style
        assert "QPushButton:hover" in style
        if theme_name == "light":
            assert theme_module.ACCENT_SOFT_LIGHT in style
        else:
            assert theme_module.ACCENT_SOFT_DARK in style


def test_extension_surface_styles_use_theme_helpers():
    row = theme_module.extension_list_row_style(selected=True, tone="danger")
    name = theme_module.extension_list_name_style()
    value = theme_module.extension_detail_value_style(tone="danger")

    assert "QFrame#extensionListRow" in row
    assert "#5f252d" in row
    assert "QLabel {" in name
    assert "#fca5a5" in value


def test_secondary_button_style_defines_neutral_action_contract():
    p = palette()
    style = theme_module.secondary_button_style(
        selector="QPushButton#demoSecondary",
        border_radius=4,
        padding="2px 8px",
        margin="3px 0",
        font_size=11,
        text_color=p["TEXT_DIM"],
    )

    assert "QPushButton#demoSecondary {" in style
    assert f"background:{p['BG3']};" in style
    assert f"color:{p['TEXT_DIM']};" in style
    assert "border-radius:4px" in style
    assert "padding:2px 8px" in style
    assert "margin:3px 0" in style
    assert "QPushButton#demoSecondary:hover" in style
    assert "QPushButton#demoSecondary:pressed" in style
    assert "QPushButton#demoSecondary:disabled" in style


def test_bordered_icon_button_style_defines_visible_icon_action_contract():
    p = palette()
    style = theme_module.bordered_icon_button_style(
        selector="QToolButton#tableAction",
        size_px=28,
        border_radius=5,
        padding="2px",
    )

    assert "QToolButton#tableAction {" in style
    assert f"background:{p['BG3']};" in style
    assert f"color:{p['TEXT_DIM']};" in style
    assert f"border:1px solid {p['BORDER']};" in style
    assert "border-radius:5px" in style
    assert "padding:2px" in style
    assert "min-width:28px" in style
    assert "QToolButton#tableAction:hover" in style
    assert "QToolButton#tableAction:pressed" in style
    assert "QToolButton#tableAction:disabled" in style


def test_dialog_shell_style_defines_modal_chrome_contract():
    p = palette()
    style = theme_module.dialog_shell_style(include_labels=True)

    assert "QDialog {" in style
    assert f"background:{p['BG2']};" in style
    assert f"color:{p['TEXT']};" in style
    assert "QLabel {" in style
    assert "background:transparent" in style


def test_transparent_scroll_area_style_defines_scroll_chrome_contract():
    p = palette()
    style = theme_module.transparent_scroll_area_style(
        selector="QScrollArea#details",
        border=f"1px solid {p['BORDER_SUBTLE']}",
    )

    assert "QScrollArea#details {" in style
    assert f"background:{p['BG2']};" in style
    assert f"border:1px solid {p['BORDER_SUBTLE']};" in style
    assert "QScrollArea#details QWidget" in style


def test_menu_style_defines_context_menu_contract():
    p = palette()
    style = theme_module.menu_style()

    assert "QMenu {" in style
    assert f"background-color:{p['BG3']};" in style
    assert "QMenu::item {" in style
    assert f"background-color:{p['SELECTION']};" in style
    assert "QMenu::item:disabled" in style
    assert "QMenu::separator" in style


def test_dialog_button_box_style_defines_action_row_contract():
    style = theme_module.dialog_button_box_style(min_button_width=84)

    assert "QDialogButtonBox {" in style
    assert "background-color:transparent" in style
    assert "QDialogButtonBox QPushButton" in style
    assert "min-width:84px" in style
    assert "QDialogButtonBox QPushButton:disabled" in style


def test_composer_shell_style_uses_qt_safe_focus_selector():
    style = theme_module.composer_shell_style()

    assert ":focus-within" not in style
    assert 'QFrame#composerShell[composerFocused="true"]' in style


def test_surface_frame_style_defines_card_surface_contract():
    p = palette()
    style = theme_module.surface_frame_style(
        selector="QFrame#card",
        bg=p["BG2"],
        border=p["BORDER_SUBTLE"],
        border_radius=7,
    )

    assert "QFrame#card {" in style
    assert f"background:{p['BG2']};" in style
    assert f"border:1px solid {p['BORDER_SUBTLE']};" in style
    assert "border-radius:7px" in style


def test_separator_frame_style_defines_thin_divider_contract():
    style = theme_module.separator_frame_style(selector="QFrame#divider", color="#123456")

    assert "QFrame#divider {" in style
    assert "background:#123456" in style
    assert "color:#123456" in style
    assert "border:none" in style
    assert "max-height:1px" in style


def test_form_field_style_defines_dialog_field_contract():
    style = theme_module.form_field_style()

    assert "QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox" in style
    assert "QComboBox {" in style
    assert "border-radius:6px" in style
    assert "padding:8px 10px" in style
    assert "padding-right:24px" in style
    assert "subcontrol-position: top right" in style
    assert f"border:1px solid {theme_module.ACCENT};" in style
    assert "QComboBoxPrivateContainer" in style
    assert "QComboBox::down-arrow" not in style
    assert "QComboBox::indicator" not in style


def test_compact_field_style_defines_toolbar_field_contract():
    style = theme_module.compact_field_style(
        selector="QLineEdit#findBox",
        font_pt=11,
        padding="3px 7px",
        border_radius=5,
    )

    assert "QLineEdit#findBox {" in style
    assert "border-radius:5px" in style
    assert "padding:3px 7px" in style
    assert "font-size:11px" in style
    assert "QLineEdit#findBox:focus" in style
    assert "QComboBoxPrivateContainer" not in style


def test_compact_field_style_expands_grouped_focus_selectors():
    style = theme_module.compact_field_style(
        selector="QLineEdit, QTextEdit",
        border_color="#334455",
    )

    assert "border:1px solid #334455;" in style
    assert "QLineEdit:focus, QTextEdit:focus" in style
    assert "QLineEdit, QTextEdit:focus" not in style


def test_editor_text_area_style_defines_full_editor_contract():
    p = palette()
    style = theme_module.editor_text_area_style(font_pt=12, padding="2px 4px")

    assert "QPlainTextEdit {" in style
    assert f"background:{p['BG3']};" in style
    assert "border:none" in style
    assert "padding:2px 4px" in style
    assert "font-family:" in style
    assert "font-size:12px" in style
    assert f"selection-background-color:{theme_module.ACCENT};" in style


def test_file_tab_style_defines_closable_editor_tab_contract():
    style = theme_module.file_tab_style()

    assert "QTabWidget#fileViewerTabs" in style
    assert "QTabWidget#fileViewerTabs QTabBar::tab" in style
    assert "padding:6px 10px" in style
    assert "min-width:88px" in style
    assert "max-width:220px" in style
    assert "QTabWidget#fileViewerTabs QTabBar::close-button" in style


def test_navigation_list_style_defines_vertical_section_nav_contract():
    style = theme_module.navigation_list_style(
        selector="QListWidget#settingsNav",
        border="0px solid #111; border-right:1px solid #222",
    )

    assert "QListWidget#settingsNav {" in style
    assert "border-right:1px solid #222" in style
    assert "border-left:3px solid transparent" in style
    assert f"border-left:3px solid {theme_module.ACCENT};" in style
    assert "QListWidget#settingsNav::item:selected:focus" in style
    assert "QListWidget#settingsNav::item:hover" in style
    assert "QListWidget#settingsNav::item:hover:!selected" not in style


def test_compact_combo_box_style_defines_dense_dropdown_contract():
    p = palette()
    style = theme_module.compact_combo_box_style(
        selector="QComboBox#scope",
        font_pt=11,
        padding="3px 7px",
        border_radius=5,
        drop_down_width=18,
        border_color=p["BORDER_SUBTLE"],
    )

    assert "QComboBox#scope {" in style
    assert f"border:1px solid {p['BORDER_SUBTLE']};" in style
    assert "padding:3px 7px" in style
    assert "QComboBox#scope:hover" in style
    assert "QComboBox#scope:focus" in style
    assert "QComboBox#scope::drop-down" in style
    assert "width:18px" in style
    assert "QComboBoxPrivateContainer" in style
    assert "QComboBox QAbstractItemView::item" in style


def test_contained_list_style_supports_grouped_panel_lists():
    style = theme_module.contained_list_style(
        selector="QListWidget#first, QListWidget#second",
        item_padding="10px 12px",
        item_radius=6,
        item_margin="0px",
        border_radius=8,
    )

    assert "QListWidget#first, QListWidget#second {" in style
    assert "border-radius:8px" in style
    assert "QListWidget#first::item, QListWidget#second::item" in style
    assert "QListWidget#first::item:hover, QListWidget#second::item:hover" in style
    assert "QListWidget#first::item:selected, QListWidget#second::item:selected" in style
    assert "padding:10px 12px" in style
    assert "margin:0px" in style


def test_popover_styles_define_transient_picker_contract():
    frame = theme_module.popover_frame_style(selector="QFrame#picker")
    list_style = theme_module.popover_list_style(selector="QListWidget#pickerList")

    assert "QFrame#picker {" in frame
    assert "border-radius:8px" in frame
    assert "QListWidget#pickerList { background:transparent; border:none; outline:none; }" in list_style
    assert "QListWidget#pickerList::item:selected" in list_style
    assert f"background:{theme_module.ACCENT}; color:white;" in list_style
    assert "QListWidget#pickerList::item:selected:focus" in list_style


def test_contained_tree_style_defines_dialog_tree_contract():
    p = palette()
    style = theme_module.contained_tree_style(
        selector="QTreeWidget#packageTree",
        header_selector="QHeaderView#packageHeader::section",
        bg=p["BG3"],
        border=p["BORDER"],
    )

    assert "QTreeWidget#packageTree {" in style
    assert f"background:{p['BG3']};" in style
    assert f"border:1px solid {p['BORDER']};" in style
    assert "QTreeWidget#packageTree::item:selected:focus" in style
    assert "QHeaderView#packageHeader::section" in style
    assert "font-weight:600" in style


def test_data_table_style_defines_settings_table_contract():
    style = theme_module.data_table_style(
        selector="QTableWidget#providers",
        header_selector="QHeaderView#providersHeader::section",
        border_radius=8,
    )

    assert "QTableWidget#providers {" in style
    assert "gridline-color:transparent" in style
    assert "border-radius:8px" in style
    assert "QTableWidget#providers::item:selected" in style
    assert "QHeaderView#providersHeader::section" in style
    assert "font-weight:600" in style


def test_splitter_style_defines_subtle_resize_handle_contract():
    style = theme_module.splitter_style(selector="QSplitter#diff", handle_px=2)

    assert "QSplitter#diff {" in style
    assert "QSplitter#diff::handle" in style
    assert "width:2px" in style
    assert "height:2px" in style
    assert "QSplitter#diff::handle:hover" in style


def test_label_helpers_define_hierarchy_contracts():
    p = palette()
    title = theme_module.title_label_style(selector="QLabel#title", font_pt=18)
    section = theme_module.section_label_style(selector="QLabel#section", text_color=p["TEXT"])
    pill = theme_module.status_pill_style(selector="QLabel#pill", tone="accent", font_pt=12)

    assert "QLabel#title {" in title
    assert "font-size:18px" in title
    assert "font-weight:650" in title
    assert "QLabel#section {" in section
    assert f"color:{p['TEXT']};" in section
    assert "font-weight:600" in section
    assert "QLabel#pill {" in pill
    assert f"color:{theme_module.ACCENT};" in pill
    assert "border:1px solid" in pill
    assert "border-radius:6px" in pill


def test_checkbox_style_uses_qt_safe_rules_and_quoted_image_url():
    style = checkbox_style(
        font_pt=13,
        indicator_px=16,
        spacing_px=8,
        checked_image="C:/repo/assets/checkmark.svg",
    )

    assert "QCheckBox::indicator" in style
    assert "QCheckBox::indicator:hover" in style
    assert "width: 16px;" in style
    assert 'image: url("C:/repo/assets/checkmark.svg");' in style
    assert f"border: 1px solid {theme_module.ACCENT};" in style
    assert "SUCCESS" not in style


def test_checkbox_style_defaults_to_standard_checkmark():
    style = checkbox_style()
    no_mark_style = checkbox_style(checked_image="")

    assert 'image: url("' in style
    assert "assets/checkmark.svg" in style
    assert 'image: url("' not in no_mark_style


@pytest.mark.parametrize("theme_name", ["dark", "modern", "light"])
def test_markdown_css_code_blocks_do_not_repaint_inline_code_background(theme_name):
    css = markdown_css(14, theme_name)
    pre_rule = re.search(r"pre \{([^}]+)\}", css).group(1)
    pre_bg = re.search(r"background-color:([^;]+);", pre_rule).group(1)
    code_rule = re.search(r"(^|})code \{([^}]+)\}", css).group(2)
    code_bg = re.search(r"background-color:([^;]+);", code_rule).group(1)
    pre_code = re.search(r"pre code, pre code span \{([^}]+)\}", css).group(1)

    assert code_bg != pre_bg
    assert "padding:10px 12px;" in pre_rule
    assert "margin:12px 8px 14px 8px;" in pre_rule
    assert "border:1px solid" in code_rule
    assert f"background-color:{pre_bg};" in pre_code
    assert "border:0;" in pre_code
    assert "padding:0;" in pre_code
    assert "border-radius:0;" in pre_code


@pytest.mark.parametrize("theme_name", ["dark", "modern", "light"])
def test_markdown_css_components_have_distinct_surfaces(theme_name):
    css = markdown_css(14, theme_name)
    code_bg = re.search(r"(^|})code \{[^}]*background-color:([^;]+);", css).group(2)
    pre_bg = re.search(r"pre \{[^}]*background-color:([^;]+);", css).group(1)
    quote_rule = re.search(r"blockquote \{([^}]+)\}", css).group(1)
    table_rule = re.search(r"th,td \{([^}]+)\}", css).group(1)
    file_style = markdown_file_link_style(theme_name)

    assert code_bg != pre_bg
    assert "background-color:" in quote_rule
    assert "border-left:3px solid" in quote_rule
    assert "border:1px solid" in table_rule
    assert "border:1px solid" in file_style
    assert f"background:{code_bg};" not in file_style


@pytest.mark.parametrize("theme_name", ["dark", "modern", "light"])
def test_reference_and_code_helpers_use_distinct_surfaces(theme_name):
    tokens = _markdown_tokens(theme_name)
    code_surface = theme_module.code_surface_colors(theme_name)
    inline_code = theme_module.inline_code_style(theme_name)
    code_block = theme_module.markdown_code_block_styles(theme=theme_name)
    file_ref = markdown_file_link_style(theme_name)
    user_ref = theme_module.user_reference_style(theme_name)
    search_match = theme_module.search_match_style(theme_name)

    assert code_surface["background"] == tokens["pre_bg"]
    assert code_surface["foreground"] == tokens["code_fg"]
    assert tokens["code_bg"] in inline_code
    assert tokens["pre_bg"] in code_block["copy"]
    assert tokens["code_bg"] not in code_block["copy"]
    assert "text-align:right" in code_block["header"]
    assert tokens["file_bg"] in file_ref
    assert file_ref != user_ref
    assert "border:1px solid" in user_ref
    assert palette(theme_name)["SELECTION"] in search_match


@pytest.mark.parametrize("theme_name", ["dark", "modern", "light"])
def test_markdown_tokens_keep_text_contrast_above_wcag_aa(theme_name):
    p = palette(theme_name)
    tokens = _markdown_tokens(theme_name)
    pairs = [
        (tokens["code_fg"], tokens["code_bg"]),
        (tokens["code_fg"], tokens["pre_bg"]),
        (tokens["file_fg"], tokens["file_bg"]),
        (p["TEXT_DIM"], tokens["quote_bg"]),
        (p["TEXT"], tokens["table_bg"]),
        (p["TEXT"], tokens["table_header_bg"]),
    ]

    for fg, bg in pairs:
        assert _contrast_ratio(fg, bg) >= 4.5


def test_modern_markdown_code_block_is_neutral_inset_surface():
    p = palette("modern")
    tokens = _markdown_tokens("modern")

    assert tokens["pre_bg"] != tokens["code_bg"]
    assert _relative_luminance(tokens["pre_bg"]) < _relative_luminance(p["BG3"])
    assert _relative_luminance(tokens["pre_bg"]) < _relative_luminance(tokens["code_bg"])


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
