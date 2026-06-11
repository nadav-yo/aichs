# Extensions

Extensions let `aichs` load extra tools, slash-command prompts, context snippets,
and lifecycle hooks from local Python files.

## Locations

| Path | Scope |
|---|---|
| `AICHS_HOME/extensions/*.py` | User-global |
| `AICHS_HOME/extensions/*/extension.py` | User-global folder extension |
| `.aichs/extensions/*.py` | Project-local |
| `.aichs/extensions/*/extension.py` | Project-local folder extension |

Project-local extensions load after user-global extensions. Tool names must be
unique.

After adding or editing an extension, run `/reload` in the composer. New agent
turns also reload extension files automatically.

You can also open the Extensions dialog, use the reload button next to the
title, or toggle an extension file between Loaded and Disabled. Disabled
extensions stay visible in the dialog but do not register tools, commands,
hooks, context, badges, or panels. Disabled-extension state is stored with user
app data under `AICHS_HOME/project/`, outside the workspace tree.

## Installing From Git

Open the Extensions dialog and choose **Add**. Paste a git URL, fetch it, choose
the discovered extensions, then install them into either:

| Choice | Target |
|---|---|
| Local project | `.aichs/extensions/` in the current workspace |
| Global user | `AICHS_HOME/extensions/` |

For now the installer supports git sources only. The source pipeline is kept
resolver-based so a future registry can add HTTP/catalog sources without
changing how extension candidates are selected or copied.

If the source contains multiple folder extensions, each folder with an
`extension.py` entrypoint is shown separately. Root-level `*.py` extension files
are also installable. Installing an extension with the same folder or file name
replaces the existing installed copy.

## File format

Each extension is either a Python file with a `register(registry)` function, or
a folder containing an `extension.py` entrypoint. Folder extensions use the
folder name as their extension id, which makes room for future multi-file
extensions.

Extensions can declare a short description for the Extensions dialog:

```json
{
  "name": "Python Language Support",
  "description": "Adds Python diagnostics, symbols, and completion.",
  "requires": {
    "executables": ["ruff"],
    "python": ["tree-sitter", "tree-sitter-python"]
  }
}
```

```python
EXTENSION_DESCRIPTION = "Adds project-specific review and guardrail helpers."


def register(registry):
    registry.metadata(description="Adds project-specific review and guardrail helpers.")
```

For folder extensions, `aichs-extension.json` `name` is used as the display
name in the Extensions dialog. Its `description` is the first static description
fallback.

Folder manifests can also declare runtime requirements:

| Field | Description |
|---|---|
| `requires.executables` | Executables that should be available on `PATH`, such as `ruff`. |
| `requires.python` | Python import/package names. Dashes are also checked as underscores, so `tree-sitter-python` maps to `tree_sitter_python`. |

Requirements are read statically from the manifest, including while an extension
is disabled or fails to load. Core records missing requirements on the extension
summary so installers, future registries, and UI surfaces can explain degraded
language support without executing extension code.

Folder manifests should declare extension permissions:

```json
{
  "permissions": {
    "tools": true,
    "commands": false,
    "context": true,
    "hooks": false,
    "ui": false,
    "language": false,
    "processes": false,
    "network": false,
    "workspace_read": false,
    "workspace_write": false,
    "extension_storage": true
  }
}
```

These permissions are a disclosure and app-level contribution contract. Core
blocks undeclared registry contributions such as tools, commands, context,
hooks, UI, language features, and process-control commands. They are not an
operating-system sandbox: enabled extensions still run local Python code in the
AICHS process, and workspace/network declarations are shown as risk disclosures.
Imported, new, or changed extensions are disabled until reviewed.

Disabled-extension state and review acknowledgements are app-owned user state
stored under `AICHS_HOME/project/`, not in the workspace `.aichs/` folder. The
review record is for visibility and prompting only; it is not a cryptographic
trust root or a tamper-proof security boundary.

