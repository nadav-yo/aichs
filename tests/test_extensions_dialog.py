from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton

from services.extension_installer import (
    ExtensionInstallCandidate,
    ExtensionInstallSource,
    discover_extension_candidates,
    install_conflicts,
)
from services.tool_registry import (
    ExtensionFileSummary,
    ExtensionOverview,
    ExtensionPermissions,
    LanguageContribution,
)
from ui.widgets.extensions_dialog import (
    ExtensionInstallDialog,
    ExtensionsDialog,
    _ExtensionDetailPane,
    _ExtensionListRow,
    _ExtensionOverviewWorker,
    _ExtensionInstallApplyWorker,
    _ExtensionInstallFetchWorker,
    _enabled_checkbox_style,
    _extension_error_text,
    _filter_extensions,
    _filter_install_candidates,
    _install_scope_combo_style,
    _list_meta_style,
    _list_name_style,
    _list_path_style,
    _list_row_style,
    _queue_toggle,
    _list_subtitle,
    _status_label_style,
    _status_tone,
    _summary_text,
)
from ui.theme import palette


def _summary(
    path="ext.py",
    status="Loaded",
    errors=None,
    description="",
    display_name="",
    languages=None,
    permissions=None,
    permission_violations=None,
    reviewed=True,
    review_required=False,
):
    return ExtensionFileSummary(
        path=path,
        status=status,
        tools=[],
        commands=[],
        contexts=[],
        hooks=[],
        badges=[],
        panels=[],
        errors=list(errors or []),
        description=description,
        display_name=display_name,
        languages=list(languages or []),
        permissions=permissions or ExtensionPermissions(declared=True),
        permission_violations=list(permission_violations or []),
        reviewed=reviewed,
        review_required=review_required,
        risk_messages=["Enabled extensions run local Python code in the AICHS process."],
    )


def test_extensions_summary_includes_disabled_count():
    overview = ExtensionOverview(files=[
        _summary("loaded.py"),
        _summary("disabled.py", status="Disabled"),
    ])

    assert _summary_text(overview) == "2 extension files · no errors · 1 disabled"


def test_extensions_summary_shows_filtered_count():
    overview = ExtensionOverview(files=[
        _summary("loaded.py"),
        _summary("disabled.py", status="Disabled"),
    ])

    assert _summary_text(overview, visible_count=1) == "1 of 2 extension files · no errors · 1 disabled"


def test_filter_extensions_by_query_and_status():
    files = [
        _summary("runtime.py", description="Runtime controls"),
        _summary("guard.py", description="Guardrails", status="Disabled"),
        _summary(
            "risky.py",
            errors=["boom"],
            reviewed=False,
            review_required=True,
        ),
    ]

    assert [file.path for file in _filter_extensions(files, query="guard")] == ["guard.py"]
    assert [file.path for file in _filter_extensions(files, status="disabled")] == ["guard.py"]
    assert [file.path for file in _filter_extensions(files, status="errors")] == ["risky.py"]
    assert [file.path for file in _filter_extensions(files, status="review")] == ["risky.py"]


def test_filter_install_candidates_matches_name_and_description(tmp_path):
    candidates = [
        ExtensionInstallCandidate(
            name="context-resilience",
            source_path=tmp_path / "context",
            entrypoint=tmp_path / "context" / "extension.py",
            kind="folder",
            display_name="Context Resilience",
            description="Compaction helpers",
        ),
        ExtensionInstallCandidate(
            name="runtime-guard.py",
            source_path=tmp_path / "guard.py",
            entrypoint=tmp_path / "guard.py",
            kind="file",
            display_name="Runtime Guard",
            description="Safety checks",
        ),
    ]

    assert [item.name for item in _filter_install_candidates(candidates, "compaction")] == [
        "context-resilience"
    ]
    assert [item.name for item in _filter_install_candidates(candidates, "context resilience")] == [
        "context-resilience"
    ]


def test_extensions_dialog_status_helpers():
    assert _status_tone(_summary()) == "success"
    assert _status_tone(_summary(status="Disabled")) == "disabled"
    assert _status_tone(_summary(status="Failed", errors=["boom"])) == "danger"
    assert "border-radius:8px" in _status_label_style("success")
    assert "background:" in _status_label_style("success")


def test_enabled_checkbox_style_keeps_label_background_transparent():
    style = _enabled_checkbox_style()

    assert "QCheckBox {" in style
    assert "background-color: transparent" in style
    assert "QCheckBox::indicator:checked" in style
    assert "SUCCESS" not in style


