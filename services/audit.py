"""Governance audit logging.

Audit is intentionally local and metadata-only in v1. The policy authority and
other governance-adjacent code write labels, decisions, hashes, and path
summaries here, while this module handles redaction and log trimming. Audit
failures are best-effort: a filesystem error must not crash personal mode or
block governed execution in v1.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config


AUDIT_LOG_NAME = "audit.jsonl"
MAX_AUDIT_BYTES = 1_000_000

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|bearer|credential)",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9._~+/=-]+"
    r"|((?:api[_-]?key|token|secret|password)=)[^,\s]+",
    re.IGNORECASE,
)


def audit_log_path() -> Path:
    return config.AICHS_HOME / "organization" / AUDIT_LOG_NAME


def append_audit_event(event: str, **details: Any) -> None:
    """Append one sanitized JSONL governance audit row.

    Callers may pass dictionaries/lists of ordinary metadata. Secret-looking
    keys and bearer/API-token text are redacted here so enforcement points do
    not each need their own redaction rules.
    """

    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": str(event or "event"),
    }
    clean = {
        str(key): _sanitize_value(value)
        for key, value in details.items()
        if value is not None
    }
    row.update(clean)
    path = audit_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        _trim_log(path)
    except OSError:
        return


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            key_text = str(key)
            clean[key_text] = "[redacted]" if _SECRET_KEY_RE.search(key_text) else _sanitize_value(item)
        return clean
    return _sanitize_text(str(value))


def _sanitize_text(value: str) -> str:
    text = str(value or "")

    def repl(match: re.Match) -> str:
        if match.group(1):
            return match.group(1) + "[redacted]"
        if match.group(2):
            return match.group(2) + "[redacted]"
        return "[redacted]"

    text = _SECRET_TEXT_RE.sub(repl, text)
    if len(text) > 500:
        return text[:500].rstrip() + "...[truncated]"
    return text


def _trim_log(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= MAX_AUDIT_BYTES:
        return
    try:
        data = path.read_bytes()[-MAX_AUDIT_BYTES:]
        first_newline = data.find(b"\n")
        if first_newline >= 0:
            data = data[first_newline + 1 :]
        path.write_bytes(data)
    except OSError:
        return
