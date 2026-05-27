import json
import os
import re
import shutil
import sys
import subprocess
from pathlib import Path

# PowerShell 7+ colors stderr/stdout; strip before UI and model context.
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;]*[ -/]*[@-~]"
    r"|\x1b\][^\x07]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)
_ORPHAN_SGR_RE = re.compile(r"(?m)^(?:\[[0-9;]*m)+")

import config
from config import (
    DEFAULT_READ_FILE_LINES,
    IGNORED,
    MAX_READ_FILE_LINES,
    MAX_TOOL_OUTPUT_CHARS,
    MAX_TOOL_OUTPUT_LINES,
    MAX_TOOL_READ_BYTES,
)
from services.shell_tool import shell_tool_name
from services.content import is_visible_message
from services.tool_policy import resolve_path, validate_tool_paths
from services.tool_registry import ToolContext, ToolRegistry, load_extensions

# ── Tool schemas ──────────────────────────────────────────────────────────────

_PATH_DESC = "Path inside the workspace (relative or absolute)."
_CHAT_SEARCH_STOPWORDS = {
    "about", "after", "again", "been", "before", "chat", "chats", "could",
    "did", "discuss", "discussed", "does", "done", "for", "from", "had",
    "has", "have", "history", "into", "look", "past", "please", "search",
    "that", "the", "this", "using", "was", "were", "what", "when", "with",
}


def _shell_tool_description() -> str:
    if sys.platform == "win32":
        return (
            "Run a PowerShell command and return stdout + stderr. "
            "Use PowerShell syntax (not POSIX sh). Prefer search_files over grep. "
            "Runs on your machine with your user account. Output is capped."
        )
    return (
        "Run a /bin/sh command and return stdout + stderr. "
        "Use POSIX shell syntax. Prefer search_files over grep when searching the repo. "
        "Runs on your machine with your user account. Output is capped."
    )


def _strip_ansi(text: str) -> str:
    text = _ANSI_ESCAPE_RE.sub("", text)
    return _ORPHAN_SGR_RE.sub("", text)


def _shell_env() -> dict:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["FORCE_COLOR"] = "0"
    env["CLICOLOR"] = "0"
    env["CLICOLOR_FORCE"] = "0"
    return env


def _powershell_command(command: str) -> str:
    return f"$PSStyle.OutputRendering = 'PlainText'; {command}"


def registry_for(cwd: str | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    _register_builtin_tools(registry)
    load_extensions(registry, cwd)
    return registry


def tools_anthropic(cwd: str | None = None) -> list:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in registry_for(cwd).all()
    ]


def tools_openai(cwd: str | None = None) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t["description"],
                "parameters":  t["input_schema"],
            },
        }
        for t in tools_anthropic(cwd)
    ]


def is_parallel_safe(name: str, cwd: str | None = None) -> bool:
    tool = registry_for(cwd).get(name)
    return bool(tool and tool.parallel_safe)


def tool_names(cwd: str | None = None) -> list[str]:
    return registry_for(cwd).names()


def tool_approval(name: str, cwd: str | None = None) -> str | None:
    tool = registry_for(cwd).get(name)
    return tool.approval if tool else None


# ── Executor ──────────────────────────────────────────────────────────────────

def execute(name: str, inputs: dict, cwd: str, on_line=None, cancel=None) -> str:
    try:
        registry = registry_for(cwd)
        tool = registry.get(name)
        if tool is None:
            return (
                f"[tool error] Unknown tool: {name}. Available tools: "
                f"{', '.join(registry.names())}. Call one of these exact tool names "
                "directly; do not wrap tool calls in script runners or namespaces."
            )

        path_err = validate_tool_paths(name, inputs, cwd)
        if path_err:
            return f"[tool error] {path_err}"

        ctx = ToolContext(cwd=cwd, on_line=on_line, cancel=cancel)
        return tool.execute(ctx, inputs)

    except Exception as exc:
        return f"[tool error] {exc}"


