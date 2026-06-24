from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import config


MCP_CONFIG_NAME = "mcp.json"
MCP_STATE_NAME = "mcp.state.json"


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    scope: Literal["global", "project"]
    raw: dict
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    env_vars: tuple[str, ...] = ()
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    bearer_token_env_var: str = ""
    auth_type: Literal["auto", "none", "oauth", "headers"] = "none"
    oauth_scope: str = ""
    oauth_redirect_uri: str = ""
    oauth_server_url: str = ""
    oauth_client_name: str = "aichs"
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    startup_timeout_sec: float = 20.0
    tool_timeout_sec: float = 60.0
    enabled: bool = True
    review_required: bool = False
    reviewed: bool = True
    fingerprint: str = ""
    errors: tuple[str, ...] = ()
    disabled_components: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.scope}:{self.name}"

    @property
    def transport(self) -> str:
        return "http" if self.url else "stdio"

    @property
    def available(self) -> bool:
        return self.enabled and not self.review_required and not self.errors

    def component_enabled(self, kind: str, name: str) -> bool:
        disabled = set(self.disabled_components.get(kind, ()))
        return "*" not in disabled and str(name or "") not in disabled


@dataclass(frozen=True)
class McpConfigSnapshot:
    servers: tuple[McpServerConfig, ...]
    errors: tuple[str, ...] = ()
    signature: tuple = ()


def global_mcp_config_path() -> Path:
    return config.AICHS_HOME / MCP_CONFIG_NAME


def project_mcp_config_path(cwd: str | None) -> Path | None:
    if not cwd:
        return None
    return Path(cwd) / config.PROJECT_AGENTS_DIR / MCP_CONFIG_NAME


def mcp_state_path() -> Path:
    return config.AICHS_HOME / "project" / MCP_STATE_NAME


def mcp_config_exists(cwd: str | None = None) -> bool:
    global_path = global_mcp_config_path()
    if global_path.exists():
        return True
    project_path = project_mcp_config_path(cwd)
    return bool(project_path and project_path.exists())


