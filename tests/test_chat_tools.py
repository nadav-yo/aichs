from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.compaction import CompactionResult
from services.chat import ChatThread
from services.tool_registry import HookContext


def _anthropic_stream_with_tool():
    stream = MagicMock()
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: None
    stream.text_stream = iter([])
    stream.get_final_message.return_value = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id="tu_1",
                name="read_file",
                input={"path": "src/main.py"},
            ),
        ],
    )
    return stream


def test_loop_anthropic_runs_tool_then_finishes(workspace, qapp):
    thread = ChatThread(
        "claude-sonnet-4-6",
        [{"role": "user", "content": "read file"}],
        "sys",
        str(workspace),
    )
    calls = {"n": 0}

    def stream_side_effect(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _anthropic_stream_with_tool()
        return _anthropic_stream_with_tool_empty_text()

    mock_client = MagicMock()
    mock_client.messages.stream.side_effect = stream_side_effect

    with patch("services.chat.anthropic.Anthropic", return_value=mock_client), patch(
        "services.chat.resolve_api_key", return_value="k"
    ), patch("services.chat.run_extension_hooks"):
        text = thread._loop_anthropic()
    assert calls["n"] >= 2
    assert any(
        m.get("role") == "user" and isinstance(m.get("content"), list)
        for m in thread.history
    )
    tool_result_message = next(
        m for m in thread.history
        if m.get("role") == "user" and isinstance(m.get("content"), list)
    )
    assert tool_result_message.get("synthetic") == "tool_results"
    assert tool_result_message["content"][-1]["type"] == "text"
    assert "Active task: read file" in tool_result_message["content"][-1]["text"]
    assert text == "done"


def _anthropic_stream_with_tool_empty_text():
    stream = MagicMock()
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: None
    stream.text_stream = iter(["done"])
    stream.get_final_message.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="done")],
    )
    return stream


def test_execute_one_blocked_by_hook(workspace, qapp):
    from services.tool_registry import HookContext, run_extension_hooks
    from tests.conftest import write_extension

    write_extension(
        workspace,
        "block.py",
        """
        def register(registry):
            registry.hook("before_tool_call", block)

        def block(ctx):
            ctx.status = "error"
            ctx.error = "nope"
        """,
    )
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    tid, name, out = thread._execute_one("tu", "read_file", {"path": "src/main.py"})
    assert name == "read_file"
    assert "nope" in out or "blocked" in out.lower()


def test_before_next_model_request_directive_updates_history(workspace, qapp):
    from tests.conftest import write_extension

    write_extension(
        workspace,
        "runtime.py",
        """
        def register(registry):
            registry.hook("before_next_model_request", on_next)

        def on_next(ctx):
            return {"action": "enqueue_message", "text": "Synthetic continuation"}
        """,
    )
    thread = ChatThread(
        "claude-sonnet-4-6",
        [{"role": "user", "content": "read file"}],
        "sys",
        str(workspace),
    )
    calls = {"n": 0}

    def stream_side_effect(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _anthropic_stream_with_tool()
        return _anthropic_stream_with_tool_empty_text()

    mock_client = MagicMock()
    mock_client.messages.stream.side_effect = stream_side_effect

    with patch("services.chat.anthropic.Anthropic", return_value=mock_client), patch(
        "services.chat.resolve_api_key", return_value="k"
    ):
        assert thread._loop_anthropic() == "done"

    assert any(
        msg.get("synthetic") == "extension" and msg.get("content") == "Synthetic continuation"
        for msg in thread.history
    )


def test_compact_and_resume_directive_replaces_history(workspace, qapp):
    thread = ChatThread(
        "claude-sonnet-4-6",
        [{"role": "user", "content": "old"}, {"role": "assistant", "content": "older"}],
        "sys",
        str(workspace),
    )
    ctx = HookContext(event="before_next_model_request", cwd=str(workspace))
    ctx.compact_and_resume(resume_prompt="resume now", force=True)
    compacted = [{"role": "user", "content": "[Conversation summary]\nsummary"}]
    result = CompactionResult(
        messages=compacted,
        summary="summary",
        cut_index=2,
        status="compacted",
        proof={"version": "aicc-compaction/v1"},
    )
    events = []
    thread.runtime_event.connect(events.append)
    with patch("services.chat.compact_with_result", return_value=result):
        assert thread._apply_runtime_directives(ctx) is True
    assert thread.history[:-1] == compacted
    assert thread.history[-1]["synthetic"] == "extension_resume"
    assert events[0]["type"] == "compaction"


def test_compact_and_resume_unchanged_appends_single_resume(workspace, qapp):
    thread = ChatThread(
        "claude-sonnet-4-6",
        [{"role": "user", "content": "old [auto-continue]"}],
        "sys",
        str(workspace),
    )
    ctx = HookContext(event="before_next_model_request", cwd=str(workspace))
    ctx.compact_and_resume(resume_prompt="resume once", force=False)
    result = CompactionResult(
        messages=list(thread.history),
        summary="",
        cut_index=0,
        status="unchanged",
        proof={"version": "aicc-compaction/v1"},
    )
    events = []
    thread.runtime_event.connect(events.append)
    with patch("services.chat.compact_with_result", return_value=result):
        assert thread._apply_runtime_directives(ctx) is True
    resumes = [
        msg for msg in thread.history
        if msg.get("synthetic") == "extension_resume"
    ]
    assert len(resumes) == 1
    assert resumes[0]["content"] == "resume once"
    assert events[0]["status"] == "unchanged"


def test_compaction_directive_failure_stops(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [{"role": "user", "content": "old"}], "sys", str(workspace))
    ctx = HookContext(event="before_next_model_request", cwd=str(workspace))
    ctx.compact_now(force=True)
    events = []
    thread.runtime_event.connect(events.append)
    with patch("services.chat.compact_with_result", side_effect=ValueError("bad ledger")):
        assert thread._apply_runtime_directives(ctx) is False
    assert events[0]["type"] == "compaction_failed"