def test_extension_list_row_keeps_child_text_surfaces_transparent():
    selected = _list_row_style(selected=True, tone="success")

    assert "background-color:" in selected
    assert "QFrame#extensionListRow QLabel { background-color:transparent; border:none; }" in selected
    assert "transparent" in _list_name_style()
    assert "transparent" in _list_meta_style("success")
    assert "transparent" in _list_path_style()


def test_install_scope_combo_styles_dropdown_with_theme_palette():
    p = palette()
    style = _install_scope_combo_style()

    assert "QComboBox QAbstractItemView" in style
    assert "QComboBoxPrivateContainer" in style
    assert "QComboBox QAbstractItemView::item" in style
    assert p["BG3"] in style
    assert p["SELECTION"] in style
    assert p["SELECTION_TEXT"] in style


def test_extensions_dialog_status_filter(qapp):
    overview = ExtensionOverview(files=[
        _summary("enabled.py"),
        _summary("disabled.py", status="Disabled"),
    ])
    dialog = ExtensionsDialog(overview)

    assert len(dialog.findChildren(_ExtensionListRow)) == 2

    dialog._set_status_filter("disabled")
    qapp.processEvents()

    rows = dialog.findChildren(_ExtensionListRow)
    assert len(rows) == 1
    assert rows[0]._file.path == "disabled.py"
    assert dialog._status_filter_btn.toolTip() == "Status filter: Disabled"


def test_extensions_dialog_uses_docs_for_api_reference(qapp):
    overview = ExtensionOverview(files=[
        _summary("runtime.py", description="Runtime controls"),
        _summary("guard.py", description="Guardrails"),
    ])
    dialog = ExtensionsDialog(overview)

    assert dialog._selected_path == "runtime.py"
    assert isinstance(dialog._detail_scroll.widget(), _ExtensionDetailPane)
    assert "API Reference" not in [button.text() for button in dialog.findChildren(QPushButton)]

    dialog._filter_edit.setText("guard")
    qapp.processEvents()

    assert dialog._selected_path == "guard.py"
    assert _summary_text(overview, visible_count=1) in dialog._summary.text()
    rows = dialog.findChildren(_ExtensionListRow)
    assert len(rows) == 1
    assert rows[0]._file.path == "guard.py"

    dialog._show_file_detail(overview.files[1])

    assert dialog._selected_path == "guard.py"
    assert isinstance(dialog._detail_scroll.widget(), _ExtensionDetailPane)


def test_extensions_dialog_uses_manifest_display_name(qapp, tmp_path):
    entrypoint = tmp_path / ".aichs" / "extensions" / "python-lang" / "extension.py"
    overview = ExtensionOverview(files=[
        _summary(str(entrypoint), display_name="Python Language Support"),
    ])

    dialog = ExtensionsDialog(overview)

    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Python Language Support" in labels
    assert "extension.py" not in labels


def test_extensions_dialog_list_subtitle_summarizes_contributions():
    file = _summary("runtime.py", description="Runtime controls")

    assert _list_subtitle(file) == "No registered contributions · Runtime controls"

    language_file = _summary(
        "language.py",
        languages=[LanguageContribution(name="python", file_patterns=["*.py"])],
    )
    assert _list_subtitle(language_file) == "1 language"


def test_extensions_dialog_shows_permissions_and_risk(qapp):
    overview = ExtensionOverview(files=[
        _summary(
            "risky.py",
            permissions=ExtensionPermissions(declared=True, tools=True, network=True),
            permission_violations=["Blocked undeclared extension contribution: hook turn_start"],
            reviewed=False,
            review_required=True,
        ),
    ])

    dialog = ExtensionsDialog(overview)
    labels = [label.text() for label in dialog.findChildren(QLabel)]

    assert any("Enabled extensions run local Python code" in text for text in labels)
    assert any("tools, network" in text for text in labels)
    assert any("Blocked undeclared extension contribution" in text for text in labels)
    assert "Loaded · blocked" in labels


def test_extension_checkbox_toggles_are_deferred(qapp, monkeypatch):
    scheduled = []
    calls = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QTimer.singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    row = _ExtensionListRow(
        _summary("disabled.py", status="Disabled"),
        selected=True,
        on_toggle=lambda path, enabled: calls.append((path, enabled)),
    )
    checkbox = row.findChildren(QCheckBox)[0]

    checkbox.setChecked(True)

    assert calls == []
    assert len(scheduled) == 1
    assert scheduled[0][0] == 0

    scheduled[0][1]()

    assert calls == [("disabled.py", True)]


