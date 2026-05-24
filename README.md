# aicc — AI Coding Companion

A desktop coding assistant built with PyQt6. Streams responses from Anthropic and OpenAI models, runs agentic tool-use loops (read, write, bash, search), and renders results with syntax highlighting and Markdown.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![PyQt6](https://img.shields.io/badge/UI-PyQt6-green)

---

## Installation

```bash
git clone https://github.com/nadav-yo/aicc
cd aicc
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### API keys

Set your keys before launching, or enter them in **Settings → Models**:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
```

---

## Features

### Chat
- **Streaming** responses from Anthropic and OpenAI models
- **Stop button** — cancel mid-stream, keeps partial response
- **Queued messages** — draft follow-ups while the agent is still responding, with cancel controls
- **Markdown rendering** — prose, tables, inline code in bubbles
- **Image / vision input** — paste or drag images into the composer
- **File mentions** — attach workspace files with `@path` autocomplete
- **Edit & resend** — double-click any user bubble to edit it
- **Regenerate** — re-run the last assistant turn
- **Branch from message** — fork the conversation at any point

### Agentic tools
The model can call tools in a loop until the task is done:

| Tool | What it does |
|---|---|
| `read_file` | Read a file in the workspace |
| `write_file` | Create or overwrite a file |
| `bash` | Run a host shell command (PowerShell on Windows, `/bin/sh` elsewhere) |
| `search_files` | Search files with `rg` when available, with a Python fallback |

Parallel tool execution — read-safe tools run concurrently.

### Workspace
- **File tree** — browse and open files; auto-refreshes on disk changes
- **File viewer** — tabbed editor with syntax highlighting (Pygments)
- **Git panel** — diff and status for the open repository
- **AGENTS.md** — project memory injected into every system prompt ([AGENTS.md standard](https://agents.md/))

### Context
- **Context ring** — live fill indicator for the context window
- **Context breakdown** — click the ring to see how tokens are distributed
- **Auto-compaction** — summarises old messages when approaching the limit

### Skills / slash commands
Type `/` in the composer to open the skill picker. Skills are Markdown files with a YAML frontmatter that set the system prompt and restrict which tools the model can use.

```
~/.aicc/skills/review.md   ← user-global
.aicc/skills/widget.md     ← project-local (wins on name collision)
```

See [docs/skills.md](docs/skills.md) for the file format.

### UX
- **Command palette** `Cmd+K` — fuzzy-search conversations, slash commands, files
- **Conversation history** — search, rename, pin, export as Markdown
- **Auto-title** — cheap background LLM call names each conversation
- **Keyboard shortcuts** — `Cmd+N` new chat, `Cmd+Enter` send, `Esc` stop, `↑` edit last message
- **Dark / modern / light themes** — follows system appearance, live-reloads

---

## Custom models

Use **Settings → Models → Add provider** or add `~/.aicc/models.json` to use any OpenAI-compatible endpoint — Gemini, Ollama, DeepSeek, OpenRouter, or a corporate proxy:

```json
{
  "providers": {
    "google": {
      "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
      "api": "openai-compatible",
      "apiKey": "GEMINI_API_KEY",
      "models": [
        { "id": "gemini-2.5-pro",   "name": "Gemini 2.5 Pro" },
        { "id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash" }
      ]
    }
  }
}
```

More examples: [docs/custom-models.md](docs/custom-models.md)

---

## Settings

`~/.aicc/settings.json` — written by the settings dialog. Keys:

| Key | Description |
|---|---|
| `anthropic_api_key` | Fallback if `ANTHROPIC_API_KEY` env var is unset |
| `openai_api_key` | Fallback if `OPENAI_API_KEY` env var is unset |
| `provider_api_keys` | Per-provider API keys for built-in and custom providers |
| `system_prompt` | Custom system prompt (overrides the default) |
| `default_models` | Per-provider default model selection |
| `theme` | `"dark"`, `"modern"`, or `"light"` |
| `font_size` | Chat font size in pt |

---

## Known Issues

- Chat runs are single-active-run, not fully independent per conversation.
- If chat A is streaming and you switch to chat B, chat A's response keeps running and saves back to chat A, but the live stream and typing indicator are not shown while chat B is visible.
- Queued messages are isolated per chat, but they only start when their originating chat is visible.
- Compaction is still global to the active panel and should be made conversation-bound before relying on it during chat switches.

---

## Project layout

```
aicc/
├── main.py                  # Entry point
├── config.py                # Paths and constants
├── services/
│   ├── model_registry.py    # Loads built-ins + ~/.aicc/models.json
│   ├── chat.py              # Streaming agentic loop (Anthropic + OpenAI)
│   ├── tools.py             # Tool implementations
│   ├── skills.py            # Skill file loader
│   ├── compaction.py        # Context compaction
│   └── workspace.py         # System prompt + AGENTS.md injection
├── ui/
│   ├── widgets/             # QWidget subclasses
│   └── theme.py             # Palette, stylesheet helpers
├── storage/                 # Conversation persistence
├── assets/
│   └── skills/              # Built-in skill definitions (empty — add your own)
└── docs/                    # Extended documentation
```

---

## Requirements

- Python 3.11+
- PyQt6 6.11+
- `anthropic`, `openai`, `markdown`, `pygments`
