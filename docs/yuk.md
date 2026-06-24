# Your User Kits  (YUKs)

YUK files (`.yuk`) are portable AICHS personalization packages. They are meant
for sharing or moving a profile, not for full backups.

## What YUK Includes

The export dialog lets you choose whole sections or individual items:

| Section | Contents |
|---|---|
| Personality & Prompts | Custom system prompt and prompt-related settings that differ from built-in defaults |
| Crew | Crew prompts, enabled flags, colors, portraits, and crew model choices |
| Skills | Global and project `.agents/skills/*.md` files |
| Extensions | Global and project extensions with enabled/disabled state |
| Avatars | Custom avatar files copied into `AICHS_HOME/avatars/` |

Models, provider configuration, API keys, conversations, runtime approvals,
extension state, and workspace history are not exported.

Prompt settings only appear when they differ from the built-in defaults. If a
prompt is not listed during export, the importing app will keep using its own
default for that prompt.

## Import Safety

Import previews package contents before applying them. Extension Python is not
executed during preview. Existing skills and extensions are shown as conflicts
so you can overwrite, skip, or rename them. Existing settings can be overwritten
or skipped.

YUK packages reject unsafe zip paths such as absolute paths, drive-prefixed
paths, `..` traversal, and symlinks.
