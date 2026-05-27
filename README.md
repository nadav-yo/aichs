# aichs - AI Choding Harness Studio

A **minimal** desktop agent studio for the repository on your machine: chat, an approval-gated tool loop, git, and an adaptable environment that supports custom extensions - not a full IDE.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://github.com/nadav-yo/aichs/actions/workflows/tests.yml/badge.svg)

## Quick start

```bash
git clone https://github.com/nadav-yo/aichs
cd aichs
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python main.py
```

Keys can also be set in **Settings → Models**. Requires Python 3.11+, PyQt6, `anthropic`, `openai`, `markdown`, `pygments`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Run the **full** suite from the repo root for the real coverage number. If you run a single test file from the IDE (or `pytest tests/test_foo.py`), pytest still measures all gated packages but only executes code that file touches — the total can look ~28% and is not meaningful.

CI enforces **≥90%** line coverage (`pytest -q --cov-fail-under=90`); the full suite currently runs **~91%+**, leaving headroom below the gate. For a quick local check without coverage: `pytest --no-cov`.

CI runs on Ubuntu and Windows ([Tests workflow](https://github.com/nadav-yo/aichs/actions/workflows/tests.yml)). Git-dependent tests skip if `git` is unavailable. Large Qt widgets (`chat_panel`, `main_window`, …) are not in the coverage gate yet.

Project agent instructions for this repo: [AGENTS.md](AGENTS.md) (loaded into the app when this folder is the workspace).

## What it does

Open a workspace folder, pick a model, and work in one window:

- **Chat** — streaming Markdown, vision, `@file` mentions, edit/resend, branch, queue while busy
- **Agent** — `read_file`, `edit_file`, `execute`, `search_files`; extension tools; parallel reads; you approve edits (once per chat) and each shell command
- **Repo** — file tree, syntax-highlighted tabs, git status/diffs, `AGENTS.md` in the system prompt
- **Context** — usage ring, breakdown, auto-compaction
- **Extras** — `/` skills, `Cmd+K` palette, themes, export/pin/search history

Paths stay inside the workspace for read/search. Shell runs as your user—not a sandbox.

## FAQ

### What does aichs stand for?

**AI Choding Harness Studio.** It is also a play on the Hebrew word "ichs" (`איכס`). The name is a little cursed on purpose.

### Why do I need aichs when I already have Cursor, Claude Code, Codex, or another agent tool?

You probably do not. Those tools are wonderful. aichs is for when you want a small, local, hackable agent workspace that is shaped around your own habits.

### Is aichs trying to replace those tools?

No. It is closer to a personal workbench: one window, your repo, your tools, your prompts, your extensions, your approvals. Like the old "this is mine" line, that is the point.

### Can I contribute something?

Very much. Please do. Small fixes, weird ideas, extensions, docs, and sharp opinions are all welcome.

### Does the S feel forced?

Yes. It is.

## Documentation

| Topic | |
|---|---|
| Custom providers (Gemini, Ollama, …) | [docs/custom-models.md](docs/custom-models.md) |
| Compaction and archiving | [docs/compact.md](docs/compact.md) |
| Extensions and custom tools | [docs/extensions.md](docs/extensions.md) |
| Slash-command skills | [docs/skills.md](docs/skills.md) |
| Settings file | [docs/configuration.md](docs/configuration.md) |
| Feature backlog | [features.md](features.md) |

## License

Copyright © 2026 AI Choding Harness Studio. [MIT License](https://opensource.org/licenses/MIT).
