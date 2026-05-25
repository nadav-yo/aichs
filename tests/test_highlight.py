from services.highlight import for_language, for_path


def test_for_path_python():
    html = for_path("print(1)\n", "main.py")
    assert "<pre" in html.lower() or "print" in html


def test_for_language_plain():
    html = for_language("hello", "text")
    assert "hello" in html
