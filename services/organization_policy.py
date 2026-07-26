"""Organization governance policy authority.

This module is intentionally the only place that knows how to load, verify, and
evaluate signed organization policy files. Callers should not parse policy JSON
directly. Instead, every model/tool/extension/MCP/skill enforcement point builds
a :class:`CapabilityRequest` and calls :func:`decide`.

The authority is designed to be invisible in personal mode. If there is no
organization policy file, every decision is allowed and no audit record is
written. Once a valid signed policy has been seen, the module remembers that the
process was governed; if the policy later disappears or fails verification,
decisions move to locked mode and fail closed.

Runtime tamper detection is stat/hash based. Each call to :func:`decide` reaches
this authority, and the authority revalidates trust/policy files whenever their
filesystem signature changes. That keeps enforcement centralized without
forcing every call site to understand signatures or cache invalidation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import config
from services.audit import append_audit_event


TRUST_FORMAT = "aichs-organization-trust/v1"
POLICY_FORMAT = "aichs-organization-policy/v1"
DENIED_MESSAGE = "Your organization policy does not allow this action."
LOCKED_MESSAGE = "Organization policy could not be verified. Governed mode is locked."

CapabilityKind = Literal[
    "model",
    "builtin_tool",
    "shell",
    "file_read",
    "file_write",
    "extension",
    "extension_tool",
    "mcp_server",
    "mcp_tool",
    "skill",
    "required_yuk",
]
DecisionResult = Literal["allow", "deny", "approval_required"]
GovernanceMode = Literal["personal", "governed", "locked"]


@dataclass(frozen=True)
class CapabilityRequest:
    """A normalized policy question from an enforcement point.

    Keep the request metadata intentionally small and non-sensitive. Audit logs
    record these fields in governed/locked mode, so callers should pass labels,
    names, content hashes, and path summaries rather than prompt text, file
    contents, command environment values, headers, or API keys.

    Field conventions:
    - ``kind`` selects the policy section and default preset behavior.
    - ``name`` is the model id, tool name, extension id, MCP tool name, or skill
      name.
    - ``owner``/``source`` identify a parent, for example the MCP server that
      owns an MCP tool.
    - ``hash`` is a SHA-256 content hash when policy can allowlist by content.
    - ``cwd`` enables workspace policy lookup and workspace-relative checks.
    """
    kind: CapabilityKind
    name: str = ""
    cwd: str = ""
    source: str = ""
    owner: str = ""
    path: str = ""
    hash: str = ""
    provider: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    """The result returned to enforcement points.

    ``approval_required`` is still an allowed governance result. It means the
    organization permits the action only if the normal user approval flow also
    approves it. ``deny`` must stop before normal approval prompts or execution.
    """
    result: DecisionResult
    message: str = ""
    mode: GovernanceMode = "personal"
    policy_id: str = ""
    policy_hash: str = ""
    key_id: str = ""

    @property
    def allowed(self) -> bool:
        return self.result in {"allow", "approval_required"}


@dataclass(frozen=True)
class GovernanceState:
    """Current organization policy status for UI and cache signatures."""
    mode: GovernanceMode
    organization_id: str = ""
    organization_name: str = ""
    policy_id: str = ""
    version: int = 0
    preset: str = "team_safe"
    policy_hash: str = ""
    key_id: str = ""
    error: str = ""


@dataclass(frozen=True)
class _VerifiedPolicy:
    path: Path
    data: dict[str, Any]
    payload_hash: str
    key_id: str
    signature: tuple


_LOCK = threading.RLock()
_CACHE_SIGNATURE: tuple = ()
_CACHE_POLICIES: tuple[_VerifiedPolicy, ...] = ()
_CACHE_STATE = GovernanceState(mode="personal")
_HAD_GOVERNED = False


def organization_dir() -> Path:
    return config.AICHS_HOME / "organization"


def trust_path() -> Path:
    return organization_dir() / "trust.json"


def policy_path() -> Path:
    return organization_dir() / "policy.json"


def workspace_policy_path(cwd: str | None) -> Path | None:
    if not cwd:
        return None
    return Path(cwd) / config.PROJECT_AICHS_DIR / "organization-policy.json"


def governance_state(cwd: str | None = None) -> GovernanceState:
    return _authority(cwd)[1]


def governance_signature(cwd: str | None = None) -> tuple:
    """Return a compact cache key for policy-sensitive registries.

    Extension and MCP discovery caches include this value so a policy edit,
    trust edit, or lock transition invalidates advertised capability lists.
    """
    policies, state = _authority(cwd)
    return (
        state.mode,
        state.policy_hash,
        state.policy_id,
        state.key_id,
        tuple(policy.signature for policy in policies),
    )


def decide(request: CapabilityRequest) -> PolicyDecision:
    """Evaluate one capability request against the current organization policy.

    This is the central enforcement API. It handles all three modes:
    ``personal`` allows without audit, ``governed`` evaluates every verified
    policy, and ``locked`` denies with a stable user-facing message. Call sites
    should generally call this before doing expensive discovery, showing normal
    approval UI, or invoking provider/tool code.
    """
    policies, state = _authority(request.cwd)
    if state.mode == "personal":
        return PolicyDecision(result="allow", mode="personal")
    if state.mode == "locked":
        decision = PolicyDecision(
            result="deny",
            message=LOCKED_MESSAGE,
            mode="locked",
            policy_id=state.policy_id,
            policy_hash=state.policy_hash,
            key_id=state.key_id,
        )
        _audit_decision(request, decision)
        return decision

    result: DecisionResult = "allow"
    message = ""
    for policy in policies:
        item = _decide_policy(policy.data, request)
        if item.result == "deny":
            result = "deny"
            message = item.message or DENIED_MESSAGE
            break
        if item.result == "approval_required":
            result = "approval_required"
            message = item.message

    decision = PolicyDecision(
        result=result,
        message=message,
        mode=state.mode,
        policy_id=state.policy_id,
        policy_hash=state.policy_hash,
        key_id=state.key_id,
    )
    _audit_decision(request, decision)
    return decision


def is_governed(cwd: str | None = None) -> bool:
    return governance_state(cwd).mode in {"governed", "locked"}


def canonical_policy_payload(policy: dict[str, Any]) -> bytes:
    """Build the exact byte payload covered by an organization signature.

    The top-level ``signature`` field is excluded, matching the public policy
    contract documented in ``docs/organization-governance.md``.
    """
    payload = dict(policy)
    payload.pop("signature", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def file_sha256(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def record_yuk_install(
    *,
    package_path: str | Path,
    manifest: dict[str, Any],
    skills: list[str],
    extensions: list[str],
) -> None:
    """Record imported YUK package metadata for later governed runtime checks.

    YUK files remain asset bundles, not policy authorities. The signed policy
    may require a package id plus package hash; this record lets runtime loading
    confirm that the package was imported and that the installed skill/extension
    assets still match what was imported.
    """
    package = Path(package_path)
    package_id = str(manifest.get("package_id") or manifest.get("name") or package.stem).strip()
    if not package_id:
        return
    record = {
        "package_id": package_id,
        "sha256": file_sha256(package),
        "name": str(manifest.get("name") or ""),
        "skills": _asset_hashes(skills, kind="skill"),
        "extensions": _asset_hashes(extensions, kind="extension"),
    }
    path = installed_yuks_path()
    data = _read_json_object(path)
    packages = data.setdefault("packages", {})
    if not isinstance(packages, dict):
        packages = {}
        data["packages"] = packages
    packages[package_id] = record
    _write_json_object(path, data)


def installed_yuks_path() -> Path:
    return organization_dir() / "yuk.installed.json"


def required_yuks_ok(cwd: str | None = None) -> tuple[bool, str]:
    """Verify required YUK package records and installed asset hashes."""
    policies, state = _authority(cwd)
    if state.mode != "governed":
        return state.mode != "locked", state.error
    required = []
    for policy in policies:
        for item in policy.data.get("required_yuks", []) or []:
            if isinstance(item, dict):
                required.append(item)
    if not required:
        return True, ""
    data = _read_json_object(installed_yuks_path())
    packages = data.get("packages", {})
    if not isinstance(packages, dict):
        packages = {}
    for item in required:
        package_id = str(item.get("package_id") or "").strip()
        sha = str(item.get("sha256") or "").strip().lower()
        record = packages.get(package_id)
        if not isinstance(record, dict):
            return False, f"Required YUK is not installed: {package_id}"
        if sha and str(record.get("sha256") or "").lower() != sha:
            return False, f"Required YUK hash mismatch: {package_id}"
        ok, message = _recorded_assets_ok(record)
        if not ok:
            return False, message
    return True, ""


def clear_governance_cache() -> None:
    global _CACHE_SIGNATURE, _CACHE_POLICIES, _CACHE_STATE, _HAD_GOVERNED
    with _LOCK:
        _CACHE_SIGNATURE = ()
        _CACHE_POLICIES = ()
        _CACHE_STATE = GovernanceState(mode="personal")
        _HAD_GOVERNED = False


def _authority(cwd: str | None) -> tuple[tuple[_VerifiedPolicy, ...], GovernanceState]:
    """Load or reuse verified policies for this workspace.

    The cache key is deliberately based on filesystem signatures for trust,
    global policy, and workspace policy. Any change to mtime or file size forces
    a re-read and signature verification on the next decision.
    """
    global _CACHE_SIGNATURE, _CACHE_POLICIES, _CACHE_STATE, _HAD_GOVERNED
    paths = [policy_path()]
    workspace = workspace_policy_path(cwd)
    if workspace is not None:
        paths.append(workspace)
    signature = (_path_signature(trust_path()), tuple(_path_signature(path) for path in paths))
    with _LOCK:
        if signature == _CACHE_SIGNATURE:
            return _CACHE_POLICIES, _CACHE_STATE

    existing = [path for path in paths if path.exists()]
    if not existing:
        with _LOCK:
            if _HAD_GOVERNED:
                _CACHE_SIGNATURE = signature
                _CACHE_POLICIES = ()
                _CACHE_STATE = GovernanceState(mode="locked", error="Organization policy file is missing.")
            else:
                _CACHE_SIGNATURE = signature
                _CACHE_POLICIES = ()
                _CACHE_STATE = GovernanceState(mode="personal")
            return _CACHE_POLICIES, _CACHE_STATE

    try:
        trust = _load_trust()
        policies = tuple(_load_verified_policy(path, trust) for path in existing)
        state = _state_for_policies(policies)
    except Exception as exc:
        state = GovernanceState(mode="locked", error=str(exc))
        policies = ()
        append_audit_event("governance.locked", error=str(exc), cwd=str(cwd or ""))

    with _LOCK:
        _CACHE_SIGNATURE = signature
        _CACHE_POLICIES = policies
        _CACHE_STATE = state
        if state.mode == "governed":
            _HAD_GOVERNED = True
        return _CACHE_POLICIES, _CACHE_STATE


def _load_trust() -> dict[str, bytes]:
    raw = _read_json_object(trust_path())
    if raw.get("format") != TRUST_FORMAT:
        raise ValueError("Organization trust file is missing or unsupported.")
    keys = raw.get("keys")
    if not isinstance(keys, list):
        raise ValueError("Organization trust file has no keys.")
    out = {}
    for item in keys:
        if not isinstance(item, dict):
            continue
        key_id = str(item.get("id") or "").strip()
        algorithm = str(item.get("algorithm") or "").strip().lower()
        public_key = str(item.get("public_key") or "").strip()
        if key_id and algorithm == "ed25519" and public_key:
            out[key_id] = base64.b64decode(public_key)
    if not out:
        raise ValueError("Organization trust file has no supported keys.")
    return out


def _load_verified_policy(path: Path, trust: dict[str, bytes]) -> _VerifiedPolicy:
    raw = _read_json_object(path)
    if raw.get("format") != POLICY_FORMAT:
        raise ValueError(f"Unsupported organization policy format: {path}")
    signature = raw.get("signature")
    if not isinstance(signature, dict):
        raise ValueError(f"Organization policy is unsigned: {path}")
    key_id = str(signature.get("key_id") or "").strip()
    algorithm = str(signature.get("algorithm") or "").strip().lower()
    value = str(signature.get("value") or "").strip()
    if algorithm != "ed25519" or not key_id or not value:
        raise ValueError(f"Organization policy signature is unsupported: {path}")
    public_key = trust.get(key_id)
    if public_key is None:
        raise ValueError(f"Organization policy key is not trusted: {key_id}")
    payload = canonical_policy_payload(raw)
    _verify_ed25519(public_key, base64.b64decode(value), payload)
    return _VerifiedPolicy(
        path=path,
        data=raw,
        payload_hash=hashlib.sha256(payload).hexdigest(),
        key_id=key_id,
        signature=_path_signature(path),
    )


def _verify_ed25519(public_key: bytes, signature: bytes, payload: bytes) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)


def _state_for_policies(policies: tuple[_VerifiedPolicy, ...]) -> GovernanceState:
    primary = policies[0]
    org = primary.data.get("organization")
    org = org if isinstance(org, dict) else {}
    try:
        version = int(primary.data.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    return GovernanceState(
        mode="governed",
        organization_id=str(org.get("id") or ""),
        organization_name=str(org.get("name") or ""),
        policy_id=str(primary.data.get("policy_id") or ""),
        version=version,
        preset=_preset(primary.data),
        policy_hash=primary.payload_hash,
        key_id=primary.key_id,
    )


def _decide_policy(policy: dict[str, Any], request: CapabilityRequest) -> PolicyDecision:
    preset = _preset(policy)
    rules = policy.get("rules")
    rules = rules if isinstance(rules, dict) else {}
    if request.kind in {"extension", "skill"}:
        ok, message = required_yuks_ok(request.cwd)
        if not ok:
            return PolicyDecision(result="deny", message=message or DENIED_MESSAGE)
    if request.kind == "model":
        return _decide_model(rules.get("models"), request, preset)
    if request.kind in {"shell", "builtin_tool", "file_read", "file_write"}:
        return _decide_tool(rules.get("tools"), request, preset)
    if request.kind in {"extension", "extension_tool"}:
        return _decide_hash_or_name(rules.get("extensions"), request, preset)
    if request.kind in {"mcp_server", "mcp_tool"}:
        return _decide_mcp(rules.get("mcps"), request, preset)
    if request.kind == "skill":
        return _decide_hash_or_name(rules.get("skills"), request, preset)
    if request.kind == "required_yuk":
        ok, message = required_yuks_ok(request.cwd)
        return PolicyDecision(result="allow" if ok else "deny", message=message)
    return PolicyDecision(result="allow")


def _decide_model(section: Any, request: CapabilityRequest, preset: str) -> PolicyDecision:
    data = section if isinstance(section, dict) else {}
    deny = _string_list(data.get("deny"))
    label = f"{request.provider}:{request.name}" if request.provider else request.name
    if _matches_any(label, deny) or _matches_any(request.name, deny):
        return PolicyDecision(result="deny", message=DENIED_MESSAGE)
    allow = _string_list(data.get("allow"))
    if allow:
        return PolicyDecision(
            result="allow" if _matches_any(label, allow) or _matches_any(request.name, allow) else "deny",
            message=DENIED_MESSAGE,
        )
    return PolicyDecision(result="deny" if preset == "strict" else "allow", message=DENIED_MESSAGE)


def _decide_tool(section: Any, request: CapabilityRequest, preset: str) -> PolicyDecision:
    data = section if isinstance(section, dict) else {}
    if request.kind == "shell":
        mode = str(data.get("shell") or "").strip().lower()
        if mode in {"deny", "denied", "block", "blocked", "false", "off", "none"}:
            return PolicyDecision(result="deny", message=DENIED_MESSAGE)
        if mode == "approval_required":
            return PolicyDecision(result="approval_required")
        if preset == "strict" and mode not in {"allow", "allowed", "true", "on", "approval_required"}:
            return PolicyDecision(result="deny", message=DENIED_MESSAGE)
    if request.kind in {"file_read", "file_write"}:
        filesystem = str(data.get("filesystem") or "workspace_only").strip().lower()
        if filesystem in {"deny", "denied", "block", "blocked"}:
            return PolicyDecision(result="deny", message=DENIED_MESSAGE)
    if _matches_any(request.name, _string_list(data.get("deny"))):
        return PolicyDecision(result="deny", message=DENIED_MESSAGE)
    allow = _string_list(data.get("allow"))
    if allow and not _matches_any(request.name, allow):
        return PolicyDecision(result="deny", message=DENIED_MESSAGE)
    return PolicyDecision(result="allow")


def _decide_hash_or_name(section: Any, request: CapabilityRequest, preset: str) -> PolicyDecision:
    data = section if isinstance(section, dict) else {}
    if request.hash and request.hash in _string_list(data.get("deny_hashes")):
        return PolicyDecision(result="deny", message=DENIED_MESSAGE)
    if request.name and _matches_any(request.name, _string_list(data.get("deny"))):
        return PolicyDecision(result="deny", message=DENIED_MESSAGE)
    allow_hashes = _string_list(data.get("allow_hashes"))
    if allow_hashes:
        return PolicyDecision(
            result="allow" if request.hash in allow_hashes else "deny",
            message=DENIED_MESSAGE,
        )
    allow_names = _string_list(data.get("allow"))
    if allow_names:
        return PolicyDecision(
            result="allow" if _matches_any(request.name, allow_names) else "deny",
            message=DENIED_MESSAGE,
        )
    return PolicyDecision(result="deny" if preset == "strict" else "allow", message=DENIED_MESSAGE)


def _decide_mcp(section: Any, request: CapabilityRequest, preset: str) -> PolicyDecision:
    data = section if isinstance(section, dict) else {}
    if request.kind == "mcp_server":
        allowed = _string_list(data.get("allow_servers"))
        denied = _string_list(data.get("deny_servers"))
        if _matches_any(request.name, denied):
            return PolicyDecision(result="deny", message=DENIED_MESSAGE)
        if allowed:
            return PolicyDecision(
                result="allow" if _matches_any(request.name, allowed) else "deny",
                message=DENIED_MESSAGE,
            )
        return PolicyDecision(result="deny" if preset == "strict" else "allow", message=DENIED_MESSAGE)
    server = request.owner or request.source
    tool = request.name
    allow_tools = data.get("allow_tools")
    deny_tools = data.get("deny_tools")
    if isinstance(deny_tools, dict) and _matches_any(tool, _string_list(deny_tools.get(server))):
        return PolicyDecision(result="deny", message=DENIED_MESSAGE)
    if isinstance(allow_tools, dict):
        allowed = _string_list(allow_tools.get(server))
        if allowed:
            return PolicyDecision(
                result="allow" if _matches_any(tool, allowed) else "deny",
                message=DENIED_MESSAGE,
            )
    return _decide_mcp(section, CapabilityRequest(kind="mcp_server", name=server, cwd=request.cwd), preset)


def _preset(policy: dict[str, Any]) -> str:
    value = str(policy.get("preset") or "team_safe").strip().lower()
    return value if value in {"team_safe", "strict"} else "team_safe"


def _matches_any(value: str, patterns: list[str]) -> bool:
    text = str(value or "")
    for pattern in patterns:
        if pattern == "*":
            return True
        if pattern.endswith("*") and text.startswith(pattern[:-1]):
            return True
        if pattern == text:
            return True
    return False


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _asset_hashes(paths: list[str], *, kind: str) -> list[dict[str, str]]:
    out = []
    for item in paths:
        path = Path(item)
        try:
            if kind == "extension":
                from services.tool_registry import extension_content_hash

                digest = extension_content_hash(path)
            else:
                digest = file_sha256(path)
        except OSError:
            continue
        out.append({"path": str(path), "sha256": digest})
    return out


def _audit_decision(request: CapabilityRequest, decision: PolicyDecision) -> None:
    if decision.mode == "personal":
        return
    append_audit_event(
        "policy.decision",
        cwd=request.cwd,
        capability=request.kind,
        name=request.name,
        source=request.source,
        owner=request.owner,
        path=_path_summary(request.path),
        hash=request.hash,
        provider=request.provider,
        decision=decision.result,
        policy_id=decision.policy_id,
        policy_hash=decision.policy_hash,
        key_id=decision.key_id,
        message=decision.message,
    )


def _path_summary(path: str) -> str:
    if not path:
        return ""
    value = str(path)
    try:
        return Path(value).name or value
    except OSError:
        return value


def _path_signature(path: Path) -> tuple:
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(path), "missing")


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")



def _recorded_assets_ok(record: dict[str, Any]) -> tuple[bool, str]:
    for kind in ("skills", "extensions"):
        values = record.get(kind)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            path = Path(str(item.get("path") or ""))
            expected = str(item.get("sha256") or "").lower()
            if not path.exists() or not expected:
                return False, f"Required YUK asset is missing: {path}"
            try:
                if kind == "extensions":
                    from services.tool_registry import extension_content_hash

                    actual = extension_content_hash(path).lower()
                else:
                    actual = file_sha256(path).lower()
            except OSError:
                return False, f"Required YUK asset is unreadable: {path}"
            if actual != expected:
                return False, f"Required YUK asset hash mismatch: {path}"
    return True, ""
