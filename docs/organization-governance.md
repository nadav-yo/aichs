# Organization Governance

Organization Governance is an opt-in trust layer for teams that need policy
enforcement and auditability. It is invisible for personal users: if no valid
organization policy exists, `aichs` behaves as it does without governance.

Governance is activated by signed policy files. Once governed mode is active,
users cannot loosen organization policy from the UI. If a previously valid
policy is changed, removed, or can no longer be verified, the app enters locked
mode and fails closed for governed capabilities.

## Goals

Organization Governance is designed around four product rules:

| Rule | Meaning |
|---|---|
| Simple by default | Personal users do not see governance concepts or extra prompts |
| Opt-in by organization | Governance activates only when signed organization policy is present |
| Enforceable when enabled | Policy decisions happen before execution or user approval |
| Auditable by default | Governed policy decisions are written as local metadata-only JSONL |

The local app can provide policy enforcement, runtime tamper detection, and
local audit evidence. It is not a complete anti-tamper boundary against a user
who can patch the running Python process or replace application code. Stronger
deployments should combine this with signed builds, managed installation,
locked policy locations, remote audit export, or a server-side gateway.

## Files

All global governance files live under `AICHS_HOME/organization/`.

| Path | Purpose |
|---|---|
| `AICHS_HOME/organization/trust.json` | Organization trust anchors |
| `AICHS_HOME/organization/policy.json` | Primary signed organization policy |
| `.aichs/organization-policy.json` | Optional signed workspace policy that can further restrict |
| `AICHS_HOME/organization/audit.jsonl` | Local metadata-only governance audit log |
| `AICHS_HOME/organization/yuk.installed.json` | Records imported YUK packages and installed asset hashes |

The workspace policy is optional. When present and valid, it is evaluated in
addition to the primary organization policy. It can further restrict behavior.
It should not be used to loosen organization policy.

## Modes

| Mode | When it applies | Behavior |
|---|---|---|
| `personal` | No organization policy file exists | Current app behavior |
| `governed` | Signed policy verifies against a trusted key | Policy enforced, audit enabled |
| `locked` | Policy exists but verification fails, or a governed policy disappears | Governed capabilities fail closed |

Examples:

- No `policy.json`: personal mode.
- `policy.json` exists but `trust.json` is missing: locked mode.
- `policy.json` has an invalid signature: locked mode.
- App starts governed, then policy is edited without resigning: locked mode.
- App starts governed, then policy file is removed: locked mode.

## Trust File

`trust.json` contains public keys that may sign organization policies.

```json
{
  "format": "aichs-organization-trust/v1",
  "keys": [
    {
      "id": "org-main-2026",
      "algorithm": "ed25519",
      "public_key": "base64-raw-ed25519-public-key"
    }
  ]
}
```

Fields:

| Field | Required | Description |
|---|---:|---|
| `format` | yes | Must be `aichs-organization-trust/v1` |
| `keys[].id` | yes | Stable key id referenced by policies |
| `keys[].algorithm` | yes | Currently only `ed25519` |
| `keys[].public_key` | yes | Base64 raw Ed25519 public key bytes |

Key ids should be stable and rotation-friendly, for example
`org-main-2026` or `security-prod-1`.

## Policy File

`policy.json` contains the signed organization policy.

```json
{
  "format": "aichs-organization-policy/v1",
  "organization": {
    "id": "org-id",
    "name": "Organization Name"
  },
  "policy_id": "default-dev-policy",
  "version": 1,
  "preset": "team_safe",
  "rules": {
    "models": {
      "allow": ["openai:*", "anthropic:*"]
    },
    "tools": {
      "shell": "approval_required",
      "filesystem": "workspace_only"
    },
    "extensions": {
      "allow_hashes": []
    },
    "mcps": {
      "allow_servers": [],
      "allow_tools": {}
    },
    "skills": {
      "allow_hashes": []
    }
  },
  "required_yuks": [
    {
      "package_id": "org-dev-kit",
      "sha256": "hex-sha256-of-yuk-file"
    }
  ],
  "signature": {
    "key_id": "org-main-2026",
    "algorithm": "ed25519",
    "value": "base64-signature"
  }
}
```

