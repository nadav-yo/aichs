from unittest.mock import MagicMock, patch

from services.compaction import _call_model, compact


def test_call_model_anthropic():
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="summary")]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp
    with patch("services.compaction.get_model_config") as cfg, patch(
        "services.compaction.resolve_api_key", return_value="k"
    ), patch("services.compaction._anthropic_client", return_value=mock_client):
        cfg.return_value = MagicMock(api="anthropic", api_key_spec="ANTHROPIC_API_KEY", base_url=None)
        assert _call_model("claude-sonnet-4-6", "prompt", 4096) == "summary"


def test_call_model_openai():
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="openai summary"))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    with patch("services.compaction.get_model_config") as cfg, patch(
        "services.compaction.resolve_api_key", return_value="k"
    ), patch("services.compaction._openai_client", return_value=mock_client) as openai_cls:
        cfg.return_value = MagicMock(
            api="openai-compatible",
            api_key_spec="OPENAI_API_KEY",
            base_url="https://api.example.com/v1",
            temperature=0.6,
            top_k=20,
            min_p=0.05,
        )
        assert _call_model("gpt-5.4-nano", "prompt", 4096) == "openai summary"
        assert openai_cls.call_args.kwargs["base_url"] == "https://api.example.com/v1"
        request = mock_client.chat.completions.create.call_args.kwargs
        assert request["temperature"] == 0.6
        assert request["extra_body"] == {"top_k": 20, "min_p": 0.05}


def test_compact_noop_when_nothing_to_cut():
    messages = [{"role": "user", "content": "hi"}]
    assert compact("claude-sonnet-4-6", messages) == messages
