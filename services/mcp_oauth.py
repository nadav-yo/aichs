from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config
from services.mcp_config import McpServerConfig


MCP_OAUTH_STATE_NAME = "mcp.oauth.json"
DEFAULT_REDIRECT_URI = "http://localhost:33331/callback"


class McpOAuthRequired(RuntimeError):
    def __init__(self, auth_url: str):
        super().__init__(
            "MCP OAuth authorization is required. Open this server in the MCP dialog and authorize it."
        )
        self.auth_url = auth_url


class McpOAuthCancelled(RuntimeError):
    pass


class McpOAuthConfigurationError(RuntimeError):
    pass


class NonInteractiveOAuthInteraction:
    def __init__(self):
        self.auth_url = ""

    async def redirect_handler(self, auth_url: str) -> None:
        self.auth_url = auth_url

    async def callback_handler(self) -> tuple[str, str | None]:
        raise McpOAuthRequired(self.auth_url)


class BlockingOAuthInteraction:
    def __init__(self, on_auth_url):
        self._on_auth_url = on_auth_url
        self._event = threading.Event()
        self._callback_url = ""
        self._error = ""

    async def redirect_handler(self, auth_url: str) -> None:
        self._on_auth_url(auth_url)

    async def callback_handler(self) -> tuple[str, str | None]:
        self._event.wait()
        if self._error:
            raise McpOAuthCancelled(self._error)
        return parse_oauth_callback_url(self._callback_url)

    def submit_callback_url(self, callback_url: str) -> None:
        self._callback_url = callback_url
        self._event.set()

    def cancel(self, message: str = "OAuth authorization was cancelled.") -> None:
        self._error = message
        self._event.set()


def mcp_oauth_state_path() -> Path:
    return config.AICHS_HOME / "project" / MCP_OAUTH_STATE_NAME


def has_oauth_tokens(server: McpServerConfig) -> bool:
    item = _server_state(server)
    return bool(item.get("tokens"))


def clear_oauth_state(server: McpServerConfig) -> None:
    state = _read_state()
    servers = state.get("servers")
    if isinstance(servers, dict):
        servers.pop(server.key, None)
        _write_state(state)


def parse_oauth_callback_url(callback_url: str) -> tuple[str, str | None]:
    params = parse_qs(urlparse(str(callback_url).strip()).query)
    code = params.get("code", [""])[0]
    if not code:
        raise ValueError("OAuth callback URL must contain a code parameter.")
    return code, params.get("state", [None])[0]


def create_oauth_http_client(server: McpServerConfig, interaction=None):
    _validate_oauth_client_configuration(server)
    try:
        import httpx
        from pydantic import AnyUrl
        from mcp.client.auth import OAuthClientProvider, TokenStorage
        from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
    except ImportError as exc:
        raise RuntimeError("Python package 'mcp' with OAuth client support is required for MCP OAuth.") from exc

    redirect_uri = server.oauth_redirect_uri or DEFAULT_REDIRECT_URI
    metadata = OAuthClientMetadata(
        client_name=server.oauth_client_name or "aichs",
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=server.oauth_scope or None,
    )

    class _PersistentTokenStorage(TokenStorage):
        async def get_tokens(self):
            item = _server_state(server)
            tokens = item.get("tokens")
            if isinstance(tokens, dict):
                return OAuthToken.model_validate(tokens)
            return None

        async def set_tokens(self, tokens) -> None:
            _update_server_state(server, "tokens", _jsonable(tokens))

        async def get_client_info(self):
            item = _server_state(server)
            client_info = item.get("client_info")
            if isinstance(client_info, dict):
                return OAuthClientInformationFull.model_validate(client_info)
            if server.oauth_client_id:
                data = _jsonable(metadata)
                data["client_id"] = server.oauth_client_id
                if server.oauth_client_secret:
                    data["client_secret"] = server.oauth_client_secret
                return OAuthClientInformationFull.model_validate(data)
            return None

        async def set_client_info(self, client_info) -> None:
            _update_server_state(server, "client_info", _jsonable(client_info))

    oauth_interaction = interaction or NonInteractiveOAuthInteraction()
    provider = OAuthClientProvider(
        server_url=server.oauth_server_url or server.url,
        client_metadata=metadata,
        storage=_PersistentTokenStorage(),
        redirect_handler=oauth_interaction.redirect_handler,
        callback_handler=oauth_interaction.callback_handler,
    )
    return httpx.AsyncClient(auth=provider, follow_redirects=True)


def _validate_oauth_client_configuration(server: McpServerConfig) -> None:
    if not _requires_configured_oauth_client(server):
        return
    if server.oauth_client_id:
        return
    state = _server_state(server)
    if isinstance(state.get("client_info"), dict) or isinstance(state.get("tokens"), dict):
        return
    raise McpOAuthConfigurationError(
        "This MCP server uses GitHub OAuth, which does not support MCP dynamic client registration. "
        "Add a pre-registered OAuth client_id to this server's mcp.json auth object, then authorize again."
    )


def _requires_configured_oauth_client(server: McpServerConfig) -> bool:
    urls = [server.url, server.oauth_server_url]
    for value in urls:
        host = urlparse(str(value or "")).netloc.lower()
        path = urlparse(str(value or "")).path.rstrip("/").lower()
        if host == "api.githubcopilot.com":
            return True
        if host == "github.com" and path == "/login/oauth":
            return True
    return False


def _server_state(server: McpServerConfig) -> dict:
    state = _read_state()
    servers = state.get("servers")
    if not isinstance(servers, dict):
        return {}
    item = servers.get(server.key)
    if not isinstance(item, dict) or item.get("fingerprint") != server.fingerprint:
        return {}
    return item


def _update_server_state(server: McpServerConfig, key: str, value) -> None:
    state = _read_state()
    servers = state.setdefault("servers", {})
    if not isinstance(servers, dict):
        servers = {}
        state["servers"] = servers
    item = servers.setdefault(server.key, {})
    if not isinstance(item, dict):
        item = {}
        servers[server.key] = item
    item["fingerprint"] = server.fingerprint
    item[key] = value
    _write_state(state)


def _read_state() -> dict:
    path = mcp_oauth_state_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(data: dict) -> None:
    path = mcp_oauth_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
