from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.chat import ChatThread


def _stream_mock(text="hi", tool_blocks=None):
    stream = MagicMock()
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: None
    stream.text_stream = iter([text])
    message = SimpleNamespace(
        content=tool_blocks or [SimpleNamespace(type="text", text=text)],
    )
    stream.get_final_message.return_value = message
    return stream


def test_loop_anthropic_text_only(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [{"role": "user", "content": "hi"}], "sys", str(workspace))
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _stream_mock("answer")

    with patch("services.chat.anthropic.Anthropic", return_value=mock_client), patch(
        "services.chat.resolve_api_key", return_value="k"
    ), patch("services.chat.run_extension_hooks"):
        text = thread._loop_anthropic()
    assert text == "answer"


def test_loop_openai_text_only(workspace, qapp):
    thread = ChatThread("gpt-5.4-nano", [{"role": "user", "content": "hi"}], "sys", str(workspace))

    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=MagicMock(content="yo", tool_calls=None))]

    stream = MagicMock()
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: None
    stream.__iter__ = lambda s: iter([chunk])

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = stream

    with patch("services.chat.OpenAI", return_value=mock_client), patch(
        "services.chat.resolve_api_key", return_value="k"
    ), patch("services.chat.run_extension_hooks"):
        text = thread._loop_openai()
    assert text == "yo"


def test_cancel_during_run(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    thread.cancel()
    assert thread._cancel.is_set()
    thread._approval_bus = None
    thread.cancel()  # no bus
