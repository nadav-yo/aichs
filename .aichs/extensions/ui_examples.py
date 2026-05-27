from pathlib import Path

from services.git_status import is_git_repo, list_file_changes, run_git


def register(registry):
    registry.status_badge(name="workspace_status", provider=workspace_status_badge)
    registry.panel(
        name="workspace_status",
        title="Workspace Status",
        provider=workspace_status_panel,
    )
    registry.status_badge(name="chat_context", provider=chat_context_badge)
    registry.panel(
        name="chat_context",
        title="Chat Context",
        provider=chat_context_panel,
    )


def workspace_status_badge(ctx):
    status = _git_status(ctx.cwd)
    changed = len(status)
    if changed == 0:
        return {
            "label": "Clean",
            "tooltip": "No git changes",
            "tone": "success",
            "panel": "workspace_status",
        }
    return {
        "label": f"{changed} changed",
        "tooltip": "Open workspace status",
        "tone": "warning",
        "panel": "workspace_status",
    }


def workspace_status_panel(ctx):
    root = Path(ctx.cwd).resolve()
    branch = _git_output(ctx.cwd, ["git", "branch", "--show-current"]) or "(detached)"
    status = _git_status(ctx.cwd)

    sections = [
        {
            "heading": "Repository",
            "items": [
                {
                    "title": "Workspace",
                    "subtitle": root.name,
                    "body": str(root),
                    "actions": [
                        {"label": "Refresh", "type": "refresh_panel"},
                    ],
                },
                {"title": "Branch", "subtitle": branch},
            ],
        }
    ]

    if status:
        sections.append({
            "heading": "Changed files",
            "items": [
                {
                    "title": entry["path"],
                    "subtitle": entry["status"],
                    "actions": [
                        {
                            "label": "Open",
                            "type": "open_file",
                            "path": entry["path"],
                        }
                    ],
                }
                for entry in status[:30]
            ],
        })
    else:
        sections.append({
            "heading": "Changed files",
            "items": [{"title": "Working tree clean"}],
        })

    return {
        "title": "Workspace Status",
        "body": "Read-only git status from .aichs/extensions/ui_examples.py.",
        "sections": sections,
    }


def chat_context_badge(ctx):
    count = len(ctx.history)
    if count == 0:
        return {
            "label": "New chat",
            "tooltip": "No messages in this conversation yet",
            "tone": "",
            "panel": "chat_context",
        }
    return {
        "label": f"{count} msgs",
        "tooltip": "Open chat context summary",
        "tone": "accent",
        "panel": "chat_context",
    }


def chat_context_panel(ctx):
    user_messages = [msg for msg in ctx.history if msg.get("role") == "user"]
    assistant_messages = [msg for msg in ctx.history if msg.get("role") == "assistant"]
    last_user = _preview_message(user_messages[-1]) if user_messages else "No user message yet."
    latest_actions = []
    if user_messages:
        latest_actions.append({
            "label": "Copy",
            "type": "copy",
            "text": last_user,
        })
        latest_actions.append({
            "label": "Ask",
            "type": "send_message",
            "text": f"Help me follow up on this request: {last_user}",
        })

    return {
        "title": "Chat Context",
        "body": "Conversation-aware UI from .aichs/extensions/ui_examples.py.",
        "sections": [
            {
                "heading": "Current conversation",
                "items": [
                    {"title": "Model", "subtitle": ctx.model or "(not selected)"},
                    {"title": "Messages", "subtitle": str(len(ctx.history))},
                    {"title": "User messages", "subtitle": str(len(user_messages))},
                    {"title": "Assistant messages", "subtitle": str(len(assistant_messages))},
                ],
            },
            {
                "heading": "Latest user request",
                "items": [{"title": last_user, "actions": latest_actions}],
            },
        ],
    }


def _git_status(cwd):
    if not is_git_repo(cwd):
        return []
    return [
        {
            "status": change.label,
            "path": change.rel_path.replace("\\", "/"),
        }
        for change in list_file_changes(cwd)
    ]


def _git_output(cwd, args):
    return run_git(args, cwd)


def _preview_message(message):
    text = _content_text(message.get("content", ""))
    text = " ".join(text.split())
    if not text:
        return "(non-text message)"
    return text[:140] + ("..." if len(text) > 140 else "")


def _content_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "file":
                parts.append(f"@{block.get('path', 'file')}")
    return " ".join(parts)
