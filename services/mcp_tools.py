from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from services.mcp_config import McpServerConfig, load_mcp_config, mcp_config_exists
from services.mcp_logs import append_mcp_log
from services.mcp_oauth import (
    McpOAuthRequired,
    NonInteractiveOAuthInteraction,
    create_oauth_http_client,
    has_oauth_tokens,
)
from services.tool_registry import ToolContext, ToolDefinition, ToolRegistry


_CACHE_LOCK = threading.RLock()
_DISCOVERY_CACHE: dict[tuple, tuple[ToolDefinition, ...]] = {}
_CAPABILITY_CACHE: dict[tuple, McpServerCapabilities] = {}
_CAPABILITY_ERROR_CACHE: dict[tuple, str] = {}
_CAPABILITY_WARMUP_KEYS: set[tuple] = set()


@dataclass(frozen=True)
class _RemoteTool:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class _OptionalListResult:
    supported: bool
    responses: list[Any]


@dataclass(frozen=True)
class McpCapability:
    name: str
    description: str = ""
    uri: str = ""
    mime_type: str = ""
    input_schema: dict | None = None
    arguments: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class McpServerCapabilities:
    tools: tuple[McpCapability, ...] = ()
    resources: tuple[McpCapability, ...] = ()
    resource_templates: tuple[McpCapability, ...] = ()
    prompts: tuple[McpCapability, ...] = ()
    supports_resources: bool = False
    supports_resource_templates: bool = False
    supports_prompts: bool = False


def clear_mcp_caches() -> None:
    with _CACHE_LOCK:
        _DISCOVERY_CACHE.clear()
        _CAPABILITY_CACHE.clear()
        _CAPABILITY_ERROR_CACHE.clear()
        _CAPABILITY_WARMUP_KEYS.clear()


def cached_mcp_server_capabilities(server: McpServerConfig) -> McpServerCapabilities | None:
    with _CACHE_LOCK:
        return _CAPABILITY_CACHE.get(_capability_cache_key(server))


def cached_mcp_server_capability_error(server: McpServerConfig) -> str:
    with _CACHE_LOCK:
        return _CAPABILITY_ERROR_CACHE.get(_capability_cache_key(server), "")


def warm_mcp_capabilities(cwd: str | None = None, *, force: bool = False) -> None:
    if not mcp_config_exists(cwd):
        return
    snapshot = load_mcp_config(cwd)
    for server in snapshot.servers:
        if not server.available:
            continue
        key = _capability_cache_key(server)
        with _CACHE_LOCK:
            if not force and (key in _CAPABILITY_CACHE or key in _CAPABILITY_ERROR_CACHE):
                continue
        try:
            mcp_server_capabilities(server)
        except BaseException:
            pass


def start_mcp_capability_warmup(cwd: str | None = None, *, force: bool = False) -> threading.Thread | None:
    if not mcp_config_exists(cwd):
        return None
    snapshot = load_mcp_config(cwd)
    pending: list[tuple[McpServerConfig, tuple]] = []
    with _CACHE_LOCK:
        for server in snapshot.servers:
            if not server.available:
                continue
            key = _capability_cache_key(server)
            if not force and (
                key in _CAPABILITY_CACHE
                or key in _CAPABILITY_ERROR_CACHE
                or key in _CAPABILITY_WARMUP_KEYS
            ):
                continue
            pending.append((server, key))
            _CAPABILITY_WARMUP_KEYS.add(key)
    if not pending:
        return None

    def _worker() -> None:
        try:
            for server, _key in pending:
                try:
                    mcp_server_capabilities(server)
                except BaseException:
                    pass
        finally:
            with _CACHE_LOCK:
                for _server, key in pending:
                    _CAPABILITY_WARMUP_KEYS.discard(key)

    thread = threading.Thread(target=_worker, name="aichs-mcp-capability-warmup", daemon=True)
    thread.start()
    return thread


def register_mcp_tools(registry: ToolRegistry, cwd: str | None = None, *, surface: str = "chat") -> None:
    if surface != "chat" or not mcp_config_exists(cwd):
        return
    for tool in mcp_tool_definitions(cwd):
        registry.tool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            execute=tool.execute,
            parallel_safe=tool.parallel_safe,
            approval=tool.approval,
            source=tool.source,
            surfaces=tool.surfaces,
            extension_id=tool.extension_id,
        )


