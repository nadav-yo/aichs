# Project Memory

Project memory is built-in durable memory for small user-confirmed facts and
decisions that should survive across chats. It is separate from compaction:
compaction keeps a long conversation resumable, while project memory stores
selected durable context explicitly.

## Commands

Use executable slash commands when you want to manage memory directly:

| Command | Description |
|---|---|
| `/savememory topic: text` | Save one durable memory item under a topic |
| `/readmemory` | List recent memory items |
| `/readmemory query` | Search memory by topic, kind, or text |

`/savememory` is treated as direct user intent, so it saves immediately. If a
model tries to save memory through `save_project_memory`, the app asks for user
approval every time.

## Scope

Project memory scope is configured in **Settings -> Prompts -> Memory** or by
setting `project_memory_scope` in settings JSON.

| Scope | Storage |
|---|---|
| `local` | `.aichs/memory/project-memory.json` in the current workspace |
| `global` | `AICHS_HOME/memory/global-memory.json` |
| `disabled` | Reads return disabled and writes are rejected |

Local project memory is the default.

## Agent Access

Normal coder turns do not receive memory tools by default. The Archivist can use
`read_project_memory` before searching chat history, and can use
`save_project_memory` only when the user clearly asks or confirms that something
should be remembered.

Save only durable context: architecture decisions, product constraints, naming
choices, process rules, or other stable facts that should help future chats. Do
not save transient plans, routine implementation details, raw tool output,
guesses, secrets, or facts easily rediscovered from the repo.
