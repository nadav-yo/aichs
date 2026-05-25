import pytest

from ui import theme


@pytest.mark.parametrize(
    "func,args",
    [
        (theme.card_frame_style, ()),
        (theme.tool_notice_style, ()),
        (theme.center_notice_style, ()),
        (theme.input_bar_style, ()),
        (theme.send_button_style, ()),
        (theme.stop_button_style, ()),
        (theme.floating_button_style, ()),
        (theme.new_chat_button_style, ()),
        (theme.icon_button_style, (28,)),
        (theme.sidebar_section_label_style, ()),
        (theme.git_changes_list_style, ()),
        (theme.file_tree_sidebar_style, ()),
        (theme.files_header_style, ()),
        (theme.search_field_style, ()),
        (theme.conversation_list_style, ()),
        (theme.timestamp_style, ()),
        (theme.list_selection_bg, ("light",)),
        (theme.markdown_file_link_style, ("dark",)),
        (theme.composer_style, (14,)),
        (theme.edit_bubble_style, (14,)),
    ],
)
def test_theme_style_helpers_return_css(func, args, qapp):
    css = func(*args)
    assert isinstance(css, str)
    assert len(css) >= 4


def test_app_and_mono_font(qapp):
    font = theme.app_font("medium")
    assert font.pointSize() > 0
    mono = theme.mono_font(12)
    assert mono.family()
