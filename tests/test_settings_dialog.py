import services.model_registry as reg
from services.commit_message import COMMIT_MESSAGE_PROMPT_ADDITION_KEY
from storage.settings import (
    FILE_EDITOR_AUTO_SAVE_KEY,
    FILE_EDITOR_TAB_SPACES_KEY,
    TRASH_RETENTION_DAYS_KEY,
    SettingsStore,
)
from ui.widgets.settings_dialog import SettingsDialog, _ProviderDialog
from ui.widgets.chat_panel import ChatPanel
from PyQt6.QtWidgets import QAbstractItemView


def _model_ids(models: list[dict]) -> list[str]:
    return [model["id"] for model in models]


def _move_model(dialog: SettingsDialog, source: int, dest: int) -> None:
    item = dialog.model_order_list.takeItem(source)
    dialog.model_order_list.insertItem(dest, item)
    dialog._apply_model_order()


def _move_provider(dialog: SettingsDialog, source: int, dest: int) -> None:
    dialog._move_provider(source, dest)


def _provider_row(dialog: SettingsDialog, provider_id: str) -> int:
    for row, provider in enumerate(dialog._providers):
        if provider["id"] == provider_id:
            return row
    raise AssertionError(f"provider not found: {provider_id}")


def test_provider_dialog_resizes_models_for_selected_provider_type(qapp):
    styles = {"hint": "", "btn": "", "field": "", "label": ""}
    dialog = _ProviderDialog(styles, set())
    try:
        dialog.show()
        qapp.processEvents()

        anthropic_height = dialog.models.height()
        assert dialog.hint.geometry().top() > dialog.models.geometry().bottom()

        dialog.kind.setCurrentIndex(dialog.kind.findData("openai"))
        qapp.processEvents()
        openai_height = dialog.models.height()
        assert dialog.hint.geometry().top() > dialog.models.geometry().bottom()

        dialog.kind.setCurrentIndex(dialog.kind.findData("custom"))
        dialog.layout().activate()
        qapp.processEvents()

        assert anthropic_height < openai_height
        assert dialog.models.height() < openai_height
        assert dialog.hint.geometry().top() > dialog.models.geometry().bottom()
    finally:
        dialog.close()


def test_provider_dialog_generation_params_have_tooltips_and_values(qapp):
    styles = {"hint": "", "btn": "", "field": "", "label": ""}
    dialog = _ProviderDialog(styles, set())
    try:
        dialog.kind.setCurrentIndex(dialog.kind.findData("custom"))
        dialog.provider_id.setText("local")
        dialog.models.setPlainText("model-a")
        dialog.temperature.setValue(0.6)
        dialog.top_k.setText("20")
        dialog.min_p.setValue(0.05)

        value = dialog.value()

        assert dialog.temperature.toolTip()
        assert dialog.top_k.toolTip()
        assert dialog.min_p.toolTip()
        assert value["temperature"] == 0.6
        assert value["top_k"] == 20
        assert value["min_p"] == 0.05
    finally:
        dialog.close()


def test_provider_dialog_top_k_zero_is_not_default(qapp):
    styles = {"hint": "", "btn": "", "field": "", "label": ""}
    dialog = _ProviderDialog(styles, set())
    try:
        dialog.kind.setCurrentIndex(dialog.kind.findData("custom"))
        dialog.provider_id.setText("local")
        dialog.models.setPlainText("model-a")
        dialog.top_k.setText("0")

        value = dialog.value()

        assert value["top_k"] == 0
    finally:
        dialog.close()


def test_provider_dialog_top_k_negative_one_is_not_default(qapp):
    styles = {"hint": "", "btn": "", "field": "", "label": ""}
    dialog = _ProviderDialog(styles, set())
    try:
        dialog.kind.setCurrentIndex(dialog.kind.findData("custom"))
        dialog.provider_id.setText("local")
        dialog.models.setPlainText("model-a")
        dialog.top_k.setText("-1")

        value = dialog.value()

        assert value["top_k"] == -1
    finally:
        dialog.close()


