from __future__ import annotations

import json

AICHS_EDITOR_REF_MIME = "application/x-aichs-editor-ref"
MAX_EDITOR_REF_TEXT_CHARS = 12_000


def editor_ref_payload(refs: list[dict]) -> bytes:
    return json.dumps(
        {"kind": "aichs-editor-ref", "refs": _clean_refs(refs)},
        separators=(",", ":"),
    ).encode("utf-8")


def parse_editor_refs(raw: bytes | bytearray | memoryview) -> list[dict]:
    try:
        data = json.loads(bytes(raw).decode("utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict) or data.get("kind") != "aichs-editor-ref":
        return []
    return _clean_refs(data.get("refs", []))


def editor_ref_text(refs: list[dict]) -> str:
    parts = []
    for ref in _clean_refs(refs):
        path = ref["path"]
        suffix = _line_suffix(ref.get("start_line", 0), ref.get("end_line", 0))
        token = f'@"{path}"' if any(ch.isspace() for ch in path) else f"@{path}"
        parts.append(token + suffix)
    return " ".join(parts)


def editor_ref_paths(refs: list[dict]) -> list[str]:
    out = []
    seen = set()
    for ref in _clean_refs(refs):
        path = ref["path"]
        key = path.casefold()
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _clean_refs(refs) -> list[dict]:
    out = []
    seen = set()
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        path = _clean_path(ref.get("path"))
        if not path:
            continue
        start_line = _positive_int(ref.get("start_line"))
        end_line = max(start_line, _positive_int(ref.get("end_line")))
        key = (path.casefold(), start_line, end_line)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "text": _clean_text(ref.get("text")),
        })
    return out


def _clean_path(value) -> str:
    return str(value or "").strip().strip("\r\n").replace("\\", "/")


def _clean_text(value) -> str:
    text = str(value or "").replace("\u2029", "\n").strip()
    return text[:MAX_EDITOR_REF_TEXT_CHARS]


def _positive_int(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, parsed)


def _line_suffix(start_line: int, end_line: int) -> str:
    if start_line <= 0:
        return ""
    if end_line <= start_line:
        return f":{start_line}"
    return f":{start_line}-{end_line}"
