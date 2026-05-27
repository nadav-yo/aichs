# Compaction and Archiving

Compaction and archiving share the same trigger point, but they serve different
jobs.

When a conversation grows past the context budget, compaction cuts off an older
prefix of the chat, asks the model for a concise continuation summary, and keeps
that summary plus the recent verbatim messages. The summary is optimized for the
next model call: current goal, important constraints, relevant files, decisions,
tool results, blockers, and the next step.

That same cut point is also the last moment when the app still has the full
pre-compact transcript in memory. Before replacing the older messages, the app
should extract archive candidates for the Archivist. These candidates are not
the compacted chat history; they are structured memory items the archiver can
deduplicate, merge, or ignore later.

## Recommended Split

Compaction should produce:

- a short continuation summary for the active conversation
- archive candidates derived from the messages being removed

The compaction path should not block on archival work. If candidate extraction
or archival storage fails, the conversation should still compact successfully
and continue. Archival can run asynchronously or be retried later.

## Archive Candidates

Good archive candidates are durable and useful across future chats:

- user preferences and standing instructions
- project facts that are not obvious from a quick repo search
- decisions made, including rejected approaches and reasons
- completed changes and the files or symbols involved
- unresolved TODOs, blockers, and follow-up questions
- useful debugging findings or test results
- important links between a task, a conversation, and a workspace path

Avoid archiving:

- raw tool output
- transient chatter
- duplicate summaries
- large pasted code or file contents
- facts that can be rediscovered cheaply from the repo

## Suggested Shape

Archive candidates should carry enough provenance to be audited later:

```json
{
  "conversation_id": "20260527_101500",
  "cwd": "C:/Users/nadav/source/repos/aichs",
  "message_range": [0, 18],
  "created_at": "2026-05-27T10:42:00",
  "kind": "decision",
  "tags": ["compaction", "archivist", "memory"],
  "text": "Compaction should emit archive candidates, but archiving should run separately and must not block conversation compaction."
}
```

The exact schema can evolve, but the boundary should stay stable: compaction
identifies what is worth carrying forward, while the Archivist owns long-term
storage policy.

## Why Not Only Search Saved Chats?

Saved conversation JSON remains searchable, but after compaction the original
prefix is no longer present in the active conversation history. The continuation
summary is intentionally lossy. Extracting archive candidates during compaction
preserves durable memory while keeping the active model context small.