Fields:

| Field | Required | Description |
|---|---:|---|
| `format` | yes | Must be `aichs-organization-policy/v1` |
| `organization.id` | recommended | Stable organization id for audit records |
| `organization.name` | recommended | Human-readable name shown in Settings |
| `policy_id` | recommended | Stable policy name shown in audit/UI |
| `version` | recommended | Integer policy version |
| `preset` | optional | `team_safe` or `strict`; default is `team_safe` |
| `rules` | optional | Capability rules |
| `required_yuks` | optional | Required imported YUK packages |
| `signature` | yes | Ed25519 signature block |

## Signature Verification

Policies are signed with Ed25519.

The signature payload is canonical JSON:

- remove the top-level `signature` field
- serialize with sorted keys
- use compact JSON separators
- UTF-8 encode the result

In Python terms, the signed payload is equivalent to:

```python
payload = dict(policy)
payload.pop("signature", None)
canonical = json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode("utf-8")
```

The app verifies this payload against `signature.value` using the public key
whose id matches `signature.key_id`.

Verification is checked at runtime, not only startup. Every policy decision
goes through the policy authority. If the trust or policy file changes, the
authority reloads and verifies before deciding.

## Presets

Presets define the default stance when a rule section does not say otherwise.

| Preset | Default behavior |
|---|---|
| `team_safe` | Mostly current behavior, with audit enabled and explicit configured restrictions enforced |
| `strict` | Deny models, shell, extensions, MCP servers/tools, and skills unless allowed |

Use `team_safe` for incremental adoption. Use `strict` for managed workspaces
where only approved capabilities should run.

## Capability Rules

Policy decisions are made against capability requests. A request has a kind
such as `model`, `shell`, `extension`, or `mcp_tool`, plus metadata such as name,
owner, path, provider, and content hash.

| Capability | Enforced at |
|---|---|
| `model` | Before a chat thread makes a provider request |
| `builtin_tool` | Tool advertisement and direct execution |
| `shell` | Shell tool advertisement, approval gate, and execution |
| `file_read` | Read/list/search tool advertisement and execution |
| `file_write` | Edit tool approval gate and execution |
| `extension` | Before extension Python is loaded |
| `extension_tool` | Tool advertisement, approval gate, and execution |
| `mcp_server` | MCP config loading |
| `mcp_tool` | MCP tool discovery, advertisement, and call execution |
| `skill` | Skill loading for slash-command picker |
| `required_yuk` | Required YUK checks used by skill/extension governance |

Policy checks happen before normal user approval prompts. A denied action does
not ask the user to approve it.

### Matching

String allow/deny lists support exact matches and suffix wildcard matches.

Examples:

| Pattern | Matches |
|---|---|
| `openai:gpt-5` | exactly `openai:gpt-5` |
| `openai:*` | any value starting with `openai:` |
| `*` | any value |

## Model Rules

Model policy uses `provider:model` when a provider id is available, and also
checks the raw model id.

```json
{
  "rules": {
    "models": {
      "allow": ["openai:*", "anthropic:claude-*"],
      "deny": ["openai:experimental-*"]
    }
  }
}
```

In `strict`, if `models.allow` is omitted, models are denied.

In `team_safe`, if `models.allow` is omitted, models are allowed unless denied.

## Tool Rules

Tool rules cover built-in tools, shell access, and filesystem behavior.

```json
{
  "rules": {
    "tools": {
      "shell": "approval_required",
      "filesystem": "workspace_only",
      "deny": ["save_project_memory"],
      "allow": ["read_file", "list_files", "search_files", "edit_file", "execute"]
    }
  }
}
```

`shell` values:

| Value | Behavior |
|---|---|
| `allow` | Shell follows normal app approval behavior |
| `approval_required` | Shell is allowed but still approval-gated |
| `deny` | Shell is blocked by organization policy |

`filesystem` values:

| Value | Behavior |
|---|---|
| `workspace_only` | Existing workspace path checks remain enforced |
| `deny` | File read/write capabilities are blocked |

The app already enforces workspace-scoped paths for built-in file tools.
Governance does not make path handling less strict.

## Extension Rules

Extensions execute local Python code in the app process. Governed mode can block
extension loading before extension code runs.

```json
{
  "rules": {
    "extensions": {
      "allow_hashes": [
        "sha256-extension-content-hash"
      ],
      "deny": ["unsafe_extension_id"]
    }
  }
}
```

Supported fields:

| Field | Description |
|---|---|
| `allow_hashes` | Exact extension content hashes allowed |
| `deny_hashes` | Exact extension content hashes denied |
| `allow` | Extension ids allowed |
| `deny` | Extension ids denied |

For single-file extensions, the content hash covers the file name and file
contents. For folder extensions, the hash covers the folder extension files and
manifest metadata discovered by the extension registry.

Blocked extensions appear as `Blocked` in extension overview data and do not
register tools, commands, hooks, context providers, docs, UI, or language
features.

In `strict`, extensions are denied unless allowlisted by hash or name.

## MCP Rules

MCP servers can expose tools that bridge into external systems. Governed mode
can block both whole servers and individual remote tools.

```json
{
  "rules": {
    "mcps": {
      "allow_servers": ["github", "linear"],
      "deny_servers": ["personal-slack"],
      "allow_tools": {
        "github": ["list_issues", "get_issue"],
        "linear": ["list_issues", "get_issue"]
      },
      "deny_tools": {
        "github": ["merge_pull_request"]
      }
    }
  }
}
```

Supported fields:

| Field | Description |
|---|---|
| `allow_servers` | MCP server names allowed |
| `deny_servers` | MCP server names denied |
| `allow_tools` | Map of server name to allowed remote tool names |
| `deny_tools` | Map of server name to denied remote tool names |

Denied servers are not available in normal MCP loading. They can still be shown
in include-disabled views with an organization-policy error so users understand
why the server is unavailable.

MCP tool checks happen during discovery and again before tool calls.

In `strict`, MCP servers are denied unless allowlisted.

## Skill Rules

Skills are Markdown prompt files loaded from `AICHS_HOME/skills/` and
`.agents/skills/`.

```json
{
  "rules": {
    "skills": {
      "allow_hashes": ["sha256-of-skill-file"],
      "deny": ["legacy_review"]
    }
  }
}
```

Supported fields:

| Field | Description |
|---|---|
| `allow_hashes` | Exact skill file SHA-256 hashes allowed |
| `deny_hashes` | Exact skill file SHA-256 hashes denied |
| `allow` | Skill names allowed |
| `deny` | Skill names denied |

Blocked skills are filtered before the slash-command picker sees them.

In `strict`, skills are denied unless allowlisted by hash or name.

## Required YUK Assets

YUK remains an asset bundle, not a policy authority. Policies can require that
a specific YUK package has been imported.

```json
{
  "required_yuks": [
    {
      "package_id": "org-dev-kit",
      "sha256": "hex-sha256-of-yuk-file"
    }
  ]
}
```

When a YUK is imported, the app records:

- package id
- package SHA-256
- installed skill paths and hashes
- installed extension paths and hashes

The record is written to:

```text
AICHS_HOME/organization/yuk.installed.json
```

At runtime, governed skill and extension checks verify that required YUK
packages are still recorded and that the installed assets still match their
recorded hashes.

If a required YUK is missing or a recorded asset is changed, governed skill and
extension capabilities are denied.

## Audit Log

Governed policy decisions are written to:

```text
AICHS_HOME/organization/audit.jsonl
```

