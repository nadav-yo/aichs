import json

from services.mcp_config import McpServerConfig
import services.mcp_logs as mcp_logs
from services.mcp_logs import (
    append_mcp_log,
    clear_mcp_logs,
    format_mcp_logs,
    format_mcp_logs_html,
    sanitize_mcp_log_text,
    sanitize_mcp_log_value,
)


def test_mcp_activity_sanitizes_auth_challenge_and_tokens(workspace):
    server = McpServerConfig(name="github", scope="project", raw={})
    append_mcp_log(
        server,
        "capabilities_failed",
        'MCP initialization failed. HTTP 400 from https://api.githubcopilot.com/mcp/. '
        'bad request: Authorization header is badly formatted Bearer error="invalid_token", '
        'error_description="Invalid token", access_token=secret-token',
    )

    text = format_mcp_logs(server)

    assert "Discovery failed" in text
    assert "Authorization header was rejected" in text
    assert 'OAuth challenge: error="invalid_token"' in text
    assert "secret-token" not in text
    assert "access_token=[redacted]" in text


def test_mcp_activity_html_uses_event_formatting(workspace):
    server = McpServerConfig(name="github", scope="project", raw={})
    append_mcp_log(server, "connect_failed", "Connection refused")

    html = format_mcp_logs_html(server)

    assert "Connect failed" in html
    assert "#fecaca" in html
    assert "Connection refused" in html


def test_clear_mcp_logs_removes_only_selected_server(workspace):
    github = McpServerConfig(name="github", scope="project", raw={})
    unreal = McpServerConfig(name="unreal", scope="project", raw={})
    append_mcp_log(github, "connect_failed", "bad token")
    append_mcp_log(unreal, "connect_succeeded", "ok")

    clear_mcp_logs(github)

    assert "No MCP activity for github yet." in format_mcp_logs(github)
    assert "Connected" in format_mcp_logs(unreal)


def test_empty_log_messages_escape_server_name(workspace):
    server = McpServerConfig(name="<github>", scope="project", raw={})

    assert format_mcp_logs(server) == "No MCP activity for <github> yet."
    html = format_mcp_logs_html(server)

    assert "No MCP activity for &lt;github&gt; yet." in html
    assert "<github>" not in html


def test_append_mcp_log_serializes_details_and_applies_limit(workspace):
    class Opaque:
        def __str__(self):
            return "opaque-object"

    server = McpServerConfig(name="github", scope="project", raw={})
    append_mcp_log(
        server,
        "tool_started",
        "first Bearer : abc123",
        ignored=None,
        nested={"secret": "refresh_token=refresh-secret", "items": ["client_secret=client-secret"]},
        tuple_value=("id_token=id-secret", Opaque()),
    )
    append_mcp_log(server, "", "second")

    text = format_mcp_logs(server, limit=1)

    assert "First" not in text
    assert "Event  second" in text

    text = format_mcp_logs(server, limit=2)

    assert "Tool call" in text
    assert "Bearer [redacted]" in text
    assert "refresh-secret" not in text
    assert "client-secret" not in text
    assert "id-secret" not in text
    assert "refresh_token=[redacted]" in text
    assert "client_secret=[redacted]" in text
    assert "id_token=[redacted]" in text
    assert "opaque-object" in text
    assert "ignored=" not in text


def test_mcp_log_html_renders_detail_rows_and_event_tones(workspace):
    server = McpServerConfig(name="github", scope="project", raw={})
    append_mcp_log(server, "connect_started", "starting", path="C:/tmp/<repo>")
    append_mcp_log(server, "tool_succeeded", "done")
    append_mcp_log(server, "custom_event", "neutral")

    html = format_mcp_logs_html(server)

    assert "Connect started" in html
    assert "Tool complete" in html
    assert "Custom Event" in html
    assert "path=C:/tmp/&lt;repo&gt;" in html
    assert "#bfdbfe" in html
    assert "#bbf7d0" in html
    assert "#e4e4e7" in html


