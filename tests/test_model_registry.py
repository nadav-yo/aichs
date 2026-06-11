import json
import os
from unittest.mock import MagicMock, patch

import pytest

import services.model_registry as reg


def test_api_key_env_var():
    assert reg.api_key_env_var("OPENAI_API_KEY") == "OPENAI_API_KEY"
    assert reg.api_key_env_var("!cmd") is None
    assert reg.api_key_env_var("sk-test") is None


def test_resolve_api_key_env_and_literal(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert reg.resolve_api_key("OPENAI_API_KEY") == "from-env"
    assert reg.resolve_api_key("sk-literal") == "sk-literal"


def test_resolve_api_key_command(monkeypatch):
    mock = MagicMock(returncode=0, stdout="cmd-key\n", stderr="")
    with patch("services.model_registry.subprocess.run", return_value=mock):
        assert reg.resolve_api_key("!echo key") == "cmd-key"


def test_get_model_config_builtin():
    cfg = reg.get_model_config("claude-sonnet-4-6")
    assert cfg.api == "anthropic"
    assert cfg.provider_id == "claude"


def test_get_model_config_unknown_fallback():
    cfg = reg.get_model_config("does-not-exist")
    assert cfg.display_name == "unknown"


def test_normalize_model_id_strips_context_suffix():
    assert reg.normalize_model_id("qwen2.5-coder:7b @ 65536") == "qwen2.5-coder:7b"


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
    assert reg.get_model_config("claude-sonnet-4-6").context_window is None
    assert reg.context_window_tokens("claude-sonnet-4-6") == 180_000


def test_generation_params_honor_provider_defaults_and_model_overrides(tmp_path, monkeypatch):
    path = tmp_path / ".aichs" / "models.json"
    monkeypatch.setattr(reg, "_MODELS_PATH", path)
    reg.save_user_providers({
        "local": {
            "api": "openai-compatible",
            "apiKey": "LOCAL_KEY",
            "baseUrl": "http://localhost:11434/v1",
            "temperature": 0.6,
            "topK": -1,
            "minP": 0.05,
            "models": [
                {"id": "model-a", "name": "Model A"},
                {"id": "model-b", "temperature": 0.2, "topK": 8, "minP": 0.1},
            ],
        }
    })
    reg.reload()

    cfg_a = reg.get_model_config("model-a")
    cfg_b = reg.get_model_config("model-b")
    assert (cfg_a.temperature, cfg_a.top_k, cfg_a.min_p) == (0.6, -1, 0.05)
    assert (cfg_b.temperature, cfg_b.top_k, cfg_b.min_p) == (0.2, 8, 0.1)


def test_load_save_user_providers(tmp_path, monkeypatch):
    path = tmp_path / ".aichs" / "models.json"
    monkeypatch.setattr(reg, "_MODELS_PATH", path)
    reg.save_user_providers({
        "local": {
            "api": "openai-compatible",
            "apiKey": "LOCAL_KEY",
            "baseUrl": "http://localhost:11434/v1",
            "models": [{"id": "llama-test", "name": "Llama Test"}],
        }
    })
    loaded = reg.load_user_providers()
    assert "local" in loaded
    reg.reload()
    assert "llama-test" in reg.MODELS.get("local", [])


def test_builtin_provider_model_order_can_be_overridden():
    original = list(reg.MODELS["claude"])
    reordered = [original[1], original[0], *original[2:]]
    try:
        reg.save_user_providers({
            "claude": {
                "api": "anthropic",
                "apiKey": "ANTHROPIC_API_KEY",
                "models": [{"id": model_id} for model_id in reordered],
            }
        })
        reg.reload()
        assert reg.MODELS["claude"] == reordered
        assert reg.get_model_config(reordered[0]).display_name
    finally:
        reg.save_user_providers({})
        reg.reload()


def test_reload_can_skip_anthropic_context_refresh(tmp_path, monkeypatch):
    path = tmp_path / ".aichs" / "models.json"
    monkeypatch.setattr(reg, "_MODELS_PATH", path)
    monkeypatch.setattr(
        reg,
        "_fetch_anthropic_context_window",
        lambda _cfg, _model_id: (_ for _ in ()).throw(AssertionError("remote refresh")),
    )
    monkeypatch.setattr(reg, "_ANTHROPIC_CONTEXT", {"old": 1})

    reg.reload(refresh_anthropic=False)

    assert reg._ANTHROPIC_CONTEXT == {}


def test_stale_anthropic_context_refresh_does_not_apply(monkeypatch):
    cfg = reg.ModelConfig(
        provider_id="claude",
        api="anthropic",
        base_url=None,
        api_key_spec="ANTHROPIC_API_KEY",
        display_name="Claude",
    )
    monkeypatch.setattr(reg, "_ANTHROPIC_CONTEXT", {"old": 1})
    monkeypatch.setattr(reg, "_CONTEXT_REFRESH_GENERATION", 2)
    monkeypatch.setattr(reg, "_fetch_anthropic_context_window", lambda _cfg, _model_id: 200_000)

    reg._refresh_anthropic_context_cache({"claude-test": cfg}, generation=1)

    assert reg._ANTHROPIC_CONTEXT == {"old": 1}


def test_merge_reorders_partial_builtin_models_and_keeps_unspecified_models():
    merged = reg._merge(
        {
            "local": {
                "api": "anthropic",
                "api_key_spec": "OLD_KEY",
                "models": [
                    {"id": "model-a", "name": "Model A"},
                    {"id": "model-b", "name": "Model B"},
                ],
            }
        },
        {
            "local": {
                "api": "openai-compatible",
                "apiKey": "NEW_KEY",
                "baseUrl": "http://localhost:11434/v1",
                "models": [
                    {"id": "model-b", "name": "Better B"},
                    {"id": "model-b"},
                    {},
                ],
            }
        },
    )

    provider = merged["local"]
    assert provider["api"] == "openai-compatible"
    assert provider["api_key_spec"] == "NEW_KEY"
    assert provider["base_url"] == "http://localhost:11434/v1"
    assert provider["models"] == [
        {"id": "model-b", "name": "Better B"},
        {"id": "model-a", "name": "Model A"},
    ]


def test_merge_skips_invalid_api(isolate_aichs_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(
        json.dumps({"providers": {"bad": {"api": "invalid", "models": []}}}),
        encoding="utf-8",
    )
    reg.reload()
    assert "bad" not in reg.MODELS
