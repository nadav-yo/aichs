# Skills (slash commands)

aichs skills follow the ideas behind **[Agent Skills](https://agentskills.io/)** - an open format for giving agents specialized instructions ([overview](https://agentskills.io/home), [specification](https://agentskills.io/specification)). Here each skill is a single Markdown file with YAML frontmatter (not a `SKILL.md` folder), loaded from `AICHS_HOME/skills/` (default `~/.aichs/skills/`) and `.aichs/skills/`.

Type `/` in the composer to pick a skill. It replaces the base system prompt for that turn. You can optionally restrict which tools the model may call.

## Locations

| Path | Scope |
|---|---|
| `AICHS_HOME/skills/*.md` | User-global |
| `.aichs/skills/*.md` | Project-local (same name overrides global) |

## File format

```markdown
---
name: review
description: Code review focused on correctness and security
tools: [read_file, search_files]
---
You are a senior reviewer. Read the relevant files, then give a concise report:
bugs, edge cases, and one concrete fix per issue. Do not edit files unless asked.
```

| Frontmatter key | Required | Description |
|---|---|---|
| `name` | no | Slash-command name (defaults to filename without `.md`) |
| `description` | no | Shown in the skill picker |
| `tools` | no | Allowlist of tool names; omit to allow all tools |

Valid built-in tool names: `read_file`, `edit_file`, `execute`, `search_files`.
Extension tool names may also be used after they are registered.

The body (after the closing `---`) is the skill system prompt for the selected turn.

### Usage Example

To use a skill, simply type `/` followed by the name of the skill (e.g., `/review`) into the composer box. This will activate the skill's context prompt and available tools for that turn.

**Example Interaction:**
1. User: `/review`
2. AICHS: "Initializing code review mode. Please provide the file or code block you would like me to review."
3. User: "Can you check `main.py` for common anti-patterns?"
4. AICHS: (Uses `read_file` tool on `main.py` and processes the review based on the skill's system prompt.)
