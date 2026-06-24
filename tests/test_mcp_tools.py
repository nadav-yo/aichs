import json
import sys
import types
from types import SimpleNamespace
import pytest

import config
from services.mcp_config import load_mcp_config
from services.mcp_config import set_mcp_component_enabled
from services.mcp_tools import (
    McpCapability,
    McpServerCapabilities,
    _RemoteTool,
    _capabilities_from_items,
    _call_tool,
    _discover_server_tools,
    _filter_capability_result,
    _format_mcp_result,
    _get_prompt,
    _http_headers,
    _http_initialize_diagnostic,
    _items_from_response,
    _list_prompts,
    _list_resource_templates,
    _list_resources,
    _mcp_error_message,
    _optional_session_call,
    _read_resource,
    _run,
    _session,
    _stdio_env,
    _strict_streamable_http_client,
    cached_mcp_server_capability_error,
    cached_mcp_server_capabilities,
    clear_mcp_caches,
    mcp_server_capabilities,
    mcp_tool_definitions,
    probe_mcp_server,
    register_mcp_tools,
    warm_mcp_capabilities,
)
from services.mcp_oauth import (
    BlockingOAuthInteraction,
    McpOAuthConfigurationError,
    McpOAuthRequired,
    NonInteractiveOAuthInteraction,
    clear_oauth_state,
    create_oauth_http_client,
    parse_oauth_callback_url,
)
from services.tool_registry import ToolRegistry
from services.tools import execute, tools_anthropic


def test_no_mcp_config_does_not_discover_servers(workspace, monkeypatch):
    def fail_discovery(_server):
        raise AssertionError("MCP discovery should not run without mcp.json")

    monkeypatch.setattr("services.mcp_tools._discover_server_tools", fail_discovery)
    names = {tool["name"] for tool in tools_anthropic(str(workspace))}
    assert not any(name.startswith("mcp__") for name in names)


def test_mcp_tools_are_exposed_from_standard_config(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            tools=(McpCapability("search.docs", "Search docs.", input_schema={"type": "object", "properties": {}}),),
            resources=(McpCapability("Guide", uri="doc://guide"),),
            prompts=(McpCapability("draft", "Draft prompt."),),
        ),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    names = {tool["name"] for tool in tools_anthropic(str(workspace))}
    assert "mcp__docs__search_docs" in names
    assert "mcp__docs__read_resource" not in names
    assert "mcp__docs__get_prompt" not in names


def test_mcp_unsupported_builtin_helpers_are_not_exposed(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            tools=(McpCapability("lookup", "Lookup.", input_schema={"type": "object"}),),
        ),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    names = {tool["name"] for tool in tools_anthropic(str(workspace))}

    assert "mcp__docs__lookup" in names
    assert "mcp__docs__list_prompts" not in names
    assert "mcp__docs__list_resource_templates" not in names
    assert "mcp__docs__list_resources" not in names
    assert "mcp__docs__read_resource" not in names


def test_warm_mcp_capabilities_populates_cache_for_startup(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    capabilities = McpServerCapabilities(
        tools=(McpCapability("lookup", "Lookup docs.", input_schema={"type": "object"}),)
    )
    calls = []

    def discover(server, **_kwargs):
        calls.append(server.name)
        return capabilities

    monkeypatch.setattr("services.mcp_tools._discover_server_capabilities", discover)
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    warm_mcp_capabilities(str(workspace))
    server = load_mcp_config(str(workspace)).servers[0]

    assert cached_mcp_server_capabilities(server) == capabilities
    assert calls == ["docs"]

    warm_mcp_capabilities(str(workspace))
    assert calls == ["docs"]


def test_mcp_tool_definitions_reuse_startup_capability_cache(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            tools=(McpCapability("lookup", "Lookup docs.", input_schema={"type": "object"}),)
        ),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)
    warm_mcp_capabilities(str(workspace))

    def fail_tool_discovery(_server):
        raise AssertionError("tool registration should use cached startup capabilities")

    monkeypatch.setattr("services.mcp_tools._discover_server_tools", fail_tool_discovery)

    names = {tool.name for tool in mcp_tool_definitions(str(workspace))}
    assert "mcp__docs__lookup" in names


def test_mcp_tool_execute_calls_remote_tool(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            tools=(McpCapability("lookup", "Lookup.", input_schema={"type": "object", "properties": {}}),),
        ),
    )
    monkeypatch.setattr("services.mcp_tools._call_tool", lambda _server, _name, _inputs: "call-token")

    def fake_run(value):
        if isinstance(value, McpServerCapabilities):
            return value
        calls.append(value)
        return "remote result"

    monkeypatch.setattr("services.mcp_tools._run", fake_run)

    assert execute("mcp__docs__lookup", {"q": "x"}, str(workspace)) == "remote result"
    assert calls


def test_mcp_remote_tool_keeps_protocol_like_name(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            tools=(McpCapability("read_resource", "Remote read.", input_schema={"type": "object", "properties": {}}),),
            resources=(McpCapability("Guide", uri="doc://guide"),),
        ),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    names = {tool["name"] for tool in tools_anthropic(str(workspace))}
    assert "mcp__docs__read_resource" in names
    assert not any(name.startswith("mcp__docs__read_resource_") for name in names)


