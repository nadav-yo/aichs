from unittest.mock import MagicMock, patch

from services.compaction import _call_model, compact


def test_call_model_anthropic():
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="summary")]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp
    with patch("services.compaction.get_model_config") as cfg, patch(
        "services.compaction.resolve_api_key", return_value="k"
    ), patch("services.compaction.anthropic.Anthropic", return_value=mock_client):
        cfg.return_value = MagicMock(api="anthropic", api_key_spec="ANTHROPIC_API_KEY", base_url=None)
        assert _call_model("claude-sonnet-4-6", "prompt") == "summary"


def test_call_model_openai():
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="openai summary"))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    with patch("services.compaction.get_model_config") as cfg, patch(
        "services.compaction.resolve_api_key", return_value="k"
    ), patch("services.compaction.OpenAI", return_value=mock_client) as openai_cls:
        cfg.return_value = MagicMock(
            api="openai-compatible",
            api_key_spec="OPENAI_API_KEY",
            base_url="https://api.example.com/v1",
        )
        assert _call_model("gpt-5.4-nano", "prompt") == "openai summary"
        assert openai_cls.call_args.kwargs["base_url"] == "https://api.example.com/v1"


def test_compact_noop_when_nothing_to_cut():
    messages = [{"role": "user", "content": "hi"}]
    assert compact("claude-sonnet-4-6", messages) == messages
