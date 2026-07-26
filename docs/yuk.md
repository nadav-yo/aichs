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

## Organization Governance

YUK remains a portable personalization package. It is not a policy authority and
it does not make governance decisions.

In governed installations, a signed organization policy can require imported
YUK packages by `package_id` and package SHA-256. During import, `aichs` records
package metadata and the installed skill/extension asset hashes in
`AICHS_HOME/organization/yuk.installed.json`. Later governed skill and extension
loading checks that record and verifies the current asset hashes before the
capability is allowed.

This gives organizations a way to require a known starter kit without treating
that kit as trusted policy. The signed policy remains the source of truth. See
[Organization Governance](organization-governance.md#required-yuk-assets).