def mcp_tool_definitions(cwd: str | None = None) -> list[ToolDefinition]:
    if not mcp_config_exists(cwd):
        return []
    snapshot = load_mcp_config(cwd)
    key = snapshot.signature
    with _CACHE_LOCK:
        cached = _DISCOVERY_CACHE.get(key)
    if cached is not None:
        return list(cached)

    tools: list[ToolDefinition] = []
    for server in snapshot.servers:
        if not server.available:
            continue
        cached_capabilities = cached_mcp_server_capabilities(server)
        try:
            if cached_capabilities is not None:
                capabilities = cached_capabilities
            else:
                capabilities = _run(_discover_server_capabilities(server))
                _store_mcp_server_capabilities(server, capabilities)
        except Exception as exc:
            message = _mcp_error_message(exc)
            append_mcp_log(server, "discovery_failed", message)
            _store_mcp_server_capability_error(server, message)
            tools.append(_server_error_tool(server, message))
            continue
        used_names: set[str] = set()
        remote_tools = _remote_tools_from_capabilities(capabilities)
        for remote in remote_tools:
            if not server.component_enabled("tools", remote.name):
                continue
            tool = _remote_tool_definition(server, remote, used_names=used_names)
            tools.append(tool)
            used_names.add(tool.name)

    frozen = tuple(tools)
    with _CACHE_LOCK:
        _DISCOVERY_CACHE[key] = frozen
    return list(frozen)


def probe_mcp_server(server: McpServerConfig, *, oauth_interaction=None) -> str:
    append_mcp_log(server, "connect_started", f"Connecting to {server.name}.")
    try:
        capabilities = _run(_discover_server_capabilities(server, oauth_interaction=oauth_interaction))
    except BaseException as exc:
        message = _mcp_error_message(exc)
        append_mcp_log(server, "connect_failed", message)
        _store_mcp_server_capability_error(server, message)
        raise RuntimeError(message) from exc
    _store_mcp_server_capabilities(server, capabilities)
    append_mcp_log(
        server,
        "connect_succeeded",
        f"Connected to {server.name}.",
        tools=len(capabilities.tools),
        resources=len(capabilities.resources),
        resource_templates=len(capabilities.resource_templates),
        prompts=len(capabilities.prompts),
    )
    lines = [
        f"Connected to {server.name}.",
        f"Transport: {server.transport}",
        f"Tools: {len(capabilities.tools)}",
        f"Resources: {len(capabilities.resources)}",
        f"Resource templates: {len(capabilities.resource_templates)}",
        f"Prompts: {len(capabilities.prompts)}",
    ]
    for title, items in (
        ("Tools", capabilities.tools),
        ("Resources", capabilities.resources),
        ("Resource templates", capabilities.resource_templates),
        ("Prompts", capabilities.prompts),
    ):
        if not items:
            continue
        lines.append("")
        lines.append(f"{title}:")
        lines.extend(f"- {item.name}" for item in items[:50])
    return "\n".join(lines)


def mcp_server_capabilities(server: McpServerConfig, *, oauth_interaction=None) -> McpServerCapabilities:
    append_mcp_log(server, "capabilities_started", f"Discovering capabilities for {server.name}.")
    try:
        capabilities = _run(_discover_server_capabilities(server, oauth_interaction=oauth_interaction))
    except BaseException as exc:
        message = _mcp_error_message(exc)
        append_mcp_log(server, "capabilities_failed", message)
        _store_mcp_server_capability_error(server, message)
        raise RuntimeError(message) from exc
    _store_mcp_server_capabilities(server, capabilities)
    append_mcp_log(
        server,
        "capabilities_succeeded",
        "",
        tools=len(capabilities.tools),
        resources=len(capabilities.resources),
        resource_templates=len(capabilities.resource_templates),
        prompts=len(capabilities.prompts),
    )
    return capabilities


def _store_mcp_server_capabilities(server: McpServerConfig, capabilities: McpServerCapabilities) -> None:
    with _CACHE_LOCK:
        key = _capability_cache_key(server)
        _CAPABILITY_CACHE[key] = capabilities
        _CAPABILITY_ERROR_CACHE.pop(key, None)


def _store_mcp_server_capability_error(server: McpServerConfig, error: str) -> None:
    with _CACHE_LOCK:
        key = _capability_cache_key(server)
        _CAPABILITY_ERROR_CACHE[key] = error
        _CAPABILITY_CACHE.pop(key, None)


def _cache_remote_tools(server: McpServerConfig, remote_tools: list[_RemoteTool]) -> None:
    key = _capability_cache_key(server)
    tools = tuple(
        McpCapability(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            enabled=server.component_enabled("tools", tool.name),
        )
        for tool in remote_tools
    )
    with _CACHE_LOCK:
        existing = _CAPABILITY_CACHE.get(key)
        if existing is None:
            _CAPABILITY_CACHE[key] = McpServerCapabilities(tools=tools)
        else:
            _CAPABILITY_CACHE[key] = McpServerCapabilities(
                tools=tools,
                resources=existing.resources,
                resource_templates=existing.resource_templates,
                prompts=existing.prompts,
                supports_resources=existing.supports_resources,
                supports_resource_templates=existing.supports_resource_templates,
                supports_prompts=existing.supports_prompts,
            )