def test_extension_detail_checkbox_toggles_are_deferred(qapp, monkeypatch):
    scheduled = []
    calls = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QTimer.singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    pane = _ExtensionDetailPane(
        _summary("enabled.py", status="Loaded"),
        on_toggle=lambda path, enabled: calls.append((path, enabled)),
    )
    checkbox = pane.findChildren(QCheckBox)[0]

    checkbox.setChecked(False)

    assert calls == []
    assert len(scheduled) == 1

    scheduled[0][1]()

    assert calls == [("enabled.py", False)]


def test_queue_toggle_ignores_missing_handler(monkeypatch):
    scheduled = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QTimer.singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    _queue_toggle(None, "extension.py", True)

    assert scheduled == []


def test_extension_overview_worker_emits_overview(qapp, monkeypatch):
    overview = ExtensionOverview(files=[_summary("runtime.py")])
    done = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.extension_overview",
        lambda cwd: overview,
    )
    worker = _ExtensionOverviewWorker(4, "C:/repo")
    worker.signals.done.connect(lambda *args: done.append(args))
    worker.run()

    assert done == [(4, overview, "")]


def test_extensions_dialog_defers_cwd_overview_to_worker(qapp, monkeypatch):
    started = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.extension_overview",
        lambda _cwd: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog = ExtensionsDialog("C:/repo")

    assert dialog._summary.text() == "Loading extensions..."
    assert isinstance(started[0], _ExtensionOverviewWorker)


def test_extensions_dialog_applies_current_overview_result(qapp):
    overview = ExtensionOverview(files=[_summary("runtime.py")])
    calls = []
    dialog = ExtensionsDialog(ExtensionOverview(files=[]), on_reload=lambda: calls.append("reload"))
    dialog._cwd = "C:/repo"
    dialog._overview_generation = 2

    dialog._on_overview_ready(2, overview, "")

    assert dialog._overview is overview
    assert dialog._selected_path == "runtime.py"
    assert calls == ["reload"]


def test_extensions_dialog_ignores_stale_overview_result(qapp):
    overview = ExtensionOverview(files=[_summary("runtime.py")])
    dialog = ExtensionsDialog(ExtensionOverview(files=[]))
    dialog._overview_generation = 2

    dialog._on_overview_ready(1, overview, "")

    assert dialog._overview.files == []
    assert dialog._selected_path == ""


def test_extensions_dialog_close_invalidates_overview_without_waiting(qapp, monkeypatch):
    waited = []
    dialog = ExtensionsDialog(ExtensionOverview(files=[]))
    dialog._overview_generation = 2
    monkeypatch.setattr(
        dialog._overview_pool,
        "waitForDone",
        lambda *_args: waited.append("wait"),
    )

    dialog.closeEvent(QCloseEvent())

    assert dialog._overview_generation == 3
    assert waited == []


def test_extensions_dialog_enable_warns_when_extension_load_fails(qapp, monkeypatch):
    path = "broken.py"
    overviews = [
        ExtensionOverview(files=[_summary(path, status="Disabled")]),
        ExtensionOverview(files=[_summary(path, status="Failed", errors=["boom"])]),
    ]
    warnings = []
    toggles = []

    def fake_overview(_cwd):
        return overviews[min(len(toggles), len(overviews) - 1)]

    monkeypatch.setattr("ui.widgets.extensions_dialog.extension_overview", fake_overview)
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.set_extension_enabled",
        lambda changed_path, enabled, _cwd: toggles.append((changed_path, enabled)),
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QMessageBox.warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QThreadPool.start",
        lambda _pool, worker: worker.run(),
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QTimer.singleShot",
        lambda _delay, callback: callback(),
    )
    dialog = ExtensionsDialog("C:/repo")

    checkbox = dialog.findChildren(QCheckBox)[0]
    checkbox.setChecked(True)

    assert toggles == [(path, True)]
    assert warnings == [("Extension failed to load", "boom")]


