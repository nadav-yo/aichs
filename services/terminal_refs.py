from __future__ import annotations

import re

TERMINAL_REF_NAME = "term"
TERMINAL_REF_MIME = "application/x-aichs-terminal-ref"
MAX_TERMINAL_REF_LINES = 400
_TERMINAL_REF_RE = re.compile(r"!term\[(\d*)\s*(?::\s*(\d*))?\]")


def build_terminal_summary(result: dict) -> str:
    command = str(result.get("command") or "").strip()
    exit_code = result.get("exit_code")
    duration = float(result.get("duration_s") or 0.0)
    line_count = int(result.get("line_count") or 0)
    stored_line_count = int(result.get("stored_line_count") or 0)
    truncated = bool(result.get("truncated"))
    ref = terminal_ref(1, max(1, stored_line_count))

    status = "running" if exit_code is None else f"exit {exit_code}"
    header = (
        f"Terminal · {status} · {duration:.1f}s · "
        f"{line_count} line{'s' if line_count != 1 else ''}"
    )
    if truncated:
        header += f" ({stored_line_count} stored)"

    parts = [header]
    if command:
        parts.append(f"Command: {command}")
    parts.append(f"Output reference: {ref}")
    return "\n\n".join(parts)


def terminal_ref(start: int, end: int) -> str:
    return f"!{TERMINAL_REF_NAME}[{start}:{end}]"


def expand_terminal_refs(text: str, previous_terminal_messages: list[dict]) -> str:
    if not text or not previous_terminal_messages:
        return ""
    matches = list(_TERMINAL_REF_RE.finditer(text))
    if not matches:
        return ""

    terminal = previous_terminal_messages[-1]
    result = terminal.get("terminal") if isinstance(terminal.get("terminal"), dict) else {}
    output = str(result.get("output") or terminal.get("terminal_output") or "")
    lines = output.splitlines()
    command = str(result.get("command") or terminal.get("terminal_command") or "").strip()

    sections = []
    for match in matches:
        requested_start, requested_end = _parse_ref_range(match, len(lines))
        if requested_start < 1 or requested_start > max(1, len(lines)):
            sections.append(
                f"{match.group(0)}: no stored terminal output lines in that range."
            )
            continue

        end = min(requested_end, len(lines))
        max_end = requested_start + MAX_TERMINAL_REF_LINES - 1
        truncated = end > max_end
        end = min(end, max_end)
        selected = lines[requested_start - 1:end]
        label = terminal_ref(requested_start, end)
        heading = f"Terminal output {label}"
        if command:
            heading += f" from command: {command}"
        body = "\n".join(selected) if selected else "(no output)"
        if truncated:
            body += f"\n\n[ref truncated: showing first {MAX_TERMINAL_REF_LINES} requested lines]"
        sections.append(f"{heading}\n```text\n{body}\n```")
    return "\n\n".join(sections)


def _parse_ref_range(match: re.Match, line_count: int) -> tuple[int, int]:
    raw_start = match.group(1)
    raw_end = match.group(2)
    start = int(raw_start) if raw_start else 1
    if raw_end is None:
        end = start
    elif raw_end:
        end = int(raw_end)
    else:
        end = line_count
    if end < start:
        end = start
    return start, end