def _remote_tools_from_capabilities(capabilities: McpServerCapabilities) -> list[_RemoteTool]:
    return [
        _RemoteTool(
            name=tool.name,
            description=tool.description,
            input_schema=_dict(tool.input_schema),
        )
        for tool in capabilities.tools
        if tool.name
    ]


def _capability_cache_key(server: McpServerConfig) -> tuple:
    disabled = tuple(
        (kind, tuple(values))
        for kind, values in sorted(server.disabled_components.items())
    )
    return (
        server.key,
        server.fingerprint,
        server.enabled,
        server.review_required,
        server.reviewed,
        tuple(server.errors),
        disabled,
    )


def _remote_tool_definition(
    server: McpServerConfig,
    remote: _RemoteTool,
    *,
    used_names: set[str] | None = None,
) -> ToolDefinition:
    name = _unique_tool_name(_mcp_tool_name(server, remote.name), remote.name, used_names or set())
    description = f"[MCP: {server.name}] {remote.description or remote.name}"
    return _definition(
        server,
        name,
        description,
        remote.input_schema or {"type": "object", "properties": {}},
        lambda ctx, inputs, srv=server, tool_name=remote.name: _run(_call_tool(srv, tool_name, dict(inputs or {}))),
    )


def _server_error_tool(server: McpServerConfig, error: str) -> ToolDefinition:
    prefix = _tool_prefix(server)
    return _definition(
        server,
        f"{prefix}connection_error",
        f"[MCP: {server.name}] Connection failed. Calling this tool returns the latest error.",
        {"type": "object", "properties": {}},
        lambda ctx, inputs, message=error: f"[tool error] MCP server '{server.name}' failed: {message}",
    )


def _definition(server: McpServerConfig, name: str, description: str, schema: dict, execute) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
        execute=execute,
        parallel_safe=False,
        approval="once",
        source="mcp",
        extension_id=f"mcp:{server.name}",
        surfaces=("chat",),
    )


async def _discover_server_tools(server: McpServerConfig, *, oauth_interaction=None) -> list[_RemoteTool]:
    async with _session(server, oauth_interaction=oauth_interaction) as session:
        responses = await _list_paginated(session, "list_tools", server.tool_timeout_sec)
        tools: list[_RemoteTool] = []
        for response in responses:
            tools.extend(_remote_tools_from_response(response))
        return tools


async def _discover_server_capabilities(server: McpServerConfig, *, oauth_interaction=None) -> McpServerCapabilities:
    async with _session(server, oauth_interaction=oauth_interaction) as session:
        tools_responses = []
        if _session_capability_supported(session, "tools"):
            tools_responses = await _list_paginated(session, "list_tools", server.tool_timeout_sec)
        supports_resources = _session_capability_supported(session, "resources")
        supports_prompts = _session_capability_supported(session, "prompts")
        resources_result = _OptionalListResult(False, [])
        templates_result = _OptionalListResult(False, [])
        prompts_result = _OptionalListResult(False, [])
        if supports_resources:
            resources_result = await _try_paginated_session_call(session, "list_resources", server.tool_timeout_sec)
            templates_result = await _try_paginated_session_call(
                session,
                "list_resource_templates",
                server.tool_timeout_sec,
            )
        if supports_prompts:
            prompts_result = await _try_paginated_session_call(session, "list_prompts", server.tool_timeout_sec)
        remote_tools: list[_RemoteTool] = []
        for response in tools_responses:
            remote_tools.extend(_remote_tools_from_response(response))
        return McpServerCapabilities(
            tools=tuple(
                McpCapability(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    enabled=server.component_enabled("tools", tool.name),
                )
                for tool in remote_tools
            ),
            resources=_capabilities_from_items(
                _items_from_responses(resources_result.responses, "resources"),
                kind="resources",
                server=server,
                name_fields=("name", "uri"),
                uri_fields=("uri",),
            ),
            resource_templates=_capabilities_from_items(
                _items_from_responses(templates_result.responses, "resourceTemplates", "resource_templates"),
                kind="resource_templates",
                server=server,
                name_fields=("name", "uriTemplate", "uri_template"),
                uri_fields=("uriTemplate", "uri_template"),
            ),
            prompts=_capabilities_from_items(
                _items_from_responses(prompts_result.responses, "prompts"),
                kind="prompts",
                server=server,
                name_fields=("name",),
                uri_fields=(),
            ),
            supports_resources=resources_result.supported,
            supports_resource_templates=templates_result.supported,
            supports_prompts=prompts_result.supported,
        )