def test_extensions_dialog_enable_exception_warns_and_rolls_back(qapp, monkeypatch):
    path = "broken.py"
    calls = []
    warnings = []
    dialog = ExtensionsDialog(ExtensionOverview(files=[_summary(path, status="Disabled")]))
    dialog._cwd = "C:/repo"
    dialog._reload = lambda: None

    def fake_set_enabled(changed_path, enabled, cwd):
        calls.append((changed_path, enabled, cwd))
        if enabled:
            raise SystemExit("extension stopped")

    monkeypatch.setattr("ui.widgets.extensions_dialog.set_extension_enabled", fake_set_enabled)
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QMessageBox.warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )

    dialog._set_enabled(path, True)

    assert calls == [(path, True, "C:/repo"), (path, False, "C:/repo")]
    assert warnings == [("Extension enable failed", "extension stopped")]


def test_extension_error_text_limits_long_error_lists():
    file = _summary("broken.py", status="Failed", errors=["one", "two", "three"])

    assert _extension_error_text(file) == "one\n\ntwo\n\n... and 1 more error(s)."


def test_extension_install_dialog_selects_candidates(qapp, tmp_path):
    dialog = ExtensionInstallDialog(str(tmp_path))
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "python-lang",
        entrypoint=tmp_path / "python-lang" / "extension.py",
        kind="folder",
        display_name="Python Language",
        description="Python support",
        source_commit="abc123def456",
        source_commit_date="2026-06-11T12:00:00+00:00",
    )

    dialog._set_candidates([candidate])

    assert dialog.scope_combo.currentData() == "local"
    assert [item.name for item in dialog.selected_candidates()] == ["python-lang"]
    checkbox, _candidate = dialog._candidate_checks[0]
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Python Language" in labels
    assert not any("Incoming: commit" in text for text in labels)
    assert "Installs disabled until reviewed" in checkbox.toolTip()
    assert "Incoming:" not in checkbox.toolTip()
    assert dialog._selection_label.text() == "1 of 1 selected"
    checkbox.setChecked(False)
    assert dialog.selected_candidates() == []
    assert dialog._selection_label.text() == "None selected"
    assert not dialog.install_btn.isEnabled()


def test_extension_install_dialog_select_all_and_deselect_all(qapp, tmp_path, monkeypatch):
    calls = {"count": 0}
    original = install_conflicts

    def counting_conflicts(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.install_conflicts",
        counting_conflicts,
    )
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._set_candidates([
        ExtensionInstallCandidate(
            name="one",
            source_path=tmp_path / "one",
            entrypoint=tmp_path / "one" / "extension.py",
            kind="folder",
            display_name="One",
            description="First",
        ),
        ExtensionInstallCandidate(
            name="two",
            source_path=tmp_path / "two",
            entrypoint=tmp_path / "two" / "extension.py",
            kind="folder",
            display_name="Two",
            description="Second",
        ),
    ])
    calls["count"] = 0

    dialog._deselect_all_btn.click()
    qapp.processEvents()

    assert dialog.selected_candidates() == []
    assert dialog._selection_label.text() == "None selected"
    assert calls["count"] == 0

    dialog._select_all_btn.click()
    qapp.processEvents()
    assert [item.name for item in dialog.selected_candidates()] == ["one", "two"]
    assert dialog._selection_label.text() == "2 of 2 selected"
    assert calls["count"] == 0


def test_extension_install_dialog_marks_conflicts_and_requires_overwrite(qapp, tmp_path):
    installed = tmp_path / ".aichs" / "extensions" / "python-lang"
    installed.mkdir(parents=True)
    (installed / "extension.py").write_text("old", encoding="utf-8")
    dialog = ExtensionInstallDialog(str(tmp_path))
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "src" / "python-lang",
        entrypoint=tmp_path / "src" / "python-lang" / "extension.py",
        kind="folder",
        display_name="Python Language",
        description="Python support",
        source_commit="abc123def456",
        source_commit_date="2026-06-11T12:00:00+00:00",
    )

    dialog._set_candidates([candidate])

    checkbox, _candidate = dialog._candidate_checks[0]
    assert not checkbox.isChecked()
    assert not dialog.install_btn.isEnabled()
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Python Language" in labels
    assert any("Already installed in local library" in text for text in labels)
    assert any("current:" in text and "incoming:" in text and "abc123def456" in text for text in labels)
    assert "Check to replace the installed extension" in checkbox.toolTip()

    checkbox.setChecked(True)
    assert dialog.selected_candidates()[0].name == "python-lang"
    assert dialog.install_btn.text() == "Replace selected"


