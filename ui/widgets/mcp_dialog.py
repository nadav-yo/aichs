from __future__ import annotations

import html
import json

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.mcp_config import (
    McpServerConfig,
    import_mcp_json,
    load_mcp_config,
    remove_mcp_server,
    review_mcp_server,
    set_mcp_component_enabled,
    set_mcp_server_enabled,
    upsert_mcp_server,
)
from services.mcp_logs import clear_mcp_logs, format_mcp_logs_html
from services.mcp_tools import (
    McpServerCapabilities,
    cached_mcp_server_capabilities,
    cached_mcp_server_capability_error,
    clear_mcp_caches,
    mcp_server_capabilities,
    probe_mcp_server,
    start_mcp_capability_warmup,
)
from services.mcp_oauth import BlockingOAuthInteraction, clear_oauth_state, has_oauth_tokens
from ui.theme import ACCENT, dialog_button_box_style, hint_label_style, palette, secondary_button_style


class _ProbeSignals(QObject):
    done = pyqtSignal(str, str)
    auth_url = pyqtSignal(str)


class _ProbeWorker(QRunnable):
    def __init__(self, server: McpServerConfig, oauth_interaction=None):
        super().__init__()
        self.signals = _ProbeSignals()
        self._server = server
        self._oauth_interaction = oauth_interaction

    def run(self):
        try:
            text = probe_mcp_server(self._server, oauth_interaction=self._oauth_interaction)
        except BaseException as exc:
            self.signals.done.emit("", str(exc))
            return
        self.signals.done.emit(text, "")


class _CapabilitySignals(QObject):
    done = pyqtSignal(str, object, str)


class _CapabilityWorker(QRunnable):
    def __init__(self, server: McpServerConfig):
        super().__init__()
        self.signals = _CapabilitySignals()
        self._server = server

    def run(self):
        try:
            capabilities = mcp_server_capabilities(self._server)
        except BaseException as exc:
            self.signals.done.emit(self._server.key, None, str(exc))
            return
        self.signals.done.emit(self._server.key, capabilities, "")


