from unittest.mock import MagicMock, patch

import pytest

from services.auto_title import clean_title, generate_title


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
        cfg.return_value = MagicMock(api="anthropic", api_key_spec="ANTHROPIC_API_KEY", base_url=None)
        title = generate_title("claude-sonnet-4-6", "user msg", "assistant msg")
    assert title == "Refactor auth module"


def test_generate_title_unknown_api_raises():
    with patch("services.auto_title.get_model_config") as cfg:
        cfg.return_value = MagicMock(api="unknown-api", api_key_spec="X", base_url=None)
        with pytest.raises(ValueError, match="No title model"):
            generate_title("custom-model", "u", "a")


def test_generate_title_openai(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="OpenAI title"))]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("services.auto_title.get_model_config") as cfg, patch(
        "services.auto_title.resolve_api_key", return_value="key"
    ), patch("services.auto_title.OpenAI", return_value=mock_client):
        cfg.return_value = MagicMock(
            api="openai-compatible",
            api_key_spec="OPENAI_API_KEY",
            base_url="https://api.example.com",
        )
        title = generate_title("gpt-5.4-nano", "hi", "there")
    assert title == "OpenAI title"
