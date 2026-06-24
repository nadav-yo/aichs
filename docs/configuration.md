# Configuration

By default, settings are stored in `~/.aichs/settings.json` (written by
**Settings** in the app). Set `AICHS_HOME` before launch to move all app-owned
user data, including settings, models, skills, conversations, extensions, MCP
state, and recent workspaces, to another directory.

| Key | Description |
|---|---|
| `anthropic_api_key` | Fallback if `ANTHROPIC_API_KEY` is unset |
| `openai_api_key` | Fallback if `OPENAI_API_KEY` is unset |
| `provider_api_keys` | Per-provider keys for built-in and custom providers |
| `system_prompt` | Overrides the default system prompt |
| `file_review_prompt_template` | Replaces the first line of **Ask File** drafts. Supports `{mention}` and `{path}` |
| `diagnostic_fix_prompt_template` | Replaces only the first line of diagnostic fix drafts. Supports `{mention}`, `{path}`, and `{line}` |
| `git_fix_prompt_template` | Replaces only the first line of git pull/push failure drafts. Supports `{action}`, `{label}`, `{repo}`, `{command}`, `{exit_code}`, and `{output}` |
| `compact_resume_prompt` | Default resume message used after compact-and-resume when no extension prompt is supplied |
| `auto_title_prompt_instructions` | Replaces the title-writing instructions; the first user message is attached automatically |
| `compaction_summary_guidance` | Optional additive guidance appended to the fixed compaction summary prompt |
| `archivist_prompt` | Replaces instructions for the built-in `/archivist` slash command; command name and tools stay fixed |
| `commit_message_prompt_addition` | Optional additive guidance appended to generated commit-message requests |
| `default_models` | Default model per provider |
| `theme` | `"dark"`, `"modern"`, or `"light"` |
| `font_size` | Chat font size (pt) |
| `trash_retention_days` | Days to keep deleted chats in Trash before permanent removal (default 14) |
| `compaction.reserve_tokens` | Optional. Tokens held back for the next reply before auto-compaction (omit to scale from each model's context window) |
| `compaction.keep_recent_tokens` | Optional. Recent message tokens to keep verbatim when compacting (omit to scale automatically) |

API keys can also be set via environment variables or **Settings → Models** before launch.

MCP servers are configured separately in standard `mcp.json` files; see
[MCP](mcp.md).