def test_mcp_connection_error_is_exposed_as_tool(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )

    def boom(_server):
        raise RuntimeError("nope")

    monkeypatch.setattr("services.mcp_tools._discover_server_capabilities", boom)
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    tools = mcp_tool_definitions(str(workspace))
    error_tool = next(tool for tool in tools if tool.name == "mcp__docs__connection_error")
    assert "nope" in error_tool.execute(None, {})


def test_mcp_resource_and_prompt_capabilities_are_not_model_tools(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            resources=(McpCapability("Guide", uri="doc://guide"),),
            resource_templates=(McpCapability("Guide template", uri="doc://{name}"),),
            prompts=(McpCapability("draft", "Draft prompt."),),
        ),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    names = {tool.name for tool in mcp_tool_definitions(str(workspace))}

    assert "mcp__docs__list_resources" not in names
    assert "mcp__docs__read_resource" not in names
    assert "mcp__docs__list_resource_templates" not in names
    assert "mcp__docs__list_prompts" not in names
    assert "mcp__docs__get_prompt" not in names


def test_mcp_disabled_components_hide_remote_and_builtin_tools(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    set_mcp_component_enabled(str(workspace), "global", "docs", "tools", "lookup", False)
    set_mcp_component_enabled(str(workspace), "global", "docs", "resources", "*", False)
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            tools=(
                McpCapability("lookup", "Lookup.", input_schema={"type": "object", "properties": {}}),
                McpCapability("search", "Search.", input_schema={"type": "object", "properties": {}}),
            ),
            resources=(McpCapability("Guide", uri="doc://guide"),),
        ),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    names = {tool.name for tool in mcp_tool_definitions(str(workspace))}
    assert "mcp__docs__lookup" not in names
    assert "mcp__docs__search" in names
    assert "mcp__docs__list_resources" not in names
    assert "mcp__docs__read_resource" not in names


def test_probe_mcp_server_reports_tool_names(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            tools=(McpCapability("lookup", "Lookup."),),
        ),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    text = probe_mcp_server(server)
    assert "Connected to docs" in text
    assert "- lookup" in text
    assert cached_mcp_server_capabilities(server).tools[0].name == "lookup"


def test_mcp_tool_definitions_uses_cached_capabilities(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(
            tools=(
                McpCapability(
                    "cached.lookup",
                    "Cached lookup.",
                    input_schema={"type": "object", "properties": {}},
                ),
            ),
            prompts=(McpCapability("draft", "Draft prompt."),),
        ),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)
    mcp_server_capabilities(server)
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_tools",
        lambda _server: (_ for _ in ()).throw(AssertionError("cached capabilities should be used")),
    )

    tools = mcp_tool_definitions(str(workspace))

    names = {tool.name for tool in tools}
    assert "mcp__docs__cached_lookup" in names
    assert cached_mcp_server_capabilities(server).prompts[0].name == "draft"


def test_mcp_capability_errors_are_cached_and_cleared(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr("services.mcp_tools._discover_server_capabilities", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "services.mcp_tools._run",
        lambda _value: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        mcp_server_capabilities(server)

    assert cached_mcp_server_capability_error(server) == "connection refused"
    assert cached_mcp_server_capabilities(server) is None

    clear_mcp_caches()

    assert cached_mcp_server_capability_error(server) == ""
    assert cached_mcp_server_capabilities(server) is None


def test_mcp_server_capabilities_reports_resources_and_prompt_state(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    set_mcp_component_enabled(str(workspace), "global", "docs", "prompts", "draft", False)
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr("services.mcp_tools._session", _FakeSessionContext)

    capabilities = mcp_server_capabilities(server)

    assert [tool.name for tool in capabilities.tools] == ["lookup"]
    assert [resource.uri for resource in capabilities.resources] == ["doc://one"]
    assert [template.uri for template in capabilities.resource_templates] == ["doc://{name}"]
    assert capabilities.prompts[0].name == "draft"
    assert capabilities.prompts[0].enabled is False
    assert cached_mcp_server_capabilities(server) == capabilities


def test_mcp_capability_discovery_does_not_probe_unadvertised_apis(workspace, monkeypatch):
    class ToolOnlySession:
        _aichs_initialize_result = {"capabilities": {"tools": {}}}

        async def list_tools(self):
            return SimpleNamespace(tools=[])

        async def list_resources(self):
            raise AssertionError("resources/list should not be called")

        async def list_resource_templates(self):
            raise AssertionError("resources/templates/list should not be called")

        async def list_prompts(self):
            raise AssertionError("prompts/list should not be called")

    class ToolOnlyContext:
        def __init__(self, *_args, **_kwargs):
            self.session = ToolOnlySession()

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr("services.mcp_tools._session", ToolOnlyContext)

    capabilities = mcp_server_capabilities(server)

    assert capabilities.tools == ()
    assert capabilities.resources == ()
    assert capabilities.prompts == ()
    assert capabilities.supports_resources is False
    assert capabilities.supports_prompts is False


def test_mcp_empty_advertised_resources_still_do_not_expose_model_helpers(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "services.mcp_tools._discover_server_capabilities",
        lambda _server, **_kwargs: McpServerCapabilities(supports_resources=True),
    )
    monkeypatch.setattr("services.mcp_tools._run", lambda value: value)

    names = {tool.name for tool in mcp_tool_definitions(str(workspace))}

    assert "mcp__docs__list_resources" not in names
    assert "mcp__docs__read_resource" not in names
    assert "mcp__docs__list_prompts" not in names


def test_mcp_result_formatting_prefers_plain_text_when_possible():
    result = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ]
        }
    )
    assert _format_mcp_result(result) == "first\nsecond"


