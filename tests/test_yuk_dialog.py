import json
import zipfile

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QLabel

from services.yuk import YukExportItem, inspect_yuk
from storage.settings import SettingsStore
import ui.widgets.settings_dialog as settings_dialog
from ui.widgets.settings_dialog import (
    SettingsDialog,
    _YukApplyWorker,
    _YukExportDialog,
    _YukExportItemsWorker,
    _YukExportPackageWorker,
    _YukInspectWorker,
    _YukImportDialog,
)


def _wait_until(qapp, predicate, timeout_ms: int = 1500):
    elapsed = 0
    while elapsed < timeout_ms:
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(25)
        elapsed += 25
    qapp.processEvents()
    assert predicate()


def test_yuk_export_dialog_select_all_controls(qapp, workspace):
    SettingsStore().save({"system_prompt": "Ship carefully."})
    dialog = _YukExportDialog(str(workspace))
    _wait_until(qapp, lambda: dialog._items_loaded)

    assert "QTreeWidget::item:selected:focus" in dialog.styleSheet()
    assert "setting:system_prompt" in dialog.selected_item_ids()

    dialog._set_all(Qt.CheckState.Unchecked)
    assert dialog.selected_item_ids() == set()

    dialog._set_all(Qt.CheckState.Checked)
    assert "setting:system_prompt" in dialog.selected_item_ids()


def test_yuk_export_items_worker_emits_discovered_items(qapp, monkeypatch):
    item = YukExportItem(
        id="setting:system_prompt",
        section="Personality & Prompts",
        label="System Prompt",
        kind="setting",
    )
    done = []

    monkeypatch.setattr(
        "ui.widgets.settings_dialog.discover_export_items",
        lambda cwd: [item],
    )
    worker = _YukExportItemsWorker(3, "C:/repo")
    worker.signals.done.connect(lambda *args: done.append(args))
    worker.run()

    assert done == [(3, [item], "")]


def test_yuk_export_dialog_defers_item_discovery_to_worker(qapp, workspace, monkeypatch):
    started = []

    monkeypatch.setattr(
        "ui.widgets.settings_dialog.discover_export_items",
        lambda _cwd: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog = _YukExportDialog(str(workspace))

    assert dialog.tree.topLevelItem(0).text(0) == "Loading export items..."
    assert not dialog._export_btn.isEnabled()
    assert isinstance(started[0], _YukExportItemsWorker)


def test_yuk_export_dialog_ignores_stale_item_results(qapp, workspace, monkeypatch):
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.start",
        lambda _pool, _worker: None,
    )
    dialog = _YukExportDialog(str(workspace))
    dialog._items_generation = 2

    dialog._on_items_ready(
        1,
        [YukExportItem(id="setting:system_prompt", section="Prompts", label="System Prompt", kind="setting")],
        "",
    )

    assert not dialog._items_loaded
    assert dialog.tree.topLevelItem(0).text(0) == "Loading export items..."
    assert not dialog._export_btn.isEnabled()


def test_yuk_export_dialog_close_and_reject_invalidate_without_waiting(qapp, workspace, monkeypatch):
    waits = []
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.start",
        lambda _pool, _worker: None,
    )
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.waitForDone",
        lambda *_args: waits.append("wait"),
    )
    dialog = _YukExportDialog(str(workspace))
    dialog._items_generation = 3

    dialog.closeEvent(QCloseEvent())

    assert dialog._items_generation == 4
    assert waits == []

    dialog._items_generation = 7
    dialog.reject()

    assert dialog._items_generation == 8
    assert waits == []


def test_yuk_export_package_worker_emits_export_result(qapp, monkeypatch):
    done = []
    calls = []
    manifest = {"items": []}

    def fake_export(path, cwd, selection, *, cancelled=None):
        calls.append((path, cwd, selection.selected_item_ids, cancelled()))
        return manifest

    monkeypatch.setattr("ui.widgets.settings_dialog.export_yuk", fake_export)
    worker = _YukExportPackageWorker(5, "C:/tmp/profile.yuk", "C:/repo", {"setting:system_prompt"})
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert calls == [("C:/tmp/profile.yuk", "C:/repo", {"setting:system_prompt"}, False)]
    assert done == [(5, manifest, "")]


