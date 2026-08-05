# Changelog

## Next

## 0.5.2

- Restore frameless chrome dragging; center Chat Focus / Review / Editor Focus
- Content-sized horizontal scroll for long names in Files and Git history
- Cap Git history commit subjects with ellipsis; show only the first ref badge (full text on hover)
- macOS pipx identity: Dock hover and Apple menu title say Aichs (works without PyObjC)
- File-tree tooltips use forward-slash paths on every OS
- Add `CHANGELOG.md` with a `## Next` section that becomes `## <version>` on release
- Ship Changelog in Help → Docs (packaged with the app; listed first)
- Require agents to document user-facing changes under `## Next` (see `AGENTS.md`)
- Check PyPI for updates on startup (at most daily) and show a dismissible upgrade banner

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
