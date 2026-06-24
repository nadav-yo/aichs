import json

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QListWidgetItem, QMessageBox

import config
from services.mcp_config import McpServerConfig, load_mcp_config
from services.mcp_logs import append_mcp_log
from services.mcp_tools import McpCapability, McpServerCapabilities
from ui.widgets.mcp_dialog import McpDialog, _McpAddDialog, _capability_summary_text, _server_detail_html


@pytest.fixture(autouse=True)
def disable_background_capability_loading(monkeypatch):
    monkeypatch.setattr(McpDialog, "_load_capabilities", lambda self, server: None)


def test_mcp_dialog_renders_capabilities_and_persists_toggle(qapp, workspace):
    server = McpServerConfig(name="docs", scope="global", raw={}, command="docs-server")
    capabilities = McpServerCapabilities(
        tools=(
            McpCapability(
                "lookup",
                "Lookup docs.",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            ),
        ),
        resources=(McpCapability("Docs", "Project docs.", uri="doc://one", enabled=True),),
        prompts=(McpCapability("draft", "Draft a note.", arguments=("topic",), enabled=True),),
    )
    dialog = McpDialog(str(workspace))
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    try:
        dialog._servers = [server]
        dialog._list.blockSignals(True)
        dialog._list.clear()
        dialog._list.addItem(QListWidgetItem("docs"))
        dialog._list.setCurrentRow(0)
        dialog._list.blockSignals(False)

        dialog._capabilities[server.key] = capabilities
        dialog._sync_detail(0)

        assert "aichs exposes MCP tools" not in dialog._detail.toPlainText()
        assert dialog._capability_status.text() == (
            "Discovered 1 model tool, 1 prompt template, 1 resource, 0 resource templates."
        )
        tool = dialog._capability_tree.topLevelItem(0)
        assert tool.text(1) == "Model tool"
        assert tool.text(2) == "lookup"
        assert "Model-callable" in tool.text(3)
        assert "Lookup docs." in tool.text(3)
        assert "input: q" in tool.text(3)

        resource = dialog._capability_tree.topLevelItem(1)
        assert resource.text(1) == "Resource"
        assert "Attachable context" in resource.text(3)
        resource.setCheckState(0, Qt.CheckState.Unchecked)
        assert load_mcp_config(str(workspace)).servers[0].component_enabled("resources", "doc://one") is False
    finally:
        dialog.close()


def test_mcp_dialog_status_text_is_not_component_row(qapp, workspace):
    server = McpServerConfig(name="docs", scope="global", raw={}, command="docs-server")
    dialog = McpDialog(str(workspace))
    try:
        dialog._render_capabilities(server, None, loading=True)

        assert dialog._capability_status.text() == "Capability discovery is pending."
        assert dialog._capability_tree.topLevelItemCount() == 0
    finally:
        dialog.close()


def test_mcp_capability_summary_lists_zero_and_nonzero_surfaces():
    summary = _capability_summary_text(
        McpServerCapabilities(
            tools=(McpCapability("lookup"), McpCapability("search")),
            prompts=(McpCapability("draft"),),
        )
    )

    assert summary == "Discovered 2 model tools, 1 prompt template, 0 resources, 0 resource templates."


