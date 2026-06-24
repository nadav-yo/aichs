import html

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QDialogButtonBox, QTextEdit,
)
from PyQt6.QtGui import QFont

from services.tool_policy import PendingApproval, repo_root
from services.shell_tool import is_shell_tool
from services.processes import ProcessStartRequest
from ui.theme import (
    code_text_edit_style,
    dialog_button_box_style,
    dialog_shell_style,
    hint_label_style,
    inline_code_style,
)


def handle_pending_approval(parent, bus, pending: PendingApproval) -> None:
    if pending.kind == "edit":
        _show_edit(parent, bus, pending)
    elif is_shell_tool(pending.kind):
        _show_shell_command(parent, bus, pending)
    elif pending.kind == "tool":
        _show_extension_tool(parent, bus, pending)


def confirm_process_start(parent, request: ProcessStartRequest) -> bool:
    dlg = QDialog(parent)
    dlg.setWindowTitle("Start long-running process?")
    _apply_dialog_style(dlg)
    layout = QVBoxLayout(dlg)

    note = QLabel(
        "This extension wants to start a long-running process. It runs as "
        "<b>you</b> on this machine and may keep running after the command returns."
    )
    note.setWordWrap(True)
    note.setStyleSheet(_muted_label_style())
    layout.addWidget(note)

    details = QLabel(
        f"Name: <b>{_escape(request.name)}</b><br>"
        f"Extension: {_code(request.extension_id or 'extension')}<br>"
        f"Workspace: {_code(request.workspace)}<br>"
        f"Cwd: {_code(request.cwd)}<br>"
        f"Stdin: {'enabled' if request.allow_stdin else 'disabled'}"
    )
    details.setWordWrap(True)
    layout.addWidget(details)

    cmd_box = QTextEdit()
    command = request.command
    if isinstance(command, list):
        command = " ".join(command)
    cmd_box.setPlainText(str(command))
    cmd_box.setReadOnly(True)
    cmd_box.setMaximumHeight(120)
    cmd_font = QFont("Consolas")
    if not cmd_font.exactMatch():
        cmd_font = QFont("Courier New")
    cmd_box.setFont(cmd_font)
    cmd_box.setStyleSheet(code_text_edit_style(selector="QTextEdit", padding="8px"))
    layout.addWidget(cmd_box)

    buttons = QDialogButtonBox()
    buttons.setStyleSheet(dialog_button_box_style())
    start = buttons.addButton("Start", QDialogButtonBox.ButtonRole.AcceptRole)
    cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
    start.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    layout.addWidget(buttons)
    return dlg.exec() == QDialog.DialogCode.Accepted


def _show_edit(parent, bus, pending: PendingApproval) -> None:
    root = repo_root(pending.cwd)
    dlg = QDialog(parent)
    dlg.setWindowTitle("Allow file edits?")
    _apply_dialog_style(dlg)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel(
        f"Allows the <b>edit_file</b> tool under:<br>{_code(root)}"
    ))
    note = QLabel(
        "This is not a sandbox. Shell commands can still change files "
        "outside this tool."
    )
    note.setWordWrap(True)
    note.setStyleSheet(_muted_label_style())
    layout.addWidget(note)

    buttons = QDialogButtonBox()
    buttons.setStyleSheet(dialog_button_box_style())
    allow = buttons.addButton("Allow", QDialogButtonBox.ButtonRole.AcceptRole)
    cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
    allow.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    layout.addWidget(buttons)

    if dlg.exec() == QDialog.DialogCode.Accepted:
        bus.complete(pending, approved=True, grant_edit=True)
    else:
        bus.complete(
            pending,
            approved=False,
            message="[tool error] User denied edit_file for this conversation.",
        )


