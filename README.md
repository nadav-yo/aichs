# aichs

**aichs is a minimal visual harness for agentic coding.**
Adapt it to your workflows, not the other way around.

Open a repository, chat with a model, and let the agent work with your code
through approved tools, git context, file references, skills, and extensions.
aichs treats the agent as something you can shape: prompts, models, UI behavior,
workflow defaults, skills, extensions, tools, hooks, badges, and panels are all
meant to be customized.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://github.com/nadav-yo/aichs/actions/workflows/tests.yml/badge.svg)

## Status

aichs is early software. It is intended for developers who are comfortable
running a local app that can read a workspace and, with approval, edit files or
run shell commands.

## Why aichs exists

Most coding agents give you a workflow. aichs gives you a workbench. It is for
developers who want to tune the prompts, tools, context, approvals, memory, and
UI around how they already work.

## Install From PyPI

Install the published command with `pipx`:

```bash
pipx install aichs
```

Start it from a repository:

```bash
cd /path/to/your/repo
aichs
```

Or pass the workspace explicitly:

```bash
aichs /path/to/your/repo
```

## Run

By default, `aichs` honors the directory it was started from. Use
`aichs --last-workspace` only when you want to reopen the previously saved
workspace.

Conversation history is stored in user data, not in your repository. Each
workspace gets a stable entry in `AICHS_HOME/workspaces.json` (default
`~/.aichs/workspaces.json`) and its chats are saved under
`AICHS_HOME/<workspace_id>/conversations/`.

API keys can be configured in **Settings -> Models** or through environment
variables. Requires Python 3.11+.

## What It Does

Open a workspace folder, pick a model, and work in one window:

- **Agentic coding**: ask the agent to inspect files, explain code, search the repo, make edits, and run approved shell commands
- **Approval-gated tools**: read, edit, search, git context, shell commands, and extension tools with workspace-scoped paths
- **Coding workspace**: file tree, syntax-highlighted tabs, git status, diffs, file references, and conversation history
- **Context management**: usage view, auto-compaction, compacted summaries, and decision memory for long sessions
- **Customization**: configurable prompts and workflow defaults, slash-command skills, command palette, project/user extensions, custom tools, hooks, badges, and panels
- **Conversation flow**: streaming Markdown, vision-capable models, file mentions, edit/resend, queued messages, pinned chats, search, and export

Shell commands and extensions run as the current user; only enable extensions
you trust.

## Contributing

Contributor setup, source installs, tests, packaging, and release notes live in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

| Topic | |
|---|---|
| Configuration | [docs/configuration.md](docs/configuration.md) |
| Custom model providers | [docs/custom-models.md](docs/custom-models.md) |
| Extensions and custom tools | [docs/extensions.md](docs/extensions.md) |
| Slash-command skills | [docs/skills.md](docs/skills.md) |
| Compaction and decision memory | [docs/compact.md](docs/compact.md) |
| Performance north star | [docs/performance-north-star.md](docs/performance-north-star.md) |
| YUK user kits | [docs/yuk.md](docs/yuk.md) |

## FAQ

### What does aichs stand for?

It is a play on Hebrew "ichs" (`איכס`), from Arabic `إخسا`, roughly "yuck" or
"ew." The name is a little cursed on purpose.

### Why use aichs when Cursor, Claude Code, Codex, and other agent tools exist?

You may not need it. Those tools are wonderful. aichs is for when you want a
small, local, hackable agentic coding workspace that is shaped around your own
habits.

### Is aichs trying to replace my IDE?

No. It is a companion workbench: one window for the agent conversation, repo
context, files, diffs, approvals, custom prompts, and extension tools.

### Can I contribute something?

Very much. Small fixes, weird ideas, extensions, docs, and sharp opinions are
all welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

### Does the S feel forced?

Yes. It is.

## License

MIT License. See [LICENSE](LICENSE).