async def _call_tool(server: McpServerConfig, name: str, arguments: dict) -> str:
    append_mcp_log(server, "tool_started", name)
    try:
        async with _session(server) as session:
            result = await asyncio.wait_for(
                session.call_tool(name, arguments=arguments),
                timeout=server.tool_timeout_sec,
            )
            text = _format_mcp_result(result)
    except BaseException as exc:
        message = _mcp_error_message(exc)
        append_mcp_log(server, "tool_failed", name, error=message)
        raise RuntimeError(message) from exc
    append_mcp_log(server, "tool_succeeded", name)
    return text


async def _list_resources(server: McpServerConfig) -> str:
    async with _session(server) as session:
        result = await _merged_paginated_result(session, "list_resources", server.tool_timeout_sec, ("resources",))
        return _format_mcp_result(
            _filter_capability_result(server, "resources", result, ("resources",), ("uri", "name"))
        )


async def _read_resource(server: McpServerConfig, uri: str) -> str:
    if not uri:
        return "[tool error] read_resource requires a uri."
    if not server.component_enabled("resources", uri):
        return f"[tool error] MCP resource is disabled: {uri}"
    async with _session(server) as session:
        result = await asyncio.wait_for(session.read_resource(uri), timeout=server.tool_timeout_sec)
        return _format_mcp_result(result)


async def _list_resource_templates(server: McpServerConfig) -> str:
    async with _session(server) as session:
        result = await _merged_paginated_result(
            session,
            "list_resource_templates",
            server.tool_timeout_sec,
            ("resourceTemplates", "resource_templates"),
        )
        return _format_mcp_result(
            _filter_capability_result(
                server,
                "resource_templates",
                result,
                ("resourceTemplates", "resource_templates"),
                ("uriTemplate", "uri_template", "name"),
            )
        )


async def _list_prompts(server: McpServerConfig) -> str:
    async with _session(server) as session:
        result = await _merged_paginated_result(session, "list_prompts", server.tool_timeout_sec, ("prompts",))
        return _format_mcp_result(_filter_capability_result(server, "prompts", result, ("prompts",), ("name",)))


async def _get_prompt(server: McpServerConfig, name: str, arguments: dict) -> str:
    if not name:
        return "[tool error] get_prompt requires a name."
    if not server.component_enabled("prompts", name):
        return f"[tool error] MCP prompt is disabled: {name}"
    async with _session(server) as session:
        result = await asyncio.wait_for(
            session.get_prompt(name, arguments=arguments),
            timeout=server.tool_timeout_sec,
        )
        return _format_mcp_result(result)


