from services.mcp_config import McpServerConfig
from services.mcp_logs import append_mcp_log, clear_mcp_logs, format_mcp_logs, format_mcp_logs_html


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