`registry.metadata(...)` is used when the extension is loaded. If there is no
manifest description, the module-level `EXTENSION_DESCRIPTION` constant,
`EXTENSION = {"description": "..."}`, or the module docstring is used as a safe
fallback, including while an extension is disabled and its `register()` function
is not executed.

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
| `ctx.extension_id` | Safe id derived from the extension filename |
| `ctx.storage.load_config(scope)` | Load project/global extension JSON config |
| `ctx.storage.save_config(data, scope)` | Save project/global extension JSON config |
| `ctx.storage.load_state(name)` | Load project-scoped extension JSON state |
| `ctx.storage.save_state(data, name)` | Save project-scoped extension JSON state |
| `ctx.storage.artifact_path(name)` | Return a project-scoped artifact path |
| `ctx.storage.save_artifact(name, content)` | Save UTF-8 text under project extension state |
| `ctx.storage.load_artifact(name, max_chars)` | Load a saved UTF-8 text artifact |
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
| `ctx.storage.artifact_path(name)` | Return a project-scoped artifact path |
| `ctx.storage.save_artifact(name, content)` | Save UTF-8 text under project extension state |
| `ctx.storage.load_artifact(name, max_chars)` | Load a saved UTF-8 text artifact |
| `ctx.runtime.notice(text)` | Show a center notice |
| `ctx.runtime.send(text)` | Send now, or queue if a run is active |
| `ctx.runtime.enqueue(text)` | Queue a normal chat message |
| `ctx.runtime.compact(force=True)` | Request normal compaction |
| `ctx.runtime.continue_after_compact(prompt, force=True)` | Queue a synthetic resume after compaction |
| `ctx.runtime.processes.start(name, command, ...)` | Start a managed long-running process |
| `ctx.runtime.processes.status(name)` | Inspect managed process state |
| `ctx.runtime.processes.tail(name, lines)` | Read recent process output |
| `ctx.runtime.processes.write(name, text)` | Write to process stdin when enabled |
| `ctx.runtime.processes.stop(name)` | Stop a managed process |

`capabilities` are shown in the Extensions view so runtime-control extensions
are visibly more powerful than prompt or UI-only extensions.

Managed process starts show a long-running process approval dialog when invoked
through the app runtime. Core owns the process handle, keeps a bounded output
buffer, stops workspace processes on app close, and emits `process_started` and
`process_exited` hooks with `ctx.process`.

## Context Snippets

Context providers append text under `Extension context` in the system prompt:

```python
def register(registry):
    registry.context("Project workflow", project_workflow)


def project_workflow(ctx):
    state = ctx.storage.load_state("workflow")
    note = state.get("note") or "Run `pytest` before claiming Python changes are verified."
    return note
```

Context providers receive `ctx.cwd`, `ctx.model`, `ctx.history`,
`ctx.extension_id`, and the same `ctx.storage` helper available to extension
tools.

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

Hook handlers receive:

| Field/API | Description |
|---|---|
| `ctx.cwd` | Current workspace path |
| `ctx.model` | Selected model id |
| `ctx.system` | Current system prompt text |
| `ctx.history` | Current runtime history |
| `ctx.tool_name` | Tool name for tool hooks |
| `ctx.inputs` | Tool inputs for tool hooks |
| `ctx.output` | Tool result or turn output, depending on event |
| `ctx.status` | `ok`, `error`, or `cancelled` |
| `ctx.error` | Error text for blocking/failure hooks |
| `ctx.process` | Process event data for process hooks |
| `ctx.extension_id` | Safe id derived from the extension filename |
| `ctx.storage.load_config(scope)` | Load project/global extension JSON config |
| `ctx.storage.save_config(data, scope)` | Save project/global extension JSON config |
| `ctx.storage.load_state(name)` | Load project-scoped extension JSON state |
| `ctx.storage.save_state(data, name)` | Save project-scoped extension JSON state |
| `ctx.storage.artifact_path(name)` | Return a project-scoped artifact path |
| `ctx.storage.save_artifact(name, content)` | Save UTF-8 text under project extension state |
| `ctx.storage.load_artifact(name, max_chars)` | Load a saved UTF-8 text artifact |

Supported hook names:

| Hook | When it runs |
|---|---|
| `turn_start` | Before a user request enters the agent loop |
| `before_model_request` | Before each model request |
| `before_tool_call` | Before a tool is approved/executed |
| `after_tool_result` | After a tool returns, before the model sees output |
| `before_next_model_request` | After tool results are recorded, before the next provider request |
| `turn_done` | After the turn finishes, errors, or is cancelled |