def test_yuk_export_package_worker_can_cancel_export(qapp, monkeypatch):
    done = []
    calls = []

    def fake_export(_path, _cwd, _selection, *, cancelled=None):
        calls.append(cancelled())
        raise RuntimeError("cancelled")

    monkeypatch.setattr("ui.widgets.settings_dialog.export_yuk", fake_export)
    worker = _YukExportPackageWorker(6, "C:/tmp/profile.yuk", "C:/repo", {"setting:system_prompt"})
    worker.signals.done.connect(lambda *args: done.append(args))
    worker.cancel()

    worker.run()

    assert calls == [True]
    assert done == [(6, None, "cancelled")]


def test_settings_yuk_export_starts_package_worker_without_exporting_on_ui_thread(
    qapp,
    monkeypatch,
    workspace,
    tmp_path,
):
    store = SettingsStore()
    store.save({"system_prompt": "Saved prompt."})
    dialog = SettingsDialog(store, cwd=str(workspace))
    dialog._ensure_page(dialog._page_ids.index("user_kit"))
    started = []

    class FakeExportDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_item_ids(self):
            return {"setting:system_prompt"}

    monkeypatch.setattr(settings_dialog, "_YukExportDialog", FakeExportDialog)
    monkeypatch.setattr(
        settings_dialog.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(tmp_path / "profile.yuk"), ""),
    )
    monkeypatch.setattr(
        settings_dialog,
        "export_yuk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog._export_yuk()

    assert dialog._yuk_export_active
    assert not dialog._yuk_export_btn.isEnabled()
    assert not dialog._yuk_import_btn.isEnabled()
    assert dialog._yuk_export_btn.text() == "Exporting..."
    assert dialog._yuk_export_status.text() == "Exporting YUK package..."
    assert isinstance(started[0], _YukExportPackageWorker)


def test_settings_close_cancels_active_yuk_export_without_waiting(qapp, workspace, tmp_path, monkeypatch):
    store = SettingsStore()
    store.save({"system_prompt": "Saved prompt."})
    dialog = SettingsDialog(store, cwd=str(workspace))
    dialog._ensure_page(dialog._page_ids.index("user_kit"))
    started = []
    waits = []
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.waitForDone",
        lambda *_args: waits.append("wait"),
    )

    dialog._start_yuk_export(str(tmp_path / "profile.yuk"), {"setting:system_prompt"})
    worker = started[0]
    generation = dialog._yuk_export_generation

    dialog.closeEvent(QCloseEvent())

    assert worker._cancel.is_set()
    assert dialog._yuk_export_generation == generation + 1
    assert dialog._yuk_export_worker is None
    assert not dialog._yuk_export_active
    assert waits == []


def test_settings_yuk_export_done_updates_state_and_reports_result(qapp, workspace, monkeypatch):
    store = SettingsStore()
    store.save({"system_prompt": "Saved prompt."})
    dialog = SettingsDialog(store, cwd=str(workspace))
    dialog._ensure_page(dialog._page_ids.index("user_kit"))
    infos = []
    warnings = []

    monkeypatch.setattr(
        settings_dialog.QMessageBox,
        "information",
        lambda *args: infos.append(args),
    )
    monkeypatch.setattr(
        settings_dialog.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )

    dialog._yuk_export_generation = 3
    dialog._yuk_export_active = True
    dialog._on_yuk_export_done(3, {"items": []}, "")

    assert not dialog._yuk_export_active
    assert dialog._yuk_export_btn.isEnabled()
    assert dialog._yuk_import_btn.isEnabled()
    assert dialog._yuk_export_status.text() == "YUK package exported."
    assert infos and infos[0][1] == "Exported"

    dialog._yuk_export_generation = 4
    dialog._yuk_export_active = True
    dialog._on_yuk_export_done(4, None, "zip failed")

    assert warnings and warnings[0][1] == "Export failed"
    assert dialog._yuk_export_status.text() == "Export failed."


def test_yuk_inspect_worker_emits_inspection(qapp, monkeypatch):
    done = []
    inspection = object()

    monkeypatch.setattr(
        "ui.widgets.settings_dialog.inspect_yuk",
        lambda path, cwd: inspection,
    )
    worker = _YukInspectWorker(6, "C:/tmp/profile.yuk", "C:/repo")
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert done == [(6, "C:/tmp/profile.yuk", inspection, "")]


