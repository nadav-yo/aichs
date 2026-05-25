import json
import re
import shutil
import sys
import subprocess
from pathlib import Path

from config import IGNORED, MAX_TOOL_OUTPUT_CHARS, MAX_TOOL_READ_BYTES
from services.tool_policy import resolve_path, validate_tool_paths
from services.tool_registry import ToolContext, ToolRegistry, load_extensions

# ── Tool schemas ──────────────────────────────────────────────────────────────

_PATH_DESC = "Path inside the workspace (relative or absolute)."


def _bash_tool_description() -> str:
    if sys.platform == "win32":
        shell = "PowerShell"
    else:
        shell = "/bin/sh"
    return (
        f"Run a shell command ({shell}) and return stdout + stderr. "
        "Runs on your machine with your user account."
    )


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
        description="Read the full contents of a file in the workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": _PATH_DESC},
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
        name="bash",
        description=_bash_tool_description(),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        execute=_execute_bash,
        source="builtin",
    )
    registry.tool(
        name="search_files",
        description="Search for a text pattern across files in a workspace directory.",
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


def _execute_read_file(ctx: ToolContext, inputs: dict) -> str:
    p = resolve_path(inputs["path"], ctx.cwd)
    return _read_text_limited(p, MAX_TOOL_READ_BYTES)


def _execute_edit_file(ctx: ToolContext, inputs: dict) -> str:
    return _edit_file(inputs, ctx.cwd)


def _execute_bash(ctx: ToolContext, inputs: dict) -> str:
    return _run_shell_command(inputs["command"], ctx.cwd, ctx.on_line, ctx.cancel)


def _execute_search_files(ctx: ToolContext, inputs: dict) -> str:
    directory = resolve_path(inputs.get("directory", ctx.cwd), ctx.cwd)
    glob = inputs.get("glob", "*")
    return _search_files(directory, glob, inputs["pattern"], ctx.cwd)


def _read_text_limited(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as f:
        raw = f.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[truncated: showing {max_bytes} of {size} bytes]"
    return text


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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines = []
    total_chars = 0
    truncated = False
    for line in proc.stdout:
        if cancel and cancel.is_set():
            proc.kill()
            break
        if total_chars < MAX_TOOL_OUTPUT_CHARS:
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
        shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
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
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:MAX_TOOL_OUTPUT_CHARS] + "\n\n[output truncated]"
