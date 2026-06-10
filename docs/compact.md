# Compaction and Decision Memory

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

## Decision Memory

Durable project decisions should live outside core compaction as an opt-in
extension.

The decision-memory extension exposes narrow tools:

- `remember_decision(topic, decision)` saves one short durable decision
- `recall_decisions(topic)` returns decisions for one topic
- `list_decision_topics()` lists known decision keys

Storage is intentionally tiny:

```json
{
  "authentication": [
    "Use JWT for API authentication."
  ],
  "compaction": [
    "Decision memory should be an opt-in extension, not part of core compaction."
  ]
}
```

The extension may add a small context snippet to the main prompt:

- recall decisions before revisiting a durable architecture, product, strategy,
  or implementation topic
- remember only strong user-confirmed decisions
- avoid transient plans, tool output, summaries, guesses, secrets, and facts
  easily rediscovered from the repo
- optionally show known topic keys, but do not inject all decision contents into
  every prompt

## Why Extension-Only

Keeping decision memory as an extension makes the behavior explicit,
workspace-disableable, and easy to inspect. It also avoids coupling memory
policy to compaction, cron jobs, or raw transcript retention.

Core infrastructure should only provide the extension API needed for this:
extension tools, commands, context providers, and hooks receive
`ctx.extension_id` and `ctx.storage`, so they can share project-scoped state
without hand-rolled paths.

For context-resilience workflows, extensions should use:

- JSON state for compact handoff notes, decisions, blockers, and next steps.
- Text artifacts for bulky tool output or reports via
  `ctx.storage.save_artifact(name, content)`.
- Context snippets to re-inject only the small current handoff.
- Runtime compaction/resume directives when a continuation should happen at a
  safe model-request boundary.

Artifacts are references, not model context by default. A handoff state entry
should point to a large artifact path and summarize why it matters instead of
injecting the full output into every turn.