def test_yuk_apply_worker_emits_import_result(qapp, monkeypatch):
    done = []
    result = object()
    calls = []

    monkeypatch.setattr(
        "ui.widgets.settings_dialog.apply_yuk",
        lambda path, cwd, choices: calls.append((path, cwd, choices)) or result,
    )
    worker = _YukApplyWorker(7, "C:/tmp/profile.yuk", "C:/repo", {"setting:system_prompt": "skip"})
    worker.signals.done.connect(lambda *args: done.append(args))

    worker.run()

    assert calls == [("C:/tmp/profile.yuk", "C:/repo", {"setting:system_prompt": "skip"})]
    assert done == [(7, result, "")]


def test_settings_yuk_import_starts_inspect_worker_without_inspecting_on_ui_thread(
    qapp,
    monkeypatch,
    workspace,
    tmp_path,
):
    store = SettingsStore()
    store.save({"system_prompt": "Saved prompt."})
    dialog = SettingsDialog(store, cwd=str(workspace))
    dialog._ensure_page(dialog._page_ids.index("user_kit"))
    started = []

    monkeypatch.setattr(
        settings_dialog.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(tmp_path / "profile.yuk"), ""),
    )
    monkeypatch.setattr(
        settings_dialog,
        "inspect_yuk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog._import_yuk()

    assert dialog._yuk_import_active
    assert not dialog._yuk_export_btn.isEnabled()
    assert not dialog._yuk_import_btn.isEnabled()
    assert dialog._yuk_import_btn.text() == "Importing..."
    assert dialog._yuk_export_status.text() == "Inspecting YUK package..."
    assert isinstance(started[0], _YukInspectWorker)


def test_settings_yuk_inspect_accept_starts_apply_worker(qapp, monkeypatch, workspace, tmp_path):
    store = SettingsStore()
    store.save({"system_prompt": "Saved prompt."})
    dialog = SettingsDialog(store, cwd=str(workspace))
    dialog._ensure_page(dialog._page_ids.index("user_kit"))
    started = []

    class FakeImportDialog:
        def __init__(self, inspection, parent=None):
            self.inspection = inspection
            self.parent = parent

        def exec(self):
            return QDialog.DialogCode.Accepted

        def choices(self):
            return {"setting:system_prompt": "skip"}

    monkeypatch.setattr(settings_dialog, "_YukImportDialog", FakeImportDialog)
    monkeypatch.setattr(
        "ui.widgets.settings_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog._yuk_import_generation = 8
    dialog._yuk_import_active = True
    dialog._on_yuk_inspect_done(8, str(tmp_path / "profile.yuk"), object(), "")

    assert dialog._yuk_import_active
    assert dialog._yuk_export_status.text() == "Importing YUK package..."
    assert isinstance(started[0], _YukApplyWorker)


def test_settings_yuk_apply_done_reports_error_and_success(qapp, monkeypatch, workspace):
    store = SettingsStore()
    store.save({"system_prompt": "Saved prompt."})
    dialog = SettingsDialog(store, cwd=str(workspace))
    dialog._ensure_page(dialog._page_ids.index("user_kit"))
    infos = []
    warnings = []
    cleared = []

    monkeypatch.setattr(
        settings_dialog.QMessageBox,
        "information",
        lambda *args: infos.append(args),
    )
    monkeypatch.setattr(
        settings_dialog.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )
    monkeypatch.setattr(settings_dialog, "clear_cache", lambda: cleared.append("clear"))

    dialog._yuk_import_generation = 9
    dialog._yuk_import_active = True
    dialog._on_yuk_apply_done(9, None, "copy failed")

    assert not dialog._yuk_import_active
    assert warnings and warnings[0][1] == "Import failed"
    assert dialog._yuk_export_status.text() == "Import failed."

    dialog._yuk_import_generation = 10
    dialog._yuk_import_active = True
    dialog._on_yuk_apply_done(10, object(), "")

    assert cleared == ["clear"]
    assert infos and infos[0][1] == "Imported"
    assert dialog.result() == SettingsDialog.DialogCode.Accepted


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

    assert "QTreeWidget::item:selected:focus" in dialog.styleSheet()
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
