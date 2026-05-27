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

You can also open the Extensions dialog, use the reload button next to the
title, or toggle an extension file between Loaded and Disabled. Disabled
extensions stay visible in the dialog but do not register tools, commands,
hooks, context, badges, or panels. The per-workspace disabled list is stored in
`.aichs/extensions.disabled.json`.

## File format

Each extension is a Python file with a `register(registry)` function.

Extensions can declare a short description for the Extensions dialog:

```python
EXTENSION_DESCRIPTION = "Adds project-specific review and guardrail helpers."


def register(registry):
    registry.metadata(description="Adds project-specific review and guardrail helpers.")
```

`registry.metadata(...)` is used when the extension is loaded. The module-level
`EXTENSION_DESCRIPTION` constant, `EXTENSION = {"description": "..."}`, or the
module docstring is used as a safe fallback, including while an extension is
disabled and its `register()` function is not executed.

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

Commands can also be executable runtime commands. Executable commands run
extension code immediately instead of becoming a prompt chip:

```python
def register(registry):
    registry.command(
        name="continue",
        description="Compact and resume the current task",
        execute=continue_command,
        capabilities=["runtime_control", "compaction", "state"],
    )


def continue_command(ctx, args):
    if args.strip() == "status":
        state = ctx.storage.load_state()
        return f"Continuation runs: {state.get('runs', 0)}"

    state = ctx.storage.load_state()
    state["runs"] = int(state.get("runs", 0)) + 1
    ctx.storage.save_state(state)
    ctx.runtime.continue_after_compact(
        "Continue from the extension handoff.",
        force=True,
    )
    return "Continuation queued."
```

Executable command context:

| Field/API | Description |
|---|---|
| `ctx.cwd` | Current workspace path |
| `ctx.model` | Selected model id |
| `ctx.history` | Current visible conversation history |
| `ctx.conversation_id` | Current conversation id, when available |
| `ctx.storage.load_config(scope)` | Load project/global extension JSON config |
| `ctx.storage.save_config(data, scope)` | Save project/global extension JSON config |
| `ctx.storage.load_state(name)` | Load project conversation-scoped JSON state |
| `ctx.storage.save_state(data, name)` | Save project conversation-scoped JSON state |
| `ctx.runtime.notice(text)` | Show a center notice |
| `ctx.runtime.send(text)` | Send now, or queue if a run is active |
| `ctx.runtime.enqueue(text)` | Queue a normal chat message |
| `ctx.runtime.compact(force=True)` | Request normal compaction |
| `ctx.runtime.continue_after_compact(prompt, force=True)` | Queue a synthetic resume after compaction |

`capabilities` are shown in the Extensions view so runtime-control extensions
are visibly more powerful than prompt or UI-only extensions.

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

## Runtime Extensions

A `pi-continue`-style extension is the reference runtime use case: pause at a
safe point, compact history, persist a handoff artifact, prove that the handoff
was accepted, and resume the same task without pretending that a new user
request happened.

Extension code still does not reach directly into Qt widgets, provider-specific
message lists, or conversation persistence. Instead, it asks core for runtime
actions through executable commands and structured hook directives.

### Implemented Gap

The current runtime-extension surface covers the main control-flow gap:

| Need | Runtime support |
|---|---|
| Executable commands | `registry.command(..., execute=handler)` |
| Extension settings/state | `ctx.storage.load_config/save_config` and `load_state/save_state` |
| Structured hook directives | Hooks can return directive dictionaries or call `ctx.compact_and_resume(...)` |
| Safe mid-turn boundary | `before_next_model_request` runs after completed tool-result batches |
| Core compaction/resume | `compact_now` and `compact_and_resume` directives are app-owned |
| Compaction proof | `compact_with_result()` returns status, cut index, proof metadata, and optional artifact |
| Structured handoff | `aicc-continuation/v1` ledger validation is available for continuation compaction |
| Capabilities | Runtime-control commands declare visible capabilities |

### Hook Directives

Hooks may keep mutating `ctx` as before, or return runtime directives:

```python
def register(registry):
    registry.hook("before_next_model_request", maybe_continue)


def maybe_continue(ctx):
    if not should_continue(ctx.history):
        return None
    return {
        "action": "compact_and_resume",
        "force": False,
        "ledger": True,
        "resume_prompt": "Continue from the validated continuation ledger.",
    }
```

Supported directive actions:

| Action | Effect |
|---|---|
| `show_notice` | Show a notice in the chat |
| `enqueue_message` | Append a synthetic user message before the next model request |
| `compact_now` | Compact the safe runtime history |
| `compact_and_resume` | Compact, then append a synthetic resume prompt |
| `block` | Stop before the next provider request |

Synthetic runtime messages are model input, not user transcript. They are hidden
from the chat view, ignored by title/search helpers, and removed from persisted
history after the consuming turn. Tool-result messages remain stored as hidden
evidence, but internal active-task anchor text is stripped after the turn.

The same actions are available as `HookContext` helpers:

```python
ctx.show_notice("Preparing continuation.")
ctx.compact_and_resume(
    resume_prompt="Continue from the handoff.",
    force=False,
    ledger=True,
)
```

### Safe Boundaries

Runtime compaction is only applied at safe model-request boundaries:

- before a provider request
- after a complete assistant/tool-result batch
- before the next provider request

It does not compact while streaming partial assistant text, while tools are
running, or between a tool call and its matching tool result.

### Continuation Ledger

When a compaction directive sets `ledger=True`, the compaction summary must
validate as `aicc-continuation/v1`:

```json
{
  "version": "aicc-continuation/v1",
  "task": "Implement runtime extensions",
  "done_when": "The runtime command and hook examples work",
  "forbid": [],
  "established": [],
  "learned": [],
  "open": [],
  "next": []
}
```

Invalid ledgers fail closed: the runtime emits a compaction failure event and
does not resume from guessed continuation state.

Examples:

- `.aichs/extensions/runtime_continue.py` shows an executable `/continue`
  command with `status`, `preview`, and `queue` subcommands, command state, a
  status panel, and a `before_next_model_request` hook that requests
  `compact_and_resume`.
- `.aichs/extensions/runtime_guard.py` shows a smaller runtime-control pattern:
  `/guard status`, a status panel, and a `before_next_model_request` hook that
  blocks repeated identical tool errors before the agent retries the same path.

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