class _session:
    def __init__(self, server: McpServerConfig, *, oauth_interaction=None):
        self._server = server
        self._oauth_interaction = oauth_interaction
        self._transport_cm = None
        self._session_cm = None
        self._session = None
        self._http_client = None
        self._session_id_callback = None

    async def __aenter__(self):
        if (
            self._server.url
            and self._server.auth_type == "oauth"
            and self._oauth_interaction is None
            and not has_oauth_tokens(self._server)
        ):
            raise McpOAuthRequired("")

        try:
            from mcp import ClientSession, StdioServerParameters
        except ImportError as exc:
            raise RuntimeError("Python package 'mcp' is required for MCP support.") from exc

        if self._server.url:
            use_oauth = self._server.auth_type == "oauth" or (
                self._server.auth_type == "auto" and has_oauth_tokens(self._server)
            )
            self._build_http_transport(use_oauth=use_oauth)
        else:
            try:
                from mcp.client.stdio import stdio_client
            except ImportError as exc:
                raise RuntimeError("Installed 'mcp' package does not support stdio clients.") from exc
            params = StdioServerParameters(
                command=self._server.command,
                args=list(self._server.args),
                env=_stdio_env(self._server),
            )
            self._transport_cm = stdio_client(params)

        try:
            return await self._open_transport(ClientSession)
        except BaseException as exc:
            await self._close(None, None, None, suppress_errors=True)
            if (
                self._server.url
                and self._server.auth_type == "auto"
                and not use_oauth
                and _looks_like_http_auth_required(exc)
            ):
                if self._oauth_interaction is None:
                    raise McpOAuthRequired("") from exc
                self._build_http_transport(use_oauth=True)
                try:
                    return await self._open_transport(ClientSession)
                except BaseException:
                    await self._close(None, None, None, suppress_errors=True)
                    raise
            raise

    async def __aexit__(self, exc_type, exc, tb):
        await self._close(exc_type, exc, tb)

    def _build_http_transport(self, *, use_oauth: bool) -> None:
        kwargs = {}
        if use_oauth:
            try:
                from mcp.client.streamable_http import streamable_http_client
            except ImportError as exc:
                raise RuntimeError("Installed 'mcp' package does not support streamable HTTP clients.") from exc
            self._http_client = create_oauth_http_client(
                self._server,
                self._oauth_interaction or NonInteractiveOAuthInteraction(),
            )
            kwargs["http_client"] = self._http_client
            self._transport_cm = streamable_http_client(self._server.url, terminate_on_close=False, **kwargs)
        else:
            headers = _http_headers(self._server)
            try:
                import httpx
            except ImportError as exc:
                raise RuntimeError("Python package 'httpx' is required for MCP HTTP transport.") from exc
            self._http_client = httpx.AsyncClient(headers=headers, follow_redirects=True)
            self._transport_cm = _strict_streamable_http_client(self._server.url, self._http_client)

    async def _open_transport(self, client_session_type):
        streams = await self._transport_cm.__aenter__()
        read_stream, write_stream = streams[0], streams[1]
        if len(streams) > 2 and callable(streams[2]):
            self._session_id_callback = streams[2]
        self._session_cm = client_session_type(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        try:
            initialize_result = await asyncio.wait_for(
                self._session.initialize(),
                timeout=self._server.startup_timeout_sec,
            )
            setattr(self._session, "_aichs_initialize_result", initialize_result)
        except asyncio.CancelledError as exc:
            diagnostic = _http_initialize_diagnostic(self._server)
            if diagnostic:
                raise RuntimeError(f"MCP initialization failed. {diagnostic}") from exc
            raise RuntimeError(
                "MCP initialization was cancelled by the transport. "
                "Check the MCP logs and verify the server URL, auth headers, and server compatibility."
            ) from exc
        except Exception as exc:
            if self._server.url and _looks_like_http_transport_failure(exc):
                diagnostic = _http_initialize_diagnostic(self._server)
                if diagnostic:
                    raise RuntimeError(f"MCP initialization failed. {diagnostic}") from exc
            raise
        return self._session

    async def _close(self, exc_type, exc, tb, *, suppress_errors: bool = False):
        first_error = None
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(exc_type, exc, tb)
            except BaseException as close_exc:
                if not suppress_errors and not _is_transport_close_noise(close_exc):
                    first_error = first_error or close_exc
            finally:
                self._session_cm = None
        if self._transport_cm is not None:
            await self._terminate_http_session()
            try:
                await self._transport_cm.__aexit__(exc_type, exc, tb)
            except BaseException as close_exc:
                if not suppress_errors and not _is_transport_close_noise(close_exc):
                    first_error = first_error or close_exc
            finally:
                self._transport_cm = None
                self._session_id_callback = None
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except BaseException as close_exc:
                if not suppress_errors and not _is_transport_close_noise(close_exc):
                    first_error = first_error or close_exc
            finally:
                self._http_client = None
        if first_error is not None:
            raise first_error

    async def _terminate_http_session(self) -> None:
        if self._http_client is None or self._session_id_callback is None or not self._server.url:
            return
        try:
            session_id = self._session_id_callback()
        except Exception:
            session_id = ""
        if not session_id:
            return
        try:
            response = await self._http_client.delete(
                self._server.url,
                headers={"mcp-session-id": str(session_id)},
            )
        except Exception:
            return
        if response.status_code in (200, 202, 204, 405):
            return


@contextlib.asynccontextmanager
async def _strict_streamable_http_client(url: str, http_client):
    try:
        import anyio
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse
    except ImportError as exc:
        raise RuntimeError("Python packages 'mcp' and 'anyio' are required for MCP HTTP transport.") from exc

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)
    state = {"session_id": "", "protocol_version": ""}

    def get_session_id() -> str:
        return state["session_id"]

    def headers() -> dict[str, str]:
        result = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if state["session_id"]:
            result["mcp-session-id"] = state["session_id"]
        if state["protocol_version"]:
            result["mcp-protocol-version"] = state["protocol_version"]
        return result

    def is_initialize(message: JSONRPCMessage) -> bool:
        return isinstance(message.root, JSONRPCRequest) and message.root.method == "initialize"

    def update_initialize_state(message: JSONRPCMessage, response) -> None:
        session_id = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
        if session_id:
            state["session_id"] = str(session_id)
        if isinstance(message.root, JSONRPCResponse):
            result = message.root.result or {}
            protocol_version = result.get("protocolVersion")
            if protocol_version:
                state["protocol_version"] = str(protocol_version)

    async def send_sse_response(response, *, initial_request: bool) -> None:
        data_lines: list[str] = []
        async for raw_line in response.aiter_lines():
            line = raw_line.strip()
            if not line:
                if data_lines:
                    message = JSONRPCMessage.model_validate_json("\n".join(data_lines))
                    if initial_request:
                        update_initialize_state(message, response)
                    await read_stream_writer.send(SessionMessage(message))
                    data_lines = []
                    if isinstance(message.root, JSONRPCResponse | JSONRPCError):
                        await response.aclose()
                        return
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            message = JSONRPCMessage.model_validate_json("\n".join(data_lines))
            if initial_request:
                update_initialize_state(message, response)
            await read_stream_writer.send(SessionMessage(message))
            if isinstance(message.root, JSONRPCResponse | JSONRPCError):
                await response.aclose()
                return
        raise RuntimeError("MCP server returned an SSE stream without a JSON-RPC response.")

    async def send_json_response(response, *, initial_request: bool) -> None:
        message = JSONRPCMessage.model_validate_json(await response.aread())
        if initial_request:
            update_initialize_state(message, response)
        await read_stream_writer.send(SessionMessage(message))

    async def post_writer() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    message = session_message.message
                    initial_request = is_initialize(message)
                    async with http_client.stream(
                        "POST",
                        url,
                        json=message.model_dump(by_alias=True, mode="json", exclude_none=True),
                        headers=headers(),
                    ) as response:
                        if response.status_code == 202:
                            continue
                        if response.status_code == 404 and isinstance(message.root, JSONRPCRequest):
                            raise RuntimeError(f"MCP session was terminated by {url}.")
                        response.raise_for_status()
                        if isinstance(message.root, JSONRPCNotification):
                            continue
                        content_type = str(response.headers.get("content-type", "")).lower()
                        if content_type.startswith("application/json"):
                            await send_json_response(response, initial_request=initial_request)
                        elif content_type.startswith("text/event-stream"):
                            await send_sse_response(response, initial_request=initial_request)
                        else:
                            raise RuntimeError(f"MCP server returned unsupported content type: {content_type or 'unknown'}")
        except Exception as exc:
            if not _is_transport_close_noise(exc):
                await read_stream_writer.send(exc)
        finally:
            await read_stream_writer.aclose()
            await write_stream.aclose()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(post_writer)
        try:
            yield read_stream, write_stream, get_session_id
        finally:
            task_group.cancel_scope.cancel()