def _register_builtin_tools(registry: ToolRegistry) -> None:
    registry.tool(
        name="read_file",
        description=(
            "Read one workspace file. Default: from the start, 64KB max. After search_files "
            "reports a line number, pass offset (1-based) and optional limit to read that "
            f"region (default {DEFAULT_READ_FILE_LINES} lines, max {MAX_READ_FILE_LINES}). "
            f"Prefer this over {shell_tool_name()} for reading a function or nearby context."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": _PATH_DESC},
                "offset": {
                    "type": "integer",
                    "description": "First line to include (1-based). Use with search_files line numbers.",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum lines to return when offset is set (default "
                        f"{DEFAULT_READ_FILE_LINES}, max {MAX_READ_FILE_LINES})."
                    ),
                },
            },
            "required": ["path"],
        },
        execute=_execute_read_file,
        parallel_safe=True,
        source="builtin",
    )
    registry.tool(
        name="edit_file",
        description=(
            "Modify workspace files. Use content to create a new file, append to "
            "add text at end-of-file, and edits[] for exact replacements. If the "
            "user asks to change a file, call this tool; do not merely describe "
            "the change."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": _PATH_DESC},
                "content": {
                    "type": "string",
                    "description": (
                        "Content for creating a new file. Use only when the file does "
                        "not exist, and do not combine with append or edits. Use "
                        "actual newline characters, not escaped '\\n' text."
                    ),
                },
                "append": {
                    "type": "string",
                    "description": (
                        "Text to append to the end of an existing file. Include any "
                        "wanted leading newline. Do not combine with content or edits. "
                        "Use actual newline characters, not escaped '\\n' text."
                    ),
                },
                "edits": {
                    "type": "array",
                    "description": (
                        "One or more targeted replacements for an existing file. "
                        "Each edit is matched against the original file, not "
                        "incrementally. For nearby changes, merge them into one edit."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {
                                "type": "string",
                                "description": (
                                    "Exact text to replace. It must be unique in the "
                                    "original file and must not overlap another edit."
                                ),
                            },
                            "newText": {
                                "type": "string",
                                "description": (
                                    "Replacement text for this targeted edit. Use "
                                    "actual newline characters, not escaped '\\n' text."
                                ),
                            },
                        },
                        "required": ["oldText", "newText"],
                    },
                },
            },
            "required": ["path"],
        },
        execute=_execute_edit_file,
        source="builtin",
    )
    registry.tool(
        name=shell_tool_name(),
        description=_shell_tool_description(),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        execute=_execute_shell_command,
        source="builtin",
    )
    registry.tool(
        name="list_files",
        description=(
            "List files and directories in the workspace. Use this to map a repo or "
            "docs folder before reading many files. Output is capped at 64KB or 2048 lines."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": f"Directory to list (default: workspace root). {_PATH_DESC}",
                },
                "glob": {
                    "type": "string",
                    "description": "Name filter e.g. '*.md' or '*' (default: all)",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to include nested paths (default: false).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum entries to return (default 200, max 1000).",
                },
            },
        },
        execute=_execute_list_files,
        parallel_safe=True,
        source="builtin",
    )
    registry.tool(
        name="search_files",
        description=(
            "Search for a text pattern across workspace files. Prefer this over "
            "reading many files when looking for APIs, docs, or examples. Output is "
            "capped at 64KB or 2048 lines."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text or basic regex to find"},
                "directory": {
                    "type": "string",
                    "description": f"Directory to search (default: workspace root). {_PATH_DESC}",
                },
                "glob": {"type": "string", "description": "File filter e.g. '*.py' (default: all)"},
            },
            "required": ["pattern"],
        },
        execute=_execute_search_files,
        parallel_safe=True,
        source="builtin",
    )
    registry.tool(
        name="search_project_chats",
        description=(
            "Search saved aichs conversations for this project and return compact, "
            "dated snippets. Read-only memory lookup; use only when past discussion "
            "or decisions are relevant."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words or phrase to search for in past chats.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum conversations to return (default 5, max 10).",
                },
            },
            "required": ["query"],
        },
        execute=_execute_search_project_chats,
        parallel_safe=True,
        source="builtin",
    )