def test_extension_install_dialog_shows_already_installed_for_matching_content(qapp, tmp_path):
    import shutil

    source = tmp_path / "source" / "python-lang"
    source.mkdir(parents=True)
    (source / "extension.py").write_text(
        'EXTENSION_DESCRIPTION = "Python support"\n\n'
        "def register(registry):\n"
        "    pass\n",
        encoding="utf-8",
    )
    installed = tmp_path / ".aichs" / "extensions" / "python-lang"
    shutil.copytree(source, installed)
    candidate = discover_extension_candidates(source.parent)[0]
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._set_candidates([candidate])

    checkbox, _candidate = dialog._candidate_checks[0]
    assert not checkbox.isChecked()
    assert not checkbox.isEnabled()
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert any(text == "Already installed" for text in labels)
    assert not any("current:" in text for text in labels)
    assert "Check to replace" not in checkbox.toolTip()
    assert (
        dialog.status_label.text()
        == "This extension is already installed."
    )
    assert dialog._selection_label.text() == "Already installed"

    dialog._select_all_btn.click()
    qapp.processEvents()
    assert dialog.selected_candidates() == []
    assert dialog._selection_label.text() == "Already installed"


def test_extension_install_dialog_select_all_skips_already_installed(qapp, tmp_path):
    import shutil

    source = tmp_path / "source" / "python-lang"
    source.mkdir(parents=True)
    (source / "extension.py").write_text(
        'EXTENSION_DESCRIPTION = "Python support"\n\n'
        "def register(registry):\n"
        "    pass\n",
        encoding="utf-8",
    )
    installed = tmp_path / ".aichs" / "extensions" / "python-lang"
    shutil.copytree(source, installed)
    installed_candidate = discover_extension_candidates(source.parent)[0]
    fresh = ExtensionInstallCandidate(
        name="fresh-ext",
        source_path=tmp_path / "fresh-ext",
        entrypoint=tmp_path / "fresh-ext" / "extension.py",
        kind="folder",
        display_name="Fresh Ext",
        description="New extension",
    )
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._set_candidates([installed_candidate, fresh])

    installed_checkbox, _ = dialog._candidate_checks[0]
    fresh_checkbox, _ = dialog._candidate_checks[1]
    assert not installed_checkbox.isEnabled()
    assert fresh_checkbox.isEnabled()

    dialog._select_all_btn.click()
    qapp.processEvents()
    assert [item.name for item in dialog.selected_candidates()] == ["fresh-ext"]
    assert not installed_checkbox.isChecked()
    assert dialog._selection_label.text() == "1 of 1 selected"


def test_extension_install_dialog_marks_global_conflicts_when_installing_local(qapp, tmp_path, monkeypatch):
    import config

    home = tmp_path / "home" / ".aichs"
    home.mkdir(parents=True)
    monkeypatch.setattr(config, "AICHS_HOME", home)
    installed = home / "extensions" / "python-lang"
    installed.mkdir(parents=True)
    (installed / "extension.py").write_text("old", encoding="utf-8")

    dialog = ExtensionInstallDialog(str(tmp_path / "project"))
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "src" / "python-lang",
        entrypoint=tmp_path / "src" / "python-lang" / "extension.py",
        kind="folder",
        display_name="Python Language",
        description="Python support",
        source_commit="abc123def456",
        source_commit_date="2026-06-11T12:00:00+00:00",
    )

    dialog._set_candidates([candidate])

    checkbox, _candidate = dialog._candidate_checks[0]
    assert not checkbox.isChecked()
    assert not dialog.install_btn.isEnabled()
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("Already installed in global library" in text for text in labels)
    assert any("current:" in text and "incoming:" in text for text in labels)
    assert "Check to replace the installed extension" in checkbox.toolTip()
    assert dialog.install_btn.text() == "Install selected"


def test_extension_install_dialog_filters_candidates(qapp, tmp_path):
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._set_candidates([
        ExtensionInstallCandidate(
            name="context-resilience",
            source_path=tmp_path / "context",
            entrypoint=tmp_path / "context" / "extension.py",
            kind="folder",
            description="Compaction helpers",
        ),
        ExtensionInstallCandidate(
            name="runtime-guard",
            source_path=tmp_path / "guard.py",
            entrypoint=tmp_path / "guard.py",
            kind="file",
            description="Safety checks",
        ),
    ])

    dialog._candidate_filter_edit.setText("compaction")
    qapp.processEvents()

    assert len(dialog._candidate_checks) == 1
    assert dialog._candidate_checks[0][1].name == "context-resilience"


