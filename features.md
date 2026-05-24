# Feature Backlog

Status: `[ ]` pending · `[-]` in progress · `[x]` done

## [x] Stop button
Replace the Send button with a red ■ Stop button while a response is streaming.
Clicking it cancels the ChatThread (including any running bash subprocess).
On cancel, the partial response is kept in the bubble and input is re-enabled.

## [x] Markdown rendering
After streaming completes, convert the bubble text from raw markdown to HTML
using the `markdown` library. Bold, italic, headers, lists, inline code all rendered.
Code blocks are already extracted by `finalize()` — only prose needs conversion.

## [x] Syntax highlighting
Use `pygments` (monokai dark theme) in the FileViewerPanel for both `open_file`
and `open_content`. Detect language from file extension or language tag.
Also applies to code opened via ArtifactCard "Open ↗".

## [x] Conversation rename
Double-click a conversation title in the history panel to edit it inline.
Press Enter to save, Escape to cancel. Persists to the JSON file via the store.

## [x] Smart auto-scroll
Track whether the user has manually scrolled up during streaming.
If yes, stop force-scrolling. Show a floating ↓ button (bottom-right of scroll area).
Clicking it resumes auto-scroll. Auto-resumes when user scrolls back to the bottom.

## [x] Settings panel
Gear icon (⚙) at the bottom of the left panel opens a QDialog.
Fields: Anthropic API Key, OpenAI API Key (password-masked).
Saved to ~/.aicc/settings.json. Loaded at startup and applied as env vars
(only if the env var isn't already set externally).

## [x] Image / vision input
Replace the QLineEdit input with a QTextEdit that accepts pasted images (Cmd+V).
Show a thumbnail strip above the text field for pending images.
On send, builds a list-format content block (text + base64 images).
Supported by both Anthropic and OpenAI vision APIs.
User bubble shows image thumbnails inline.

## [x] Search conversations
Search bar at the top of the History tab (replaces or sits above the list).
Filters conversation items by title as the user types.
Searches message content too (loads JSON files on demand).
"No results" label when nothing matches.

---

**Chat UX**

## [x] Regenerate response
Re-run the last assistant turn without retyping the user message.
Removes the last assistant bubble, truncates history, and resubmits.

## [x] Edit & resend
Click a user bubble to edit it inline.
Truncates history after that point and resends the edited message.

## [x] Copy actions
Copy bubble text via context menu or keyboard shortcut.
Copy code block content from an ArtifactCard.

## [x] Message timestamps
Show send time on hover or as a subtle secondary line in each bubble.
Persist `created_at` per message in the conversation JSON.

## [x] Branch from message
Fork the conversation at any message into a new thread.
Copies history up to that point into a new conversation file.

---

**Input & attachments**

## [x] Drag-and-drop images
Drop image files onto the composer to attach them.
Same thumbnail strip and send flow as paste (Cmd+V).

## [x] File attachments
Attach a file from the Files tab or via `@filename` autocomplete in the composer.
Send as a text excerpt or base64 for supported types.

## [x] Custom system prompt
Editable in Settings with a reset-to-default button.
Saved to `~/.aicc/settings.json` and passed to `build_system()`.

## [x] Default model
Remember last-used or preferred model per provider in `settings.json`.
Applied when starting a new conversation.

---

**History & workspace**

## [x] Pin conversations
Pin important threads to the top of the History list.
Persisted in conversation JSON or a sidecar index.

## [x] File tree refresh
Auto-refresh the Files tab when the workspace changes on disk.
Highlight files created or modified during the current session.

## [x] Remember layout
Persist splitter sizes and last-open workspace path to `settings.json`.
Restored on startup.

---

**Safety & control**

## [ ] Undo file changes
Track every `write_file` call during a session. "Undo last change" button in the
git panel (or Cmd+Z) restores the previous file content from an in-memory snapshot.

---

**Context & memory**

## [x] Context usage ring
Circular progress indicator in the chat bar showing context window fill level.
Click to open a breakdown: system prompt, rules (AGENTS.md), workspace, tools,
skills, and messages — each with size in KB and token estimate.

## [x] Project memory (AGENTS.md)
Read `AGENTS.md` from the workspace root (OpenAI Codex / community standard).
Injected into the system prompt under `## Project Memory (AGENTS.md)`.
Green banner shown below the top bar when active.

## [x] Auto-title via AI
After the first assistant reply, fire a cheap background LLM call
(`haiku` / `gpt-4.1-mini`) to generate a 5–7 word title.
Replaces the current "first 50 chars of user message" fallback.

## [x] Conversation export
Save the current conversation as a Markdown file (bubbles + code blocks).
Accessible from a right-click menu on the conversation item or a toolbar button.

---

**Power UX**

## [x] Keyboard shortcuts
Global: Cmd+N new chat, Cmd+W close viewer tab, Cmd+, open settings,
Esc stop streaming. In chat: ↑ to re-edit last message, Cmd+Enter to send.

## [x] Command palette
Cmd+K opens a fuzzy-search palette over recent conversations, slash-commands
(/new, /export, /compact, /model, /clear), and file names. Inspired by VS Code and Pi.

---

**Agentic**

## [ ] Task progress panel
While the agent is in its tool-use loop, show a collapsible panel listing
each step: ✓ read_file, ✓ bash, ⟳ write_file… Gives a live map of what
the agent is doing, like Claude Code's task list.

## [x] Parallel tool execution
When the model returns multiple tool_use blocks in one turn, execute them
concurrently using a thread pool rather than sequentially.
Significant speedup for read-heavy tasks.
## [-] Skills / slash commands
Type `/` in the composer to open a fuzzy-filtered skill picker.
Skills live in `assets/skills/*.md` (built-in) and `~/.aicc/skills/*.md` (user).
Each skill has a name, description, prompt, and optional tool allowlist.
Selected skill shown as a dismissible chip; prompt replaces system base for that turn.