def _show_shell_command(parent, bus, pending: PendingApproval) -> None:
    command = pending.inputs.get("command", "")
    policy = pending.policy
    dlg = QDialog(parent)
    dlg.setWindowTitle("Run command?")
    _apply_dialog_style(dlg)
    layout = QVBoxLayout(dlg)

    if not policy.bash_warning_shown:
        warn = QLabel(
            "Runs as <b>you</b> on this machine — not limited to the project folder. "
            "Confirmations reduce mistakes; they do not isolate the agent."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(_muted_label_style())
        layout.addWidget(warn)
        policy.bash_warning_shown = True

    cmd_box = QTextEdit()
    cmd_box.setPlainText(command)
    cmd_box.setReadOnly(True)
    cmd_box.setMaximumHeight(120)
    cmd_font = QFont("Consolas")
    if not cmd_font.exactMatch():
        cmd_font = QFont("Courier New")
    cmd_box.setFont(cmd_font)
    cmd_box.setStyleSheet(code_text_edit_style(selector="QTextEdit", padding="8px"))
    layout.addWidget(cmd_box)

    row = QHBoxLayout()
    run_btn = QPushButton("Run")
    skip_btn = QPushButton("Don't ask again")
    skip_hint = QLabel("Rest of this conversation only")
    skip_hint.setStyleSheet(_muted_label_style(font_pt=11))
    cancel_btn = QPushButton("Cancel")
    run_btn.clicked.connect(dlg.accept)
    skip_btn.clicked.connect(lambda: dlg.done(2))
    cancel_btn.clicked.connect(dlg.reject)
    row.addWidget(run_btn)
    row.addWidget(skip_btn)
    row.addWidget(skip_hint)
    row.addStretch()
    row.addWidget(cancel_btn)
    layout.addLayout(row)

    code = dlg.exec()
    if code == QDialog.DialogCode.Accepted:
        bus.complete(pending, approved=True)
    elif code == 2:
        bus.complete(pending, approved=True, grant_bash_skip=True)
    else:
        bus.complete(
            pending,
            approved=False,
            message="[tool error] User denied shell command.",
        )


def _show_extension_tool(parent, bus, pending: PendingApproval) -> None:
    name = pending.tool_name or "extension tool"
    source = str(getattr(pending, "tool_source", "") or "extension")
    owner = str(getattr(pending, "tool_owner", "") or "")
    source_label = "MCP" if source == "mcp" else "extension"
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"Allow {source_label} tool?")
    _apply_dialog_style(dlg)
    layout = QVBoxLayout(dlg)

    note = QLabel(
        f"Allow the <b>{_escape(name)}</b> {source_label} tool for this conversation?"
    )
    note.setWordWrap(True)
    layout.addWidget(note)

    if owner:
        owner_label = QLabel(f"Source: {_code(owner)}")
        owner_label.setWordWrap(True)
        owner_label.setStyleSheet(_muted_label_style())
        layout.addWidget(owner_label)

    caution = QLabel(
        "MCP servers and extensions can connect to external systems or run local code. "
        "Only allow tools from sources you trust."
    )
    caution.setWordWrap(True)
    caution.setStyleSheet(_muted_label_style())
    layout.addWidget(caution)

    buttons = QDialogButtonBox()
    buttons.setStyleSheet(dialog_button_box_style())
    allow_once = buttons.addButton("Allow once", QDialogButtonBox.ButtonRole.AcceptRole)
    allow_chat = buttons.addButton("Allow this conversation", QDialogButtonBox.ButtonRole.ActionRole)
    cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
    allow_once.clicked.connect(dlg.accept)
    allow_chat.clicked.connect(lambda: dlg.done(2))
    cancel.clicked.connect(dlg.reject)
    layout.addWidget(buttons)

    code = dlg.exec()
    if code == QDialog.DialogCode.Accepted:
        bus.complete(pending, approved=True)
    elif code == 2:
        bus.complete(pending, approved=True, grant_extension_tool=True)
    else:
        bus.complete(
            pending,
            approved=False,
            message=f"[tool error] User denied extension tool {name}.",
        )


def _apply_dialog_style(dlg: QDialog) -> None:
    dlg.setStyleSheet(dialog_shell_style(include_labels=True))


def _muted_label_style(font_pt: int | None = None) -> str:
    return hint_label_style(font_pt=font_pt)


def _escape(value) -> str:
    return html.escape(str(value))


def _code(value) -> str:
    return f'<code style="{inline_code_style()}">{_escape(value)}</code>'