def test_extension_install_fetch_starts_worker_without_preparing_on_ui_thread(qapp, tmp_path, monkeypatch):
    dialog = ExtensionInstallDialog(str(tmp_path))
    started = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.prepare_extension_install_source",
        lambda _url: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog.url_edit.setText("https://example.test/ext.git")
    dialog._fetch()

    assert dialog._fetch_active
    assert dialog._source is None
    assert not dialog.fetch_btn.isEnabled()
    assert not dialog.install_btn.isEnabled()
    assert isinstance(started[0], _ExtensionInstallFetchWorker)


def test_extension_install_fetch_worker_emits_source_and_cleans_previous(qapp, tmp_path, monkeypatch):
    previous = ExtensionInstallSource(
        url="old",
        kind="git",
        checkout_path=tmp_path / "old-checkout",
        temp_dir=tmp_path / "old-temp",
        candidates=[],
    )
    source = ExtensionInstallSource(
        url="new",
        kind="git",
        checkout_path=tmp_path / "new-checkout",
        temp_dir=tmp_path / "new-temp",
        candidates=[],
    )
    cleaned = []
    done = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.cleanup_extension_install_source",
        lambda item: cleaned.append(item),
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.prepare_extension_install_source",
        lambda url: source,
    )

    worker = _ExtensionInstallFetchWorker(7, "https://example.test/ext.git", previous)
    worker.signals.done.connect(lambda *args: done.append(args))
    worker.run()

    assert cleaned == [previous]
    assert done == [(7, source, "")]


def test_extension_install_dialog_applies_current_fetch_result(qapp, tmp_path):
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "python-lang",
        entrypoint=tmp_path / "python-lang" / "extension.py",
        kind="folder",
        description="Python support",
    )
    source = ExtensionInstallSource(
        url="url",
        kind="git",
        checkout_path=tmp_path / "checkout",
        temp_dir=tmp_path / "temp",
        candidates=[candidate],
    )
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._fetch_generation = 3
    dialog._fetch_active = True
    dialog.fetch_btn.setEnabled(False)

    dialog._on_fetch_done(3, source, "")

    assert not dialog._fetch_active
    assert dialog._source is source
    assert dialog.fetch_btn.isEnabled()
    assert dialog.install_btn.isEnabled()
    assert [item.name for item in dialog.selected_candidates()] == ["python-lang"]
    assert dialog.status_label.text() == ""
    assert not dialog.status_label.isVisible()


def test_extension_install_dialog_ignores_stale_fetch_result_and_cleans_source(qapp, tmp_path, monkeypatch):
    source = ExtensionInstallSource(
        url="stale",
        kind="git",
        checkout_path=tmp_path / "checkout",
        temp_dir=tmp_path / "temp",
        candidates=[],
    )
    cleaned = []
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._fetch_generation = 4
    dialog._fetch_active = True

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.cleanup_extension_install_source",
        lambda item: cleaned.append(item),
    )

    dialog._on_fetch_done(3, source, "")

    assert dialog._fetch_active
    assert dialog._source is None
    assert cleaned == [source]


def test_extension_install_apply_worker_installs_and_cleans_source(qapp, tmp_path, monkeypatch):
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "python-lang",
        entrypoint=tmp_path / "python-lang" / "extension.py",
        kind="folder",
    )
    source = ExtensionInstallSource(
        url="url",
        kind="git",
        checkout_path=tmp_path / "checkout",
        temp_dir=tmp_path / "temp",
        candidates=[candidate],
    )
    calls = []
    cleaned = []
    done = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.install_extension_candidates",
        lambda candidates, *, scope, cwd: calls.append((candidates, scope, cwd)) or ["ok"],
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.cleanup_extension_install_source",
        lambda item: cleaned.append(item),
    )

    worker = _ExtensionInstallApplyWorker(8, [candidate], "local", str(tmp_path), source)
    worker.signals.done.connect(lambda *args: done.append(args))
    worker.run()

    assert calls == [([candidate], "local", str(tmp_path))]
    assert cleaned == [source]
    assert done == [(8, ["ok"], "")]


