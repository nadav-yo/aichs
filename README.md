# aichs

Local desktop agent workspace for software projects. aichs combines chat,
approval-gated tools, git context, file references, conversation history,
compaction, and project-specific extensions in a small PyQt app.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://github.com/nadav-yo/aichs/actions/workflows/tests.yml/badge.svg)

## Status

aichs is early software. It is intended for developers who are comfortable
running a local app that can read a workspace and, with approval, edit files or
run shell commands.

## Install From Source

```bash
git clone https://github.com/nadav-yo/aichs
cd aichs
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python main.py
```

API keys can be configured in **Settings -> Models** or through environment
variables. Requires Python 3.11+.

## Features

- Chat with streaming Markdown, vision-capable models, file mentions, edit/resend, and queued messages
- Approval-gated tools for reading, editing, searching, shell commands, git context, and extension tools
- Workspace browser with file tree, syntax-highlighted tabs, git status, and diffs
- Context usage view with auto-compaction and compacted conversation summaries
- Skills, slash commands, command palette, pinned/exported/searchable conversations
- Project and user extensions for custom tools, prompt context, hooks, badges, and panels

Tool paths are scoped to the workspace. Shell commands and extensions run as the
current user; only enable extensions you trust.

## Documentation

| Topic | |
|---|---|
| Configuration | [docs/configuration.md](docs/configuration.md) |
| Custom model providers | [docs/custom-models.md](docs/custom-models.md) |
| Extensions and custom tools | [docs/extensions.md](docs/extensions.md) |
| Slash-command skills | [docs/skills.md](docs/skills.md) |
| Compaction and decision memory | [docs/compact.md](docs/compact.md) |

## Development

Run the full suite from the repository root:

```bash
pytest -q --cov-fail-under=90
```

Single-file test runs are useful while iterating, but they do not represent the
real coverage number because coverage is measured across the configured package
set. For a quick local check without coverage:

```bash
pytest --no-cov
```

Project agent instructions live in [AGENTS.md](AGENTS.md).

## Packaging

For local development, use `python main.py`. For a distributable desktop build,
use PyInstaller:

```bash
python tools/build_package.py
```

Outputs are written under `dist/`:

| OS | Output |
|---|---|
| Windows | `dist/aichs/aichs.exe` |
| macOS | `dist/aichs.app` and `dist/aichs/` |
| Linux | `dist/aichs/aichs` |

Build on each target OS for that OS; PyInstaller is not a cross-compiler.

## License

MIT License. See [LICENSE](LICENSE).
