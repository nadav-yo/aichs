from datetime import datetime, timedelta

from PyQt6.QtWidgets import QApplication

from ui.widgets.left_panel import _normalize_activity_key
from ui.widgets.workspace_dashboard import WorkspaceDashboard


def test_normalize_activity_key_maps_legacy_names():
    assert _normalize_activity_key("workspace") == "home"
    assert _normalize_activity_key("chats") == "sessions"
    assert _normalize_activity_key("files") == "files"


def test_home_dashboard_session_context_without_conversation(qapp, workspace):
    dashboard = WorkspaceDashboard(str(workspace), defer_refresh=True)
    try:
        dashboard.set_session_context({})
        assert dashboard._active_title.text() == "No active session"
        assert not dashboard._open_session_btn.isEnabled()
    finally:
        dashboard.close()


def test_home_dashboard_session_context_shows_current_model_only_when_provided(qapp, workspace):
    dashboard = WorkspaceDashboard(str(workspace), defer_refresh=True)
    try:
        dashboard.set_session_context(
            {
                "conversation_path": "/tmp/chat.json",
                "title": "Auth cleanup",
                "updated_at": (datetime.now() - timedelta(hours=2)).isoformat(),
                "message_count": 12,
                "open_file_count": 2,
                "current_model": "qwen3:32b",
            }
        )
        assert dashboard._active_title.text() == "Auth cleanup"
        assert "12 messages" in dashboard._active_meta.text()
        assert dashboard._active_model.text() == "Current model: qwen3:32b"
    finally:
        dashboard.close()