class McpDialog(QDialog):
    def __init__(self, cwd: str, parent=None):
        super().__init__(parent)
        self._cwd = cwd
        self._servers: list[McpServerConfig] = []
        self._pool = QThreadPool(self)
        self._capabilities: dict[str, McpServerCapabilities] = {}
        self._capability_errors: dict[str, str] = {}
        self._loading_capability_keys: set[str] = set()
        self._syncing_capability_tree = False
        self._showing_logs_key = ""
        self.setWindowTitle("MCP")
        self.resize(980, 680)

        root = QVBoxLayout(self)
        header = QLabel("Model Context Protocol")
        root.addWidget(header)
        hint = QLabel(
            "Configure standard mcp.json servers. aichs keeps enable/review state separately."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(hint_label_style())
        root.addWidget(hint)

        splitter = QSplitter(self)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._sync_detail)
        splitter.addWidget(self._list)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(8, 0, 0, 0)
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setMaximumHeight(190)
        detail_layout.addWidget(self._detail)

        self._capability_status = QLabel("")
        self._capability_status.setWordWrap(True)
        self._capability_status.setStyleSheet(hint_label_style())
        detail_layout.addWidget(self._capability_status)

        self._capability_tree = QTreeWidget()
        self._capability_tree.setHeaderLabels(["Enabled", "Surface", "Name", "Details"])
        self._capability_tree.setAlternatingRowColors(False)
        self._capability_tree.setRootIsDecorated(False)
        self._capability_tree.setIndentation(0)
        self._capability_tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._capability_tree.setStyleSheet(_capability_tree_style())
        self._capability_tree.itemChanged.connect(self._component_toggled)
        detail_layout.addWidget(self._capability_tree, 1)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setVisible(False)
        detail_layout.addWidget(self._log_view, 1)

        action_row = QHBoxLayout()
        self._enable = QPushButton("Enable")
        self._approve = QPushButton("Approve MCP")
        self._connect = QPushButton("Authorize")
        self._logs = QPushButton("Activity")
        self._clear_activity = QPushButton("Clear Activity")
        self._disconnect = QPushButton("Disconnect")
        self._edit = QPushButton("Edit")
        self._remove = QPushButton("Delete")
        self._enable.clicked.connect(self._toggle_selected)
        self._approve.clicked.connect(self._approve_selected)
        self._connect.clicked.connect(self._connect_selected)
        self._logs.clicked.connect(self._logs_selected)
        self._clear_activity.clicked.connect(self._clear_activity_selected)
        self._disconnect.clicked.connect(self._disconnect_selected)
        self._edit.clicked.connect(self._edit_selected)
        self._remove.clicked.connect(self._remove_selected)
        self._oauth_interaction = None
        for btn in (
            self._approve,
            self._enable,
            self._connect,
            self._logs,
            self._clear_activity,
            self._disconnect,
            self._edit,
            self._remove,
        ):
            btn.setStyleSheet(secondary_button_style())
            action_row.addWidget(btn)
        action_row.addStretch()
        detail_layout.addLayout(action_row)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox()
        buttons.setStyleSheet(dialog_button_box_style())
        add_btn = buttons.addButton("Add", QDialogButtonBox.ButtonRole.ActionRole)
        paste_btn = buttons.addButton("Import JSON", QDialogButtonBox.ButtonRole.ActionRole)
        reload_btn = buttons.addButton("Reload", QDialogButtonBox.ButtonRole.ActionRole)
        close_btn = buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        add_btn.clicked.connect(self._add_server)
        paste_btn.clicked.connect(self._import_json)
        reload_btn.clicked.connect(lambda: self._reload(force=True))
        close_btn.clicked.connect(self.reject)
        root.addWidget(buttons)
        self._reload()

    def _reload(self, *, force: bool = False):
        if force:
            clear_mcp_caches()
            self._capabilities.clear()
            self._capability_errors.clear()
            start_mcp_capability_warmup(self._cwd, force=True)
        snapshot = load_mcp_config(self._cwd, include_disabled=True)
        self._servers = list(snapshot.servers)
        current_key = self._selected().key if self._selected() else ""
        self._list.clear()
        for server in self._servers:
            item = QListWidgetItem(_server_title(server))
            item.setData(32, server.key)
            self._list.addItem(item)
        if snapshot.errors:
            self._detail.setPlainText("\n".join(snapshot.errors))
        if self._servers:
            row = next((i for i, s in enumerate(self._servers) if s.key == current_key), 0)
            self._list.setCurrentRow(row)
        else:
            self._detail.setPlainText("No MCP servers configured.")
            self._sync_buttons(None)

    def _selected(self) -> McpServerConfig | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._servers):
            return None
        return self._servers[row]

    def _sync_detail(self, _row: int):
        server = self._selected()
        self._showing_logs_key = ""
        self._sync_buttons(server)
        if server is None:
            self._detail.setPlainText("No MCP server selected.")
            self._render_capabilities(None, None)
            return
        self._sync_cached_capabilities(server)
        self._detail.setHtml(_server_detail_html(server))
        if not server.available:
            self._render_capabilities(server, None)
        elif server.key in self._capabilities:
            self._render_capabilities(server, self._capabilities[server.key])
        elif server.key in self._capability_errors:
            self._render_capabilities(server, None, error=self._capability_errors[server.key])
        else:
            self._render_capabilities(server, None, loading=True)

    def _sync_buttons(self, server: McpServerConfig | None):
        selected = server is not None
        review_required = bool(server and server.review_required)
        oauth_capable = bool(server and server.auth_type == "oauth")
        has_tokens = bool(server and oauth_capable and has_oauth_tokens(server))
        auth_needed = bool(server and server.available and oauth_capable and not has_tokens)
        retry_needed = bool(server and server.available and server.key in self._capability_errors)
        can_connect = selected and (auth_needed or retry_needed)

        self._approve.setVisible(selected and review_required)
        self._approve.setEnabled(selected and review_required)
        self._enable.setVisible(selected and not review_required)
        self._enable.setEnabled(selected and not review_required)
        self._connect.setVisible(can_connect)
        self._connect.setEnabled(can_connect)
        self._logs.setVisible(selected)
        self._logs.setEnabled(selected)
        showing_activity = bool(server and self._showing_logs_key == server.key)
        self._clear_activity.setVisible(showing_activity)
        self._clear_activity.setEnabled(showing_activity)
        self._disconnect.setVisible(selected and has_tokens)
        self._disconnect.setEnabled(selected and has_tokens)
        self._edit.setVisible(selected)
        self._edit.setEnabled(selected)
        self._remove.setVisible(selected)
        self._remove.setEnabled(selected)
        if server is not None:
            self._enable.setText("Disable" if server.enabled else "Enable")
            self._connect.setText("Authorize" if auth_needed else "Connect")
            self._logs.setText("Capabilities" if self._showing_logs_key == server.key else "Activity")

    def _toggle_selected(self):
        server = self._selected()
        if server is None:
            return
        set_mcp_server_enabled(self._cwd, server.scope, server.name, not server.enabled)
        clear_mcp_caches()
        start_mcp_capability_warmup(self._cwd, force=True)
        self._reload()

    def _approve_selected(self):
        server = self._selected()
        if server is None:
            return
        review_mcp_server(self._cwd, server.scope, server.name)
        set_mcp_server_enabled(self._cwd, server.scope, server.name, True)
        clear_mcp_caches()
        start_mcp_capability_warmup(self._cwd, force=True)
        self._reload()

    def _connect_selected(self):
        server = self._selected()
        if server is None:
            return
        self._connect.setEnabled(False)
        action = "Authorizing" if self._connect.text() == "Authorize" else "Connecting"
        self._detail.setPlainText(f"{action} {server.name}...")
        worker = _ProbeWorker(server)
        self._oauth_interaction = BlockingOAuthInteraction(worker.signals.auth_url.emit)
        worker._oauth_interaction = self._oauth_interaction
        worker.signals.auth_url.connect(self._on_auth_url)
        worker.signals.done.connect(self._on_probe_done)
        self._pool.start(worker)

    def _disconnect_selected(self):
        server = self._selected()
        if server is None:
            return
        clear_oauth_state(server)
        clear_mcp_caches()
        start_mcp_capability_warmup(self._cwd, force=True)
        self._reload()

    def _logs_selected(self):
        server = self._selected()
        if server is None:
            return
        if self._showing_logs_key == server.key:
            self._showing_logs_key = ""
            self._sync_cached_capabilities(server)
            if server.key in self._capabilities:
                self._render_capabilities(server, self._capabilities[server.key])
            elif server.key in self._capability_errors:
                self._render_capabilities(server, None, error=self._capability_errors[server.key])
            elif server.available:
                self._render_capabilities(server, None, loading=True)
            else:
                self._render_capabilities(server, None)
            self._sync_buttons(server)
            return
        self._showing_logs_key = server.key
        self._capability_tree.setVisible(False)
        self._log_view.setVisible(True)
        self._capability_status.setText("Recent MCP activity for this server.")
        self._log_view.setHtml(format_mcp_logs_html(server))
        self._sync_buttons(server)

    def _clear_activity_selected(self):
        server = self._selected()
        if server is None:
            return
        clear_mcp_logs(server)
        self._log_view.setHtml(format_mcp_logs_html(server))

    def _edit_selected(self):
        server = self._selected()
        if server is None:
            return
        dialog = _McpAddDialog(self, server=server)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            upsert_mcp_server(self._cwd, dialog.scope(), dialog.server_name(), dialog.server_entry())
        except Exception as exc:
            QMessageBox.warning(self, "MCP server not updated", str(exc))
            return
        clear_mcp_caches()
        self._capabilities.pop(server.key, None)
        self._capability_errors.pop(server.key, None)
        start_mcp_capability_warmup(self._cwd, force=True)
        self._reload()

    def _remove_selected(self):
        server = self._selected()
        if server is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove MCP server",
            f"Remove '{server.name}' from the {server.scope} MCP config?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not remove_mcp_server(self._cwd, server.scope, server.name):
            QMessageBox.warning(self, "MCP server not removed", "The selected MCP server was not found.")
            return
        clear_mcp_caches()
        self._capabilities.pop(server.key, None)
        self._capability_errors.pop(server.key, None)
        start_mcp_capability_warmup(self._cwd, force=True)
        self._reload()

    def _on_auth_url(self, auth_url: str):
        QDesktopServices.openUrl(QUrl(auth_url))
        callback_url, ok = QInputDialog.getText(
            self,
            "MCP OAuth",
            "Complete authorization in the browser, then paste the final callback URL.",
        )
        if self._oauth_interaction is None:
            return
        if ok and callback_url.strip():
            self._oauth_interaction.submit_callback_url(callback_url.strip())
        else:
            self._oauth_interaction.cancel()

    def _on_probe_done(self, text: str, error: str):
        self._connect.setEnabled(True)
        self._oauth_interaction = None
        server = self._selected()
        if error:
            if server is not None:
                self._capability_errors[server.key] = error
                self._sync_buttons(server)
            self._detail.setPlainText(f"MCP connection failed:\n\n{error}")
            return
        self._detail.setPlainText(text)
        if server is not None and server.available:
            self._capability_errors.pop(server.key, None)
            self._capabilities.pop(server.key, None)
            self._sync_cached_capabilities(server)
            if server.key in self._capabilities:
                self._render_capabilities(server, self._capabilities[server.key])
            self._sync_buttons(server)

    def _sync_cached_capabilities(self, server: McpServerConfig):
        capabilities = cached_mcp_server_capabilities(server)
        if capabilities is not None:
            self._capabilities[server.key] = capabilities
            self._capability_errors.pop(server.key, None)
            return
        error = cached_mcp_server_capability_error(server)
        if error:
            self._capability_errors[server.key] = error

    def _load_capabilities(self, server: McpServerConfig):
        if server.key in self._loading_capability_keys:
            return
        self._capability_errors.pop(server.key, None)
        self._loading_capability_keys.add(server.key)
        worker = _CapabilityWorker(server)
        worker.signals.done.connect(self._on_capabilities_done)
        self._pool.start(worker)
        self._sync_buttons(server)

    def _on_capabilities_done(self, server_key: str, capabilities: object, error: str):
        self._loading_capability_keys.discard(server_key)
        server = self._selected()
        if error:
            self._capability_errors[server_key] = error
            if server is not None and server.key == server_key:
                self._render_capabilities(server, None, error=error)
                self._sync_buttons(server)
            return
        if isinstance(capabilities, McpServerCapabilities):
            self._capability_errors.pop(server_key, None)
            self._capabilities[server_key] = capabilities
            if server is not None and server.key == server_key:
                self._render_capabilities(server, capabilities)
                self._sync_buttons(server)

    def _render_capabilities(
        self,
        server: McpServerConfig | None,
        capabilities: McpServerCapabilities | None,
        *,
        loading: bool = False,
        error: str = "",
    ):
        self._syncing_capability_tree = True
        if server is not None and self._showing_logs_key == server.key:
            self._syncing_capability_tree = False
            return
        self._log_view.setVisible(False)
        self._capability_tree.setVisible(True)
        self._capability_tree.clear()
        self._capability_status.setText("")
        if server is None:
            self._capability_tree.setEnabled(False)
            self._syncing_capability_tree = False
            return
        self._capability_tree.setEnabled(server.available)
        if server.review_required:
            self._capability_status.setText("Review required before capability discovery.")
        elif not server.enabled:
            self._capability_status.setText("Enable the server to inspect its advertised capabilities.")
        elif server.errors:
            self._capability_status.setText("; ".join(server.errors))
        elif loading:
            self._capability_status.setText("Capability discovery is pending.")
        elif error:
            self._capability_status.setText(f"Connection failed: {error}")
        elif capabilities is None:
            self._capability_status.setText("No capability data.")
        else:
            self._capability_status.setText(_capability_summary_text(capabilities))
            self._add_capability_rows("Model tool", "tools", capabilities.tools)
            self._add_capability_rows("Resource", "resources", capabilities.resources)
            self._add_capability_rows("Resource template", "resource_templates", capabilities.resource_templates)
            self._add_capability_rows("Prompt template", "prompts", capabilities.prompts)
            if self._capability_tree.topLevelItemCount() == 0:
                self._capability_status.setText("No advertised components.")
        for index, width in enumerate((86, 120, 260, 460)):
            self._capability_tree.setColumnWidth(index, width)
        self._syncing_capability_tree = False

    def _add_capability_rows(self, label: str, kind: str, items: tuple):
        usage_hint = _capability_usage_hint(label)
        for item in items:
            detail_parts = [usage_hint] if usage_hint else []
            if item.description:
                detail_parts.append(item.description)
            if item.uri and item.uri != item.name:
                detail_parts.append(item.uri)
            if item.mime_type:
                detail_parts.append(item.mime_type)
            if item.arguments:
                detail_parts.append("args: " + ", ".join(item.arguments))
            if item.input_schema:
                properties = item.input_schema.get("properties") if isinstance(item.input_schema, dict) else None
                if isinstance(properties, dict) and properties:
                    detail_parts.append("input: " + ", ".join(sorted(str(key) for key in properties)[:8]))
            row = QTreeWidgetItem(["", label, item.name, " · ".join(detail_parts)])
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(0, Qt.CheckState.Checked if item.enabled else Qt.CheckState.Unchecked)
            row.setData(0, 32, (kind, item.uri or item.name))
            _style_capability_row(row, label, enabled=item.enabled)
            self._capability_tree.addTopLevelItem(row)

    def _component_toggled(self, item: QTreeWidgetItem, _column: int):
        if self._syncing_capability_tree:
            return
        data = item.data(0, 32)
        if not isinstance(data, tuple) or len(data) != 2:
            return
        server = self._selected()
        if server is None:
            return
        kind, name = data
        enabled = item.checkState(0) == Qt.CheckState.Checked
        try:
            set_mcp_component_enabled(self._cwd, server.scope, server.name, str(kind), str(name), enabled)
        except Exception as exc:
            QMessageBox.warning(self, "MCP component not updated", str(exc))
            return
        clear_mcp_caches()
        self._capabilities.pop(server.key, None)
        self._capability_errors.pop(server.key, None)
        start_mcp_capability_warmup(self._cwd, force=True)

    def _add_server(self):
        dialog = _McpAddDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            upsert_mcp_server(self._cwd, dialog.scope(), dialog.server_name(), dialog.server_entry())
        except Exception as exc:
            QMessageBox.warning(self, "MCP server not added", str(exc))
            return
        clear_mcp_caches()
        start_mcp_capability_warmup(self._cwd, force=True)
        self._reload()

    def _import_json(self):
        dialog = _McpImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            import_mcp_json(self._cwd, dialog.scope(), dialog.payload())
        except Exception as exc:
            QMessageBox.warning(self, "MCP JSON not imported", str(exc))
            return
        clear_mcp_caches()
        start_mcp_capability_warmup(self._cwd, force=True)
        self._reload()


