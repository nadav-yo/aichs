from __future__ import annotations

import re

from config import MAX_STORED_TERMINAL_OUTPUT_CHARS

TERMINAL_REF_NAME = "term"
TERMINAL_REF_MIME = "application/x-aichs-terminal-ref"
MAX_TERMINAL_REF_LINES = 400
# Every reference names its source terminal explicitly:
# ``#term[abc123:2:4]``.
_TERMINAL_REF_RE = re.compile(
    r"#term\[(?P<terminal_id>[A-Za-z0-9][A-Za-z0-9_-]*):"
    r"(?P<start>\d+)\s*:\s*(?P<end>\d+)\]"
)


def build_terminal_summary(result: dict) -> str:
    command = str(result.get("command") or "").strip()
    exit_code = result.get("exit_code")
    duration = float(result.get("duration_s") or 0.0)
    line_count = int(result.get("line_count") or 0)
    stored_line_count = int(result.get("stored_line_count") or 0)
    truncated = bool(result.get("truncated"))
    ref = terminal_ref(
        1,
        max(1, stored_line_count),
        str(result.get("terminal_id") or ""),
    )

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


def retain_terminal_output_tail(
    current: str,
    chunk: str,
    limit: int = MAX_STORED_TERMINAL_OUTPUT_CHARS,
) -> tuple[str, bool]:
    text = str(current or "") + str(chunk or "")
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    tail = text[-limit:]
    newline = tail.find("\n")
    if 0 <= newline < len(tail) - 1:
        tail = tail[newline + 1 :]
    return tail, True


def terminal_ref(start: int, end: int, terminal_id: str) -> str:
    terminal_id = str(terminal_id or "").strip()
    if not terminal_id:
        return ""
    return f"#{TERMINAL_REF_NAME}[{terminal_id}:{start}:{end}]"

def normalize_terminal_ref(ref: str) -> str:
    text = str(ref or "").strip()
    match = _TERMINAL_REF_RE.fullmatch(text)
    if not match:
        return text
    start, end = _parse_ref_range(match, 1_000_000_000)
    return terminal_ref(start, end, match.group("terminal_id") or "")


def has_terminal_refs(text: str) -> bool:
    return bool(_TERMINAL_REF_RE.search(str(text or "")))


def terminal_ref_ids(text: str) -> set[str]:
    """Return explicit terminal IDs referenced by *text* (legacy refs omit one)."""
    return {
        terminal_id
        for match in _TERMINAL_REF_RE.finditer(str(text or ""))
        if (terminal_id := str(match.group("terminal_id") or "").strip())
    }


def terminal_ref_id(ref: str) -> str:
    """Return the optional terminal ID embedded in one reference token."""
    match = _TERMINAL_REF_RE.fullmatch(str(ref or "").strip())
    return str(match.group("terminal_id") or "") if match else ""



def selection_capture_key(start: int, end: int) -> str:
    return f"{int(start)}:{int(end)}"


def expand_terminal_refs(text: str, previous_terminal_messages: list[dict]) -> str:
    if not text or not previous_terminal_messages:
        return ""
    matches = list(_TERMINAL_REF_RE.finditer(text))
    if not matches:
        return ""

    sections = []
    for match in matches:
        terminal_id = str(match.group("terminal_id") or "").strip()
        terminal = _terminal_for_ref(previous_terminal_messages, terminal_id)
        if terminal is None:
            label = terminal_ref(1, 1, terminal_id)
            sections.append(f"{label}: terminal output is no longer available.")
            continue
        result = terminal.get("terminal") if isinstance(terminal.get("terminal"), dict) else {}
        output = str(result.get("output") or terminal.get("terminal_output") or "")
        lines = output.splitlines()
        command = str(result.get("command") or terminal.get("terminal_command") or "").strip()
        captures = _selection_captures(result)
        requested_start, requested_end = _parse_ref_range(match, max(len(lines), 1))
        captured = captures.get(selection_capture_key(requested_start, requested_end))
        if captured is None and (
            requested_start < 1 or requested_start > max(1, len(lines))
        ):
            sections.append(
                f"{match.group(0)}: no stored terminal output lines in that range."
            )
            continue

        if captured is not None:
            end = requested_end
            selected = str(captured).splitlines()
            truncated = len(selected) > MAX_TERMINAL_REF_LINES
            selected = selected[:MAX_TERMINAL_REF_LINES]
        else:
            end = min(requested_end, len(lines))
            max_end = requested_start + MAX_TERMINAL_REF_LINES - 1
            truncated = end > max_end
            end = min(end, max_end)
            selected = lines[requested_start - 1:end]
        label = terminal_ref(requested_start, end, terminal_id)
        heading = f"Terminal output {label}"
        if command:
            heading += f" from command: {command}"
        body = "\n".join(selected) if selected else "(no output)"
        if truncated:
            body += f"\n\n[ref truncated: showing first {MAX_TERMINAL_REF_LINES} requested lines]"
        sections.append(f"{heading}\n```text\n{body}\n```")
    return "\n\n".join(sections)


def _selection_captures(result: dict) -> dict[str, str]:
    raw = result.get("selection_captures")
    if not isinstance(raw, dict):
        return {}
    captures: dict[str, str] = {}
    for key, value in raw.items():
        text = str(value or "")
        if not str(key or "").strip() or not text.strip():
            continue
        captures[str(key)] = text
    return captures


def _parse_ref_range(match: re.Match, line_count: int) -> tuple[int, int]:
    raw_start = match.group("start")
    raw_end = match.group("end")
    start = int(raw_start)
    end = int(raw_end)
    if end < start:
        end = start
    return start, end


def _terminal_for_ref(messages: list[dict], terminal_id: str) -> dict | None:
    for message in reversed(messages):
        result = message.get("terminal") if isinstance(message.get("terminal"), dict) else {}
        if str(result.get("terminal_id") or "") == terminal_id:
            return message
    return None
