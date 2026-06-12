from services.tool_registry import (
    extension_panel_data,
    extension_status_badges,
)
from ui.widgets.extension_panel_dialog import _is_supported_action
from tests.conftest import write_extension


def test_status_badge_and_panel(workspace):
    write_extension(
        workspace,
        "ui_ext.py",
        """
        def register(registry):
            registry.status_badge(
                name="tests",
                provider=lambda ctx: {"label": "OK", "tooltip": "Tests"},
            )
            registry.panel(
                name="tests",
                title="Tests",
                provider=lambda ctx: {"title": "Tests", "body": "All green"},
            )
        """,
    )
    cwd = str(workspace)
    badges, errors = extension_status_badges(cwd)
    assert not errors
    assert len(badges) == 1
    assert badges[0][1]["label"] == "OK"

    title, data, panel_errors = extension_panel_data(cwd, "tests")
    assert title == "Tests"
    assert data["body"] == "All green"
    assert not panel_errors


def test_panel_failure_recorded(workspace):
    write_extension(
        workspace,
        "bad_panel.py",
        """
        def register(registry):
            registry.panel(name="bad", title="Bad", provider=lambda ctx: 1 / 0)
        """,
    )
    cwd = str(workspace)
    title, data, errors = extension_panel_data(cwd, "bad")
    assert title == "Bad"
    assert "failed" in data["body"].lower() or "Panel" in data["body"]
    assert errors


def test_run_extension_command_panel_action_supported():
    assert _is_supported_action({"type": "run_extension_command"})