Each line is JSON. Audit events are metadata-only by default.

Example:

```json
{
  "ts": "2026-07-06T10:00:00+00:00",
  "event": "policy.decision",
  "capability": "shell",
  "name": "execute",
  "decision": "deny",
  "policy_id": "default-dev-policy",
  "policy_hash": "hex...",
  "key_id": "org-main-2026",
  "cwd": "C:/repo"
}
```

Audit records may include:

- capability kind
- capability name
- source and owner
- workspace path
- path summary
- content hash
- model provider
- decision result
- policy id/hash/key id
- verification failures

Audit logging redacts common secret-bearing fields such as:

- API keys
- tokens
- bearer values
- authorization headers
- passwords
- client secrets

Audit logging does not intentionally record prompt contents, file contents, or
full shell command output.

## User-Facing Behavior

Personal mode:

- no governance UI
- no extra prompts
- no policy checks beyond existing app behavior

Governed mode:

- Settings shows a small managed status
- disallowed actions return a plain organization-policy message
- blocked tools are not advertised to the model
- blocked extensions/MCPs/skills do not load
- policy decisions are audited

Locked mode:

- governed capabilities fail closed
- Settings shows the policy verification failure
- policy decisions and lock events are audited when possible

User-facing denial text is intentionally simple:

```text
Your organization policy does not allow this action.
```

## Example Policies

### Team Safe

Use this when adopting governance gradually.

```json
{
  "format": "aichs-organization-policy/v1",
  "organization": {"id": "example", "name": "Example Org"},
  "policy_id": "team-safe",
  "version": 1,
  "preset": "team_safe",
  "rules": {
    "models": {
      "allow": ["openai:*", "anthropic:*"]
    },
    "tools": {
      "shell": "approval_required",
      "filesystem": "workspace_only"
    },
    "extensions": {
      "deny": ["unknown_high_risk_extension"]
    },
    "mcps": {
      "allow_servers": ["github"],
      "allow_tools": {
        "github": ["list_issues", "get_issue", "list_pull_requests"]
      }
    }
  }
}
```

### Strict

Use this when only approved capabilities may run.

```json
{
  "format": "aichs-organization-policy/v1",
  "organization": {"id": "example", "name": "Example Org"},
  "policy_id": "strict-dev",
  "version": 1,
  "preset": "strict",
  "rules": {
    "models": {
      "allow": ["openai:gpt-5", "anthropic:claude-sonnet-*"]
    },
    "tools": {
      "shell": "deny",
      "filesystem": "workspace_only",
      "allow": ["read_file", "list_files", "search_files", "edit_file"]
    },
    "extensions": {
      "allow_hashes": ["approved-extension-hash"]
    },
    "skills": {
      "allow_hashes": ["approved-skill-hash"]
    },
    "mcps": {
      "allow_servers": ["github"],
      "allow_tools": {
        "github": ["list_issues", "get_issue"]
      }
    }
  },
  "required_yuks": [
    {
      "package_id": "org-dev-kit",
      "sha256": "approved-yuk-package-hash"
    }
  ]
}
```

Remember: the actual policy file must include a valid `signature` block.

## Signing Workflow

A typical organization rollout:

1. Generate and store an Ed25519 private key outside developer machines.
2. Publish the raw public key in `trust.json`.
3. Write a policy without the `signature` field.
4. Canonicalize and sign the policy payload.
5. Add the `signature` block.
6. Deploy `trust.json` and `policy.json` together.
7. Open Settings and confirm the workspace is shown as managed.
8. Test a known denied action and confirm audit output.

Pseudocode:

```python
import base64
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

policy = json.load(open("policy.unsigned.json", "r", encoding="utf-8"))
payload = dict(policy)
payload.pop("signature", None)
canonical = json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode("utf-8")

private_key = Ed25519PrivateKey.generate()
signature = private_key.sign(canonical)
policy["signature"] = {
    "key_id": "org-main-2026",
    "algorithm": "ed25519",
    "value": base64.b64encode(signature).decode("ascii")
}
```