def test_mcp_result_formatting_keeps_structured_payload():
    result = {
        "content": [{"type": "text", "text": "visible"}],
        "structuredContent": {"ok": True},
    }
    text = _format_mcp_result(result)
    assert '"structuredContent"' in text
    assert '"ok": true' in text


def test_mcp_result_formatting_preserves_standard_tool_execution_error():
    result = {
        "content": [{"type": "text", "text": "rate limited"}],
        "isError": True,
    }

    assert _format_mcp_result(result) == "[tool error] rate limited"


def test_mcp_result_formatting_keeps_non_text_standard_content():
    result = {
        "content": [
            {"type": "text", "text": "screenshot"},
            {"type": "image", "mimeType": "image/png", "data": "abc123"},
        ],
        "isError": False,
    }

    text = _format_mcp_result(result)

    assert '"type": "image"' in text
    assert '"mimeType": "image/png"' in text


def test_mcp_error_message_unwraps_task_group_noise():
    exc = ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("HTTP 400 from MCP server")])

    assert _mcp_error_message(exc) == "HTTP 400 from MCP server"


def test_mcp_error_message_hides_raw_event_loop_noise():
    message = _mcp_error_message(RuntimeError("no running event loop"))

    assert message == "MCP transport closed unexpectedly while processing the request."
    assert "no running event loop" not in message