def _stdio_env(server: McpServerConfig) -> dict[str, str]:
    env = os.environ.copy()
    for name in server.env_vars:
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(server.env)
    return env


def _http_headers(server: McpServerConfig) -> dict[str, str]:
    headers = {_normalize_header_name(key): str(value) for key, value in server.headers.items()}
    if server.bearer_token_env_var:
        token = os.environ.get(server.bearer_token_env_var, "")
        if token:
            headers.setdefault("Authorization", f"Bearer {token}")
    return headers


def _normalize_header_name(value: str) -> str:
    if str(value).lower() == "authorization":
        return "Authorization"
    return str(value)


def _mcp_error_message(exc: BaseException) -> str:
    leaves = _mcp_error_leaves(exc)
    for leaf in leaves:
        text = str(leaf).strip()
        lowered = text.lower()
        if (
            text
            and "unhandled errors in a taskgroup" not in lowered
            and "no running event loop" not in lowered
        ):
            return text
    text = str(exc).strip()
    if text and "no running event loop" not in text.lower():
        return text
    if any("no running event loop" in str(leaf).lower() for leaf in leaves):
        return "MCP transport closed unexpectedly while processing the request."
    return type(exc).__name__


def _mcp_error_leaves(exc: BaseException) -> list[BaseException]:
    children = getattr(exc, "exceptions", None)
    if not children:
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, BaseException):
            return _mcp_error_leaves(cause)
        context = getattr(exc, "__context__", None)
        if isinstance(context, BaseException):
            return _mcp_error_leaves(context)
        return [exc]
    leaves: list[BaseException] = []
    for child in children:
        if isinstance(child, BaseException):
            leaves.extend(_mcp_error_leaves(child))
    return leaves or [exc]


def _is_transport_close_noise(exc: BaseException) -> bool:
    text = " ".join([_mcp_error_message(exc), *[str(leaf) for leaf in _mcp_error_leaves(exc)]]).lower()
    return any(
        marker in text
        for marker in (
            "cancel scope",
            "generator didn't stop after athrow",
            "generatorexit",
            "unhandled errors in a taskgroup",
            "no running event loop",
        )
    )


