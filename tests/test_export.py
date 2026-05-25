import json
from pathlib import Path
from unittest.mock import patch

from services.export import (
    _fmt_ts,
    _skip_user_message,
    conversation_to_markdown,
    default_export_name,
    export_conversation_dialog,
    export_conversation_file,
)


def test_default_export_name_sanitizes():
    assert default_export_name({"title": "Hello: World!"}) == "Hello-World.md"


def test_conversation_to_markdown():
    data = {
        "title": "Chat",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-01-01T12:00:00",
        "messages": [
            {"role": "user", "content": "Hi", "created_at": "2026-01-01T12:00:01"},
            {"role": "assistant", "content": "Hello", "created_at": "2026-01-01T12:00:02"},
            {"role": "tool", "content": "ignored"},
        ],
    }
    md = conversation_to_markdown(data)
    assert "# Chat" in md
    assert "**Model:**" in md
    assert "## You" in md
    assert "## Agent" in md
    assert "Hi" in md
    assert "Hello" in md


def test_skip_user_message_tool_results_only():
    assert _skip_user_message([{"type": "tool_result", "content": "x"}])
    assert not _skip_user_message("real question")


def test_fmt_ts_invalid():
    assert _fmt_ts("not-a-date") == "not-a-date"


def test_user_blocks_with_image_and_timestamp():
    from services.export import _assistant_blocks, _user_blocks

    lines = _user_blocks(
        [{"type": "text", "text": "see"}, {"type": "image"}],
        "2026-01-01T12:00:00",
    )
    assert "*2026" in "\n".join(lines)
    assert "[Image attached]" in "\n".join(lines)
    assert "## You" in lines

    alines = _assistant_blocks("Reply", "2026-01-01T12:01:00")
    assert "## Agent" in alines

    crew_lines = _assistant_blocks("Found", "", "Scout")
    assert "## Scout" in crew_lines


def test_conversation_metadata_updated_only():
    md = conversation_to_markdown({
        "title": "T",
        "updated_at": "2026-02-01T10:00:00",
        "messages": [],
    })
    assert "**Updated:**" in md
    assert "**Created:**" not in md


def test_export_dialog_cancelled(qapp):
    with patch(
        "services.export.QFileDialog.getSaveFileName",
        return_value=("", ""),
    ):
        assert export_conversation_dialog({"title": "X", "messages": []}) is False


def test_export_dialog_writes_markdown(qapp, tmp_path):
    dest = tmp_path / "out"
    with patch(
        "services.export.QFileDialog.getSaveFileName",
        return_value=(str(dest), ""),
    ):
        ok = export_conversation_dialog(
            {"title": "Export me", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert ok is True
    assert dest.with_suffix(".md").read_text(encoding="utf-8").startswith("# Export me")


def test_export_skips_tool_only_user_messages():
    md = conversation_to_markdown({
        "title": "T",
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "content": "done"}]},
            {"role": "user", "content": "Hello"},
        ],
    })
    assert md.count("## You") == 1
    assert "Hello" in md


def test_export_uses_crew_speaker_name():
    md = conversation_to_markdown({
        "title": "T",
        "messages": [
            {
                "role": "assistant",
                "content": "Found",
                "crew": {"id": "scout", "name": "Scout"},
            },
        ],
    })
    assert "## Scout" in md


def test_export_user_blocks_skip_non_dict():
    from services.export import _user_blocks

    lines = _user_blocks(["not-a-block", {"type": "text", "text": "ok"}], "")
    assert "ok" in "\n".join(lines)


def test_skip_user_message_empty_list():
    assert _skip_user_message([])


def test_export_conversation_file(tmp_path, qapp):
    conv = tmp_path / "c.json"
    conv.write_text(
        json.dumps({"title": "From file", "messages": []}),
        encoding="utf-8",
    )
    with patch(
        "services.export.QFileDialog.getSaveFileName",
        return_value=(str(tmp_path / "saved.md"), ""),
    ):
        assert export_conversation_file(str(conv)) is True
