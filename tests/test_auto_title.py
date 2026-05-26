from unittest.mock import MagicMock, patch

import pytest

from services.auto_title import clean_title, fallback_title, generate_title


class TestCleanTitle:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  Hello World  ", "Hello World"),
            ('"Quoted title"', "Quoted title"),
            ("Title: My Topic", "My Topic"),
            ("line one\nline two", "line one"),
            ("x" * 80, "x" * 57 + "…"),
            ("", "Untitled"),
        ],
    )
    def test_clean_title(self, raw, expected):
        assert clean_title(raw) == expected


def test_generate_title_anthropic(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="  Refactor auth module  ")]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    with patch("services.auto_title.get_model_config") as cfg, patch(
        "services.auto_title.resolve_api_key", return_value="key"
    ), patch("services.auto_title.anthropic.Anthropic", return_value=mock_client):
        cfg.return_value = MagicMock(
            provider_id="claude",
            api="anthropic",
            api_key_spec="ANTHROPIC_API_KEY",
            base_url=None,
        )
        title = generate_title("claude-sonnet-4-6", "user msg")
    assert title == "Refactor auth module"
    prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "FIRST USER MESSAGE" in prompt
    assert "assistant" not in prompt.lower()


def test_generate_title_unknown_api_raises():
    with patch("services.auto_title.get_model_config") as cfg:
        cfg.return_value = MagicMock(
            provider_id="custom",
            api="unknown-api",
            api_key_spec="X",
            base_url=None,
        )
        with pytest.raises(ValueError, match="No title model"):
            generate_title("custom-model", "u")


def test_generate_title_openai(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="OpenAI title"))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("services.auto_title.get_model_config") as cfg, patch(
        "services.auto_title.resolve_api_key", return_value="key"
    ), patch("services.auto_title.OpenAI", return_value=mock_client):
        cfg.return_value = MagicMock(
            provider_id="openai",
            api="openai-compatible",
            api_key_spec="OPENAI_API_KEY",
            base_url="https://api.example.com",
        )
        title = generate_title("gpt-5.4-nano", "hi")
    assert title == "OpenAI title"


def test_generate_title_uses_current_model_for_custom_provider(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="Custom title"))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("services.auto_title.get_model_config") as cfg, patch(
        "services.auto_title.resolve_api_key", return_value="key"
    ), patch("services.auto_title.OpenAI", return_value=mock_client):
        cfg.return_value = MagicMock(
            provider_id="local",
            api="openai-compatible",
            api_key_spec="LOCAL_KEY",
            base_url="http://localhost:11434/v1",
        )
        title = generate_title("local-model", "hi")

    assert title == "Custom title"
    assert mock_client.chat.completions.create.call_args.kwargs["model"] == "local-model"


def test_generate_title_truncates_user_preview(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="Short")]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    with patch("services.auto_title.get_model_config") as cfg, patch(
        "services.auto_title.resolve_api_key", return_value="key"
    ), patch("services.auto_title.anthropic.Anthropic", return_value=mock_client):
        cfg.return_value = MagicMock(
            provider_id="claude",
            api="anthropic",
            api_key_spec="ANTHROPIC_API_KEY",
            base_url=None,
        )
        generate_title("claude-sonnet-4-6", "x" * 200)

    prompt = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "x" * 100 in prompt
    assert "x" * 101 not in prompt


def test_generate_title_empty_api_response_uses_fallback(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="")]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    with patch("services.auto_title.get_model_config") as cfg, patch(
        "services.auto_title.resolve_api_key", return_value="key"
    ), patch("services.auto_title.anthropic.Anthropic", return_value=mock_client):
        cfg.return_value = MagicMock(
            provider_id="claude",
            api="anthropic",
            api_key_spec="ANTHROPIC_API_KEY",
            base_url=None,
        )
        title = generate_title("claude-sonnet-4-6", "why is my auth module broken?")

    assert title == "Why Is My Auth Module Broken"


def test_generate_title_rejects_handshake_title(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="Awaiting task instructions")]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    with patch("services.auto_title.get_model_config") as cfg, patch(
        "services.auto_title.resolve_api_key", return_value="key"
    ), patch("services.auto_title.anthropic.Anthropic", return_value=mock_client):
        cfg.return_value = MagicMock(
            provider_id="claude",
            api="anthropic",
            api_key_spec="ANTHROPIC_API_KEY",
            base_url=None,
        )
        title = generate_title("claude-sonnet-4-6", "can we support maestro e2e testing here?")

    assert title == "Support Maestro E2E Testing Here"


def test_fallback_title_extracts_meaningful_words():
    assert fallback_title("can you research and plan maestro e2e testing here?") == (
        "Research Plan Maestro E2E Testing Here"
    )


def test_fallback_title_strips_mentions_and_markup():
    assert fallback_title("@Archivist **have** we discussed playwright?") == (
        "Discussed Playwright"
    )