## Working Notes and Large Outputs

Use JSON state for compact handoff data and artifacts for bulky text. Artifacts
are stored under `.aichs/state/<extension-id>/artifacts/` with sanitized file
names, so an extension does not need to hand-roll project paths.

```python
def register(registry):
    registry.tool(
        name="save_handoff",
        description="Persist a compact continuation handoff.",
        input_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "next": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary"],
        },
        execute=save_handoff,
    )
    registry.context("Current handoff", current_handoff)
    registry.hook("after_tool_result", spool_large_output)


def save_handoff(ctx, inputs):
    ctx.storage.save_state({
        "summary": inputs["summary"],
        "next": inputs.get("next", []),
    }, name="handoff")
    return "Handoff saved."


def current_handoff(ctx):
    handoff = ctx.storage.load_state("handoff")
    if not handoff:
        return ""
    return f"Handoff: {handoff.get('summary', '')}"


def spool_large_output(ctx):
    if len(ctx.output) <= 12000:
        return
    path = ctx.storage.save_artifact(f"{ctx.tool_name}-latest.txt", ctx.output)
    ctx.output = (
        f"[large output saved]\n"
        f"Tool: {ctx.tool_name}\n"
        f"Path: {path}\n"
        f"Preview:\n{ctx.output[:2000]}"
    )
```

Store explicit working state: decisions, findings, next steps, blockers, and
paths to large outputs. Do not store hidden reasoning, secrets, or raw
transcripts by default.

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
| Handoff/artifact storage | Hooks, tools, commands, and context providers share `ctx.storage` |
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

