import os
import sys
from pathlib import Path

from config import IGNORED, MAX_TREE_ENTRIES_PER_DIR, SYSTEM_PROMPT
from services.crew import crew_roster_prompt
from services.subprocess_utils import run_no_window
from services.tool_registry import extension_context_snippets
from services.shell_tool import SHELL_TOOL_NAME


def agents_md(repo_path: str) -> Path | None:
    """Return path to AGENTS.md if present in the repo root, else None."""
    p = Path(repo_path) / "AGENTS.md"
    return p if p.exists() else None


def build_system(repo_path: str, prompt: str | None = None) -> str:
    """Return the full system prompt with live workspace context appended."""
    base, agents, workspace, extensions = system_parts(repo_path, prompt)
    parts = [
        base,
        "Use the following project context silently. Do not announce that you loaded, "
        "integrated, or reviewed these sections unless the user asks about context.",
    ]
    if agents:
        parts.append(f"## Project Instructions (AGENTS.md)\n{agents}")
    parts.append(f"## Workspace\n{workspace}")
    parts.append(f"## Crew\n{crew_roster_prompt()}")
    if extensions:
        parts.append(f"## Extension Context\n{extensions}")
    return "\n\n".join(parts)


def system_parts(repo_path: str, prompt: str | None = None) -> tuple[str, str, str, str]:
    """Return (base prompt, AGENTS.md body, workspace context, extension context)."""
    base = prompt if prompt else SYSTEM_PROMPT
    agents = ""
    mem = agents_md(repo_path)
    if mem:
        content = mem.read_text(errors="replace").strip()
        if content:
            agents = content
    workspace = _build_context(repo_path)
    extensions = _build_extension_context(repo_path)
    return base, agents, workspace, extensions


def _build_context(repo_path: str) -> str:
    lines = [
        f"Working directory: {repo_path}",
        f"Host shell: {_host_shell_name()}",
        "Tool use: call only the exact advertised tool names; never wrap tool calls in script runners or provider-specific namespaces.",
        "Broad review: list/search first, then read targeted files in small batches; narrow the task if tool output is truncated.",
        "",
        "File tree:",
    ]
    lines += _tree(repo_path, repo_path)

    status = _run(["git", "status", "--short"], repo_path)
    if status:
        lines += ["", "Git status:", status]

    log = _run(["git", "log", "--oneline", "-5"], repo_path)
    if log:
        lines += ["", "Recent commits:", log]

    return "\n".join(lines)


def _build_extension_context(repo_path: str) -> str:
    snippets, _errors = extension_context_snippets(repo_path)
    if not snippets:
        return ""
    lines = []
    for name, text in snippets:
        lines += [f"### {name}", text]
    return "\n".join(lines)


def _host_shell_name() -> str:
    if sys.platform == "win32":
        return (
            f"PowerShell on Windows. Call the {SHELL_TOOL_NAME} tool with PowerShell syntax; "
            "prefer search_files over grep."
        )
    return (
        f"POSIX /bin/sh. Call the {SHELL_TOOL_NAME} tool with sh-compatible commands; "
        "prefer search_files over grep."
    )


def _tree(base: str, path: str, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 3:
        return ["    …"]
    lines = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return []
    visible = [
        e for e in entries
        if e.name not in IGNORED and not e.name.startswith(".")
    ]
    for e in visible[:MAX_TREE_ENTRIES_PER_DIR]:
        lines.append(f"{prefix}├── {e.name}")
        if e.is_dir():
            lines += _tree(base, e.path, prefix + "│   ", depth + 1)
    omitted = len(visible) - MAX_TREE_ENTRIES_PER_DIR
    if omitted > 0:
        lines.append(f"{prefix}├── … {omitted} more")
    return lines


def _run(cmd: list, cwd: str) -> str:
    try:
        r = run_no_window(cmd, cwd=cwd, capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""