def test_sanitize_mcp_log_text_covers_token_forms():
    text = sanitize_mcp_log_text(
        'Bearer token-one '
        'Bearer:error-token '
        'Bearer error="invalid_token" '
        '"access_token":"json-access" '
        'refresh_token=refresh-value '
        'id_token=id-value '
        'client_secret=client-value '
        'Authorization header is badly formatted'
    )

    assert "token-one" not in text
    assert "error-token" not in text
    assert "json-access" not in text
    assert "refresh-value" not in text
    assert "id-value" not in text
    assert "client-value" not in text
    assert text.count("Bearer [redacted]") == 2
    assert 'OAuth challenge: error="invalid_token"' in text
    assert '"access_token":"[redacted]"' in text
    assert "refresh_token=[redacted]" in text
    assert "id_token=[redacted]" in text
    assert "client_secret=[redacted]" in text
    assert "Authorization header was rejected" in text


def test_sanitize_mcp_log_value_recurses_nested_structures():
    value = sanitize_mcp_log_value(
        {
            "token": "Bearer secret",
            7: ["access_token=abc", {"nested": "client_secret=xyz"}],
            "count": 3,
        }
    )

    assert value == {
        "token": "Bearer [redacted]",
        "7": ["access_token=[redacted]", {"nested": "client_secret=[redacted]"}],
        "count": 3,
    }


def test_read_log_rows_skips_corrupt_and_non_object_rows(workspace, monkeypatch):
    path = mcp_logs.mcp_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"server": "github", "scope": "project", "event": "connect_started"},
        {"server": "github", "scope": "project", "event": "connect_succeeded"},
    ]
    path.write_text(
        "not-json\n42\n"
        + "\n".join(json.dumps(row) for row in rows)
        + "\n",
        encoding="utf-8",
    )

    assert mcp_logs._read_log_rows(limit=1) == [rows[-1]]
    assert mcp_logs._read_log_rows(limit=10) == rows

    monkeypatch.setattr(mcp_logs, "MAX_LOG_BYTES", 90)
    path.write_bytes(
        b"x" * 80
        + b"\n"
        + b"\n".join(json.dumps(row).encode("utf-8") for row in rows)
        + b"\n"
    )

    assert mcp_logs._read_log_rows(limit=10) == [rows[-1]]


def test_trim_log_keeps_complete_tail_rows(workspace, monkeypatch):
    monkeypatch.setattr(mcp_logs, "MAX_LOG_BYTES", 512)
    server = McpServerConfig(name="github", scope="project", raw={})
    for idx in range(8):
        append_mcp_log(server, "connect_succeeded", f"message {idx}")

    text = mcp_logs.mcp_log_path().read_text(encoding="utf-8")

    assert "message 0" not in text
    assert "message 7" in text
    assert all(line.startswith("{") for line in text.splitlines())
    assert "Connected" in format_mcp_logs(server)


def test_mcp_log_file_errors_are_ignored(monkeypatch):
    class BadParent:
        def mkdir(self, **_kwargs):
            raise OSError("no mkdir")

    class BadPath:
        parent = BadParent()

        def exists(self):
            return True

        def read_bytes(self):
            raise OSError("no read")

        def write_text(self, *_args, **_kwargs):
            raise OSError("no write")

        def open(self, *_args, **_kwargs):
            raise OSError("no open")

        def stat(self):
            raise OSError("no stat")

    class BadTrimPath:
        def stat(self):
            return type("Stat", (), {"st_size": mcp_logs.MAX_LOG_BYTES + 1})()

        def read_bytes(self):
            raise OSError("no trim read")

    server = McpServerConfig(name="github", scope="project", raw={})
    bad_path = BadPath()
    monkeypatch.setattr(mcp_logs, "mcp_log_path", lambda: bad_path)

    append_mcp_log(server, "connect_started", "ignored")
    clear_mcp_logs(server)
    assert mcp_logs._read_log_rows(limit=5) == []
    mcp_logs._trim_log(bad_path)
    mcp_logs._trim_log(BadTrimPath())