class _McpAddDialog(QDialog):
    _MASKED_HEADER_VALUE = "********"

    def __init__(self, parent=None, *, server: McpServerConfig | None = None):
        super().__init__(parent)
        self._server = server
        self.setWindowTitle("Edit MCP Server" if server is not None else "Add MCP Server")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._scope = QComboBox()
        self._scope.addItems(["global", "project"])
        self._transport = QComboBox()
        self._transport.addItem("Command (stdio)", "stdio")
        self._transport.addItem("URL (HTTP)", "http")
        self._name = QLineEdit()
        self._command = QLineEdit()
        self._args = QLineEdit()
        self._url = QLineEdit()
        self._headers = QTreeWidget()
        self._headers.setHeaderLabels(["Header", "Value"])
        self._headers.setRootIsDecorated(False)
        self._headers.setIndentation(0)
        self._headers.setAlternatingRowColors(False)
        self._headers.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._headers.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self._headers.setFixedHeight(108)
        self._headers.setStyleSheet(_capability_tree_style())
        self._add_header_btn = QPushButton("Add")
        self._remove_header_btn = QPushButton("Remove")
        self._add_header_btn.setStyleSheet(secondary_button_style())
        self._remove_header_btn.setStyleSheet(secondary_button_style())
        self._add_header_btn.clicked.connect(self._add_header_row)
        self._remove_header_btn.clicked.connect(self._remove_header_row)
        self._headers_box = QWidget()
        headers_layout = QVBoxLayout(self._headers_box)
        headers_layout.setContentsMargins(0, 0, 0, 0)
        headers_layout.addWidget(self._headers)
        headers_buttons = QHBoxLayout()
        headers_buttons.addWidget(self._add_header_btn)
        headers_buttons.addWidget(self._remove_header_btn)
        headers_buttons.addStretch()
        headers_layout.addLayout(headers_buttons)
        self._bearer_token_env = QLineEdit()
        self._bearer_token_env.setPlaceholderText("MCP_TOKEN")
        self._http_auth = QComboBox()
        self._http_auth.addItem("Auto (standard)", "auto")
        self._http_auth.addItem("OAuth", "oauth")
        self._http_auth.addItem("No auth", "none")
        self._http_auth.addItem("Static headers", "headers")
        self._oauth_scope = QLineEdit()
        self._oauth_scope.setPlaceholderText("optional space-separated scopes")
        self._oauth_redirect_uri = QLineEdit()
        self._oauth_redirect_uri.setPlaceholderText("http://localhost:33331/callback")
        self._oauth_server_url = QLineEdit()
        self._oauth_server_url.setPlaceholderText("optional authorization resource URL")
        self._oauth_client_id = QLineEdit()
        self._oauth_client_secret = QLineEdit()
        self._oauth_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._env = QPlainTextEdit()
        self._env.setPlaceholderText('{"TOKEN": "value"}')
        self._env.setFixedHeight(90)
        form.addRow("Scope", self._scope)
        form.addRow("Type", self._transport)
        form.addRow("Name", self._name)
        self._command_label = QLabel("Command")
        self._args_label = QLabel("Args JSON")
        self._url_label = QLabel("URL")
        self._http_auth_label = QLabel("Auth")
        self._oauth_scope_label = QLabel("OAuth scope")
        self._oauth_redirect_uri_label = QLabel("Redirect URI")
        self._oauth_server_url_label = QLabel("OAuth server URL")
        self._oauth_client_id_label = QLabel("Client ID")
        self._oauth_client_secret_label = QLabel("Client secret")
        self._headers_label = QLabel("Headers")
        self._bearer_token_label = QLabel("Bearer token env")
        form.addRow(self._command_label, self._command)
        form.addRow(self._args_label, self._args)
        form.addRow(self._url_label, self._url)
        form.addRow(self._http_auth_label, self._http_auth)
        form.addRow(self._oauth_scope_label, self._oauth_scope)
        form.addRow(self._oauth_redirect_uri_label, self._oauth_redirect_uri)
        form.addRow(self._oauth_server_url_label, self._oauth_server_url)
        form.addRow(self._oauth_client_id_label, self._oauth_client_id)
        form.addRow(self._oauth_client_secret_label, self._oauth_client_secret)
        form.addRow(self._headers_label, self._headers_box)
        form.addRow(self._bearer_token_label, self._bearer_token_env)
        form.addRow("Env JSON", self._env)
        layout.addLayout(form)
        hint = QLabel("Command servers use stdio. URL servers use streamable HTTP.")
        hint.setWordWrap(True)
        hint.setStyleSheet(hint_label_style())
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._transport.currentIndexChanged.connect(self._sync_transport_fields)
        self._http_auth.currentIndexChanged.connect(self._sync_transport_fields)
        if server is not None:
            self._load_server(server)
        self._sync_transport_fields()

    def scope(self) -> str:
        return self._scope.currentText()

    def server_name(self) -> str:
        return self._name.text().strip()

    def server_entry(self) -> dict:
        entry = {}
        transport = self._transport.currentData()
        if transport == "http":
            url = self._url.text().strip()
            if not url:
                raise ValueError("URL is required for HTTP MCP servers.")
            entry["url"] = url
            auth_type = self._http_auth.currentData()
            if auth_type == "auto":
                pass
            elif auth_type == "oauth":
                auth = {"type": "oauth"}
                scope = self._oauth_scope.text().strip()
                redirect_uri = self._oauth_redirect_uri.text().strip()
                server_url = self._oauth_server_url.text().strip()
                client_id = self._oauth_client_id.text().strip()
                client_secret = self._oauth_client_secret.text().strip()
                if scope:
                    auth["scope"] = scope
                if redirect_uri:
                    auth["redirect_uri"] = redirect_uri
                if server_url:
                    auth["server_url"] = server_url
                if client_id:
                    auth["client_id"] = client_id
                if client_secret:
                    auth["client_secret"] = client_secret
                entry["auth"] = auth
            elif auth_type == "headers":
                entry["auth"] = "headers"
                headers = self._header_rows()
                if headers:
                    entry["headers"] = headers
                bearer_token_env = self._bearer_token_env.text().strip()
                if bearer_token_env:
                    entry["bearer_token_env_var"] = bearer_token_env
            else:
                entry["auth"] = "none"
        else:
            command = self._command.text().strip()
            if not command:
                raise ValueError("Command is required for stdio MCP servers.")
            entry["command"] = command
            args_text = self._args.text().strip()
            if args_text:
                args = json.loads(args_text)
                if not isinstance(args, list):
                    raise ValueError("Args JSON must be an array.")
                entry["args"] = args
        env_text = self._env.toPlainText().strip()
        if env_text:
            env = json.loads(env_text)
            if not isinstance(env, dict):
                raise ValueError("Env JSON must be an object.")
            entry["env"] = env
        return entry

    def _sync_transport_fields(self, *_args):
        is_http = self._transport.currentData() == "http"
        for widget in (self._command_label, self._command, self._args_label, self._args):
            widget.setVisible(not is_http)
        auth_type = self._http_auth.currentData()
        is_oauth = is_http and auth_type == "oauth"
        is_headers = is_http and auth_type == "headers"
        for widget in (
            self._url_label,
            self._url,
            self._http_auth_label,
            self._http_auth,
        ):
            widget.setVisible(is_http)
        for widget in (
            self._oauth_scope_label,
            self._oauth_scope,
            self._oauth_redirect_uri_label,
            self._oauth_redirect_uri,
            self._oauth_server_url_label,
            self._oauth_server_url,
            self._oauth_client_id_label,
            self._oauth_client_id,
            self._oauth_client_secret_label,
            self._oauth_client_secret,
        ):
            widget.setVisible(is_oauth)
        for widget in (
            self._headers_label,
            self._headers_box,
            self._bearer_token_label,
            self._bearer_token_env,
        ):
            widget.setVisible(is_headers)

    def _load_server(self, server: McpServerConfig):
        self._scope.setCurrentText(server.scope)
        self._scope.setEnabled(False)
        self._name.setText(server.name)
        self._name.setEnabled(False)
        if server.url:
            self._transport.setCurrentIndex(self._transport.findData("http"))
            self._url.setText(server.url)
            auth_index = self._http_auth.findData(server.auth_type)
            if auth_index < 0:
                auth_index = self._http_auth.findData("oauth" if server.auth_type == "auto" else "none")
            self._http_auth.setCurrentIndex(auth_index)
            self._oauth_scope.setText(server.oauth_scope)
            self._oauth_redirect_uri.setText(server.oauth_redirect_uri)
            self._oauth_server_url.setText(server.oauth_server_url)
            self._oauth_client_id.setText(server.oauth_client_id)
            self._oauth_client_secret.setText(server.oauth_client_secret)
            for key, value in sorted(server.headers.items()):
                self._add_header_row(str(key), str(value), masked=True)
            self._bearer_token_env.setText(server.bearer_token_env_var)
        else:
            self._transport.setCurrentIndex(self._transport.findData("stdio"))
            self._command.setText(server.command)
            if server.args:
                self._args.setText(json.dumps(list(server.args)))
        self._transport.setEnabled(False)
        if server.env:
            self._env.setPlainText(json.dumps(server.env, indent=2, sort_keys=True))

    def _add_header_row(self, key: str = "", value: str = "", *, masked: bool = False):
        if not isinstance(key, str):
            key = ""
        if not isinstance(value, str):
            value = ""
        display_value = self._MASKED_HEADER_VALUE if masked and value else value
        item = QTreeWidgetItem([key, display_value])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        if masked:
            item.setData(1, 32, value)
        self._headers.addTopLevelItem(item)
        for index, width in enumerate((190, 280)):
            self._headers.setColumnWidth(index, width)
        if not key and not value:
            self._headers.setCurrentItem(item, 0)
            self._headers.editItem(item, 0)

    def _remove_header_row(self):
        item = self._headers.currentItem()
        if item is None:
            return
        index = self._headers.indexOfTopLevelItem(item)
        if index >= 0:
            self._headers.takeTopLevelItem(index)

    def _header_rows(self) -> dict[str, str]:
        headers = {}
        for index in range(self._headers.topLevelItemCount()):
            item = self._headers.topLevelItem(index)
            key = item.text(0).strip()
            value = item.text(1)
            original = item.data(1, 32)
            if isinstance(original, str) and value == self._MASKED_HEADER_VALUE:
                value = original
            if not key and not value.strip():
                continue
            if not key:
                raise ValueError("Header name is required.")
            if key in headers:
                raise ValueError(f"Duplicate header: {key}")
            headers[key] = value
        return headers


