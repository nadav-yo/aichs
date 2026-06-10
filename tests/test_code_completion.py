from services.code_completion import LocalCompletionProvider, _completion_scan_window, prefix_at


def test_prefix_at_returns_word_fragment_before_cursor():
    content = "alpha beta_value.gamma"

    assert prefix_at(content, len(content)) == "gamma"
    assert prefix_at(content, content.index(".")) == "beta_value"


def test_local_completion_provider_returns_keywords_and_document_words():
    provider = LocalCompletionProvider()

    items = provider.complete(
        path="demo.py",
        content="def render_scene():\n    return renderer\n",
        position=len("def re"),
        prefix="re",
    )
    labels = [item.label for item in items]

    assert "return" in labels
    assert "render_scene" in labels
    assert "renderer" in labels
    assert "re" not in labels


def test_local_completion_provider_is_case_insensitive():
    provider = LocalCompletionProvider()

    items = provider.complete(
        path="demo.txt",
        content="WidgetFactory widget_count\n",
        position=3,
        prefix="wid",
    )

    assert [item.label for item in items] == ["widget_count", "WidgetFactory"]


def test_local_completion_provider_ignores_empty_prefix():
    provider = LocalCompletionProvider()

    assert provider.complete(path="demo.py", content="alpha beta", position=3, prefix="") == []


def test_completion_scan_window_clips_large_documents():
    content = "a" * 200000 + "needle" + "b" * 200000
    window = _completion_scan_window(content, 200003)

    assert "needle" in window
    assert len(window) < len(content)
