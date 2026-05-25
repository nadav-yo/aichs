from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.chat import ChatThread


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