def test_settings_save_writes_generation_params_to_models_json(qapp, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg.save_user_providers({})
    reg.reload()

    try:
        store = SettingsStore()
        dialog = SettingsDialog(store)
        dialog._providers = [{
            "id": "local",
            "kind": "custom",
            "api": "openai-compatible",
            "base_url": "http://localhost:11434/v1",
            "api_key": "test-key",
            "api_key_spec": "LOCAL_KEY",
            "temperature": 0.6,
            "top_k": 0,
            "min_p": 0.05,
            "models": [{"id": "model-a", "name": "Model A"}],
        }]

        dialog._save()

        provider = reg.load_user_providers()["local"]
        assert provider["temperature"] == 0.6
        assert provider["topK"] == 0
        assert provider["minP"] == 0.05
    finally:
        reg.save_user_providers({})
        reg.reload()


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

        assert dialog.providers_table.columnCount() == 5
        assert dialog.providers_table.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop
        assert not dialog.providers_table.item(row, 0).icon().isNull()
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


def test_file_editor_auto_save_setting_is_saved(qapp):
    store = SettingsStore()
    dialog = SettingsDialog(store)

    assert dialog.file_editor_auto_save_check.isChecked() is False
    dialog.file_editor_auto_save_check.setChecked(True)
    dialog._save()

    assert store.load()[FILE_EDITOR_AUTO_SAVE_KEY] is True


def test_file_editor_tab_spaces_setting_is_saved(qapp):
    store = SettingsStore()
    dialog = SettingsDialog(store)

    assert dialog.file_editor_tab_spaces_spin.value() == 4
    dialog.file_editor_tab_spaces_spin.setValue(2)
    dialog._save()

    assert store.load()[FILE_EDITOR_TAB_SPACES_KEY] == 2


def test_trash_retention_setting_is_saved(qapp):
    store = SettingsStore()
    dialog = SettingsDialog(store)

    assert dialog.trash_retention_spin.value() == 14
    dialog.trash_retention_spin.setValue(30)
    dialog._save()

    assert store.load()[TRASH_RETENTION_DAYS_KEY] == 30


def test_commit_message_guidance_setting_is_saved_and_reloaded(qapp):
    store = SettingsStore()
    dialog = SettingsDialog(store)

    assert dialog.commit_message_guidance.toPlainText() == ""
    dialog.commit_message_guidance.setPlainText("Keep commits short.")
    dialog._save()

    assert store.load()[COMMIT_MESSAGE_PROMPT_ADDITION_KEY] == "Keep commits short."
    reloaded = SettingsDialog(store)
    assert reloaded.commit_message_guidance.toPlainText() == "Keep commits short."


def test_empty_commit_message_guidance_is_saved_as_optional_noop(qapp):
    store = SettingsStore()
    dialog = SettingsDialog(store)

    dialog.commit_message_guidance.setPlainText("   ")
    dialog._save()

    assert store.load()[COMMIT_MESSAGE_PROMPT_ADDITION_KEY] == ""


def test_provider_order_drag_is_saved_and_reloaded(qapp, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg.save_user_providers({
        "local-a": {
            "api": "openai-compatible",
            "apiKey": "LOCAL_A_KEY",
            "models": [{"id": "model-a"}],
        },
        "local-b": {
            "api": "openai-compatible",
            "apiKey": "LOCAL_B_KEY",
            "models": [{"id": "model-b"}],
        },
    })
    reg.reload()

    try:
        store = SettingsStore()
        store.save({
            "provider_api_keys": {
                "local-a": "key-a",
                "local-b": "key-b",
            },
        })
        dialog = SettingsDialog(store)

        _move_provider(dialog, 1, 0)
        dialog._save()

        saved = store.load()
        assert saved["provider_order"][:2] == ["local-b", "local-a"]

        reloaded = SettingsDialog(store)
        assert [provider["id"] for provider in reloaded._providers[:2]] == [
            "local-b", "local-a",
        ]
    finally:
        reg.save_user_providers({})
        reg.reload()


def test_chat_panel_configured_providers_follow_saved_order(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reg.save_user_providers({})
    reg.reload()

    try:
        store = SettingsStore()
        store.save({
            "provider_api_keys": {
                "claude": "anthropic-key",
                "openai": "openai-key",
            },
            "provider_order": ["openai", "claude"],
        })
        class DummyPanel:
            pass

        panel = DummyPanel()
        panel._settings = store

        assert ChatPanel._configured_providers(panel)[:2] == ["openai", "claude"]
    finally:
        reg.save_user_providers({})
        reg.reload()
