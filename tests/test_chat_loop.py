from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.chat import ChatThread


def _stream_mock(text="hi", tool_blocks=None):
    stream = MagicMock()
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: None
    stream.text_stream = iter([text])
    message = SimpleNamespace(
        content=tool_blocks or [SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=10,
            cache_read_input_tokens=20,
            cache_creation_input_tokens=0,
            output_tokens=3,
        ),
    )
    stream.get_final_message.return_value = message
    return stream


def test_loop_anthropic_text_only(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [{"role": "user", "content": "hi"}], "sys", str(workspace))
    thread._model_cfg = SimpleNamespace(
        provider_id="claude",
        api="anthropic",
        api_key_spec="ANTHROPIC_API_KEY",
        base_url=None,
        temperature=0.4,
        top_k=20,
        min_p=0.05,
    )
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _stream_mock("answer")

    with patch("services.chat.anthropic.Anthropic", return_value=mock_client), patch(
        "services.chat.resolve_api_key", return_value="k"
    ), patch("services.chat.run_extension_hooks"):
        text = thread._loop_anthropic()
    assert text == "answer"
    assert thread.last_usage["input_tokens"] == 30
    assert thread.last_usage["cached_input_tokens"] == 20
    request = mock_client.messages.stream.call_args.kwargs
    assert request["temperature"] == 0.4
    assert "extra_body" not in request


def test_loop_openai_text_only(workspace, qapp):
    thread = ChatThread("gpt-5.4-nano", [{"role": "user", "content": "hi"}], "sys", str(workspace))
    thread._model_cfg = SimpleNamespace(
        provider_id="local",
        api="openai-compatible",
        api_key_spec="OPENAI_API_KEY",
        base_url="http://localhost:11434/v1",
        temperature=0.6,
        top_k=20,
        min_p=0.05,
    )

    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=MagicMock(content="yo", tool_calls=None))]

    stream = MagicMock()
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: None
    usage_chunk = MagicMock()
    usage_chunk.choices = []
    usage_chunk.usage = {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "total_tokens": 105,
        "prompt_tokens_details": {"cached_tokens": 80},
    }
    stream.__iter__ = lambda s: iter([chunk, usage_chunk])

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = stream

    with patch("services.chat.OpenAI", return_value=mock_client), patch(
        "services.chat.resolve_api_key", return_value="k"
    ), patch("services.chat.run_extension_hooks"):
        text = thread._loop_openai()
    assert text == "yo"
    assert thread.last_usage["cached_input_tokens"] == 80
    assert thread.last_usage["output_tokens"] == 5
    request = mock_client.chat.completions.create.call_args.kwargs
    assert request["temperature"] == 0.6
    assert request["extra_body"] == {"top_k": 20, "min_p": 0.05}


def _openai_stream(*deltas):
    stream = MagicMock()
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: None
    chunks = []
    for delta in deltas:
        chunk = MagicMock()
        chunk.choices = [SimpleNamespace(delta=delta)]
        chunk.usage = None
        chunks.append(chunk)
    stream.__iter__ = lambda s: iter(chunks)
    return stream


def test_loop_openai_strips_chatml_token_content_before_native_tool_call(workspace, qapp):
    thread = ChatThread(
        "gpt-5.4-nano",
        [{"role": "user", "content": "list files"}],
        "sys",
        str(workspace),
    )
    chunks = []
    thread.chunk.connect(chunks.append)
    tool_call = SimpleNamespace(
        index=0,
        id="call_1",
        function=SimpleNamespace(
            name="list_files",
            arguments='{"directory": ".", "glob": "*.py"}',
        ),
    )

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        _openai_stream(SimpleNamespace(content="\n\n<|im_end|>\n", tool_calls=[tool_call])),
        _openai_stream(SimpleNamespace(content="done", tool_calls=None)),
    ]

    with patch("services.chat.OpenAI", return_value=mock_client), patch(
        "services.chat.resolve_api_key", return_value="k"
    ), patch("services.chat.run_extension_hooks"):
        text = thread._loop_openai()

    assert text == "done"
    assert chunks == ["done"]
    assert thread.history[1]["role"] == "assistant"
    assert thread.history[1]["content"] is None
    assert thread.history[1]["tool_calls"][0]["id"] == "call_1"


def test_cancel_during_run(workspace, qapp):
    thread = ChatThread("claude-sonnet-4-6", [], "sys", str(workspace))
    thread.cancel()
    assert thread._cancel.is_set()
    thread._approval_bus = None
    thread.cancel()  # no bus
