# aicc — AI Coding Companion

A **minimal** desktop agent for the repository on your machine: chat, an approval-gated tool loop, git, and an adaptable environment that supports custom extensions—not a full IDE.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://github.com/nadav-yo/aicc/actions/workflows/tests.yml/badge.svg)

## Quick start

```bash
git clone https://github.com/nadav-yo/aicc
cd aicc
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

CI runs on Ubuntu and Windows ([Tests workflow](https://github.com/nadav-yo/aicc/actions/workflows/tests.yml)). Git-dependent tests skip if `git` is unavailable. Large Qt widgets (`chat_panel`, `main_window`, …) are not in the coverage gate yet.

Project agent instructions for this repo: [AGENTS.md](AGENTS.md) (loaded into the app when this folder is the workspace).

## What it does

Open a workspace folder, pick a model, and work in one window:

- **Chat** — streaming Markdown, vision, `@file` mentions, edit/resend, branch, queue while busy
- **Agent** — `read_file`, `edit_file`, `execute`, `search_files`; extension tools; parallel reads; you approve edits (once per chat) and each shell command
- **Repo** — file tree, syntax-highlighted tabs, git status/diffs, `AGENTS.md` in the system prompt
- **Context** — usage ring, breakdown, auto-compaction
- **Extras** — `/` skills, `Cmd+K` palette, themes, export/pin/search history

Paths stay inside the workspace for read/search. Shell runs as your user—not a sandbox.

## Documentation

| Topic | |
|---|---|
| Custom providers (Gemini, Ollama, …) | [docs/custom-models.md](docs/custom-models.md) |
| Extensions and custom tools | [docs/extensions.md](docs/extensions.md) |
| Slash-command skills | [docs/skills.md](docs/skills.md) |
| Settings file | [docs/configuration.md](docs/configuration.md) |
| Feature backlog | [features.md](features.md) |

## License

Copyright © 2026 AI Coding Companion. [MIT License](https://opensource.org/licenses/MIT).
