from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from services.mcp_config import McpServerConfig


MCP_LOG_NAME = "mcp.log"
MAX_LOG_BYTES = 256_000


def mcp_log_path() -> Path:
    return config.AICHS_HOME / "project" / MCP_LOG_NAME


def append_mcp_log(server: McpServerConfig, event: str, message: str = "", **details: Any) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server": server.name,
        "scope": server.scope,
        "event": str(event or "event"),
        "message": sanitize_mcp_log_text(str(message or "")),
    }
    clean_details = {str(key): sanitize_mcp_log_value(_jsonable(value)) for key, value in details.items() if value is not None}
    if clean_details:
        row["details"] = clean_details
    path = mcp_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        _trim_log(path)
    except OSError:
        return


def format_mcp_logs(server: McpServerConfig, *, limit: int = 200) -> str:
    rows = _server_log_rows(server, limit=limit)
    if not rows:
        return f"No MCP activity for {server.name} yet."
    lines = []
    for row in rows:
        line = _plain_log_line(row)
        if line:
            lines.append(line)
    return "\n".join(lines)


def format_mcp_logs_html(server: McpServerConfig, *, limit: int = 200) -> str:
    rows = _server_log_rows(server, limit=limit)
    if not rows:
        return (
            "<html><body style='margin:0; font-family:Segoe UI, sans-serif; color:#a1a1aa;'>"
            f"No MCP activity for {html.escape(server.name)} yet."
            "</body></html>"
        )
    body = []
    for row in rows:
        ts = html.escape(str(row.get("ts") or ""))
        event_name = str(row.get("event") or "event")
        event = html.escape(_event_label(event_name))
        message = html.escape(sanitize_mcp_log_text(str(row.get("message") or "")))
        details = row.get("details")
        detail_text = ""
        if isinstance(details, dict) and details:
            rendered = ", ".join(
                f"{key}={sanitize_mcp_log_value(value)}" for key, value in sorted(details.items())
            )
            detail_text = html.escape(rendered)
        tone = _event_tone(event_name)
        detail_html = f"<div class='details'>{detail_text}</div>" if detail_text else ""
        body.append(
            "<div class='row'>"
            f"<span class='ts'>{ts}</span>"
            f"<span class='badge' style='color:{tone['fg']}; background:{tone['bg']}; border-color:{tone['border']};'>{event}</span>"
            f"<span class='msg'>{message}</span>"
            f"{detail_html}"
            "</div>"
        )
    return (
        "<html><head><style>"
        "body { margin:0; font-family:Segoe UI, sans-serif; color:#e4e4e7; }"
        ".row { padding:7px 4px; border-bottom:1px solid #27272a; }"
        ".ts { color:#a1a1aa; font-family:Consolas, monospace; margin-right:10px; }"
        ".badge { border:1px solid; border-radius:5px; padding:1px 6px; font-weight:600; font-size:12px; margin-right:10px; }"
        ".msg { color:#e4e4e7; }"
        ".details { color:#a1a1aa; margin:4px 0 0 132px; font-family:Consolas, monospace; }"
        "</style></head><body>"
        + "".join(body)
        + "</body></html>"
    )


def clear_mcp_logs(server: McpServerConfig) -> None:
    path = mcp_log_path()
    rows = [
        row
        for row in _read_log_rows(limit=10_000)
        if not (row.get("server") == server.name and row.get("scope") == server.scope)
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows),
            encoding="utf-8",
        )
    except OSError:
        return


def _server_log_rows(server: McpServerConfig, *, limit: int) -> list[dict]:
    return [
        row
        for row in _read_log_rows(limit=max(limit * 4, limit))
        if row.get("server") == server.name and row.get("scope") == server.scope
    ][-limit:]


def _plain_log_line(row: dict) -> str:
    ts = str(row.get("ts") or "")
    event = _event_label(str(row.get("event") or "event"))
    message = sanitize_mcp_log_text(str(row.get("message") or ""))
    line = f"{ts}  {event}"
    if message:
        line += f"  {message}"
    details = row.get("details")
    if isinstance(details, dict) and details:
        rendered = ", ".join(
            f"{key}={sanitize_mcp_log_value(value)}" for key, value in sorted(details.items())
        )
        line += f"  ({rendered})"
    return line


def sanitize_mcp_log_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_mcp_log_text(value)
    if isinstance(value, list):
        return [sanitize_mcp_log_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_mcp_log_value(item) for key, item in value.items()}
    return value


def sanitize_mcp_log_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"Bearer\s*:\s*(?!error=)([A-Za-z0-9._~+/=-]+)",
        "Bearer [redacted]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"Bearer\s+(?!error=)([A-Za-z0-9._~+/=-]+)",
        "Bearer [redacted]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'((?:access|refresh|id)_token"\s*:\s*")[^"]+',
        r"\1[redacted]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"((?:access|refresh|id)_token=)[^,\s)]+",
        r"\1[redacted]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(client_secret=)[^,\s)]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"Authorization header is badly formatted", "Authorization header was rejected", text)
    text = re.sub(r"Bearer error=", "OAuth challenge: error=", text)
    return text


def _event_label(event: str) -> str:
    labels = {
        "connect_started": "Connect started",
        "connect_succeeded": "Connected",
        "connect_failed": "Connect failed",
        "capabilities_started": "Discovery started",
        "capabilities_succeeded": "Discovery complete",
        "capabilities_failed": "Discovery failed",
        "discovery_failed": "Discovery failed",
        "tool_started": "Tool call",
        "tool_succeeded": "Tool complete",
        "tool_failed": "Tool failed",
    }
    return labels.get(event, event.replace("_", " ").title())


def _event_tone(event: str) -> dict[str, str]:
    if event.endswith("_failed"):
        return {"fg": "#fecaca", "bg": "#341417", "border": "#7f1d1d"}
    if event.endswith("_started") or event == "tool_started":
        return {"fg": "#bfdbfe", "bg": "#172554", "border": "#1d4ed8"}
    if event.endswith("_succeeded") or event == "tool_succeeded":
        return {"fg": "#bbf7d0", "bg": "#123322", "border": "#166534"}
    return {"fg": "#e4e4e7", "bg": "#27272a", "border": "#3f3f46"}


def _read_log_rows(*, limit: int) -> list[dict]:
    path = mcp_log_path()
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if len(data) > MAX_LOG_BYTES:
        data = data[-MAX_LOG_BYTES:]
        first_newline = data.find(b"\n")
        if first_newline >= 0:
            data = data[first_newline + 1 :]
    rows = []
    for line in data.decode("utf-8", errors="replace").splitlines()[-limit:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _trim_log(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= MAX_LOG_BYTES:
        return
    try:
        data = path.read_bytes()[-MAX_LOG_BYTES:]
        first_newline = data.find(b"\n")
        if first_newline >= 0:
            data = data[first_newline + 1 :]
        path.write_bytes(data)
    except OSError:
        return


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)