def _looks_like_http_auth_required(exc: BaseException) -> bool:
    text = _mcp_error_message(exc).lower()
    return any(
        marker in text
        for marker in (
            "401",
            "unauthorized",
            "www-authenticate",
            "authorization header",
            "access token",
            "authentication required",
        )
    )


def _looks_like_http_transport_failure(exc: BaseException) -> bool:
    text = _mcp_error_message(exc).lower()
    return any(
        marker in text
        for marker in (
            "httpstatuserror",
            "client error",
            "server error",
            "bad request",
            "for url",
        )
    )


def _http_initialize_diagnostic(server: McpServerConfig) -> str:
    if not server.url:
        return ""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "aichs", "version": "0"},
            },
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **_http_headers(server),
    }
    try:
        req = urlrequest.Request(server.url, data=payload, headers=headers, method="POST")
        with urlrequest.urlopen(req, timeout=min(max(server.startup_timeout_sec, 1.0), 5.0)) as response:
            if 200 <= response.status < 300:
                return ""
            return f"HTTP {response.status} from {server.url}."
    except urlerror.HTTPError as exc:
        body = exc.read(500).decode("utf-8", errors="replace").strip()
        auth = exc.headers.get("www-authenticate", "")
        parts = [f"HTTP {exc.code} from {server.url}."]
        if body:
            parts.append(body)
        if auth:
            parts.append(_redact_header_value(auth))
        return " ".join(parts)
    except OSError as exc:
        return str(exc)


def _redact_header_value(value: str) -> str:
    text = str(value)
    for marker in ("access_token=", "refresh_token=", "client_secret="):
        if marker in text.lower():
            return "Authentication challenge returned sensitive details."
    return text


def _format_mcp_result(result: Any) -> str:
    data = _jsonable(result)
    if isinstance(data, dict):
        content = data.get("content")
        structured = data.get("structuredContent")
        if structured is None:
            structured = data.get("structured_content")
        is_error = bool(data.get("isError") or data.get("is_error"))
        if is_error:
            text = _mcp_text_content(content)
            if text:
                return f"[tool error] {text}"
        if isinstance(content, list) and structured is None and not is_error:
            texts = [
                str(item.get("text"))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text") is not None
            ]
            if texts and len(texts) == len(content):
                return "\n".join(texts)
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def _mcp_text_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    texts = [
        str(item.get("text"))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text") is not None
    ]
    return "\n".join(texts).strip()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    return value


async def _optional_session_call(session, method_name: str, timeout: float) -> Any:
    method = getattr(session, method_name, None)
    if not callable(method):
        return {}
    try:
        return await asyncio.wait_for(method(), timeout=timeout)
    except asyncio.CancelledError:
        return {}
    except Exception:
        return {}


async def _optional_paginated_session_call(session, method_name: str, timeout: float) -> list[Any]:
    method = getattr(session, method_name, None)
    if not callable(method):
        return []
    try:
        return await _list_paginated(session, method_name, timeout)
    except asyncio.CancelledError:
        return []
    except Exception:
        return []


async def _try_paginated_session_call(session, method_name: str, timeout: float) -> _OptionalListResult:
    method = getattr(session, method_name, None)
    if not callable(method):
        return _OptionalListResult(False, [])
    try:
        return _OptionalListResult(True, await _list_paginated(session, method_name, timeout))
    except asyncio.CancelledError:
        return _OptionalListResult(False, [])
    except Exception:
        return _OptionalListResult(False, [])


async def _list_paginated(session, method_name: str, timeout: float, *, max_pages: int = 100) -> list[Any]:
    method = getattr(session, method_name)
    responses = []
    cursor = None
    seen_cursors = set()
    for _page_index in range(max_pages):
        response = await asyncio.wait_for(_call_paginated_method(method, cursor), timeout=timeout)
        responses.append(response)
        cursor = _next_cursor(response)
        if not cursor:
            break
        if cursor in seen_cursors:
            raise RuntimeError(f"MCP server repeated pagination cursor for {method_name}.")
        seen_cursors.add(cursor)
    else:
        raise RuntimeError(f"MCP server exceeded {max_pages} pages for {method_name}.")
    return responses


async def _call_paginated_method(method, cursor: str | None) -> Any:
    if cursor:
        try:
            return await method(cursor=cursor)
        except TypeError:
            return await method(cursor)
    return await method()


def _next_cursor(response: Any) -> str:
    data = _jsonable(response)
    if not isinstance(data, dict):
        return ""
    return str(data.get("nextCursor") or data.get("next_cursor") or "").strip()