def _execute_read_file(ctx: ToolContext, inputs: dict) -> str:
    p = resolve_path(inputs["path"], ctx.cwd)
    if not p.exists():
        return f"[tool error] File does not exist: {_display_path(p, ctx.cwd)}"
    if not p.is_file():
        return f"[tool error] Not a file: {_display_path(p, ctx.cwd)}"
    if "offset" in inputs or "limit" in inputs:
        offset = _bounded_int(inputs.get("offset"), default=1, minimum=1, maximum=10_000_000)
        limit = _bounded_int(
            inputs.get("limit"),
            default=DEFAULT_READ_FILE_LINES,
            minimum=1,
            maximum=MAX_READ_FILE_LINES,
        )
        return _read_text_lines(p, offset, limit, MAX_TOOL_READ_BYTES)
    return _read_text_limited(p, MAX_TOOL_READ_BYTES)


def _execute_edit_file(ctx: ToolContext, inputs: dict) -> str:
    return _edit_file(inputs, ctx.cwd)


def _execute_shell_command(ctx: ToolContext, inputs: dict) -> str:
    return _run_shell_command(inputs["command"], ctx.cwd, ctx.on_line, ctx.cancel)


def _execute_list_files(ctx: ToolContext, inputs: dict) -> str:
    directory = resolve_path(inputs.get("directory", ctx.cwd), ctx.cwd)
    glob = inputs.get("glob") or "*"
    recursive = _as_bool(inputs.get("recursive", False))
    limit = _bounded_int(inputs.get("limit"), default=200, minimum=1, maximum=1000)
    return _list_files(directory, glob, recursive, limit, ctx.cwd)


def _execute_search_files(ctx: ToolContext, inputs: dict) -> str:
    directory = resolve_path(inputs.get("directory", ctx.cwd), ctx.cwd)
    glob = inputs.get("glob", "*")
    return _search_files(directory, glob, inputs["pattern"], ctx.cwd)


def _execute_search_project_chats(ctx: ToolContext, inputs: dict) -> str:
    return _search_project_chats(
        str(inputs.get("query") or ""),
        ctx.cwd,
        inputs.get("limit"),
    )


