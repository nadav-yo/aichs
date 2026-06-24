import json

import config
from services.mcp_config import (
    import_mcp_json,
    load_mcp_config,
    mcp_config_exists,
    remove_mcp_server,
    review_mcp_server,
    set_mcp_component_enabled,
    set_mcp_server_enabled,
    upsert_mcp_server,
    write_mcp_json,
)


def test_mcp_config_missing_is_zero_overhead_fast_path(workspace):
    assert not mcp_config_exists(str(workspace))
    snapshot = load_mcp_config(str(workspace))
    assert snapshot.servers == ()
    assert snapshot.errors == ()


def test_mcp_config_loads_global_mcpservers_shape(workspace):
    path = config.AICHS_HOME / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "context7": {
                        "command": "npx",
                        "args": ["-y", "@upstash/context7-mcp"],
                        "env": {"TOKEN": "value"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_mcp_config(str(workspace))
    assert [server.name for server in snapshot.servers] == ["context7"]
    server = snapshot.servers[0]
    assert server.scope == "global"
    assert server.command == "npx"
    assert server.args == ("-y", "@upstash/context7-mcp")
    assert server.env == {"TOKEN": "value"}
    assert server.enabled is True
    assert server.review_required is False


def test_project_mcp_config_requires_review_before_enable(workspace):
    local = workspace / ".agents" / "mcp.json"
    local.parent.mkdir(parents=True)
    local.write_text(
        json.dumps({"mcpServers": {"local-docs": {"command": "python", "args": ["server.py"]}}}),
        encoding="utf-8",
    )

    first = load_mcp_config(str(workspace), include_disabled=True)
    server = first.servers[0]
    assert server.scope == "project"
    assert server.review_required is True
    assert server.enabled is False

    assert review_mcp_server(str(workspace), "project", "local-docs")
    set_mcp_server_enabled(str(workspace), "project", "local-docs", True)
    reviewed = load_mcp_config(str(workspace), include_disabled=True).servers[0]
    assert reviewed.review_required is False
    assert reviewed.enabled is True


def test_legacy_project_aichs_mcp_config_is_not_loaded(workspace):
    legacy = workspace / ".aichs" / "mcp.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"mcpServers": {"legacy": {"command": "legacy-server"}}}),
        encoding="utf-8",
    )

    assert not mcp_config_exists(str(workspace))
    assert load_mcp_config(str(workspace)).servers == ()


def test_mcp_config_accepts_http_servers_and_headers(workspace):
    path = config.AICHS_HOME / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "linear": {
                        "url": "https://mcp.linear.app/mcp",
                        "http_headers": {"X-Test": "1"},
                        "bearer_token_env_var": "LINEAR_TOKEN",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    server = load_mcp_config(str(workspace)).servers[0]
    assert server.transport == "http"
    assert server.url == "https://mcp.linear.app/mcp"
    assert server.headers == {"X-Test": "1"}
    assert server.bearer_token_env_var == "LINEAR_TOKEN"
    assert server.auth_type == "headers"


def test_mcp_config_accepts_oauth_auth_shape(workspace):
    path = config.AICHS_HOME / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "linear": {
                        "url": "https://mcp.linear.app/mcp",
                        "auth": {
                            "type": "oauth",
                            "scope": "read write",
                            "redirect_uri": "http://localhost:9999/callback",
                            "server_url": "https://mcp.linear.app",
                            "client_id": "client-id",
                            "client_secret": "client-secret",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    server = load_mcp_config(str(workspace)).servers[0]
    assert server.auth_type == "oauth"
    assert server.oauth_scope == "read write"
    assert server.oauth_redirect_uri == "http://localhost:9999/callback"
    assert server.oauth_server_url == "https://mcp.linear.app"
    assert server.oauth_client_id == "client-id"
    assert server.oauth_client_secret == "client-secret"


def test_mcp_config_can_import_and_upsert_standard_json(workspace):
    import_mcp_json(
        str(workspace),
        "global",
        {"mcpServers": {"first": {"command": "first-server"}}},
    )
    upsert_mcp_server(
        str(workspace),
        "global",
        "second",
        {"url": "https://example.test/mcp", "startup_timeout_sec": 2, "tool_timeout_sec": 3},
    )

    servers = {server.name: server for server in load_mcp_config(str(workspace)).servers}
    assert set(servers) == {"first", "second"}
    assert servers["second"].startup_timeout_sec == 2
    assert servers["second"].tool_timeout_sec == 3


def test_mcp_config_remove_server_deletes_config_and_state(workspace):
    import_mcp_json(
        str(workspace),
        "global",
        {"mcpServers": {"docs": {"command": "docs-server"}, "other": {"command": "other"}}},
    )
    set_mcp_server_enabled(str(workspace), "global", "docs", False)
    set_mcp_component_enabled(str(workspace), "global", "docs", "tools", "lookup", False)

    assert remove_mcp_server(str(workspace), "global", "docs") is True

    saved = json.loads((config.AICHS_HOME / "mcp.json").read_text(encoding="utf-8"))
    assert set(saved["mcpServers"]) == {"other"}
    state = json.loads((config.AICHS_HOME / "project" / "mcp.state.json").read_text(encoding="utf-8"))
    assert "global:docs" not in state.get("servers", {})
    assert [server.name for server in load_mcp_config(str(workspace), include_disabled=True).servers] == ["other"]
    assert remove_mcp_server(str(workspace), "global", "docs") is False


def test_mcp_config_import_accepts_github_servers_shape(workspace):
    path = import_mcp_json(
        str(workspace),
        "global",
        {
            "servers": {
                "github": {
                    "type": "http",
                    "url": "https://api.githubcopilot.com/mcp/",
                }
            }
        },
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "mcpServers" in saved
    assert "servers" not in saved
    assert saved["mcpServers"]["github"]["type"] == "http"
    server = load_mcp_config(str(workspace)).servers[0]
    assert server.name == "github"
    assert server.transport == "http"
    assert server.url == "https://api.githubcopilot.com/mcp/"
    assert server.auth_type == "auto"


def test_mcp_config_explicit_http_no_auth_stays_none(workspace):
    path = config.AICHS_HOME / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local-http": {
                        "url": "http://localhost:3000/mcp",
                        "auth": "none",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    server = load_mcp_config(str(workspace)).servers[0]
    assert server.auth_type == "none"


def test_mcp_component_enabled_state_round_trips(workspace):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )

    set_mcp_component_enabled(str(workspace), "global", "docs", "tools", "lookup", False)
    set_mcp_component_enabled(str(workspace), "global", "docs", "resources", "*", False)
    server = load_mcp_config(str(workspace)).servers[0]

    assert server.disabled_components == {
        "resources": ("*",),
        "tools": ("lookup",),
    }
    assert server.component_enabled("tools", "lookup") is False
    assert server.component_enabled("tools", "other") is True
    assert server.component_enabled("resources", "doc://one") is False

    set_mcp_component_enabled(str(workspace), "global", "docs", "tools", "lookup", True)
    server = load_mcp_config(str(workspace)).servers[0]
    assert server.component_enabled("tools", "lookup") is True


def test_mcp_component_enabled_rejects_invalid_inputs(workspace):
    try:
        set_mcp_component_enabled(str(workspace), "global", "docs", "bad", "x", False)
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("invalid MCP component kind should fail")

    try:
        set_mcp_component_enabled(str(workspace), "global", "docs", "tools", "", False)
    except ValueError as exc:
        assert "required" in str(exc)
    else:
        raise AssertionError("empty MCP component name should fail")


def test_mcp_config_global_disabled_server_is_hidden_by_default(workspace):
    path = config.AICHS_HOME / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"off": {"command": "server", "enabled": False}}}),
        encoding="utf-8",
    )

    assert load_mcp_config(str(workspace)).servers == ()
    assert [server.name for server in load_mcp_config(str(workspace), include_disabled=True).servers] == ["off"]


def test_mcp_config_reports_invalid_server_entries(workspace):
    path = config.AICHS_HOME / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"bad": {"command": "server", "url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )

    server = load_mcp_config(str(workspace), include_disabled=True).servers[0]
    assert server.errors == ("MCP server cannot define both 'command' and 'url'.",)
    assert server.available is False


def test_write_mcp_json_uses_portable_shape(tmp_path):
    target = tmp_path / "mcp.json"
    write_mcp_json(target, {"demo": {"command": "server"}})
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "mcpServers": {"demo": {"command": "server"}}
    }


def test_import_mcp_json_rejects_missing_server_wrappers(workspace):
    try:
        import_mcp_json(str(workspace), "global", {"tools": {}})
    except ValueError as exc:
        assert "mcpServers" in str(exc)
        assert "servers" in str(exc)
    else:
        raise AssertionError("import_mcp_json should reject missing server wrappers")

