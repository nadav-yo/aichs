from services.content import (
    build_user_content,
    compact_ephemeral_attachments,
    content_length,
    content_preview,
    content_text,
    file_blocks,
    image_blocks,
    is_visible_message,
    prepare_for_anthropic,
    prepare_for_openai,
    prepare_for_storage,
)
from services.terminal_refs import build_terminal_summary, expand_terminal_refs


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
    assert all(b.get("ephemeral") for b in blocks if b["type"] in {"image", "file"})


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
    assert content_text(None) == ""
    assert content_preview(None) == ""
    assert content_length(None) == 0


def test_content_length_nested():
    nested = [{"type": "tool_result", "content": [{"type": "text", "text": "ok"}]}]
    assert content_length(nested) > 0


def test_prepare_for_providers():
    messages = [
        {
            "role": "user",
            "created_at": "ignored",
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
    assert "created_at" not in openai[0]


def test_prepare_anthropic_preserves_tool_results_with_anchor_text():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "file"},
                {"type": "text", "text": "Continue the active task."},
            ],
        }
    ]
    anthropic = prepare_for_anthropic(messages)
    assert anthropic[0]["content"][0]["type"] == "tool_result"
    assert anthropic[0]["content"][1]["type"] == "text"


def test_prepare_prefixes_crew_assistant_for_model_context():
    messages = [
        {
            "role": "assistant",
            "content": "found it",
            "crew": {"id": "scout", "name": "Scout"},
            "created_at": "ignored",
        }
    ]
    assert prepare_for_anthropic(messages)[0] == {
        "role": "assistant",
        "content": "Scout: found it",
    }
    assert prepare_for_openai(messages)[0] == {
        "role": "assistant",
        "content": "Scout: found it",
    }


def test_prepare_skips_crew_bubble_after_lead_synthesis():
    messages = [
        {"role": "user", "content": "check"},
        {"role": "assistant", "content": "found it", "crew": {"id": "scout", "name": "Scout"}},
        {"role": "assistant", "content": "summary"},
        {"role": "user", "content": "next"},
    ]
    anthropic = prepare_for_anthropic(messages)
    assert [m["content"] for m in anthropic] == ["check", "summary", "next"]


def test_prepare_keeps_direct_crew_reply_without_lead_synthesis():
    messages = [
        {"role": "user", "content": "@Scout check"},
        {"role": "assistant", "content": "found it", "crew": {"id": "scout", "name": "Scout"}},
        {"role": "user", "content": "explain"},
    ]
    openai = prepare_for_openai(messages)
    assert openai[1]["content"] == "Scout: found it"


def test_compact_ephemeral_attachments_removes_payloads():
    messages = [
        {
            "role": "user",
            "content": build_user_content(
                "look",
                [{"media_type": "image/png", "data": "abc" * 100}],
                [{"path": "a.py", "content": "print('x')", "size": 10}],
            ),
        }
    ]

    compacted = compact_ephemeral_attachments(messages)

    assert messages[0]["content"][1]["content"] == "print('x')"
    blocks = compacted[0]["content"]
    assert blocks[1]["type"] == "file"
    assert blocks[1]["content"] == ""
    assert blocks[1]["omitted_after_turn"] is True
    assert blocks[2]["type"] == "text"
    assert "Image attachment omitted" in blocks[2]["text"]
    assert content_length(compacted[0]["content"]) < content_length(messages[0]["content"])


def test_prepare_for_storage_removes_runtime_only_messages():
    messages = [
        {"role": "user", "content": "real"},
        {
            "role": "user",
            "synthetic": "tool_results",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "missing"},
                {"type": "text", "text": "Continue the active user task.", "internal": True},
            ],
        },
        {"role": "user", "content": "guard instruction", "synthetic": "extension"},
        {"role": "user", "content": "resume", "synthetic": "extension_resume"},
        {"role": "user", "content": "anchor", "synthetic": "active_task"},
    ]

    stored = prepare_for_storage(messages)

    assert [msg.get("synthetic") for msg in stored] == [None, "tool_results"]
    assert stored[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "missing"}
    ]


def test_is_visible_message_hides_runtime_internals():
    assert is_visible_message({"role": "user", "content": "real"})
    assert not is_visible_message({"role": "tool", "content": "result"})
    assert not is_visible_message({
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "function": {"name": "read_file"}}],
    })
    assert not is_visible_message({
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "tu_1", "name": "read_file"}],
    })
    assert is_visible_message({
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I'll check."},
            {"type": "tool_use", "id": "tu_1", "name": "read_file"},
        ],
    })
    assert not is_visible_message({"role": "user", "synthetic": "tool_results"})
    assert not is_visible_message({"role": "user", "synthetic": "extension"})
    assert not is_visible_message({"role": "user", "synthetic": "chat_refs"})


def test_prepare_file_block_notes_omitted_attachment():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "again"},
                {
                    "type": "file",
                    "path": "a.py",
                    "content": "",
                    "size": 42,
                    "omitted_after_turn": True,
                },
            ],
        }
    ]

    anthropic = prepare_for_anthropic(messages)
    assert "content omitted after the original turn" in anthropic[0]["content"][1]["text"]


def test_terminal_summary_uses_reference_not_full_context():
    output = "\n".join(f"line {i}" for i in range(1, 51))
    summary = build_terminal_summary({
        "command": "pytest -q",
        "exit_code": 0,
        "duration_s": 1.2,
        "line_count": 50,
        "stored_line_count": 50,
        "output": output,
    })

    assert "Output reference: !term[1:50]" in summary
    assert "Command: pytest -q" in summary
    assert "line 1" not in summary
    assert "line 31" not in summary


def test_terminal_refs_expand_into_model_context():
    messages = [
        {
            "role": "assistant",
            "synthetic": "terminal_result",
            "content": "Terminal summary\nReference: !term[1:4]",
            "terminal": {
                "command": "pytest -q",
                "output": "one\ntwo\nthree\nfour",
                "stored_line_count": 4,
            },
        },
        {"role": "user", "content": "explain !term[2:3]"},
    ]

    anthropic = prepare_for_anthropic(messages)
    assert "one" not in anthropic[1]["content"]
    assert "two\nthree" in anthropic[1]["content"]
    assert "from command: pytest -q" in anthropic[1]["content"]


def test_terminal_refs_expand_exact_line_without_shift():
    messages = [
        {
            "role": "assistant",
            "synthetic": "terminal_result",
            "content": "Terminal summary\nOutput reference: !term[1:2]",
            "terminal": {
                "command": "dir",
                "output": (
                    "-a---          25/05/2026    13:53            223 pytest.ini\n"
                    "-a---          27/05/2026    23:02           3736 README.md"
                ),
                "stored_line_count": 2,
            },
        },
        {"role": "user", "content": "can you read this file? !term[2:2]"},
    ]

    anthropic = prepare_for_anthropic(messages)

    assert "README.md" in anthropic[1]["content"]
    assert "pytest.ini" not in anthropic[1]["content"]


def test_expand_terminal_refs_without_previous_terminal_is_empty():
    assert expand_terminal_refs("see !term[1:2]", []) == ""
