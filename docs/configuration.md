# Configuration

Settings are stored in `~/.aichs/settings.json` (written by **Settings** in the app).

| Key | Description |
|---|---|
| `anthropic_api_key` | Fallback if `ANTHROPIC_API_KEY` is unset |
| `openai_api_key` | Fallback if `OPENAI_API_KEY` is unset |
| `provider_api_keys` | Per-provider keys for built-in and custom providers |
| `system_prompt` | Overrides the default system prompt |
| `default_models` | Default model per provider |
| `theme` | `"dark"`, `"modern"`, or `"light"` |
| `font_size` | Chat font size (pt) |
| `compaction.reserve_tokens` | Optional. Tokens held back for the next reply before auto-compaction (omit to scale from each model's context window) |
| `compaction.keep_recent_tokens` | Optional. Recent message tokens to keep verbatim when compacting (omit to scale automatically) |

API keys can also be set via environment variables or **Settings → Models** before launch.