def _read_text_limited(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as f:
        raw = f.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[truncated: showing {max_bytes} of {size} bytes]"
    return text


def _normalize_read_line(line: str) -> str:
    if line.endswith("\r\n"):
        line = line[:-2]
    elif line.endswith("\r"):
        line = line[:-1]
    return line + "\n"


def _read_text_lines(path: Path, offset: int, limit: int, max_bytes: int) -> str:
    selected: list[str] = []
    byte_count = 0
    file_lines = 0
    truncated_bytes = False

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        for line_no, line in enumerate(f, start=1):
            file_lines = line_no
            if line_no < offset:
                continue
            if len(selected) >= limit:
                continue
            line = _normalize_read_line(line)
            line_bytes = len(line.encode("utf-8"))
            if byte_count + line_bytes > max_bytes:
                truncated_bytes = True
                break
            selected.append(line)
            byte_count += line_bytes

    if offset > file_lines and file_lines > 0:
        return f"(empty: offset {offset} is past end of file at line {file_lines})"
    if offset > 1 and file_lines == 0:
        return f"(empty: offset {offset} is past end of file)"

    text = "".join(selected)
    if not text and offset <= max(file_lines, 1):
        return f"(empty: no lines in range starting at {offset})"

    end_line = offset + len(selected) - 1
    truncated_lines = file_lines > end_line and len(selected) == limit
    notes = [f"[read: lines {offset}-{end_line} of {file_lines}]"]
    if truncated_lines:
        notes.append(f"[truncated: more lines follow line {end_line}]")
    if truncated_bytes:
        notes.append(f"[truncated: {max_bytes} byte limit reached]")
    return text + "\n\n" + " ".join(notes)


def _edit_file(inputs: dict, cwd: str) -> str:
    path = resolve_path(inputs["path"], cwd)
    has_content = "content" in inputs
    has_append = "append" in inputs
    edits = inputs.get("edits")

    mode_count = int(has_content) + int(has_append) + int(edits is not None)
    if mode_count != 1:
        return "[tool error] edit_file requires exactly one of content, append, or edits"

    if has_content:
        content = inputs["content"]
        if not isinstance(content, str):
            return "[tool error] edit_file content must be a string"
        newline_err = _literal_newline_error("content", content)
        if newline_err:
            return newline_err
        if path.exists():
            return f"[tool error] File already exists: {_display_path(path, cwd)}"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="") as f:
            f.write(content)
        return f"Created {_display_path(path, cwd)} ({len(content)} chars)"

    if has_append:
        append = inputs["append"]
        if not isinstance(append, str):
            return "[tool error] edit_file append must be a string"
        newline_err = _literal_newline_error("append", append)
        if newline_err:
            return newline_err
        if not path.exists():
            return f"[tool error] File does not exist: {_display_path(path, cwd)}"
        if not path.is_file():
            return f"[tool error] Not a file: {_display_path(path, cwd)}"
        if append == "":
            return f"[tool error] No changes made to {_display_path(path, cwd)}"
        with path.open("a", encoding="utf-8", newline="") as f:
            f.write(append)
        return f"Appended {len(append)} chars to {_display_path(path, cwd)}"

    if not isinstance(edits, list) or not edits:
        return "[tool error] edit_file requires content for create or a non-empty edits array"
    if not path.exists():
        return f"[tool error] File does not exist: {_display_path(path, cwd)}"
    if not path.is_file():
        return f"[tool error] Not a file: {_display_path(path, cwd)}"

    with path.open("r", encoding="utf-8", newline="") as f:
        current = f.read()

    replacements = []
    for idx, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return f"[tool error] edits[{idx}] must be an object"
        old_text = edit.get("oldText")
        new_text = edit.get("newText")
        if not isinstance(old_text, str) or old_text == "":
            return f"[tool error] edits[{idx}].oldText must be a non-empty string"
        if not isinstance(new_text, str):
            return f"[tool error] edits[{idx}].newText must be a string"
        newline_err = _literal_newline_error(f"edits[{idx}].newText", new_text)
        if newline_err:
            return newline_err
        matches = [m.start() for m in re.finditer(re.escape(old_text), current)]
        if len(matches) != 1:
            return (
                f"[tool error] edits[{idx}].oldText in {_display_path(path, cwd)} "
                f"must match exactly once; found {len(matches)}."
            )
        start = matches[0]
        replacements.append((start, start + len(old_text), new_text, idx))

    replacements.sort(key=lambda item: item[0])
    for prev, cur in zip(replacements, replacements[1:]):
        if prev[1] > cur[0]:
            return (
                f"[tool error] edits[{prev[3]}] and edits[{cur[3]}] overlap in "
                f"{_display_path(path, cwd)}. Merge them into one edit."
            )

    updated = current
    for start, end, new_text, _ in reversed(replacements):
        updated = updated[:start] + new_text + updated[end:]
    if updated == current:
        return f"[tool error] No changes made to {_display_path(path, cwd)}"

    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(updated)
    delta = len(updated) - len(current)
    return (
        f"Edited {_display_path(path, cwd)}: {len(replacements)} replacement(s), "
        f"{delta:+d} chars"
    )


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _literal_newline_error(label: str, text: str) -> str | None:
    if "\\n" in text and "\n" not in text:
        return (
            f"[tool error] edit_file {label} contains literal '\\n' text. "
            "Use actual newline characters in the JSON string."
        )
    return None


