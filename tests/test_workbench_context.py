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
