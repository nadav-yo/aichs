import json
import re
import shutil
import sys
import subprocess
from pathlib import Path

from config import IGNORED, MAX_TOOL_OUTPUT_CHARS, MAX_TOOL_READ_BYTES

# ── Tool schemas ──────────────────────────────────────────────────────────────

TOOLS_ANTHROPIC = [
    {
        "name": "read_file",
        "description": "Read the full contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or repo-relative path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write (or overwrite) a file with the given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "bash",
        "description": (
            "Run a host shell command and return stdout + stderr. "
            "On Windows, commands run in PowerShell. On macOS/Linux, commands run in /bin/sh. "
            "Keep commands short and safe."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for a text pattern across files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern":   {"type": "string", "description": "Text or basic regex to find"},
                "directory": {"type": "string", "description": "Directory to search (default: cwd)"},
                "glob":      {"type": "string", "description": "File filter e.g. '*.py' (default: all)"},
            },
            "required": ["pattern"],
        },
    },
]

# OpenAI wraps the same schema in a function envelope
TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name":        t["name"],
            "description": t["description"],
            "parameters":  t["input_schema"],
        },
    }
    for t in TOOLS_ANTHROPIC
]


# ── Executor ──────────────────────────────────────────────────────────────────

def execute(name: str, inputs: dict, cwd: str, on_line=None, cancel=None) -> str:
    try:
        if name == "read_file":
            p = _resolve(inputs["path"], cwd)
            return _read_text_limited(p, MAX_TOOL_READ_BYTES)

        elif name == "write_file":
            p = _resolve(inputs["path"], cwd)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(inputs["content"])
            return f"Wrote {len(inputs['content'])} chars to {p}"

        elif name == "bash":
            return _run_shell_command(inputs["command"], cwd, on_line, cancel)

        elif name == "search_files":
            directory = _resolve(inputs.get("directory", cwd), cwd)
            glob      = inputs.get("glob", "*")
            return _search_files(directory, glob, inputs["pattern"], cwd)

        else:
            return f"Unknown tool: {name}"

    except Exception as exc:
        return f"[tool error] {exc}"


def _resolve(path: str, cwd: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path(cwd) / p


def _read_text_limited(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as f:
        raw = f.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[truncated: showing {max_bytes} of {size} bytes]"
    return text


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
        return str(path.relative_to(Path(cwd)))
    except ValueError:
        return str(path)


def _trim_output(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:MAX_TOOL_OUTPUT_CHARS] + "\n\n[output truncated]"