class _McpImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import MCP JSON")
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Scope"))
        self._scope = QComboBox()
        self._scope.addItems(["global", "project"])
        top.addWidget(self._scope)
        top.addStretch()
        layout.addLayout(top)
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText('{\n  "mcpServers": {\n    "context7": {\n      "command": "npx",\n      "args": ["-y", "@upstash/context7-mcp"]\n    }\n  }\n}')
        layout.addWidget(self._text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def scope(self) -> str:
        return self._scope.currentText()

    def payload(self) -> dict:
        data = json.loads(self._text.toPlainText() or "{}")
        if not isinstance(data, dict):
            raise ValueError("MCP JSON must be an object.")
        return data


def _server_title(server: McpServerConfig) -> str:
    if server.review_required:
        status = "Needs review"
    elif server.errors:
        status = "Invalid"
    elif server.enabled:
        status = "Enabled"
    else:
        status = "Disabled"
    return f"{server.name}  ·  {status}  ·  {server.scope}"


def _server_detail_html(server: McpServerConfig) -> str:
    rows = [
        ("Scope", server.scope),
        ("Transport", server.transport),
        ("Enabled", "yes" if server.enabled else "no"),
        ("Reviewed", "yes" if server.reviewed else "no"),
    ]
    if server.review_required:
        rows.append(("Status", "needs review before it can run"))
    if server.command:
        rows.append(("Command", server.command))
        if server.args:
            rows.append(("Args", json.dumps(list(server.args))))
    if server.url:
        rows.append(("URL", server.url))
        auth_label = _auth_detail_label(server)
        rows.append(("Auth", auth_label))
        show_oauth_state = server.auth_type == "oauth" or has_oauth_tokens(server)
        if show_oauth_state:
            rows.append(("OAuth tokens", "stored" if has_oauth_tokens(server) else "not connected"))
            if server.oauth_scope:
                rows.append(("OAuth scope", server.oauth_scope))
            if server.oauth_redirect_uri:
                rows.append(("OAuth redirect URI", server.oauth_redirect_uri))
            if server.oauth_server_url:
                rows.append(("OAuth server URL", server.oauth_server_url))
    if server.env:
        rows.append(("Env", ", ".join(sorted(server.env))))
    if server.env_vars:
        rows.append(("Env vars inherited", ", ".join(server.env_vars)))

    p = palette()
    status_tone = _server_status_tone(server)
    row_html = "".join(
        "<tr>"
        f"<td class='key'>{html.escape(key)}</td>"
        f"<td>{html.escape(value)}</td>"
        "</tr>"
        for key, value in rows
    )
    error_html = ""
    if server.errors:
        error_html = "".join(f"<div class='error'>{html.escape(error)}</div>" for error in server.errors)
    return f"""
    <html><head><style>
      body {{ margin:0; color:{p['TEXT']}; font-family:Segoe UI, sans-serif; }}
      .title {{ font-size:18px; font-weight:650; margin-bottom:6px; }}
      .badge {{ color:{status_tone['fg']}; background:{status_tone['bg']};
        border:1px solid {status_tone['border']}; border-radius:5px;
        padding:1px 7px; font-size:12px; font-weight:650; }}
      table {{ border-collapse:collapse; margin-top:4px; }}
      td {{ padding:2px 14px 2px 0; vertical-align:top; }}
      .key {{ color:{p['TEXT_DIM']}; font-weight:600; }}
      .error {{ color:#fca5a5; margin-top:6px; }}
    </style></head><body>
      <div class='title'>{html.escape(server.name)} <span class='badge'>{html.escape(status_tone['label'])}</span></div>
      <table>{row_html}</table>
      {error_html}
    </body></html>
    """


def _auth_detail_label(server: McpServerConfig) -> str:
    if server.auth_type == "auto":
        return "Standard MCP"
    if server.auth_type == "oauth":
        return "OAuth"
    if server.auth_type == "headers":
        return "Static headers"
    return "No auth"


def _server_status_tone(server: McpServerConfig) -> dict[str, str]:
    if server.errors:
        return {"label": "Invalid", "fg": "#fecaca", "bg": "#341417", "border": "#7f1d1d"}
    if server.review_required:
        return {"label": "Needs review", "fg": "#fde68a", "bg": "#332816", "border": "#854d0e"}
    if server.enabled:
        return {"label": "Enabled", "fg": "#bbf7d0", "bg": "#123322", "border": "#166534"}
    return {"label": "Disabled", "fg": "#d4d4d8", "bg": "#27272a", "border": "#3f3f46"}


def _capability_summary_text(capabilities: McpServerCapabilities) -> str:
    counts = [
        _count_label(len(capabilities.tools), "model tool"),
        _count_label(len(capabilities.prompts), "prompt template"),
        _count_label(len(capabilities.resources), "resource"),
        _count_label(len(capabilities.resource_templates), "resource template"),
    ]
    return "Discovered " + ", ".join(counts) + "."


def _count_label(count: int, noun: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def _capability_usage_hint(label: str) -> str:
    return {
        "Model tool": "Model-callable",
        "Prompt template": "User-invoked prompt",
        "Resource": "Attachable context",
        "Resource template": "Parameterized context",
    }.get(label, "")


def _style_capability_row(row: QTreeWidgetItem, label: str, *, enabled: bool):
    tones = {
        "Model tool": "#8ab4ff",
        "Resource": "#64d6a2",
        "Resource template": "#facc15",
        "Prompt template": "#c4b5fd",
    }
    color = QColor(tones.get(label, ACCENT))
    if not enabled:
        color = QColor(palette()["TEXT_DIM"])
    row.setForeground(1, QBrush(color))
    font = row.font(1)
    font.setWeight(QFont.Weight.DemiBold)
    row.setFont(1, font)


def _capability_tree_style() -> str:
    p = palette()
    return (
        "QTreeWidget {"
        f" background:{p['BG2']}; alternate-background-color:{p['BG2']};"
        f" color:{p['TEXT']}; border:none; outline:none;"
        "}"
        "QTreeWidget::item {"
        " background:transparent; border:none; padding:4px 2px;"
        "}"
        "QTreeWidget::item:hover {"
        f" background:{p['BG3']}; color:{p['TEXT']};"
        "}"
        "QTreeWidget::item:selected, QTreeWidget::item:selected:active,"
        "QTreeWidget::item:selected:!active {"
        " background:transparent;"
        f" color:{p['TEXT']};"
        "}"
        "QTreeWidget::indicator { width:14px; height:14px; }"
        "QHeaderView::section {"
        f" background:{p['BG2']}; color:{p['TEXT_DIM']}; border:none;"
        " padding:6px 8px; font-weight:500;"
        "}"
    )
