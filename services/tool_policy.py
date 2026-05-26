"""Repo path checks and per-conversation tool approval state."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from services.shell_tool import SHELL_TOOL_NAME, is_shell_tool


@dataclass
class ConversationToolPolicy:
    edit_approved: bool = False
    bash_skip_prompts: bool = False
    bash_warning_shown: bool = False
    approved_extension_tools: set[str] = field(default_factory=set)


@dataclass
class PendingApproval:
    kind: str  # "edit" | "execute" | "tool"
    inputs: dict
    cwd: str
    policy: ConversationToolPolicy
    tool_name: str = ""
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    denied_message: str = "User denied."
    grant_edit: bool = False
    grant_bash_skip: bool = False
    grant_extension_tool: bool = False


def repo_root(cwd: str) -> Path:
    return Path(cwd).resolve()


def resolve_path(path: str, cwd: str) -> Path:
    root = repo_root(cwd)
    p = Path(path)
    return (p if p.is_absolute() else root / p).resolve()


def path_in_repo(path: Path, cwd: str) -> bool:
    root = repo_root(cwd)
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def validate_tool_paths(name: str, inputs: dict, cwd: str) -> str | None:
    """Return an error message if paths are outside the workspace, else None."""
    if name == "read_file":
        return _check_path_input(inputs, cwd, "read_file")
    if name == "edit_file":
        return _check_path_input(inputs, cwd, "edit_file")
    if name in ("list_files", "search_files"):
        directory = inputs.get("directory") or cwd
        label = "list directory" if name == "list_files" else "search directory"
        return _check_resolved_path(directory, cwd, label)
    return None


def _check_path_input(inputs: dict, cwd: str, tool_name: str) -> str | None:
    path = inputs.get("path")
    if not path:
        aliases = [key for key in ("file_path", "filepath", "filename") if key in inputs]
        hint = ""
        if aliases:
            hint = f" Use the exact argument name 'path', not {', '.join(aliases)}."
        return f"Missing {tool_name} path.{hint}"
    return _check_resolved_path(str(path), cwd, f"{tool_name} path")


def _check_resolved_path(path: str, cwd: str, label: str) -> str | None:
    if not path:
        return f"Missing {label}"
    resolved = resolve_path(path, cwd)
    if not path_in_repo(resolved, cwd):
        return (
            f"{label} must stay inside the workspace ({repo_root(cwd)}). "
            f"Got: {resolved}"
        )
    return None


class ToolApprovalBus(QObject):
    approval_needed = pyqtSignal(object)  # PendingApproval

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._current: PendingApproval | None = None

    def check(
        self,
        name: str,
        inputs: dict,
        cwd: str,
        policy: ConversationToolPolicy,
        is_cancelled: Callable[[], bool],
    ) -> str | None:
        """Return a tool result string on block/deny, or None to proceed."""
        err = validate_tool_paths(name, inputs, cwd)
        if err:
            return f"[tool error] {err}"

        if name in ("read_file", "list_files", "search_files"):
            return None

        if name == "edit_file":
            if policy.edit_approved:
                return None
            return self._wait_for_ui("edit", inputs, cwd, policy, is_cancelled)

        if is_shell_tool(name):
            if policy.bash_skip_prompts:
                return None
            return self._wait_for_ui(SHELL_TOOL_NAME, inputs, cwd, policy, is_cancelled)

        return None

    def check_extension_tool(
        self,
        name: str,
        inputs: dict,
        cwd: str,
        policy: ConversationToolPolicy,
        is_cancelled: Callable[[], bool],
    ) -> str | None:
        if name in policy.approved_extension_tools:
            return None
        return self._wait_for_ui("tool", inputs, cwd, policy, is_cancelled, tool_name=name)

    def cancel_wait(self, message: str = "[cancelled]") -> None:
        with self._lock:
            pending = self._current
        if pending is None:
            return
        pending.approved = False
        pending.denied_message = message
        pending.event.set()

    def complete(
        self,
        pending: PendingApproval,
        *,
        approved: bool,
        grant_edit: bool = False,
        grant_bash_skip: bool = False,
        grant_extension_tool: bool = False,
        message: str = "User denied.",
    ) -> None:
        pending.approved = approved
        pending.denied_message = message
        pending.grant_edit = grant_edit
        pending.grant_bash_skip = grant_bash_skip
        pending.grant_extension_tool = grant_extension_tool
        pending.event.set()

    def _wait_for_ui(
        self,
        kind: str,
        inputs: dict,
        cwd: str,
        policy: ConversationToolPolicy,
        is_cancelled: Callable[[], bool],
        tool_name: str = "",
    ) -> str | None:
        pending = PendingApproval(
            kind=kind,
            inputs=inputs,
            cwd=cwd,
            policy=policy,
            tool_name=tool_name,
        )
        with self._lock:
            self._current = pending
        self.approval_needed.emit(pending)
        pending.event.wait()
        with self._lock:
            if self._current is pending:
                self._current = None

        if is_cancelled():
            return "[cancelled]"
        if pending.grant_edit:
            policy.edit_approved = True
        if pending.grant_bash_skip:
            policy.bash_skip_prompts = True
        if pending.grant_extension_tool and pending.tool_name:
            policy.approved_extension_tools.add(pending.tool_name)
        if not pending.approved:
            return pending.denied_message
        return None