async def _merged_paginated_result(
    session,
    method_name: str,
    timeout: float,
    keys: tuple[str, ...],
) -> dict:
    pages = await _list_paginated(session, method_name, timeout)
    result = {}
    for page in pages:
        data = _jsonable(page)
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if key not in ("nextCursor", "next_cursor") and key not in result:
                result[key] = value
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                result.setdefault(key, [])
                if isinstance(result[key], list):
                    result[key].extend(value)
                break
    return result


def _remote_tools_from_response(response: Any) -> list[_RemoteTool]:
    return [
        _RemoteTool(
            name=str(getattr(tool, "name", "") or ""),
            description=str(getattr(tool, "description", "") or ""),
            input_schema=_dict(getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)),
        )
        for tool in getattr(response, "tools", []) or []
        if str(getattr(tool, "name", "") or "")
    ]


def _items_from_response(response: Any, *keys: str) -> list[dict]:
    data = _jsonable(response)
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _items_from_responses(responses: list[Any], *keys: str) -> list[dict]:
    items = []
    for response in responses:
        items.extend(_items_from_response(response, *keys))
    return items


def _session_capability_supported(session, name: str) -> bool:
    if not hasattr(session, "_aichs_initialize_result"):
        return True
    data = _jsonable(getattr(session, "_aichs_initialize_result", None))
    if not isinstance(data, dict):
        return False
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    capability = capabilities.get(name)
    return capability is not None


def _filter_capability_result(
    server: McpServerConfig,
    kind: str,
    response: Any,
    keys: tuple[str, ...],
    identity_fields: tuple[str, ...],
) -> Any:
    data = _jsonable(response)
    if not isinstance(data, dict):
        return data
    filtered = dict(data)
    for key in keys:
        value = filtered.get(key)
        if not isinstance(value, list):
            continue
        kept = []
        for item in value:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            identity = _first_string(item, identity_fields)
            if identity and server.component_enabled(kind, identity):
                kept.append(item)
        filtered[key] = kept
    return filtered


def _capabilities_from_items(
    items: list[dict],
    *,
    kind: str,
    server: McpServerConfig,
    name_fields: tuple[str, ...],
    uri_fields: tuple[str, ...],
) -> tuple[McpCapability, ...]:
    capabilities = []
    for item in items:
        name = _first_string(item, name_fields)
        uri = _first_string(item, uri_fields)
        identity = uri or name
        if not identity:
            continue
        arguments = []
        raw_args = item.get("arguments")
        if isinstance(raw_args, list):
            for arg in raw_args:
                if isinstance(arg, dict) and arg.get("name"):
                    arguments.append(str(arg["name"]))
        capabilities.append(
            McpCapability(
                name=name or uri,
                description=_first_string(item, ("description", "title")),
                uri=uri,
                mime_type=_first_string(item, ("mimeType", "mime_type")),
                arguments=tuple(arguments),
                enabled=server.component_enabled(kind, identity),
            )
        )
    return tuple(capabilities)


def _first_string(data: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = data.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def _dict(value: Any) -> dict:
    value = _jsonable(value)
    return value if isinstance(value, dict) else {}


def _run(coro) -> str | list[_RemoteTool]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run_coroutine_sync(coro)
    thread_result = {}

    def runner():
        try:
            thread_result["value"] = _run_coroutine_sync(coro)
        except BaseException as exc:
            thread_result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in thread_result:
        exc = thread_result["error"]
        raise RuntimeError(_mcp_error_message(exc)) from exc
    return thread_result.get("value")


def _run_coroutine_sync(coro):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(coro)
        except BaseException as exc:
            raise RuntimeError(_mcp_error_message(exc)) from exc
        cleanup_error = None
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            if not _is_transport_close_noise(exc):
                cleanup_error = exc
        if cleanup_error is not None:
            raise RuntimeError(_mcp_error_message(cleanup_error)) from cleanup_error
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _tool_prefix(server: McpServerConfig) -> str:
    return f"mcp__{_safe_name(server.name)}__"


def _mcp_tool_name(server: McpServerConfig, tool_name: str) -> str:
    return f"{_tool_prefix(server)}{_safe_name(tool_name)}"


def _unique_tool_name(candidate: str, original: str, used: set[str]) -> str:
    if candidate not in used:
        return candidate
    suffix = hashlib.sha1(str(original).encode("utf-8")).hexdigest()[:8]
    alt = f"{candidate}_{suffix}"
    index = 2
    while alt in used:
        alt = f"{candidate}_{suffix}_{index}"
        index += 1
    return alt


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(value or "tool"))
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_") or "tool"
