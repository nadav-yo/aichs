from pathlib import Path

from storage.repository import ConversationStore, workspace_id
from storage.workspace_session import (
    load_workspace_session,
    normalize_session,
    resolve_open_file_path,
    save_workspace_session,
    session_has_restorable_state,
    session_path,
)


def test_session_path_uses_workspace_id(workspace):
    assert session_path(workspace).name == f"{workspace_id(workspace)}.json"


def test_normalize_session_defaults():
    session = normalize_session({})
    assert session["version"] == 1
    assert session["conversation_id"] == ""
    assert session["open_files"] == []
    assert session["viewer_visible"] is False
    assert session["context_panel"] == "run_log"
    assert session["context_collapsed"] is True


def test_normalize_session_open_files_and_active_flag():
    session = normalize_session({
        "open_files": [
            {"path": "a.py", "line": 3, "active": False},
            {"path": "b.py", "line": 1, "active": True},
        ],
    })
    assert session["open_files"] == [
        {"path": "a.py", "line": 3, "active": False},
        {"path": "b.py", "line": 1, "active": True},
    ]


def test_normalize_session_ensures_one_active_file():
    session = normalize_session({
        "open_files": [
            {"path": "a.py", "line": 1},
            {"path": "b.py", "line": 2},
        ],
    })
    assert session["open_files"][0]["active"] is True
    assert session["open_files"][1]["active"] is False


def test_session_has_restorable_state():
    assert session_has_restorable_state({}) is False
    assert session_has_restorable_state({"conversation_id": "abc"}) is True
    assert session_has_restorable_state({"open_files": [{"path": "x.py"}]}) is True


def test_save_and_load_roundtrip(workspace, isolate_aichs_home):
    payload = {
        "conversation_id": "chat-1",
        "open_files": [{"path": str(workspace / "src" / "main.py"), "line": 4, "active": True}],
        "viewer_visible": True,
        "workbench_sizes": [640, 420],
        "context_panel": "language",
        "context_collapsed": False,
    }
    save_workspace_session(workspace, payload)
    loaded = load_workspace_session(workspace)
    assert loaded["conversation_id"] == "chat-1"
    assert loaded["open_files"][0]["line"] == 4
    assert loaded["viewer_visible"] is True
    assert loaded["workbench_sizes"] == [640, 420]
    assert loaded["context_panel"] == "language"
    assert loaded["context_collapsed"] is False
    assert loaded["updated_at"]


def test_load_missing_session_returns_defaults(workspace):
    assert load_workspace_session(workspace) == normalize_session({})


def test_resolve_open_file_path(workspace):
    rel = resolve_open_file_path("src/main.py", workspace)
    assert Path(rel) == (workspace / "src" / "main.py").resolve()


def test_file_viewer_open_file_states_and_restore(qapp, workspace):
    from ui.widgets.file_viewer import FileViewerPanel

    panel = FileViewerPanel(str(workspace))
    first = workspace / "src" / "main.py"
    second = workspace / "src" / "second.py"
    second.parent.mkdir(parents=True, exist_ok=True)
    second.write_text("print('two')\n", encoding="utf-8")

    panel.open_file(str(first), repo_root=str(workspace), line_no=2)
    panel.open_file(str(second), repo_root=str(workspace))

    states = panel.open_file_states()
    assert len(states) == 2
    assert states[-1]["active"] is True
    assert states[0]["path"] == str(first.resolve())

    panel.close_all_tabs()
    skipped = panel.restore_open_files(states, repo_root=str(workspace))
    assert skipped == []
    assert panel.open_paths() == [str(first.resolve()), str(second.resolve())]
    assert panel.active_path() == str(second.resolve())


def test_main_window_restores_saved_session(qapp, workspace, monkeypatch):
    import time

    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QMessageBox
    from ui.widgets.file_viewer import _TextFileTab

    store = ConversationStore(str(workspace))
    conv_id = "resume-chat"
    store.save(conv_id, {"id": conv_id, "title": "Resume me", "messages": []})
    target = workspace / "src" / "main.py"
    save_workspace_session(
        workspace,
        {
            "conversation_id": conv_id,
            "open_files": [{"path": str(target), "line": 1, "active": True}],
            "viewer_visible": True,
            "workbench_sizes": [500, 400],
            "context_panel": "run_log",
            "context_collapsed": True,
        },
    )

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        "ui.widgets.file_viewer._TextFileTab._refresh_diagnostics",
        lambda self, delay_ms=None: None,
    )

    from ui.main_window import MainWindow

    window = MainWindow(startup_workspace=str(workspace))
    window.show()
    window._maybe_restore_workspace_session()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if window._chat.current_conversation_id() == conv_id:
            break
        time.sleep(0.01)

    qapp.processEvents()
    QThreadPool.globalInstance().waitForDone(1500)
    for widget in qapp.allWidgets():
        if isinstance(widget, _TextFileTab):
            widget._worker_pool.waitForDone(1500)
    qapp.processEvents()

    assert window._chat.current_conversation_id() == conv_id
    assert window._viewer.has_open_tabs()
    assert window._viewer.isVisible()