def _run_shell_command(command: str, cwd: str, on_line=None, cancel=None) -> str:
    proc = subprocess.Popen(
        _shell_command_args(command),
        cwd=cwd,
        env=_shell_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines = []
    total_chars = 0
    total_lines = 0
    truncated = False
    for line in proc.stdout:
        line = _strip_ansi(line)
        if cancel and cancel.is_set():
            proc.kill()
            break
        if total_chars < MAX_TOOL_OUTPUT_CHARS and total_lines < MAX_TOOL_OUTPUT_LINES:
            total_lines += 1
            remaining = MAX_TOOL_OUTPUT_CHARS - total_chars
            if len(line) > remaining:
                lines.append(line[:remaining])
                total_chars = MAX_TOOL_OUTPUT_CHARS
                truncated = True
            else:
                lines.append(line)
                total_chars += len(line)
        else:
            truncated = True
        if on_line:
            on_line(line.rstrip("\n"))
    proc.wait(timeout=5)
    out = "".join(lines).strip() or "(no output)"
    if truncated:
        out += "\n\n[output truncated]"
    if proc.returncode not in (0, None):
        out += f"\n[exit {proc.returncode}]"
    return out


def _shell_command_args(command: str) -> list[str]:
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh")
        shell = pwsh or shutil.which("powershell") or "powershell"
        if pwsh:
            command = _powershell_command(command)
        return [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
    return ["/bin/sh", "-c", command]


def _list_files(directory: Path, glob: str, recursive: bool, limit: int, cwd: str) -> str:
    if not directory.exists():
        return f"Directory not found: {directory}"
    if not directory.is_dir():
        return f"Not a directory: {directory}"

    paths = sorted(
        _iter_list_paths(directory, glob, recursive),
        key=lambda p: (not p.is_dir(), _display_path(p, cwd).casefold()),
    )
    lines = []
    for path in paths[:limit]:
        suffix = "/" if path.is_dir() else ""
        lines.append(f"{_display_path(path, cwd)}{suffix}")

    if not lines:
        return "(no files)"

    omitted = len(paths) - len(lines)
    text = "\n".join(lines)
    if omitted > 0:
        text += f"\n\n[truncated: showing first {len(lines)} entries; {omitted} omitted]"
    return _trim_output(text)


def _iter_list_paths(directory: Path, glob: str, recursive: bool):
    iterator = directory.rglob(glob) if recursive else directory.glob(glob)
    for path in iterator:
        if path == directory:
            continue
        try:
            rel_parts = path.relative_to(directory).parts
        except ValueError:
            continue
        if any(part in IGNORED for part in rel_parts):
            continue
        yield path


def _search_files(directory: Path, glob: str, pattern: str, cwd: str) -> str:
    if not directory.exists():
        return f"Directory not found: {directory}"
    if not directory.is_dir():
        return f"Not a directory: {directory}"

    rg_output = _search_files_with_rg(directory, glob, pattern, cwd)
    if rg_output is not None:
        return rg_output

    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        return f"Invalid search pattern: {exc}"

    lines = []
    for path in sorted(_iter_search_paths(directory, glob)):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, start=1):
                    if matcher.search(line):
                        lines.append(f"{_display_path(path, cwd)}:{line_no}:{line.rstrip()}")
        except OSError as exc:
            lines.append(f"{_display_path(path, cwd)}: [read error: {exc}]")

    return _trim_output("\n".join(lines) or "(no matches)")


def _search_project_chats(query: str, cwd: str, limit=None) -> str:
    query = str(query or "").strip()
    if not query:
        return "[tool error] search_project_chats requires a query."
    try:
        max_results = int(limit) if limit is not None else 5
    except (TypeError, ValueError):
        max_results = 5
    max_results = max(1, min(10, max_results))

    conv_dir = Path(config.CONV_DIR)
    if not conv_dir.exists():
        return "(no saved conversations)"

    cwd_path = Path(cwd).resolve()
    matches = []
    for path in sorted(conv_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        scope = _conversation_project_scope(data, cwd_path)
        if scope is None:
            continue
        snippets = _conversation_snippets(data, query)
        title = str(data.get("title") or "Untitled")
        title_hit = query.casefold() in title.casefold()
        if not snippets and not title_hit:
            continue
        score = (4 if title_hit else 0) + len(snippets)
        matches.append((score, str(data.get("updated_at") or ""), path, data, scope, snippets))

    if not matches:
        return f"(no matches for {query!r} in project chat history)"

    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    lines = [f"Found {min(len(matches), max_results)} chat match(es) for {query!r}:"]
    for idx, (_score, _updated, path, data, scope, snippets) in enumerate(matches[:max_results], start=1):
        title = str(data.get("title") or "Untitled")
        updated = str(data.get("updated_at") or data.get("created_at") or "unknown date")
        scope_note = "current project" if scope == "project" else "legacy unscoped"
        lines.append(f"\n{idx}. {title} ({updated}, {scope_note}, id: {data.get('id') or path.stem})")
        if snippets:
            for snippet in snippets[:3]:
                lines.append(f"   - {snippet}")
        else:
            lines.append("   - Title matched; no message snippet found.")
    return _trim_output("\n".join(lines))


def _conversation_project_scope(data: dict, cwd_path: Path) -> str | None:
    saved_cwd = str(data.get("cwd") or "").strip()
    if not saved_cwd:
        return "legacy"
    try:
        if Path(saved_cwd).resolve() == cwd_path:
            return "project"
    except OSError:
        return None
    return None


def _conversation_snippets(data: dict, query: str) -> list[str]:
    terms = _chat_search_terms(query)
    snippets = []
    for msg in data.get("messages", []):
        if not is_visible_message(msg):
            continue
        text = _message_text(msg.get("content", ""))
        folded = text.casefold()
        if not any(term in folded for term in terms):
            continue
        role = str(msg.get("role") or "message")
        snippets.append(f"{role}: {_snippet(text, terms)}")
        if len(snippets) >= 5:
            break
    return snippets


def _chat_search_terms(query: str) -> list[str]:
    terms = [
        term.casefold()
        for term in re.findall(r"\w+", query)
        if len(term) >= 3 and term.casefold() not in _CHAT_SEARCH_STOPWORDS
    ]
    return terms or [query.casefold()]


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image":
                    parts.append("[image]")
                elif "content" in block:
                    parts.append(_message_text(block["content"]))
        return " ".join(parts)
    return str(content)


def _snippet(text: str, terms: list[str], radius: int = 120) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    folded = compact.casefold()
    positions = [folded.find(term) for term in terms if term and folded.find(term) >= 0]
    if not positions:
        return compact[: radius * 2]
    pos = min(positions)
    start = max(0, pos - radius)
    end = min(len(compact), pos + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


def _search_files_with_rg(directory: Path, glob: str, pattern: str, cwd: str) -> str | None:
    if not shutil.which("rg"):
        return None

    search_root = _display_path(directory, cwd)
    try:
        r = subprocess.run(
            [
                "rg",
                "--line-number",
                "--with-filename",
                "--color",
                "never",
                "--glob",
                glob,
                pattern,
                search_root,
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return "Search timed out after 30 seconds"

    if r.returncode == 0:
        return _trim_output(r.stdout.strip() or "(no matches)")
    if r.returncode == 1:
        return "(no matches)"

    output = (r.stderr or r.stdout).strip()
    return _trim_output(output or f"rg failed with exit code {r.returncode}")


def _iter_search_paths(directory: Path, glob: str):
    for path in directory.rglob(glob):
        if any(part in IGNORED for part in path.relative_to(directory).parts):
            continue
        if path.is_file():
            yield path


def _display_path(path: Path, cwd: str) -> str:
    try:
        return str(path.relative_to(Path(cwd).resolve()))
    except ValueError:
        return str(path)


def _trim_output(text: str) -> str:
    truncated = False
    lines = text.splitlines(keepends=True)
    if len(lines) > MAX_TOOL_OUTPUT_LINES:
        text = "".join(lines[:MAX_TOOL_OUTPUT_LINES])
        truncated = True
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS]
        truncated = True
    if truncated:
        return text + "\n\n[output truncated]"
    return text
