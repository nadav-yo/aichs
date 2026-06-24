# MCP

`aichs` can act as an MCP host. It reads standard `mcp.json` files, starts
configured MCP servers, discovers their tools, and exposes those tools to the
selected model alongside built-in tools.

## Configuration

Use the portable `mcpServers` JSON shape used by MCP clients such as Claude and
Cursor:

```json
{
  "mcpServers": {
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"],
      "env": {}
    },
    "linear": {
      "url": "https://mcp.linear.app/mcp"
    }
  }
}
```

The import dialog also accepts vendor examples that wrap entries in `servers`,
such as GitHub Copilot MCP snippets. Imported configs are normalized to
`mcpServers` when saved.

Locations:

| Path | Scope |
|---|---|
| `AICHS_HOME/mcp.json` | User-global |
| `.agents/mcp.json` | Project-local |

Project-local MCP servers are disabled until reviewed in the MCP dialog.

`aichs` stores app-specific state outside the portable config:

| Path | Contents |
|---|---|
| `AICHS_HOME/project/mcp.state.json` | Enabled/disabled status, per-component allow/disable state, and project review fingerprints |
| `AICHS_HOME/project/mcp.oauth.json` | OAuth client and token data for remote MCP servers |
| `AICHS_HOME/project/mcp.log` | Recent MCP connection, discovery, and tool-call events |

Do not put secrets in project-local `mcp.json`; prefer user-global config,
environment-variable references, or OAuth.

## HTTP Auth

Standard MCP server config does not require an auth type field. For URL servers,
`aichs` defaults to auto mode: it first connects with plain streamable HTTP, and
if the server responds with an OAuth challenge it asks you to authorize.

Some servers need host-specific OAuth settings such as scopes or pre-registered
client credentials. `aichs` accepts an optional compatibility `auth` object for
those cases:

```json
{
  "mcpServers": {
    "linear": {
      "url": "https://mcp.linear.app/mcp",
      "auth": {
        "type": "oauth",
        "scope": "read write",
        "redirect_uri": "http://localhost:33331/callback"
      }
    }
  }
}
```

Authorize OAuth servers from the MCP dialog. `aichs` opens the authorization URL,
asks for the final callback URL, and stores the resulting OAuth token state
outside `mcp.json`.

Some OAuth providers do not support MCP dynamic client registration. GitHub
Copilot MCP advertises GitHub OAuth, which requires a pre-registered OAuth
client for third-party hosts. Configure those servers with `auth.client_id`
and, if required by the provider, `auth.client_secret` before pressing
Authorize.

Static headers are still supported for non-standard servers, gateways, or manual
bearer-token setups:

```json
{
  "mcpServers": {
    "gateway": {
      "url": "https://example.test/mcp",
      "auth": "headers",
      "bearer_token_env_var": "MCP_TOKEN"
    }
  }
}
```

For a truly public or local HTTP MCP server, the standard config is just the
URL:

```json
{
  "mcpServers": {
    "local": {
      "url": "http://localhost:3000/mcp"
    }
  }
}
```

## Tools, Resources, And Prompts

MCP server tools are exposed with stable names:

```text
mcp__server_name__tool_name
```

The MCP dialog shows the current advertised tools, resources, resource
templates, and prompts for enabled servers. Each item can be unchecked to hide
or block that component without editing the portable `mcp.json`.

Only MCP server tools are advertised to the selected model. Resources, resource
templates, and prompt templates are shown in the MCP dialog so the user can see
what the server provides and enable or disable individual items. They are not
published as synthetic `list_resources` or `list_prompts` model tools.

Use the server's **Activity** button in the MCP dialog to switch the capability
view to recent connection, discovery, and tool-call events for that server.
Activity output is sanitized so credentials and bearer tokens are not shown.
The Activity view can be cleared per server without removing other MCP activity.

Disabled remote tools are not advertised to the model. Disabled resources,
resource templates, and prompts are hidden or blocked in user-facing MCP UI
surfaces.

MCP tools require approval by default before use in a conversation.

## Tool Results

`aichs` follows the standard MCP `tools/call` result shape. Plain text-only
results are displayed as text. Results with structured or non-text content are
kept as structured JSON for the model/debug path and rendered cleanly in the
chat tool-details expander.

Supported MCP content blocks:

| Type | Chat details behavior |
|---|---|
| `text` | Shown as text |
| `image` | Image preview is shown; base64 data is hidden from the text view |
| `audio` | MIME type and payload size are summarized; base64 data is hidden |
| `resource_link` | Resource title/name, URI, and MIME type are shown |
| `resource` | Embedded text resources are shown; embedded binary resources are summarized; embedded image blobs can be previewed |

`structuredContent` is shown as formatted JSON. Standard MCP tool execution
errors that return `isError: true` are treated as tool errors in the chat UI.

## Overhead

If no global or project `mcp.json` exists, the MCP path does not start servers,
open network connections, discover tools, or import the MCP SDK. Normal chats
only perform cheap config-file existence checks.