def load_mcp_config(cwd: str | None = None, *, include_disabled: bool = False) -> McpConfigSnapshot:
    if not mcp_config_exists(cwd):
        return McpConfigSnapshot(servers=(), errors=(), signature=())

    state = _read_json_object(mcp_state_path())
    servers: list[McpServerConfig] = []
    errors: list[str] = []
    signatures = []

    for scope, path in _config_paths(cwd):
        if path is None or not path.exists():
            continue
        try:
            raw = _read_json_object(path)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            signatures.append((scope, str(path), "error", str(exc)))
            continue
        try:
            stat = path.stat()
            signatures.append((scope, str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signatures.append((scope, str(path), "missing"))
        for name, entry in _server_entries(raw).items():
            server = _server_config(scope, name, entry, state)
            if include_disabled or server.enabled or server.review_required:
                servers.append(server)

    return McpConfigSnapshot(
        servers=tuple(servers),
        errors=tuple(errors),
        signature=tuple(signatures) + _state_signature(state),
    )


def set_mcp_server_enabled(cwd: str | None, scope: str, name: str, enabled: bool) -> None:
    state = _read_json_object(mcp_state_path())
    servers = state.setdefault("servers", {})
    if not isinstance(servers, dict):
        servers = {}
        state["servers"] = servers
    item = servers.setdefault(_state_key(scope, name), {})
    if not isinstance(item, dict):
        item = {}
        servers[_state_key(scope, name)] = item
    item["enabled"] = bool(enabled)
    _write_json_object(mcp_state_path(), state)


def set_mcp_component_enabled(
    cwd: str | None,
    scope: str,
    server_name: str,
    kind: str,
    component_name: str,
    enabled: bool,
) -> None:
    if kind not in {"tools", "resources", "resource_templates", "prompts"}:
        raise ValueError("unknown MCP component kind")
    name = str(component_name or "").strip()
    if not name:
        raise ValueError("MCP component name is required.")
    state = _read_json_object(mcp_state_path())
    servers = state.setdefault("servers", {})
    if not isinstance(servers, dict):
        servers = {}
        state["servers"] = servers
    item = servers.setdefault(_state_key(scope, server_name), {})
    if not isinstance(item, dict):
        item = {}
        servers[_state_key(scope, server_name)] = item
    disabled = item.setdefault("disabled_components", {})
    if not isinstance(disabled, dict):
        disabled = {}
        item["disabled_components"] = disabled
    values = disabled.setdefault(kind, [])
    if not isinstance(values, list):
        values = []
    values = [str(value) for value in values if str(value or "").strip()]
    if enabled:
        values = [value for value in values if value != name]
    elif name not in values:
        values.append(name)
    disabled[kind] = sorted(values)
    _write_json_object(mcp_state_path(), state)


def review_mcp_server(cwd: str | None, scope: str, name: str) -> bool:
    snapshot = load_mcp_config(cwd, include_disabled=True)
    server = next(
        (item for item in snapshot.servers if item.scope == scope and item.name == name),
        None,
    )
    if server is None:
        return False
    state = _read_json_object(mcp_state_path())
    reviewed = state.setdefault("reviewed", {})
    if not isinstance(reviewed, dict):
        reviewed = {}
        state["reviewed"] = reviewed
    reviewed[server.key] = server.fingerprint
    _write_json_object(mcp_state_path(), state)
    return True


def write_mcp_json(path: str | Path, servers: dict) -> None:
    clean = {"mcpServers": servers if isinstance(servers, dict) else {}}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert_mcp_server(cwd: str | None, scope: str, name: str, entry: dict) -> Path:
    target = global_mcp_config_path() if scope == "global" else project_mcp_config_path(cwd)
    if target is None:
        raise ValueError("Project MCP config requires a workspace.")
    data = _read_json_object(target)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    safe_name = _safe_server_name(name)
    if not safe_name:
        raise ValueError("MCP server name is required.")
    servers[safe_name] = dict(entry)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def remove_mcp_server(cwd: str | None, scope: str, name: str) -> bool:
    target = global_mcp_config_path() if scope == "global" else project_mcp_config_path(cwd)
    if target is None:
        raise ValueError("Project MCP config requires a workspace.")
    data = _read_json_object(target)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = data.get("servers")
    if not isinstance(servers, dict):
        return False
    safe_name = _safe_server_name(name)
    if safe_name not in servers:
        return False
    del servers[safe_name]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _remove_mcp_server_state(scope, safe_name)
    return True


def import_mcp_json(cwd: str | None, scope: str, data: dict) -> Path:
    raw = _server_entries_source(data)
    if not isinstance(raw, dict):
        raise ValueError("MCP JSON must contain an object named 'mcpServers' or 'servers'.")
    target = global_mcp_config_path() if scope == "global" else project_mcp_config_path(cwd)
    if target is None:
        raise ValueError("Project MCP config requires a workspace.")
    existing = _read_json_object(target)
    servers = existing.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        existing["mcpServers"] = servers
    for name, entry in raw.items():
        safe_name = _safe_server_name(name)
        if safe_name and isinstance(entry, dict):
            servers[safe_name] = dict(entry)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _config_paths(cwd: str | None) -> list[tuple[Literal["global", "project"], Path | None]]:
    return [
        ("global", global_mcp_config_path()),
        ("project", project_mcp_config_path(cwd)),
    ]


def _server_entries(data: dict) -> dict[str, dict]:
    raw = _server_entries_source(data)
    if not isinstance(raw, dict):
        return {}
    return {
        _safe_server_name(name): dict(value)
        for name, value in raw.items()
        if _safe_server_name(name) and isinstance(value, dict)
    }


def _server_entries_source(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("mcpServers")
    if raw is None:
        raw = data.get("servers")
    return raw if isinstance(raw, dict) else None


def _server_config(scope: str, name: str, entry: dict, state: dict) -> McpServerConfig:
    fingerprint = _fingerprint(entry)
    key = _state_key(scope, name)
    item_state = state.get("servers", {}).get(key, {})
    if not isinstance(item_state, dict):
        item_state = {}
    reviewed = state.get("reviewed", {}).get(key) == fingerprint if isinstance(state.get("reviewed"), dict) else False
    review_required = scope == "project" and not reviewed

    entry_enabled = bool(entry.get("enabled", True))
    state_enabled = item_state.get("enabled")
    enabled = bool(state_enabled) if isinstance(state_enabled, bool) else entry_enabled
    if review_required:
        enabled = False

    command = str(entry.get("command") or "").strip()
    url = str(entry.get("url") or "").strip()
    errors = []
    if not command and not url:
        errors.append("MCP server requires either 'command' or 'url'.")
    if command and url:
        errors.append("MCP server cannot define both 'command' and 'url'.")

    headers = {}
    for key_name in ("headers", "http_headers"):
        value = entry.get(key_name)
        if isinstance(value, dict):
            headers.update({str(k): str(v) for k, v in value.items()})
    auth = _auth_config(entry, headers)

    return McpServerConfig(
        name=name,
        scope=scope if scope in ("global", "project") else "global",
        raw=dict(entry),
        command=command,
        args=tuple(str(arg) for arg in entry.get("args", []) if isinstance(entry.get("args", []), list)),
        env={str(k): str(v) for k, v in entry.get("env", {}).items()} if isinstance(entry.get("env"), dict) else {},
        env_vars=tuple(str(item) for item in entry.get("env_vars", []) if isinstance(entry.get("env_vars"), list)),
        url=url,
        headers=headers,
        bearer_token_env_var=str(entry.get("bearer_token_env_var") or "").strip(),
        auth_type=auth["type"],
        oauth_scope=auth["scope"],
        oauth_redirect_uri=auth["redirect_uri"],
        oauth_server_url=auth["server_url"],
        oauth_client_name=auth["client_name"],
        oauth_client_id=auth["client_id"],
        oauth_client_secret=auth["client_secret"],
        startup_timeout_sec=_float_setting(entry.get("startup_timeout_sec"), 20.0),
        tool_timeout_sec=_float_setting(entry.get("tool_timeout_sec"), 60.0),
        enabled=enabled,
        review_required=review_required,
        reviewed=reviewed or scope == "global",
        fingerprint=fingerprint,
        errors=tuple(errors),
        disabled_components=_disabled_components(item_state),
    )


def _auth_config(entry: dict, headers: dict[str, str]) -> dict:
    raw = entry.get("auth")
    oauth = entry.get("oauth")
    auth_was_explicit = "auth" in entry or "oauth" in entry
    auth_type = "auto" if entry.get("url") else "none"
    config_data = {}
    if isinstance(raw, str):
        auth_type = raw.strip().lower()
    elif isinstance(raw, dict):
        auth_type = str(raw.get("type") or raw.get("auth_type") or "").strip().lower()
        config_data.update(raw)
    if isinstance(oauth, dict):
        auth_type = "oauth"
        config_data.update(oauth)
    elif oauth is True:
        auth_type = "oauth"
    if auth_type not in ("auto", "none", "oauth", "headers", "header", "static"):
        auth_type = "auto" if entry.get("url") and not auth_was_explicit else "none"
    if auth_type in ("header", "static"):
        auth_type = "headers"
    if auth_type in ("auto", "none") and (headers or entry.get("bearer_token_env_var")):
        auth_type = "headers"
    return {
        "type": auth_type,
        "scope": str(config_data.get("scope") or entry.get("oauth_scope") or "").strip(),
        "redirect_uri": str(config_data.get("redirect_uri") or entry.get("oauth_redirect_uri") or "").strip(),
        "server_url": str(config_data.get("server_url") or entry.get("oauth_server_url") or "").strip(),
        "client_name": str(config_data.get("client_name") or entry.get("oauth_client_name") or "aichs").strip()
        or "aichs",
        "client_id": str(config_data.get("client_id") or entry.get("oauth_client_id") or "").strip(),
        "client_secret": str(config_data.get("client_secret") or entry.get("oauth_client_secret") or "").strip(),
    }


def _state_key(scope: str, name: str) -> str:
    return f"{scope}:{_safe_server_name(name)}"


def _remove_mcp_server_state(scope: str, name: str) -> None:
    path = mcp_state_path()
    state = _read_json_object(path)
    key = _state_key(scope, name)
    changed = False
    servers = state.get("servers")
    if isinstance(servers, dict) and key in servers:
        del servers[key]
        changed = True
    reviewed = state.get("reviewed")
    if isinstance(reviewed, dict) and key in reviewed:
        del reviewed[key]
        changed = True
    if changed:
        _write_json_object(path, state)


def _safe_server_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(value or ""))
    return safe.strip("._-")


def _fingerprint(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _float_setting(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(1.0, min(600.0, number))


def _disabled_components(state: dict) -> dict[str, tuple[str, ...]]:
    raw = state.get("disabled_components")
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for kind in ("tools", "resources", "resource_templates", "prompts"):
        values = raw.get(kind)
        if not isinstance(values, list):
            continue
        items = tuple(sorted({str(value) for value in values if str(value or "").strip()}))
        if items:
            cleaned[kind] = items
    return cleaned


def _state_signature(state: dict) -> tuple:
    try:
        raw = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        raw = ""
    return (("state", hashlib.sha256(raw.encode("utf-8")).hexdigest()),)


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_object(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
