# Extensions

Extensions let `aichs` load extra tools, slash-command prompts, context snippets,
and lifecycle hooks from local Python files.

## Locations

| Path | Scope |
|---|---|
| `~/.aichs/extensions/*.py` | User-global |
| `.aichs/extensions/*.py` | Project-local |

Project-local extensions load after user-global extensions. Tool names must be
unique.

After adding or editing an extension, run `/reload` in the composer. New agent
turns also reload extension files automatically.

## File format

Each extension is a Python file with a `register(registry)` function.

## Tools

Tools are callable by the model:

```python
def register(registry):
    registry.tool(
        name="hello",
        description="Return a short greeting.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        },
        execute=hello,
        approval="once",
    )


def hello(ctx, inputs):
    name = inputs.get("name") or "there"
    return f"Hello, {name}."
```

`execute` receives:

| Argument | Description |
|---|---|
| `ctx.cwd` | Current workspace path |
| `ctx.on_line` | Optional callback for streaming command-like output |
| `ctx.cancel` | Cancellation event |
| `ctx.is_cancelled()` | Convenience cancellation check |
| `inputs` | The model-provided JSON arguments |

`approval="once"` asks the user before the tool is first used in a conversation.
Omit it for trusted, read-only local tools.

## Slash Commands

Extension commands are prompt modes. Selecting one from the `/` picker activates
it as a chip for the next user message:

```python
def register(registry):
    registry.command(
        name="ship_check",
        description="Review the current diff before shipping",
        prompt=(
            "Review the current diff for correctness, missing tests, and release "
            "risk. Read files as needed. Do not edit files unless explicitly asked."
        ),
        tools=["read_file", "search_files", "execute"],
    )
```

Built-in `/compact` and `/reload` remain immediate app commands.

## Context Snippets

Context providers append text under `Extension context` in the system prompt:

```python
def register(registry):
    registry.context("Project workflow", project_workflow)


def project_workflow(ctx):
    return "Run `pytest` before claiming Python changes are verified."
```

## Hooks

Hooks observe lifecycle events and can lightly adjust tool calls/results:

```python
def register(registry):
    registry.hook("before_tool_call", before_tool_call)
    registry.hook("after_tool_result", after_tool_result)


def before_tool_call(ctx):
    if ctx.tool_name == "execute" and "git push" in ctx.inputs.get("command", ""):
        ctx.status = "error"
        ctx.output = "[tool error] git push is blocked by project policy."


def after_tool_result(ctx):
    if ctx.tool_name == "execute" and len(ctx.output) > 12000:
        ctx.output = ctx.output[:12000] + "\n\n[trimmed by extension]"
```

Supported hook names:

| Hook | When it runs |
|---|---|
| `turn_start` | Before a user request enters the agent loop |
| `before_model_request` | Before each model request |
| `before_tool_call` | Before a tool is approved/executed |
| `after_tool_result` | After a tool returns, before the model sees output |
| `turn_done` | After the turn finishes, errors, or is cancelled |

## UI Contributions

Extensions can declare small UI contributions. They return structured data;
`aichs` owns the actual widgets, layout, and styling.

### Status Badges

Status badges appear in the chat top bar. Clicking a badge opens a panel.

```python
def register(registry):
    registry.status_badge(name="tests", provider=test_badge)
    registry.panel(name="tests", title="Tests", provider=test_panel)


def test_badge(ctx):
    return {
        "label": "Tests",
        "tooltip": "Open test status",
        "tone": "accent",
        "panel": "tests",
    }
```

Badge fields:

| Field | Description |
|---|---|
| `label` | Required button text |
| `tooltip` | Optional hover text |
| `tone` | Optional: `success`, `danger`, `warning`, `accent` |
| `panel` | Optional panel name to open; defaults to the badge name |
| `visible` | Set to `False` to hide the badge |

### Panels

Panels open from status badges. They are also listed in the top-bar
`Extensions` view with the rest of the registered contributions.

```python
def test_panel(ctx):
    return {
        "title": "Tests",
        "body": "Last run: not yet run.",
        "sections": [
            {
                "heading": "Failures",
                "items": [
                    {
                        "title": "test_example",
                        "subtitle": "tests/test_example.py:12",
                        "body": "Expected 1, got 0.",
                    }
                ],
            }
        ],
    }
```

Panel providers may also return a plain string.

Panel schema:

| Field | Type | Description |
|---|---|---|
| `title` | string | Optional panel heading. Falls back to the registered panel title. |
| `body` | string | Optional text shown before sections. |
| `sections` | list | Optional list of section objects or strings. |

Section schema:

| Field | Type | Description |
|---|---|---|
| `heading` | string | Optional section heading. |
| `body` | string | Optional text shown before the section items. |
| `items` | list | Optional list of item objects or strings. |

Item schema:

| Field | Type | Description |
|---|---|---|
| `title` | string | Primary row text. Defaults to `Item`. |
| `subtitle` | string | Optional secondary text. |
| `body` | string | Optional detail text. |
| `action` | object | Optional single action. |
| `actions` | list | Optional list of action objects. |

Action schema:

| Field | Type | Description |
|---|---|---|
| `label` | string | Button text. Defaults to the action type. |
| `type` | string | Supported: `open_file`, `copy`, `refresh_panel`, `send_message`. |
| `path` | string | For `open_file`: workspace-relative path. |
| `text` | string | For `copy`: text to copy. For `send_message`: message text to send or queue. |

Supported actions:

| Type | Behavior |
|---|---|
| `open_file` | Opens a workspace-relative file in the viewer. |
| `copy` | Copies `text` to the clipboard. |
| `refresh_panel` | Re-runs the panel provider and redraws the panel. |
| `send_message` | Sends or queues `text` as a normal chat message. |

String shortcuts:

| Return value | Rendering |
|---|---|
| panel returns `"text"` | Body text |
| section is `"text"` | Body text |
| item is `"text"` | Single card with that title |

Currently unsupported in panel data:

| Not supported yet |
|---|
| tool-running buttons |
| file links inside text |
| custom icons |
| custom colors per row |
| arbitrary PyQt widgets |
| HTML or Markdown rendering |

see [Examples](../.aichs/extensions/)
