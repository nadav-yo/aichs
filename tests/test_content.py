from services.content import (
    build_user_content,
    content_length,
    content_preview,
    content_text,
    file_blocks,
    image_blocks,
    prepare_for_anthropic,
    prepare_for_openai,
)


def test_build_user_content_text_only():
    assert build_user_content("hi", [], []) == "hi"


def test_build_user_content_multimodal():
    blocks = build_user_content(
        "see",
        [{"media_type": "image/png", "data": "abc"}],
        [{"path": "a.py", "content": "x", "size": 1}],
    )
    types = {b["type"] for b in blocks}
    assert types == {"text", "image", "file"}


def test_content_helpers():
    blocks = [
        {"type": "text", "text": "hello"},
        {"type": "image", "data": "x" * 10},
        {"type": "file", "path": "f.py", "content": "code"},
        {"type": "tool_result", "content": "done"},
    ]
    assert content_text(blocks) == "hello"
    assert "[image]" in content_preview(blocks)
    assert "[file: f.py]" in content_preview(blocks)
    assert content_length(blocks) > 10
    assert len(image_blocks(blocks)) == 1
    assert len(file_blocks(blocks)) == 1


def test_content_length_nested():
    nested = [{"type": "tool_result", "content": [{"type": "text", "text": "ok"}]}]
    assert content_length(nested) > 0


def test_prepare_for_providers():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image", "media_type": "image/png", "data": "abc"},
            ],
        }
    ]
    anthropic = prepare_for_anthropic(messages)
    assert anthropic[0]["content"][1]["type"] == "image"
    assert "source" in anthropic[0]["content"][1]

    openai = prepare_for_openai(messages)
    assert openai[0]["content"][1]["type"] == "image_url"
