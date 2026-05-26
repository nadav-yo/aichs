import services.model_registry as reg
from storage.settings import SettingsStore
from ui.widgets.chat_panel import ChatPanel


def test_resolve_model_unknown_uses_first_configured_provider(qapp, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg.save_user_providers({
        "ollama": {
            "api": "openai-compatible",
            "baseUrl": "http://localhost:11434/v1",
            "models": [
                {"id": "llama3.2:latest"},
                {"id": "qwen2.5:7b"},
            ],
        }
    })
    reg.reload()

    try:
        store = SettingsStore()
        store.save({
            "provider_order": ["ollama"],
            "default_models": {"ollama": "qwen2.5:7b"},
        })
        panel = type("Panel", (), {})()
        panel._settings = store
        panel._configured_providers = lambda: ChatPanel._configured_providers(panel)
        panel._default_model_for_provider = (
            lambda provider: ChatPanel._default_model_for_provider(panel, provider)
        )

        resolved = ChatPanel._resolve_model(panel, "removed-old-model")
        assert resolved == "qwen2.5:7b"
        assert reg.MODEL_PROVIDER[resolved] == "ollama"
    finally:
        reg.save_user_providers({})
        reg.reload()
