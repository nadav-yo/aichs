# AGENTS.md

Rules for agents editing **this repo** (the aichs PyQt6 app).

## Stack

Python 3.11+, PyQt6. Core logic: `services/` (especially `tools.py`, `tool_policy.py`, `tool_registry.py`, `chat.py`), `storage/`, `ui/`. User data and examples: `~/.aichs/`; per-project `.aichs/skills/`, `.aichs/extensions/`.

## Tests

From repo root only:

```bash
pytest -q --cov-fail-under=90
```

Keep measured coverage **~91%+** (gate is 90%). A single-file/IDE run is not a valid coverage check. New behavior → tests in `tests/`; use `tmp_path` / `tests/conftest.py` (isolated fake home).

## Editing

- Small diffs; match existing patterns.
- Tool paths must stay in the workspace (`services/tool_policy.py`).
- Extensions: `register(registry)` in `.aichs/extensions/*.py`.
- Do not commit or push unless asked.

## Docs

[extensions](docs/extensions.md) · [skills](docs/skills.md) · [models](docs/custom-models.md) · [settings](docs/configuration.md)
