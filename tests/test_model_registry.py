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


def test_load_save_user_providers(tmp_path, monkeypatch):
    path = tmp_path / ".aicc" / "models.json"
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


def test_merge_skips_invalid_api(isolate_aicc_home):
    from config import SETTINGS_PATH

    SETTINGS_PATH.write_text(
        json.dumps({"providers": {"bad": {"api": "invalid", "models": []}}}),
        encoding="utf-8",
    )
    reg.reload()
    assert "bad" not in reg.MODELS
