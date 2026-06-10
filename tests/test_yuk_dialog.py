import json
import zipfile

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from services.yuk import inspect_yuk
from storage.settings import SettingsStore
import ui.widgets.settings_dialog as settings_dialog
from ui.widgets.settings_dialog import SettingsDialog, _YukExportDialog, _YukImportDialog


def test_yuk_export_dialog_select_all_controls(qapp, workspace):
    SettingsStore().save({"system_prompt": "Ship carefully."})
    dialog = _YukExportDialog(str(workspace))

    assert "setting:system_prompt" in dialog.selected_item_ids()

    dialog._set_all(Qt.CheckState.Unchecked)
    assert dialog.selected_item_ids() == set()

    dialog._set_all(Qt.CheckState.Checked)
    assert "setting:system_prompt" in dialog.selected_item_ids()


def test_yuk_import_dialog_conflict_choice(qapp, workspace, tmp_path):
    SettingsStore().save({"system_prompt": "Existing."})
    package = tmp_path / "profile.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr(
            "yuk.json",
            json.dumps({
                "format": "aichs-yuk/v1",
                "name": "Conflict",
                "settings": {"system_prompt": "Imported."},
                "items": [{"id": "setting:system_prompt", "kind": "setting", "section": "Personality & Prompts", "label": "System Prompt"}],
            }),
        )

    inspection = inspect_yuk(package, str(workspace))
    dialog = _YukImportDialog(inspection)
    combo = dialog._action_widgets["setting:system_prompt"]
    combo.setCurrentIndex(combo.findData("skip"))

    assert dialog.choices()["setting:system_prompt"] == "skip"


def test_yuk_import_dialog_shows_warnings(qapp, workspace, tmp_path):
    package = tmp_path / "future.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr(
            "yuk.json",
            json.dumps({
                "format": "aichs-yuk/v2",
                "settings": {},
                "items": [{"id": "future", "kind": "workflow", "label": "Workflow"}],
            }),
        )

    dialog = _YukImportDialog(inspect_yuk(package, str(workspace)))
    labels = [label.text() for label in dialog.findChildren(QLabel)]

    assert any("Warnings:" in text and "aichs-yuk/v2" in text for text in labels)


def test_yuk_import_dialog_shows_extension_disclosure(qapp, workspace, tmp_path):
    package = tmp_path / "extensions.yuk"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr(
            "yuk.json",
            json.dumps({
                "format": "aichs-yuk/v1",
                "settings": {},
                "items": [{
                    "id": "extension:project:demo",
                    "kind": "extension_folder",
                    "section": "Extensions",
                    "label": "demo",
                    "scope": "project",
                    "name": "demo",
                    "archive_path": "extensions/project/demo",
                    "permissions_declared": True,
                    "permissions": {"tools": True, "network": True},
                }],
            }),
        )
        zf.writestr("extensions/project/demo/extension.py", "def register(registry): pass\n")

    dialog = _YukImportDialog(inspect_yuk(package, str(workspace)))
    labels = [dialog.tree.topLevelItem(0).child(0).text(2)]

    assert any("Install disabled" in text and "tools" in text and "network" in text for text in labels)


def test_settings_yuk_export_detects_unsaved_prompt_changes(qapp, monkeypatch, workspace):
    store = SettingsStore()
    store.save({"system_prompt": "Saved prompt."})
    dialog = SettingsDialog(store, cwd=str(workspace))
    dialog._ensure_page(dialog._page_ids.index("crew"))
    dialog.system_prompt.setPlainText("Unsaved prompt.")
    warnings = []

    monkeypatch.setattr(settings_dialog.QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(
        settings_dialog,
        "_YukExportDialog",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("export dialog should not open")),
    )

    dialog._export_yuk()

    assert warnings
    assert "Save settings first" in warnings[0][1]


def test_settings_yuk_export_unsaved_check_is_clean_after_load(qapp, workspace):
    store = SettingsStore()
    store.save({"system_prompt": "Saved prompt."})
    dialog = SettingsDialog(store, cwd=str(workspace))
    dialog._ensure_yuk_pages()

    assert dialog._has_unsaved_yuk_changes() is False
