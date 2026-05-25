import services.model_registry as reg
from storage.settings import SettingsStore
from ui.widgets.settings_dialog import SettingsDialog


def _model_ids(models: list[dict]) -> list[str]:
    return [model["id"] for model in models]


def _move_model(dialog: SettingsDialog, source: int, dest: int) -> None:
    item = dialog.model_order_list.takeItem(source)
    dialog.model_order_list.insertItem(dest, item)
    dialog._apply_model_order()


def _provider_row(dialog: SettingsDialog, provider_id: str) -> int:
    for row, provider in enumerate(dialog._providers):
        if provider["id"] == provider_id:
            return row
    raise AssertionError(f"provider not found: {provider_id}")


def test_model_order_drag_updates_provider_order_without_default_column(qapp, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg.save_user_providers({
        "local": {
            "api": "openai-compatible",
            "apiKey": "LOCAL_KEY",
            "baseUrl": "http://localhost:11434/v1",
            "models": [
                {"id": "model-a", "name": "Model A"},
                {"id": "model-b", "name": "Model B"},
                {"id": "model-c", "name": "Model C"},
            ],
        }
    })
    reg.reload()

    try:
        store = SettingsStore()
        store.save({"provider_api_keys": {"local": "test-key"}})
        dialog = SettingsDialog(store)
        row = _provider_row(dialog, "local")

        dialog.providers_table.selectRow(row)
        _move_model(dialog, 2, 0)

        assert dialog.providers_table.columnCount() == 4
        assert _model_ids(dialog._providers[row]["models"]) == [
            "model-c", "model-a", "model-b",
        ]
        assert not dialog.model_order_list.item(0).icon().isNull()
    finally:
        reg.save_user_providers({})
        reg.reload()


def test_save_preserves_existing_default_after_reorder(qapp, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg.save_user_providers({
        "local": {
            "api": "openai-compatible",
            "apiKey": "LOCAL_KEY",
            "baseUrl": "http://localhost:11434/v1",
            "models": [
                {"id": "model-a"},
                {"id": "model-b"},
                {"id": "model-c"},
            ],
        }
    })
    reg.reload()

    try:
        store = SettingsStore()
        store.save({
            "provider_api_keys": {"local": "test-key"},
            "default_models": {"local": "model-b"},
        })
        dialog = SettingsDialog(store)
        row = _provider_row(dialog, "local")

        dialog.providers_table.selectRow(row)
        _move_model(dialog, 2, 0)
        dialog._save()

        assert _model_ids(dialog._providers[row]["models"]) == [
            "model-c", "model-a", "model-b",
        ]
        assert store.load()["default_models"]["local"] == "model-b"
    finally:
        reg.save_user_providers({})
        reg.reload()


def test_builtin_model_order_is_saved_as_provider_override(qapp, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reg.save_user_providers({})
    reg.reload()
    original = list(reg.MODELS["claude"])

    try:
        store = SettingsStore()
        store.save({"provider_api_keys": {"claude": "test-key"}})
        dialog = SettingsDialog(store)
        row = _provider_row(dialog, "claude")

        dialog.providers_table.selectRow(row)
        _move_model(dialog, 1, 0)
        dialog._save()

        user_providers = reg.load_user_providers()
        assert _model_ids(user_providers["claude"]["models"])[:2] == [
            original[1], original[0],
        ]
        assert store.load()["default_models"] == {}
    finally:
        reg.save_user_providers({})
        reg.reload()


def test_model_order_list_disables_without_provider(qapp):
    dialog = SettingsDialog(SettingsStore())

    dialog._refresh_model_order_list(-1)

    assert not dialog.model_order_list.isEnabled()
    assert dialog.model_order_list.count() == 0