def test_mcp_env_and_header_helpers(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "command": "docs-server",
                        "env": {"LOCAL": "set"},
                        "env_vars": ["INHERITED"],
                        "url": "",
                    },
                    "http": {
                        "url": "https://example.test/mcp",
                        "headers": {"X-Test": "1"},
                        "bearer_token_env_var": "TOKEN",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("INHERITED", "yes")
    monkeypatch.setenv("TOKEN", "secret")
    servers = {server.name: server for server in load_mcp_config(str(workspace)).servers}

    env = _stdio_env(servers["docs"])
    assert env["LOCAL"] == "set"
    assert env["INHERITED"] == "yes"
    assert _http_headers(servers["http"]) == {
        "X-Test": "1",
        "Authorization": "Bearer secret",
    }


def test_mcp_authorization_header_preserves_configured_value(workspace):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "url": "https://api.githubcopilot.com/mcp/",
                        "auth": "headers",
                        "headers": {"authorization": "Bearer: token"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]

    assert _http_headers(server)["Authorization"] == "Bearer: token"



def test_mcp_initialize_diagnostic_reports_http_error(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp", "auth": "headers"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]

    class Response:
        headers = {"www-authenticate": "Bearer error=\"invalid_token\""}

        def read(self, _size=-1):
            return b"bad request"

    def fail(_req, timeout=None):
        response = Response()
        raise __import__("urllib.error").error.HTTPError(
            "https://example.test/mcp",
            400,
            "Bad Request",
            response.headers,
            response,
        )

    monkeypatch.setattr("services.mcp_tools.urlrequest.urlopen", fail)

    message = _http_initialize_diagnostic(server)

    assert "HTTP 400" in message
    assert "bad request" in message
    assert "invalid_token" in message


def test_mcp_capability_response_helpers_filter_and_extract(workspace):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    set_mcp_component_enabled(str(workspace), "global", "docs", "resources", "doc://blocked", False)
    server = load_mcp_config(str(workspace)).servers[0]
    response = {
        "resources": [
            {"uri": "doc://one", "description": "One"},
            {"uri": "doc://blocked", "description": "Blocked"},
            "kept-non-dict",
        ]
    }

    assert _items_from_response(response, "resources") == response["resources"][:2]
    filtered = _filter_capability_result(server, "resources", response, ("resources",), ("uri", "name"))
    assert filtered["resources"] == [{"uri": "doc://one", "description": "One"}, "kept-non-dict"]
    capabilities = _capabilities_from_items(
        [
            {"name": "draft", "description": "Draft", "arguments": [{"name": "topic"}]},
            {"uriTemplate": "doc://{name}", "mimeType": "text/plain"},
            {"description": "missing identity"},
        ],
        kind="prompts",
        server=server,
        name_fields=("name", "uriTemplate"),
        uri_fields=("uriTemplate",),
    )
    assert capabilities[0].arguments == ("topic",)
    assert capabilities[1].uri == "doc://{name}"


def test_optional_session_call_handles_missing_and_failing_methods():
    class Session:
        async def fail(self):
            raise RuntimeError("boom")

    assert __import__("asyncio").run(_optional_session_call(Session(), "missing", 1)) == {}
    assert __import__("asyncio").run(_optional_session_call(Session(), "fail", 1)) == {}


def test_mcp_oauth_callback_url_parser():
    assert parse_oauth_callback_url("http://localhost/callback?code=abc&state=xyz") == ("abc", "xyz")
    with pytest.raises(ValueError):
        parse_oauth_callback_url("http://localhost/callback?state=xyz")


def test_noninteractive_oauth_reports_required_url():
    interaction = NonInteractiveOAuthInteraction()

    async def run():
        await interaction.redirect_handler("https://auth.example/authorize")
        await interaction.callback_handler()

    with pytest.raises(McpOAuthRequired) as exc:
        __import__("asyncio").run(run())
    assert exc.value.auth_url == "https://auth.example/authorize"


def test_blocking_oauth_interaction_waits_for_callback():
    seen = []
    interaction = BlockingOAuthInteraction(seen.append)

    async def run():
        await interaction.redirect_handler("https://auth.example/authorize")
        interaction.submit_callback_url("http://localhost/callback?code=abc")
        return await interaction.callback_handler()

    assert __import__("asyncio").run(run()) == ("abc", None)
    assert seen == ["https://auth.example/authorize"]


def test_oauth_http_client_persists_tokens_and_client_info(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "url": "https://example.test/mcp",
                        "auth": {
                            "type": "oauth",
                            "scope": "read",
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
    calls = []

    fake_httpx = types.ModuleType("httpx")
    fake_pydantic = types.ModuleType("pydantic")
    fake_auth = types.ModuleType("mcp.client.auth")
    fake_shared_auth = types.ModuleType("mcp.shared.auth")

    class _FakeModel:
        def __init__(self, **kwargs):
            self.data = dict(kwargs)

        def model_dump(self, **_kwargs):
            return dict(self.data)

        @classmethod
        def model_validate(cls, data):
            return cls(**data)

    class _FakeProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            calls.append(("provider", kwargs))

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            calls.append(("client", kwargs))

    fake_httpx.AsyncClient = _FakeAsyncClient
    fake_pydantic.AnyUrl = lambda value: value
    fake_auth.OAuthClientProvider = _FakeProvider
    fake_auth.TokenStorage = object
    fake_shared_auth.OAuthClientInformationFull = _FakeModel
    fake_shared_auth.OAuthClientMetadata = _FakeModel
    fake_shared_auth.OAuthToken = _FakeModel
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setitem(sys.modules, "pydantic", fake_pydantic)
    monkeypatch.setitem(sys.modules, "mcp.client.auth", fake_auth)
    monkeypatch.setitem(sys.modules, "mcp.shared.auth", fake_shared_auth)

    client = create_oauth_http_client(server)
    provider = client.kwargs["auth"]
    storage = provider.kwargs["storage"]

    async def use_storage():
        assert await storage.get_tokens() is None
        await storage.set_tokens(_FakeModel(access_token="token"))
        assert (await storage.get_tokens()).data == {"access_token": "token"}
        client_info = await storage.get_client_info()
        assert client_info.data["client_id"] == "client-id"
        await storage.set_client_info(_FakeModel(client_id="stored"))
        assert (await storage.get_client_info()).data == {"client_id": "stored"}

    __import__("asyncio").run(use_storage())
    assert provider.kwargs["server_url"] == "https://example.test/mcp"
    assert (config.AICHS_HOME / "project" / "mcp.oauth.json").exists()
    clear_oauth_state(server)


def test_github_copilot_oauth_without_client_id_is_actionable(workspace):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps(
            {
                "servers": {
                    "github": {
                        "type": "http",
                        "url": "https://api.githubcopilot.com/mcp/",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]

    with pytest.raises(McpOAuthConfigurationError) as exc:
        create_oauth_http_client(server)

    message = str(exc.value)
    assert "dynamic client registration" in message
    assert "client_id" in message


class _FakeSessionContext:
    def __init__(self, server, **_kwargs):
        self.server = server
        self.session = _FakeSession()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    async def list_tools(self):
        tool = SimpleNamespace(
            name="lookup",
            description="Lookup docs.",
            inputSchema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        return SimpleNamespace(tools=[tool])

    async def call_tool(self, name, arguments=None):
        return {"content": [{"type": "text", "text": f"{name}:{arguments['q']}"}]}

    async def list_resources(self):
        return {"resources": [{"uri": "doc://one"}]}

    async def read_resource(self, uri):
        return {"contents": [{"uri": uri, "text": "body"}]}

    async def list_resource_templates(self):
        return {"resourceTemplates": [{"uriTemplate": "doc://{name}"}]}

    async def list_prompts(self):
        return {"prompts": [{"name": "draft"}]}

    async def get_prompt(self, name, arguments=None):
        return {"messages": [{"role": "user", "content": f"{name}:{arguments['x']}"}]}


class _PagedSessionContext:
    def __init__(self, server, **_kwargs):
        self.server = server
        self.session = _PagedSession()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _PagedSession:
    async def list_tools(self, cursor=None):
        if cursor is None:
            return SimpleNamespace(
                tools=[SimpleNamespace(name="lookup", description="Lookup docs.", inputSchema={})],
                nextCursor="tools-2",
            )
        assert cursor == "tools-2"
        return SimpleNamespace(tools=[SimpleNamespace(name="search", description="Search docs.", inputSchema={})])

    async def list_resources(self, cursor=None):
        if cursor is None:
            return {"resources": [{"uri": "doc://one"}], "nextCursor": "resources-2"}
        assert cursor == "resources-2"
        return {"resources": [{"uri": "doc://two"}]}

    async def list_resource_templates(self, cursor=None):
        if cursor is None:
            return {"resourceTemplates": [{"uriTemplate": "doc://{name}"}], "nextCursor": "templates-2"}
        assert cursor == "templates-2"
        return {"resourceTemplates": [{"uriTemplate": "guide://{slug}"}]}

    async def list_prompts(self, cursor=None):
        if cursor is None:
            return {"prompts": [{"name": "draft"}], "nextCursor": "prompts-2"}
        assert cursor == "prompts-2"
        return {"prompts": [{"name": "summarize"}]}


class _NoRunningLoopClose:
    async def __aexit__(self, exc_type, exc, tb):
        raise RuntimeError("no running event loop")


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda server: _discover_server_tools(server), "lookup"),
        (lambda server: _call_tool(server, "lookup", {"q": "abc"}), "lookup:abc"),
        (lambda server: _list_resources(server), "doc://one"),
        (lambda server: _read_resource(server, "doc://one"), "body"),
        (lambda server: _list_resource_templates(server), "doc://{name}"),
        (lambda server: _list_prompts(server), "draft"),
        (lambda server: _get_prompt(server, "draft", {"x": 1}), "draft:1"),
    ],
)
def test_mcp_async_wrappers_use_session(workspace, monkeypatch, call, expected):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr("services.mcp_tools._session", _FakeSessionContext)

    result = call(server)
    value = __import__("asyncio").run(result)
    if isinstance(value, list):
        assert value[0].name == expected
    else:
        assert expected in value


def test_read_resource_and_get_prompt_validate_required_inputs(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr("services.mcp_tools._session", _FakeSessionContext)

    assert __import__("asyncio").run(_read_resource(server, "")) == "[tool error] read_resource requires a uri."
    assert __import__("asyncio").run(_get_prompt(server, "", {})) == "[tool error] get_prompt requires a name."


def test_mcp_session_close_ignores_no_running_event_loop_noise(workspace):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    session = _session(server)
    session._session_cm = _NoRunningLoopClose()

    __import__("asyncio").run(session._close(None, None, None))


def test_mcp_capabilities_walk_paginated_lists(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr("services.mcp_tools._session", _PagedSessionContext)

    capabilities = mcp_server_capabilities(server)

    assert [tool.name for tool in capabilities.tools] == ["lookup", "search"]
    assert [resource.uri for resource in capabilities.resources] == ["doc://one", "doc://two"]
    assert [template.uri for template in capabilities.resource_templates] == ["doc://{name}", "guide://{slug}"]
    assert [prompt.name for prompt in capabilities.prompts] == ["draft", "summarize"]


def test_mcp_list_helpers_walk_paginated_lists(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-server"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr("services.mcp_tools._session", _PagedSessionContext)

    resources = __import__("asyncio").run(_list_resources(server))
    templates = __import__("asyncio").run(_list_resource_templates(server))
    prompts = __import__("asyncio").run(_list_prompts(server))

    assert "doc://one" in resources
    assert "doc://two" in resources
    assert "doc://{name}" in templates
    assert "guide://{slug}" in templates
    assert "draft" in prompts
    assert "summarize" in prompts


def test_register_mcp_tools_skips_non_chat_surface_without_config(workspace):
    registry = ToolRegistry()
    register_mcp_tools(registry, str(workspace), surface="canvas")
    assert registry.names() == []


def test_run_executes_coroutine():
    async def value():
        return "ok"

    assert _run(value()) == "ok"


def test_run_unwraps_exception_group_noise():
    async def fail():
        raise ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("HTTP 400 from MCP server")])

    with pytest.raises(RuntimeError, match="HTTP 400 from MCP server"):
        _run(fail())


class _FakeTransport:
    entered = False
    exited = False
    exit_exc_type = None
    session_id = None

    async def __aenter__(self):
        self.entered = True
        type(self).entered = True
        callback = (lambda: type(self).session_id) if type(self).session_id else None
        return ("read", "write", callback)

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        type(self).exited = True
        type(self).exit_exc_type = exc_type
        return False


class _FakeClientSession:
    initialized = False

    def __init__(self, read, write):
        self.read = read
        self.write = write

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        self.initialized = True


class _FakeStdioParams:
    def __init__(self, command, args=None, env=None):
        self.command = command
        self.args = args
        self.env = env


def _install_fake_mcp_message_modules(monkeypatch):
    shared = types.ModuleType("mcp.shared")
    message = types.ModuleType("mcp.shared.message")
    mcp_types = types.ModuleType("mcp.types")

    class JSONRPCRequest:
        def __init__(self, *, jsonrpc="2.0", id=0, method="", params=None, **_kwargs):
            self.jsonrpc = jsonrpc
            self.id = id
            self.method = method
            self.params = params

        def as_dict(self):
            data = {"jsonrpc": self.jsonrpc, "id": self.id, "method": self.method}
            if self.params is not None:
                data["params"] = self.params
            return data

    class JSONRPCNotification:
        def __init__(self, *, jsonrpc="2.0", method="", params=None, **_kwargs):
            self.jsonrpc = jsonrpc
            self.method = method
            self.params = params

        def as_dict(self):
            data = {"jsonrpc": self.jsonrpc, "method": self.method}
            if self.params is not None:
                data["params"] = self.params
            return data

    class JSONRPCResponse:
        def __init__(self, *, jsonrpc="2.0", id=0, result=None, **_kwargs):
            self.jsonrpc = jsonrpc
            self.id = id
            self.result = result or {}

        def as_dict(self):
            return {"jsonrpc": self.jsonrpc, "id": self.id, "result": self.result}

    class JSONRPCError:
        def __init__(self, *, jsonrpc="2.0", id=0, error=None, **_kwargs):
            self.jsonrpc = jsonrpc
            self.id = id
            self.error = error or {}

        def as_dict(self):
            return {"jsonrpc": self.jsonrpc, "id": self.id, "error": self.error}

    class JSONRPCMessage:
        def __init__(self, root):
            self.root = root

        def model_dump(self, **_kwargs):
            return self.root.as_dict()

        @classmethod
        def model_validate_json(cls, data):
            payload = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
            if "error" in payload:
                return cls(JSONRPCError(**payload))
            if "result" in payload:
                return cls(JSONRPCResponse(**payload))
            if "id" in payload:
                return cls(JSONRPCRequest(**payload))
            return cls(JSONRPCNotification(**payload))

    class SessionMessage:
        def __init__(self, message, metadata=None):
            self.message = message
            self.metadata = metadata

    message.SessionMessage = SessionMessage
    mcp_types.JSONRPCMessage = JSONRPCMessage
    mcp_types.JSONRPCError = JSONRPCError
    mcp_types.JSONRPCNotification = JSONRPCNotification
    mcp_types.JSONRPCRequest = JSONRPCRequest
    mcp_types.JSONRPCResponse = JSONRPCResponse
    monkeypatch.setitem(sys.modules, "mcp.shared", shared)
    monkeypatch.setitem(sys.modules, "mcp.shared.message", message)
    monkeypatch.setitem(sys.modules, "mcp.types", mcp_types)
    return SimpleNamespace(
        JSONRPCMessage=JSONRPCMessage,
        JSONRPCError=JSONRPCError,
        JSONRPCNotification=JSONRPCNotification,
        JSONRPCRequest=JSONRPCRequest,
        JSONRPCResponse=JSONRPCResponse,
        SessionMessage=SessionMessage,
    )


def _install_fake_mcp_sdk(monkeypatch, calls):
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = _FakeClientSession
    mcp.StdioServerParameters = _FakeStdioParams
    client = types.ModuleType("mcp.client")
    stdio = types.ModuleType("mcp.client.stdio")
    http = types.ModuleType("mcp.client.streamable_http")
    fake_httpx = types.ModuleType("httpx")
    _install_fake_mcp_message_modules(monkeypatch)

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            calls.append(("httpx", kwargs))

        async def delete(self, url, headers=None):
            calls.append(("httpx_delete", url, headers))
            return SimpleNamespace(status_code=202)

        async def aclose(self):
            calls.append(("httpx_close", self.kwargs))

    def stdio_client(params):
        calls.append(("stdio", params.command, list(params.args), params.env.get("LOCAL")))
        return _FakeTransport()

    def streamable_http_client(url, **kwargs):
        calls.append(("http", url, kwargs.get("http_client")))
        return _FakeTransport()

    fake_httpx.AsyncClient = _FakeAsyncClient
    stdio.stdio_client = stdio_client
    http.streamable_http_client = streamable_http_client
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", http)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)


@pytest.mark.parametrize(
    "entry",
    [
        {"command": "server", "args": ["--stdio"], "env": {"LOCAL": "1"}},
        {"url": "https://example.test/mcp", "headers": {"X-Test": "1"}},
    ],
)
def test_session_uses_sdk_transports(workspace, monkeypatch, entry):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": entry}}),
        encoding="utf-8",
    )
    calls = []
    _install_fake_mcp_sdk(monkeypatch, calls)
    server = load_mcp_config(str(workspace)).servers[0]

    async def use_session():
        async with _session(server) as item:
            assert isinstance(item, _FakeClientSession)
            assert item.initialized is True

    __import__("asyncio").run(use_session())
    assert calls


def test_session_auto_http_starts_without_oauth_client(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    calls = []
    _install_fake_mcp_sdk(monkeypatch, calls)
    monkeypatch.setattr(
        "services.mcp_tools.create_oauth_http_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OAuth client should not be created")),
    )
    server = load_mcp_config(str(workspace)).servers[0]

    async def use_session():
        async with _session(server) as item:
            assert isinstance(item, _FakeClientSession)

    __import__("asyncio").run(use_session())
    assert ("httpx", {"headers": {}, "follow_redirects": True}) in calls


def test_session_auto_http_reports_oauth_required_on_auth_challenge(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    calls = []
    _FakeTransport.entered = False
    _FakeTransport.exited = False
    _install_fake_mcp_sdk(monkeypatch, calls)
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr(
        "services.mcp_tools.create_oauth_http_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OAuth flow should not start")),
    )

    async def auth_required_initialize(self):
        raise RuntimeError("401 Unauthorized: missing Authorization header")

    monkeypatch.setattr(_FakeClientSession, "initialize", auth_required_initialize)

    async def use_session():
        async with _session(server):
            pass

    with pytest.raises(McpOAuthRequired):
        __import__("asyncio").run(use_session())

    assert any(item[0] == "httpx_close" for item in calls)


def test_session_accepts_202_session_termination(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    calls = []
    server = load_mcp_config(str(workspace)).servers[0]
    _install_fake_mcp_sdk(monkeypatch, calls)
    ctx = _session(server)
    ctx._session_id_callback = lambda: "session-1"

    class Client:
        async def delete(self, url, headers=None):
            calls.append(("httpx_delete", url, headers))
            return SimpleNamespace(status_code=202)

    ctx._http_client = Client()

    async def terminate():
        await ctx._terminate_http_session()

    __import__("asyncio").run(terminate())

    assert ("httpx_delete", "https://example.test/mcp", {"mcp-session-id": "session-1"}) in calls


def test_session_initialize_cancelled_gets_actionable_error(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "url": "https://example.test/mcp",
                        "auth": "headers",
                        "headers": {"X-Test": "1"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    calls = []
    _install_fake_mcp_sdk(monkeypatch, calls)
    server = load_mcp_config(str(workspace)).servers[0]

    async def cancelled_initialize(self):
        raise __import__("asyncio").CancelledError("transport cancelled")

    monkeypatch.setattr(_FakeClientSession, "initialize", cancelled_initialize)
    monkeypatch.setattr("services.mcp_tools._http_initialize_diagnostic", lambda _server: "")

    async def use_session():
        async with _session(server):
            pass

    with pytest.raises(RuntimeError, match="MCP initialization was cancelled"):
        __import__("asyncio").run(use_session())


def test_session_closes_transport_when_initialize_fails(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "url": "https://example.test/mcp",
                        "auth": "headers",
                        "headers": {"X-Test": "1"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    calls = []
    _FakeTransport.entered = False
    _FakeTransport.exited = False
    _install_fake_mcp_sdk(monkeypatch, calls)
    server = load_mcp_config(str(workspace)).servers[0]

    async def fail_initialize(self):
        self.initialized = False
        raise RuntimeError("init failed")

    monkeypatch.setattr(_FakeClientSession, "initialize", fail_initialize)

    async def use_session():
        async with _session(server):
            pass

    with pytest.raises(RuntimeError, match="init failed"):
        __import__("asyncio").run(use_session())

    assert any(item[0] == "httpx_close" for item in calls)


def test_session_http_status_startup_failure_closes_transport_normally(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    calls = []
    _FakeTransport.entered = False
    _FakeTransport.exited = False
    _FakeTransport.exit_exc_type = object
    _install_fake_mcp_sdk(monkeypatch, calls)
    server = load_mcp_config(str(workspace)).servers[0]

    async def fail_initialize(self):
        raise RuntimeError("HTTPStatusError: Client error '400 ' for url 'https://example.test/mcp'")

    monkeypatch.setattr(_FakeClientSession, "initialize", fail_initialize)
    monkeypatch.setattr(
        "services.mcp_tools._http_initialize_diagnostic",
        lambda _server: "HTTP 400 from https://example.test/mcp. bad request",
    )

    async def use_session():
        async with _session(server):
            pass

    with pytest.raises(RuntimeError, match="MCP initialization failed.*HTTP 400"):
        __import__("asyncio").run(use_session())

    assert any(item[0] == "httpx_close" for item in calls)


def test_strict_streamable_http_waits_for_initialized_before_tools_list(monkeypatch):
    sdk = _install_fake_mcp_message_modules(monkeypatch)
    order = []

    class Response:
        def __init__(self, status_code, payload=None, headers=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.headers = headers or {}
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aread(self):
            return json.dumps(self._payload).encode("utf-8")

        async def aiter_lines(self):
            yield "event: message"
            yield f"data: {json.dumps(self._payload)}"
            yield ""
            yield ": still open"

        async def aclose(self):
            self.closed = True

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class Client:
        def stream(self, _method, _url, *, json=None, headers=None):
            method = json.get("method")
            order.append(method)
            if method == "tools/list" and "notifications/initialized" not in order:
                return Response(400)
            if method == "initialize":
                return Response(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": json["id"],
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "docs", "version": "1"},
                        },
                    },
                    {"content-type": "application/json", "mcp-session-id": "session-1"},
                )
            if method == "notifications/initialized":
                return Response(202)
            if method == "tools/list":
                assert headers["mcp-session-id"] == "session-1"
                assert headers["mcp-protocol-version"] == "2025-06-18"
                return Response(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": json["id"],
                        "result": {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]},
                    },
                    {"content-type": "application/json"},
                )
            return Response(500)

    async def run():
        async with _strict_streamable_http_client("https://example.test/mcp", Client()) as streams:
            read_stream, write_stream = streams[0], streams[1]
            await write_stream.send(
                sdk.SessionMessage(
                    sdk.JSONRPCMessage(
                        sdk.JSONRPCRequest(
                            id=0,
                            method="initialize",
                            params={
                                "protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "clientInfo": {"name": "aichs-test", "version": "0"},
                            },
                        )
                    )
                )
            )
            await read_stream.receive()
            await write_stream.send(
                sdk.SessionMessage(
                    sdk.JSONRPCMessage(sdk.JSONRPCNotification(method="notifications/initialized"))
                )
            )
            await write_stream.send(
                sdk.SessionMessage(
                    sdk.JSONRPCMessage(sdk.JSONRPCRequest(id=1, method="tools/list", params={}))
                )
            )
            message = await read_stream.receive()
            return message.message.root.result["tools"][0]["name"]

    assert __import__("asyncio").run(run()) == "lookup"
    assert order == ["initialize", "notifications/initialized", "tools/list"]


def test_strict_streamable_http_suppresses_sse_close_loop_noise(monkeypatch):
    sdk = _install_fake_mcp_message_modules(monkeypatch)

    class Response:
        def __init__(self, status_code, payload=None, headers=None, close_error=False):
            self.status_code = status_code
            self._payload = payload or {}
            self.headers = headers or {}
            self._close_error = close_error

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aread(self):
            return json.dumps(self._payload).encode("utf-8")

        async def aiter_lines(self):
            yield "event: message"
            yield f"data: {json.dumps(self._payload)}"
            yield ""

        async def aclose(self):
            if self._close_error:
                raise RuntimeError("no running event loop")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class Client:
        def stream(self, _method, _url, *, json=None, headers=None):
            method = json.get("method")
            if method == "initialize":
                return Response(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": json["id"],
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"prompts": {}},
                            "serverInfo": {"name": "docs", "version": "1"},
                        },
                    },
                    {"content-type": "application/json", "mcp-session-id": "session-1"},
                )
            if method == "notifications/initialized":
                return Response(202)
            if method == "prompts/list":
                return Response(
                    200,
                    {"jsonrpc": "2.0", "id": json["id"], "result": {"prompts": [{"name": "draft"}]}},
                    {"content-type": "text/event-stream"},
                    close_error=True,
                )
            return Response(500)

    async def run():
        async with _strict_streamable_http_client("https://example.test/mcp", Client()) as streams:
            read_stream, write_stream = streams[0], streams[1]
            await write_stream.send(
                sdk.SessionMessage(
                    sdk.JSONRPCMessage(
                        sdk.JSONRPCRequest(
                            id=0,
                            method="initialize",
                            params={
                                "protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "clientInfo": {"name": "aichs-test", "version": "0"},
                            },
                        )
                    )
                )
            )
            await read_stream.receive()
            await write_stream.send(
                sdk.SessionMessage(sdk.JSONRPCMessage(sdk.JSONRPCNotification(method="notifications/initialized")))
            )
            await write_stream.send(
                sdk.SessionMessage(sdk.JSONRPCMessage(sdk.JSONRPCRequest(id=1, method="prompts/list", params={})))
            )
            message = await read_stream.receive()
            return message.message.root.result["prompts"][0]["name"]

    assert __import__("asyncio").run(run()) == "draft"


def test_session_uses_oauth_http_client(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp", "auth": "oauth"}}}),
        encoding="utf-8",
    )
    calls = []
    _install_fake_mcp_sdk(monkeypatch, calls)
    server = load_mcp_config(str(workspace)).servers[0]
    oauth_client = SimpleNamespace(closed=False)

    async def close_client():
        oauth_client.closed = True

    oauth_client.aclose = close_client
    monkeypatch.setattr("services.mcp_tools.create_oauth_http_client", lambda srv, interaction: oauth_client)

    async def use_session():
        async with _session(server, oauth_interaction=object()) as item:
            assert isinstance(item, _FakeClientSession)

    __import__("asyncio").run(use_session())
    assert ("http", "https://example.test/mcp", oauth_client) in calls
    assert oauth_client.closed is True


def test_session_does_not_start_oauth_flow_without_tokens_or_interaction(workspace, monkeypatch):
    (config.AICHS_HOME / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"url": "https://example.test/mcp", "auth": "oauth"}}}),
        encoding="utf-8",
    )
    server = load_mcp_config(str(workspace)).servers[0]
    monkeypatch.setattr(
        "services.mcp_tools.create_oauth_http_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OAuth flow should not start")),
    )

    async def use_session():
        async with _session(server):
            pass

    with pytest.raises(McpOAuthRequired):
        __import__("asyncio").run(use_session())
