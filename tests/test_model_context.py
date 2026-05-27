from unittest.mock import MagicMock, patch

import services.model_registry as reg
from ui.widgets.settings_dialog import (
    _apply_legacy_provider_context,
    _models_to_text,
    _parse_models,
)


def test_parse_models_with_context_window():
    models = _parse_models(
        "llama3.1:8b = Llama 3.1 8B @ 32768\n"
        "qwen2.5-coder:7b @ 65536"
    )
    assert models == [
        {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "contextWindow": 32768},
        {"id": "qwen2.5-coder:7b", "contextWindow": 65536},
    ]


def test_models_to_text_includes_context_for_custom():
    text = _models_to_text(
        [{"id": "llama-test", "name": "Llama", "contextWindow": 8192}],
        include_context=True,
    )
    assert text == "llama-test = Llama @ 8192"


def test_apply_legacy_provider_context_migrates_provider_default():
    models = _apply_legacy_provider_context(
        [{"id": "llama-test", "name": "Llama"}],
        8192,
    )
    assert models[0]["contextWindow"] == 8192


def test_context_window_tokens_honors_per_model_override(tmp_path, monkeypatch):
    path = tmp_path / ".aichs" / "models.json"
    monkeypatch.setattr(reg, "_MODELS_PATH", path)
    reg.save_user_providers({
        "ollama": {
            "api": "openai-compatible",
            "apiKey": "ollama",
            "baseUrl": "http://localhost:11434/v1",
            "models": [{"id": "llama-test", "name": "Llama", "contextWindow": 8192}],
        }
    })
    reg.reload()
    assert reg.context_window_tokens("llama-test") == 8192
    assert reg.get_model_config("llama-test").provider_id == "ollama"


def test_context_window_tokens_uses_provider_fallback_for_legacy_json(tmp_path, monkeypatch):
    path = tmp_path / ".aichs" / "models.json"
    monkeypatch.setattr(reg, "_MODELS_PATH", path)
    reg.save_user_providers({
        "ollama": {
            "api": "openai-compatible",
            "apiKey": "ollama",
            "baseUrl": "http://localhost:11434/v1",
            "contextWindow": 16384,
            "models": [{"id": "llama-test", "name": "Llama"}],
        }
    })
    reg.reload()
    assert reg.context_window_tokens("llama-test") == 16384


def test_context_window_tokens_uses_anthropic_sdk_cache(monkeypatch):
    monkeypatch.setattr(reg, "_ANTHROPIC_CONTEXT", {"claude-sonnet-4-6": 200_000})
    assert reg.context_window_tokens("claude-sonnet-4-6") == 200_000
    assert reg.get_model_config("claude-sonnet-4-6").context_window is None


def test_refresh_anthropic_context_cache(monkeypatch):
    monkeypatch.setattr(reg, "_ANTHROPIC_CONTEXT", {})
    mock_info = MagicMock(max_input_tokens=200_000)
    mock_client = MagicMock()
    mock_client.models.retrieve.return_value = mock_info
    with patch.object(reg, "resolve_api_key", return_value="test-key"), patch(
        "anthropic.Anthropic", return_value=mock_client,
    ):
        reg._refresh_anthropic_context_cache()
    assert reg._ANTHROPIC_CONTEXT["claude-sonnet-4-6"] == 200_000
    mock_client.models.retrieve.assert_any_call("claude-sonnet-4-6")
