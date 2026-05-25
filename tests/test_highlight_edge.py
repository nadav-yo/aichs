from services.highlight import for_language, for_path


def test_for_path_unknown_extension():
    html = for_path("plain text", "file.unknownext123")
    assert "plain text" in html


def test_for_language_invalid():
    html = for_language("x = 1", "not-a-real-language-xyz")
    assert "x = 1" in html