def test_extension_install_apply_worker_cleans_source_on_install_error(qapp, tmp_path, monkeypatch):
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "python-lang",
        entrypoint=tmp_path / "python-lang" / "extension.py",
        kind="folder",
    )
    source = ExtensionInstallSource(
        url="url",
        kind="git",
        checkout_path=tmp_path / "checkout",
        temp_dir=tmp_path / "temp",
        candidates=[candidate],
    )
    cleaned = []
    done = []

    def fail_install(_candidates, *, scope, cwd):
        raise RuntimeError(f"copy failed for {scope}:{cwd}")

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.install_extension_candidates",
        fail_install,
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.cleanup_extension_install_source",
        lambda item: cleaned.append(item),
    )

    worker = _ExtensionInstallApplyWorker(9, [candidate], "local", str(tmp_path), source)
    worker.signals.done.connect(lambda *args: done.append(args))
    worker.run()

    assert cleaned == [source]
    assert done == [(9, [], f"copy failed for local:{tmp_path}")]


def test_extension_install_apply_worker_reports_cleanup_error(qapp, tmp_path, monkeypatch):
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "python-lang",
        entrypoint=tmp_path / "python-lang" / "extension.py",
        kind="folder",
    )
    source = ExtensionInstallSource(
        url="url",
        kind="git",
        checkout_path=tmp_path / "checkout",
        temp_dir=tmp_path / "temp",
        candidates=[candidate],
    )
    done = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.install_extension_candidates",
        lambda _candidates, *, scope, cwd: ["ok"],
    )

    def fail_cleanup(_source):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.cleanup_extension_install_source",
        fail_cleanup,
    )

    worker = _ExtensionInstallApplyWorker(10, [candidate], "local", str(tmp_path), source)
    worker.signals.done.connect(lambda *args: done.append(args))
    worker.run()

    assert done == [(10, [], "cleanup failed")]


def test_extension_install_click_starts_worker_without_copying_on_ui_thread(qapp, tmp_path, monkeypatch):
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "python-lang",
        entrypoint=tmp_path / "python-lang" / "extension.py",
        kind="folder",
    )
    source = ExtensionInstallSource(
        url="url",
        kind="git",
        checkout_path=tmp_path / "checkout",
        temp_dir=tmp_path / "temp",
        candidates=[candidate],
    )
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._source = source
    dialog._set_candidates([candidate])
    started = []

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.install_extension_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should run in worker")),
    )
    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QThreadPool.start",
        lambda _pool, worker: started.append(worker),
    )

    dialog._install()

    assert dialog._install_active
    assert not dialog.fetch_btn.isEnabled()
    assert not dialog.install_btn.isEnabled()
    assert dialog.status_label.text() == "Installing extensions..."
    assert isinstance(started[0], _ExtensionInstallApplyWorker)


def test_extension_install_dialog_applies_install_success(qapp, tmp_path, monkeypatch):
    infos = []
    source = ExtensionInstallSource(
        url="url",
        kind="git",
        checkout_path=tmp_path / "checkout",
        temp_dir=tmp_path / "temp",
        candidates=[],
    )
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._source = source
    dialog._install_generation = 2
    dialog._install_active = True
    dialog.fetch_btn.setEnabled(False)

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QMessageBox.information",
        lambda _parent, title, text: infos.append((title, text)),
    )

    dialog._on_install_done(2, ["ok"], "")

    assert not dialog._install_active
    assert dialog._source is None
    assert dialog.fetch_btn.isEnabled()
    assert dialog.result() == ExtensionInstallDialog.DialogCode.Accepted
    assert infos == [(
        "Extensions installed",
        "Installed extensions are disabled until you review and enable them.",
    )]


def test_extension_install_dialog_applies_install_error(qapp, tmp_path, monkeypatch):
    candidate = ExtensionInstallCandidate(
        name="python-lang",
        source_path=tmp_path / "python-lang",
        entrypoint=tmp_path / "python-lang" / "extension.py",
        kind="folder",
    )
    warnings = []
    dialog = ExtensionInstallDialog(str(tmp_path))
    dialog._set_candidates([candidate])
    dialog._install_generation = 5
    dialog._install_active = True
    dialog.fetch_btn.setEnabled(False)
    dialog.install_btn.setEnabled(False)

    monkeypatch.setattr(
        "ui.widgets.extensions_dialog.QMessageBox.warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )

    dialog._on_install_done(5, [], "copy failed")

    assert not dialog._install_active
    assert dialog.fetch_btn.isEnabled()
    assert dialog.install_btn.isEnabled()
    assert dialog.status_label.text() == "Install failed."
    assert warnings == [("Install failed", "copy failed")]
