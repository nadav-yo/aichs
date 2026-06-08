from services.key_bindings import shortcut_sequences


def test_default_file_search_shortcuts_include_control_and_meta():
    assert shortcut_sequences("file_search") == ("Ctrl+P", "Meta+P")


def test_default_text_search_shortcuts_include_control_and_meta():
    assert shortcut_sequences("text_search") == ("Ctrl+Shift+F", "Meta+Shift+F")


def test_shortcut_sequences_accept_custom_string():
    saved = {"keyboard_shortcuts": {"file_search": "Alt+P"}}

    assert shortcut_sequences("file_search", saved) == ("Alt+P",)


def test_shortcut_sequences_accept_custom_list_and_skip_blanks():
    saved = {"keyboard_shortcuts": {"file_search": ["Alt+P", "", "Ctrl+Shift+P"]}}

    assert shortcut_sequences("file_search", saved) == ("Alt+P", "Ctrl+Shift+P")
