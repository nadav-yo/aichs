# Compaction

Compaction is only context management. When a conversation grows past the
context budget, compaction cuts off an older prefix of the chat, asks the model
for a concise continuation summary, and keeps that summary plus recent verbatim
messages.

The compacted summary is optimized for the next model call: current goal,
important constraints, relevant files, decisions, tool results, blockers, and
the next step. It is not a durable archive.

## Raw History

Compaction replaces the saved conversation messages with the compacted history.
The old raw prefix is not kept in the active conversation JSON.

Do not automatically save raw compaction archives by default. That would make
compaction a hidden retention mechanism instead of a straightforward context
cleanup operation.

## Related State

Durable project memory, handoff notes, and large-output retention live outside
core compaction. Project memory is a separate built-in store with local, global,
and disabled scopes; see [Project Memory](project-memory.md). Compaction itself
stays focused on context reduction and resumability.

Extensions can still provide their own context-resilience workflows. Extension
tools, commands, context providers, and hooks receive `ctx.extension_id` and
`ctx.storage`, so they can share project-scoped state without hand-rolled paths.

For extension-owned workflows, use:

- JSON state for compact handoff notes, decisions, blockers, and next steps.
- Text artifacts for bulky tool output or reports via
  `ctx.storage.save_artifact(name, content)`.
- Context snippets to re-inject only the small current handoff.
- Runtime compaction/resume directives when a continuation should happen at a
  safe model-request boundary.

Artifacts are references, not model context by default. A handoff state entry
should point to a large artifact path and summarize why it matters instead of
injecting the full output into every turn.