For real deployments, keep the private key in a secure signing process. Do not
ship it with the app or store it in a workspace.

## Implementation Map

The governance authority is intentionally small and central. New enforcement
points should call `services.organization_policy.decide()` instead of reading
policy JSON directly.

| Area | Main files | Enforcement behavior |
|---|---|---|
| Policy authority | `services/organization_policy.py` | Loads trust/policy files, verifies signatures, caches by file stat, evaluates capability requests, and returns allow/deny/approval decisions |
| Audit | `services/audit.py` | Writes sanitized metadata-only JSONL rows and trims the local log |
| Built-in tools and approvals | `services/tool_policy.py`, `services/tools.py` | Checks governance before normal approval prompts and filters blocked advertised tools |
| Extensions | `services/tool_registry.py` | Blocks disallowed extensions before executing extension Python and includes governance state in discovery cache keys |
| MCP | `services/mcp_config.py`, `services/mcp_tools.py` | Blocks disallowed MCP servers/tools and includes governance state in discovery cache keys |
| Skills | `services/skills.py` | Computes skill hashes and filters blocked skills before the slash picker sees them |
| Models | `services/chat.py` | Blocks disallowed provider/model pairs before provider calls |
| YUK import | `services/yuk.py` | Records imported package ids, package hashes, installed skill paths, and installed extension hashes |
| Settings UI | `ui/widgets/settings_dialog.py` | Shows compact Managed/Locked status only when governance is active |

A new governed capability should follow this pattern:

1. Build a `CapabilityRequest` with the least sensitive metadata that still
   identifies the capability.
2. Call `decide()` before discovery, advertisement, approval UI, or execution.
3. Stop immediately on `deny` and show the returned message.
4. Continue to existing approval flow on `approval_required`.
5. Include `governance_signature(cwd)` in any cache that stores discovered or
   advertised capabilities.

Do not treat YUK packages, extension review state, MCP state, or UI settings as
policy authorities. They can provide metadata or local user choices, but signed
organization policy remains the source of truth.

## Troubleshooting

### Settings does not show Managed

Check:

- `AICHS_HOME` is the directory you expect
- `AICHS_HOME/organization/policy.json` exists
- `AICHS_HOME/organization/trust.json` exists
- the policy signature uses a key id present in trust
- the policy `format` fields are correct

### Workspace is locked

Common causes:

- policy was edited after signing
- trust file changed
- public key does not match signing key
- `signature.value` is not valid base64
- policy file was removed after governed mode was active

### Extension is blocked

Check:

- extension content hash changed
- policy `extensions.allow_hashes` contains the current hash
- required YUK packages and recorded assets still match
- policy preset is not `strict` without an allowlist

### MCP server is missing

Check:

- server name is present in `mcps.allow_servers`
- server is not listed in `mcps.deny_servers`
- project-local MCP still needs normal review if not blocked by policy
- remote tools are not blocked by `allow_tools` or `deny_tools`

### Skill does not appear

Check:

- skill file has valid frontmatter
- skill name or SHA-256 is allowlisted
- required YUK asset hashes still match

## Security Notes

Governance checks are meaningful app-level enforcement, but local apps have
limits:

- a user who can patch Python code can bypass local policy checks
- a user who can replace the app binary can bypass local enforcement
- local audit logs can be deleted by users with filesystem access
- extension code still runs in-process once allowed

For high-assurance environments, combine local governance with:

- signed application builds
- managed installers
- read-only policy deployment
- remote audit upload
- model/tool gateways that enforce policy server-side
- OS or MDM controls around app and policy files

## Related Docs

- [Configuration](configuration.md)
- [YUK user kits](yuk.md)
- [Extensions](extensions.md)
- [MCP](mcp.md)
- [Skills](skills.md)
