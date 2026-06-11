import re
from pathlib import Path

from config import MAX_TOOL_READ_BYTES


MENTION_RE = re.compile(r'@(?:"([^"]+)"|([^\s@]*[^\s@.,:;!?)\]}]))')


def message_file_refs(text: str, hidden_refs: list[str] | None = None) -> list[str]:
    refs = [
        (match.group(1) or match.group(2) or "").strip()
        for match in MENTION_RE.finditer(str(text or ""))
    ]
    refs.extend(hidden_refs or [])
    return refs


def files_for_refs(cwd: str, refs: list[str]) -> list[dict]:
    root = Path(cwd).resolve()
    seen: set[str] = set()
    files: list[dict] = []
    for raw in refs:
        lookup = str(raw or "").replace("\\", "/")
        if not lookup:
            continue
        key = lookup.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            path = (root / lookup).resolve()
            path.relative_to(root)
        except (OSError, ValueError):
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            with path.open("rb") as f:
                data = f.read(MAX_TOOL_READ_BYTES + 1)
        except OSError:
            continue
        truncated = len(data) > MAX_TOOL_READ_BYTES
        content = data[:MAX_TOOL_READ_BYTES].decode("utf-8", errors="replace")
        files.append({
            "path": raw,
            "content": content,
            "truncated": truncated,
            "size": size,
        })
    return files
