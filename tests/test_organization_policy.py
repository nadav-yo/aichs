
import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import config
from services.audit import audit_log_path
from services.mcp_config import load_mcp_config, write_mcp_json
from services.organization_policy import (
    POLICY_FORMAT,
    TRUST_FORMAT,
    CapabilityRequest,
    canonical_policy_payload,
    decide,
    file_sha256,
    governance_state,
    record_yuk_install,
)
from services.skills import load_all
from services.tool_policy import ConversationToolPolicy, ToolApprovalBus
from services.tool_registry import ToolRegistry, extension_content_hash, extension_overview, load_extensions
from tests.conftest import write_extension


def _write_signed_policy(cwd=None, *, rules=None, preset="team_safe", required_yuks=None):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    org_dir = config.AICHS_HOME / "organization"
    org_dir.mkdir(parents=True, exist_ok=True)
    (org_dir / "trust.json").write_text(json.dumps({
        "format": TRUST_FORMAT,
        "keys": [{
            "id": "org-main-2026",
            "algorithm": "ed25519",
            "public_key": base64.b64encode(public).decode("ascii"),
        }],
    }), encoding="utf-8")
    policy = {
        "format": POLICY_FORMAT,
        "organization": {"id": "org", "name": "Example Org"},
        "policy_id": "dev-policy",
        "version": 1,
        "preset": preset,
        "rules": rules or {},
        "required_yuks": list(required_yuks or []),
    }
    payload = canonical_policy_payload(policy)
    policy["signature"] = {
        "key_id": "org-main-2026",
        "algorithm": "ed25519",
        "value": base64.b64encode(key.sign(payload)).decode("ascii"),
    }
    path = org_dir / "policy.json"
    path.write_text(json.dumps(policy, indent=2, sort_keys=True), encoding="utf-8")
    return path, policy


def test_governance_personal_without_policy(workspace):
    assert governance_state(str(workspace)).mode == "personal"
    decision = decide(CapabilityRequest(kind="shell", name="execute", cwd=str(workspace)))
    assert decision.result == "allow"


def test_signed_policy_loads_and_invalid_signature_locks(workspace):
    path, _policy = _write_signed_policy(rules={"tools": {"shell": "deny"}})
    state = governance_state(str(workspace))
    assert state.mode == "governed"
    assert state.organization_name == "Example Org"

    data = json.loads(path.read_text(encoding="utf-8"))
    data["rules"]["tools"]["shell"] = "allow"
    path.write_text(json.dumps(data), encoding="utf-8")

    state = governance_state(str(workspace))
    assert state.mode == "locked"
    decision = decide(CapabilityRequest(kind="shell", name="execute", cwd=str(workspace)))
    assert decision.result == "deny"


def test_missing_trust_key_locks_when_policy_exists(workspace):
    path, policy = _write_signed_policy()
    trust = config.AICHS_HOME / "organization" / "trust.json"
    trust.write_text(json.dumps({"format": TRUST_FORMAT, "keys": []}), encoding="utf-8")
    assert governance_state(str(workspace)).mode == "locked"
    assert decide(CapabilityRequest(kind="model", name="x", cwd=str(workspace))).result == "deny"
    assert path.exists() and policy["signature"]["key_id"] == "org-main-2026"


def test_tool_policy_denies_before_approval(qapp, workspace):
    _write_signed_policy(rules={"tools": {"shell": "deny"}})
    bus = ToolApprovalBus()
    approvals = []
    bus.approval_needed.connect(lambda pending: approvals.append(pending))
    out = bus.check("execute", {"command": "echo hi"}, str(workspace), ConversationToolPolicy(), lambda: False)
    assert out == "[tool error] Your organization policy does not allow this action."
    assert approvals == []


def test_extension_hash_allowlist_and_tamper(workspace):
    ext = write_extension(workspace, "governed.py", """
        def register(registry):
            registry.tool(name="hello", description="hello", input_schema={"type":"object"}, execute=lambda ctx, inputs: "ok")
    """)
    digest = extension_content_hash(ext)
    _write_signed_policy(preset="strict", rules={"extensions": {"allow_hashes": [digest]}})
    registry = ToolRegistry()
    load_extensions(registry, str(workspace))
    assert "hello" in registry.names()

    ext.write_text("def register(registry):\n    pass\n", encoding="utf-8")
    registry = ToolRegistry()
    load_extensions(registry, str(workspace))
    assert "hello" not in registry.names()
    overview = extension_overview(str(workspace))
    assert overview.files[0].status == "Blocked"


def test_mcp_denied_server_is_not_available(workspace):
    _write_signed_policy(preset="strict", rules={"mcps": {"allow_servers": ["approved"]}})
    path = workspace / ".agents" / "mcp.json"
    write_mcp_json(path, {"blocked": {"command": "python", "args": ["server.py"]}})
    snapshot = load_mcp_config(str(workspace))
    assert snapshot.servers == ()
    blocked = load_mcp_config(str(workspace), include_disabled=True).servers[0]
    assert blocked.enabled is False
    assert "organization policy" in blocked.errors[-1]


def test_skills_filter_by_hash_and_required_yuk_tamper(workspace, tmp_path):
    skills = workspace / ".agents" / "skills"
    skills.mkdir(parents=True)
    skill = skills / "review.md"
    skill.write_text("---\nname: review\n---\nReview carefully.\n", encoding="utf-8")
    package = tmp_path / "org-kit.yuk"
    package.write_text("package", encoding="utf-8")
    record_yuk_install(
        package_path=package,
        manifest={"package_id": "org-kit", "name": "Org Kit"},
        skills=[str(skill)],
        extensions=[],
    )
    _write_signed_policy(
        rules={"skills": {"allow_hashes": [file_sha256(skill)]}},
        required_yuks=[{"package_id": "org-kit", "sha256": file_sha256(package)}],
    )
    assert [item.name for item in load_all(str(workspace))] == ["review"]

    skill.write_text("---\nname: review\n---\nChanged.\n", encoding="utf-8")
    assert load_all(str(workspace)) == []


def test_audit_redacts_sensitive_details(workspace):
    _write_signed_policy(rules={"tools": {"shell": "deny"}})
    decide(CapabilityRequest(
        kind="shell",
        name="execute",
        cwd=str(workspace),
        metadata={"Authorization": "Bearer abc123", "safe": "ok"},
    ))
    text = audit_log_path().read_text(encoding="utf-8")
    assert "abc123" not in text
    assert "policy.decision" in text


def test_settings_shows_managed_status(qapp, workspace):
    from PyQt6.QtWidgets import QLabel
    from storage.settings import SettingsStore
    from ui.widgets.settings_dialog import SettingsDialog

    _write_signed_policy()
    dialog = SettingsDialog(SettingsStore(), cwd=str(workspace))
    labels = [w for w in dialog.findChildren(QLabel) if w.objectName() == "organizationManagedStatus"]
    assert labels
    assert "Managed by Example Org" in labels[0].text()
