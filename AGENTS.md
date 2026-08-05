# AGENTS.md

Rules for agents editing **this repo** (the aichs PyQt6 app).

## Stack

Python 3.11+, PyQt6. Core logic: `services/` (especially `tools.py`, `tool_policy.py`, `tool_registry.py`, `chat.py`), `storage/`, `ui/`. User data and examples: `~/.aichs/`; per-project `.agents/skills/`, `.aichs/extensions/`.

## Tests

From repo root only:

```bash
pytest -q --cov-fail-under=90
```

Keep measured coverage **~91%+** (gate is 90%). A single-file/IDE run is not a valid coverage check. New behavior → tests in `tests/`; use `tmp_path` / `tests/conftest.py` (isolated fake home).

## Editing

- Small diffs; match existing patterns.
- Tool paths must stay in the workspace (`services/tool_policy.py`).
- Extensions: `register(registry)` in `.aichs/extensions/*.py` or `.aichs/extensions/*/extension.py`.
- Do not commit or push unless asked.

## Changelog

- Document user-facing changes in [`CHANGELOG.md`](CHANGELOG.md) under `## Next` in the same change as the code.
- Keep bullets short and concrete; skip pure refactors, test-only, or internal churn unless it affects users.
- Do not rewrite released version sections except to fix factual errors.
- On release, `## Next` is renamed to `## <version>` and a new empty `## Next` is opened (see `.github/workflows/release.yml`).

## Docs

[extensions](docs/extensions.md) · [skills](docs/skills.md) · [models](docs/custom-models.md) · [settings](docs/configuration.md) · [changelog](CHANGELOG.md)