Examples live outside the app repo at
[nadav-yo/aichs-extensions](https://github.com/nadav-yo/aichs-extensions).
They are opt-in and are not shipped with `aichs` by default.

## Language Features

Extensions can add optional file-editor language features. The core app owns the
editor UI and routing; extensions return structured data.

`aichs` does not ship language support by default. Python, tree-sitter grammars,
LSP servers, linters, and other language packages are opt-in extension
dependencies.

```python
def register(registry):
    registry.language(
        name="python",
        file_patterns=["*.py"],
        diagnostics=diagnostics,
        symbols=symbols,
        completion=completion,
        code_actions=code_actions,
        apply_code_action=apply_code_action,
        format_document=format_document,
    )


def diagnostics(ctx):
    return [{
        "line": 3,
        "column": 8,
        "severity": "warning",
        "message": "Example warning",
        "source": "example",
    }]
```

Language providers receive:

| Field/API | Description |
|---|---|
| `ctx.cwd` | Current workspace path. |
| `ctx.path` | Current file path. |
| `ctx.content` | Current file text. |
| `ctx.position` | Cursor offset when requesting completion. |
| `ctx.prefix` | Current word prefix when requesting completion. |
| `ctx.action_id` | Requested code action id when applying an action. |
| `ctx.diagnostics` | Diagnostics selected for a code-action request. |
| `ctx.extension_id` | Safe id derived from the extension filename. |
| `ctx.storage.load_config(scope)` | Load project/global extension JSON config. |
| `ctx.storage.save_config(data, scope)` | Save project/global extension JSON config. |
| `ctx.storage.load_state(name)` | Load project-scoped extension JSON state. |
| `ctx.storage.save_state(data, name)` | Save project-scoped extension JSON state. |
| `ctx.storage.artifact_path(name)` | Return a project-scoped artifact path. |
| `ctx.storage.save_artifact(name, content)` | Save UTF-8 text under project extension state. |
| `ctx.storage.load_artifact(name, max_chars)` | Load a saved UTF-8 text artifact. |

Diagnostic fields:

| Field | Description |
|---|---|
| `line` | 1-based line number. |
| `column` | 0-based column number. |
| `end_line` / `end_column` | Optional range end. |
| `severity` | `error`, `warning`, `info`, or `hint`. |
| `message` | Diagnostic message. |
| `source` | Optional source label, such as a linter name. |
| `code` | Optional diagnostic code. |
| `fix_available` | Optional boolean indicating whether the provider knows a fix exists. |
| `fix_safety` | Optional: `safe`, `unsafe`, or empty when unknown. |
| `data` / `metadata` | Optional provider-specific object, such as rule URLs. |

Symbol fields:

| Field | Description |
|---|---|
| `name` | Symbol name. |
| `kind` | Symbol kind, such as `class` or `function`. |
| `line` / `column` | Symbol location. |
| `end_line` / `end_column` | Optional range end. |

Completion providers may return strings or objects with `label`, `insert_text`,
and optional `detail`.

Code actions are split into listing and applying. `code_actions(ctx)` returns
available actions for the current file or selected diagnostics:

```python
def code_actions(ctx):
    return [{
        "id": "ruff.fix",
        "title": "Apply Ruff safe fixes in file",
        "kind": "quickfix",
        "source": "ruff",
        "diagnostic_code": "F401",
        "safety": "safe",
    }]
```

Code action fields:

| Field | Description |
|---|---|
| `id` | Stable id passed back to `apply_code_action`. |
| `title` | Human-readable action label. |
| `kind` | Optional action kind, such as `quickfix` or `source.fixAll`. |
| `source` | Optional provider label. |
| `diagnostic_code` / `code` | Optional diagnostic code the action relates to. |
| `safety` | `safe` or `unsafe`. If omitted, `safe` defaults to true. |
| `safe` | Backward-compatible boolean. `safety` is preferred for new extensions. |
| `data` / `metadata` | Optional provider-specific object. |

`apply_code_action(ctx)` receives `ctx.action_id`, `ctx.content`, and any
selected `ctx.diagnostics`. It returns replacement content and/or a message:

```python
def apply_code_action(ctx):
    if ctx.action_id != "ruff.fix":
        return None
    return {
        "content": fixed_content,
        "message": "Applied Ruff safe fixes.",
    }
```

For backward compatibility, extensions may still implement apply behavior inside
`code_actions(ctx)` by checking `ctx.action_id`; `apply_code_action` is preferred
for new extensions.

Document formatting is separate from lint fixes:

```python
def format_document(ctx):
    return {
        "content": formatted_content,
        "message": "Formatted with Ruff.",
    }
```

Formatting and code-action providers should return replacement text rather than
writing files directly. Core decides when and how that text is applied or saved.

Core routes language features through `services.language_features.LanguageService`.
The module-level functions (`diagnostics`, `symbols`, `completions`,
`code_actions`, `apply_code_action`, `format_document`, and `format_file`) remain stable
compatibility wrappers over that service.

`format_file(cwd, path, content=None)` is a core formatting command path for
future UI, tools, or extension commands. If `content` is provided, core formats
that buffer. If `content` is omitted, core reads the file from the workspace.
In both cases it returns replacement content and never writes the formatted text
back to disk.

`language_status(cwd)` reports registered language support without invoking
linters or formatters. Each status includes:

| Field | Description |
|---|---|
| `extension_id` | Extension that registered the language. |
| `language` | Registered language name. |
| `file_patterns` | Patterns handled by the language contribution. |
| `features` | Enabled feature names, such as `diagnostics` or `format_document`. |
| `requirements` | Static manifest requirements grouped by type. |
| `missing_requirements` | Missing requirements, such as `executable:ruff`. |
| `ready` | True when no requirements are missing. |

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
| `type` | string | Supported: `open_file`, `copy`, `refresh_panel`, `send_message`, `run_extension_command`. |
| `path` | string | For `open_file`: workspace-relative path. |
| `text` | string | For `copy`: text to copy. For `send_message`: message text to send or queue. |
| `command` | string | For `run_extension_command`: executable extension command name. |
| `args` | string | For `run_extension_command`: arguments passed to the command. |
| `refresh` | boolean | If true, refresh the panel after the action runs. |

Supported actions:

| Type | Behavior |
|---|---|
| `open_file` | Opens a workspace-relative file in the viewer. |
| `copy` | Copies `text` to the clipboard. |
| `refresh_panel` | Re-runs the panel provider and redraws the panel. |
| `send_message` | Sends or queues `text` as a normal chat message. |
| `run_extension_command` | Runs an executable extension command without adding a chat message. |

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

See [nadav-yo/aichs-extensions](https://github.com/nadav-yo/aichs-extensions)
for opt-in example extensions.