def test_mcp_dialog_selection_reads_cached_capabilities_without_loading(qapp, workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    capabilities = McpServerCapabilities(tools=(McpCapability("lookup", "Lookup docs."),))
    monkeypatch.setattr(
        "ui.widgets.mcp_dialog.cached_mcp_server_capabilities",
        lambda _server: capabilities,
    )
    monkeypatch.setattr(
        "ui.widgets.mcp_dialog.cached_mcp_server_capability_error",
        lambda _server: "",
    )

    def fail_loading(_self, _server):
        raise AssertionError("selecting an MCP row should not start capability discovery")

    monkeypatch.setattr(McpDialog, "_load_capabilities", fail_loading)
    dialog = McpDialog(str(workspace))
    try:
        assert dialog._capability_tree.topLevelItem(0).text(2) == "lookup"
    finally:
        dialog.close()


def test_mcp_dialog_auto_auth_summary_is_not_oauth_noise():
    server = McpServerConfig(
        name="unreal-mcp",
        scope="project",
        raw={},
        url="http://127.0.0.1:8000/mcp",
        auth_type="auto",
        reviewed=True,
    )

    html = _server_detail_html(server)

    assert "Standard MCP" in html
    assert "plain HTTP" not in html
    assert "OAuth tokens" not in html


def test_mcp_dialog_logs_button_replaces_capability_view(qapp, workspace):
    server = McpServerConfig(
        name="unreal-mcp",
        scope="project",
        raw={},
        url="http://127.0.0.1:8000/mcp",
        auth_type="auto",
        reviewed=True,
    )
    append_mcp_log(server, "connect_failed", "Connection refused")
    dialog = McpDialog(str(workspace))
    try:
        dialog._servers = [server]
        dialog._list.blockSignals(True)
        dialog._list.clear()
        dialog._list.addItem(QListWidgetItem("unreal-mcp"))
        dialog._list.setCurrentRow(0)
        dialog._list.blockSignals(False)
        dialog._sync_buttons(server)

        dialog._logs_selected()

        assert not dialog._log_view.isHidden()
        assert dialog._capability_tree.isHidden()
        assert "Connect failed" in dialog._log_view.toPlainText()
        assert "Connection refused" in dialog._log_view.toPlainText()
        assert dialog._logs.text() == "Capabilities"
        assert not dialog._clear_activity.isHidden()

        dialog._clear_activity_selected()

        assert "No MCP activity for unreal-mcp yet." in dialog._log_view.toPlainText()

        dialog._logs_selected()

        assert dialog._log_view.isHidden()
        assert not dialog._capability_tree.isHidden()
        assert dialog._logs.text() == "Activity"
        assert dialog._clear_activity.isHidden()
    finally:
        dialog.close()


def test_mcp_dialog_remove_button_deletes_selected_server(qapp, workspace, monkeypatch):
    dialog = McpDialog(str(workspace))
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = McpServerConfig(name="docs", scope="global", raw={}, command="docs-server")
    monkeypatch.setattr(
        "ui.widgets.mcp_dialog.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    try:
        dialog._servers = [server]
        dialog._list.blockSignals(True)
        dialog._list.clear()
        dialog._list.addItem(QListWidgetItem("docs"))
        dialog._list.setCurrentRow(0)
        dialog._list.blockSignals(False)
        assert dialog._list.count() == 1
        dialog._remove_selected()

        assert dialog._list.count() == 0
        assert load_mcp_config(str(workspace), include_disabled=True).servers == ()
    finally:
        dialog.close()


def test_mcp_dialog_actions_are_contextual(qapp, workspace):
    dialog = McpDialog(str(workspace))
    try:
        command_server = McpServerConfig(name="docs", scope="global", raw={}, command="docs-server")
        dialog._sync_buttons(command_server)

        assert not dialog._enable.isHidden()
        assert not dialog._edit.isHidden()
        assert not dialog._remove.isHidden()
        assert dialog._approve.isHidden()
        assert dialog._connect.isHidden()
        assert dialog._disconnect.isHidden()

        review_server = McpServerConfig(
            name="local",
            scope="project",
            raw={},
            command="local-server",
            enabled=False,
            review_required=True,
            reviewed=False,
        )
        dialog._sync_buttons(review_server)

        assert not dialog._approve.isHidden()
        assert dialog._approve.text() == "Approve MCP"
        assert dialog._enable.isHidden()

        oauth_server = McpServerConfig(
            name="github",
            scope="global",
            raw={},
            url="https://api.githubcopilot.com/mcp/",
            auth_type="oauth",
        )
        dialog._sync_buttons(oauth_server)

        assert not dialog._connect.isHidden()
        assert dialog._connect.text() == "Authorize"

        auto_server = McpServerConfig(
            name="unreal",
            scope="global",
            raw={},
            url="http://127.0.0.1:8000/mcp",
            auth_type="auto",
        )
        dialog._sync_buttons(auto_server)

        assert dialog._connect.isHidden()
        assert dialog._disconnect.isHidden()

        dialog._capability_errors[auto_server.key] = "Connection refused"
        dialog._sync_buttons(auto_server)

        assert not dialog._connect.isHidden()
        assert dialog._connect.text() == "Connect"
    finally:
        dialog.close()


def test_mcp_dialog_edit_updates_existing_server(qapp, workspace, monkeypatch):
    dialog = McpDialog(str(workspace))
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "old-server"}}}),
        encoding="utf-8",
    )
    server = McpServerConfig(name="docs", scope="global", raw={}, command="old-server")

    class FakeEditDialog:
        def __init__(self, parent=None, *, server=None):
            assert server.name == "docs"

        def exec(self):
            return QDialog.DialogCode.Accepted

        def scope(self):
            return "global"

        def server_name(self):
            return "docs"

        def server_entry(self):
            return {"command": "new-server", "args": ["--stdio"]}

    monkeypatch.setattr("ui.widgets.mcp_dialog._McpAddDialog", FakeEditDialog)
    try:
        dialog._servers = [server]
        dialog._list.blockSignals(True)
        dialog._list.clear()
        dialog._list.addItem(QListWidgetItem("docs"))
        dialog._list.setCurrentRow(0)
        dialog._list.blockSignals(False)

        dialog._edit_selected()

        saved = json.loads((config.AICHS_HOME / "mcp.json").read_text(encoding="utf-8"))
        assert saved["mcpServers"]["docs"] == {"args": ["--stdio"], "command": "new-server"}
    finally:
        dialog.close()


def test_mcp_edit_dialog_masks_and_preserves_header_values(qapp):
    server = McpServerConfig(
        name="github",
        scope="global",
        raw={},
        url="https://api.githubcopilot.com/mcp/",
        auth_type="headers",
        headers={"Authorization": "Bearer secret-token"},
    )
    dialog = _McpAddDialog(server=server)
    try:
        item = dialog._headers.topLevelItem(0)

        assert item.text(0) == "Authorization"
        assert item.text(1) == "********"
        assert dialog.server_entry()["headers"] == {"Authorization": "Bearer secret-token"}

        item.setText(1, "Bearer changed-token")

        assert dialog.server_entry()["headers"] == {"Authorization": "Bearer changed-token"}
    finally:
        dialog.close()
