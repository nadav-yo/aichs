# Changelog

User-facing changes for aichs. New work goes under **Next**. On release, that section is renamed to the version and a fresh **Next** is opened.

## Next

- Add `CHANGELOG.md` with a `## Next` section that becomes `## <version>` on release
- Require agents to document user-facing changes under `## Next` (see `AGENTS.md`)

## 0.5.1

- Fix model and provider pickers not refreshing after Settings edits until restart
- Use the Archivist avatar for `/archivist` slash-mode replies
- Linkify backtick-wrapped workspace file paths in assistant replies
- Show token usage as ↓ input / ↑ output beside locale-stable timestamps
- Keep clock time on non-today chat timestamps (add date; year when needed)
- Set the application display name to Aichs for OS chrome (pipx / Dock)
- Move Chats / Files / Git / Inspect / Canvas and focus modes into the frameless title bar

## 0.5.0

- Refine workbench navigation and terminal tabs
