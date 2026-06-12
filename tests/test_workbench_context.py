import time

from services.language_features import Diagnostic, LanguageFeatureStatus
from services.language_snapshot import LanguageStatusSnapshot
from ui.widgets.workbench_context import (
    WorkbenchContextPanel,
    _language_context_key,
)


def _wait_until(qapp, predicate, timeout_s: float = 2.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate()


def _status(language: str = "python") -> LanguageFeatureStatus:
    return LanguageFeatureStatus(
        extension_id=f"{language}-extension",
        language=language,
        file_patterns=("*.py",),
        features=("diagnostics", "code_actions", "format_document"),
    )


def test_workbench_context_applies_language_status_from_worker(qapp, workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    calls = []

    def build_snapshot(context):
        calls.append(dict(context))
        return LanguageStatusSnapshot(
            path=str(path),
            repo_root=str(workspace),
            is_text=True,
            statuses=(_status(),),
        )

    monkeypatch.setattr(
        "ui.widgets.workbench_context.build_language_status_snapshot",
        build_snapshot,
    )
    panel = WorkbenchContextPanel()
    available_changes = []
    panel.language_available_changed.connect(available_changes.append)

    panel.set_language_context({
        "path": str(path),
        "repo_root": str(workspace),
        "is_text": True,
        "diagnostics": [
            Diagnostic(
                path=str(path),
                line=1,
                column=0,
                severity="warning",
                message="demo warning",
                source="demo",
            )
        ],
    })
    _wait_until(qapp, lambda: panel._language_statuses and not panel._language_status_threads)

    assert [call["path"] for call in calls] == [str(path)]
    assert available_changes == [True]
    assert panel._language_icon.text() == "Py"
    assert panel._language_type.text() == "Python"
    assert panel._language_file.text() == "src/main.py"
    assert panel._language_diagnostics.item(0).text().startswith("1:1 warning [demo]")
    assert not panel._language_format_btn.isHidden()
    assert panel._language_format_btn.isEnabled()
    panel.shutdown()


def test_workbench_context_reuses_inflight_language_status_for_same_file(qapp, workspace):
    path = workspace / "src" / "main.py"
    panel = WorkbenchContextPanel()
    starts = []

    def fake_start(generation, context):
        starts.append((generation, dict(context)))
        panel._language_status_inflight_key = _language_context_key(context)

    panel._start_language_status_refresh = fake_start
    context = {
        "path": str(path),
        "repo_root": str(workspace),
        "is_text": True,
    }

    panel.set_language_context(context)
    panel.set_language_context({
        **context,
        "diagnostics": [
            Diagnostic(
                path=str(path),
                line=1,
                column=0,
                severity="warning",
                message="while loading",
            )
        ],
    })

    assert len(starts) == 1
    assert starts[0][0] == 1
    assert panel._language_status_generation == 1
    assert [diagnostic.message for diagnostic in panel._language_diagnostics_data] == ["while loading"]
    panel.shutdown()


def test_workbench_context_reuses_loaded_language_status_for_same_file(qapp, workspace, monkeypatch):
    path = workspace / "src" / "main.py"
    calls = []

    def build_snapshot(context):
        calls.append(dict(context))
        return LanguageStatusSnapshot(
            path=str(path),
            repo_root=str(workspace),
            is_text=True,
            statuses=(_status(),),
        )

    monkeypatch.setattr(
        "ui.widgets.workbench_context.build_language_status_snapshot",
        build_snapshot,
    )
    panel = WorkbenchContextPanel()
    context = {
        "path": str(path),
        "repo_root": str(workspace),
        "is_text": True,
    }

    panel.set_language_context(context)
    _wait_until(qapp, lambda: panel._language_statuses and not panel._language_status_threads)
    panel.set_language_context({
        **context,
        "diagnostics": [
            Diagnostic(
                path=str(path),
                line=2,
                column=0,
                severity="warning",
                message="new diagnostic",
            )
        ],
    })
    qapp.processEvents()

    assert [call["path"] for call in calls] == [str(path)]
    assert panel._language_type.text() == "Python"
    assert panel._language_diagnostics.item(0).text().endswith("new diagnostic")
    panel.shutdown()


def test_workbench_context_ignores_stale_language_status_snapshot(qapp, workspace):
    panel = WorkbenchContextPanel()
    old_path = workspace / "src" / "main.py"
    current_path = workspace / "notes.txt"
    context = {
        "path": str(current_path),
        "repo_root": str(workspace),
        "is_text": True,
    }
    panel._language_context = context
    panel._language_status_generation = 2
    panel._language_status_key = _language_context_key(context)
    stale_snapshot = LanguageStatusSnapshot(
        path=str(old_path),
        repo_root=str(workspace),
        is_text=True,
        statuses=(_status(),),
    )

    panel._apply_language_status_snapshot(1, stale_snapshot)
    panel._apply_language_status_snapshot(2, stale_snapshot)

    assert panel._language_statuses == []
    assert panel._language_available is False
    panel.shutdown()


def test_workbench_context_shows_ask_file_for_unknown_text_file(qapp, workspace):
    path = workspace / "notes.txt"
    panel = WorkbenchContextPanel()
    panel.set_language_context({
        "path": str(path),
        "repo_root": str(workspace),
        "is_text": True,
    })
    qapp.processEvents()

    assert panel._language_type.text() == "No language"
    assert panel._language_file.text() == "notes.txt"
    assert not panel._language_actions_label.isHidden()
    assert not panel._language_ask_file_btn.isHidden()
    assert panel._language_ask_file_btn.isEnabled()
    assert panel._language_format_btn.isHidden()
    panel.shutdown()


def test_workbench_context_ask_fix_all_emits_all_diagnostics(qapp, workspace):
    path = workspace / "src" / "main.py"
    first = Diagnostic(
        path=str(path),
        line=1,
        column=0,
        severity="warning",
        message="first",
        source="demo",
    )
    second = Diagnostic(
        path=str(path),
        line=2,
        column=0,
        severity="warning",
        message="second",
        source="demo",
    )
    panel = WorkbenchContextPanel()
    emitted = []
    panel.language_chat_fix_all_requested.connect(lambda diagnostics: emitted.append(list(diagnostics)))

    panel._language_context = {
        "path": str(path),
        "repo_root": str(workspace),
        "is_text": True,
        "diagnostics": [first, second],
    }
    panel._language_diagnostics_data = [first, second]
    panel._language_statuses = [_status()]
    panel._set_language_available(True)
    panel._render_language()
    panel._language_diagnostics.setCurrentRow(0)

    assert panel._language_ask_btn.text() == "Ask Fix all"
    panel._language_ask_btn.click()

    assert emitted == [[first, second]]
    panel.shutdown()


def test_workbench_context_right_click_fix_uses_selected_diagnostic(qapp, workspace):
    path = workspace / "src" / "main.py"
    first = Diagnostic(
        path=str(path),
        line=1,
        column=0,
        severity="warning",
        message="first",
        source="demo",
    )
    second = Diagnostic(
        path=str(path),
        line=2,
        column=0,
        severity="warning",
        message="second",
        source="demo",
    )
    panel = WorkbenchContextPanel()
    emitted = []
    panel.language_chat_fix_requested.connect(lambda diagnostics: emitted.append(list(diagnostics)))

    panel._language_context = {
        "path": str(path),
        "repo_root": str(workspace),
        "is_text": True,
        "diagnostics": [first, second],
    }
    panel._language_diagnostics_data = [first, second]
    panel._language_statuses = [_status()]
    panel._set_language_available(True)
    panel._render_language()
    panel._language_diagnostics.setCurrentRow(1)

    panel._request_language_chat_fix()

    assert emitted == [[second]]
    panel.shutdown()
